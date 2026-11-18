**Assessment: recognizably Saudi, but not consistently natural.** The assistant often uses suitable Saudi expressions such as «ودك»، «وش»، «هلا والله» and «إيميلك». Its main weaknesses are repetition, switching to formal administrative language, awkward phrasing, and missing the purpose of a follow-up. Adding more slang alone will not solve these.

I tested the configured `openai/gpt-oss-120b` model with the existing response temperature of 0.1. The sample contains 51 generated replies: 45 turns across 14 full-graph conversations, plus six fixed-state Respond/Validate probes. Of these, 49 replies are OLE5 and two are RiCH. The fixed opening message was reviewed separately. All conversations completed, and all 51 final replies passed the application's existing guards. The two existing language/response-guard test files also passed: **29 tests**.

These are live model outputs, with temporary sheets, synthetic customer data, mock tickets, and the repository's stub knowledge passages. Live Qdrant retrieval was not tested. In particular, the stub only recognizes fixed questions, so no-answer rates and escalation behavior here are not measurements of production retrieval. This is a qualitative review of one sample per case, not a native-speaker panel or statistical benchmark. Exact outputs and per-turn metadata are in [transcripts.json](transcripts.json).

**What already works**

- Greeting: «وعليكم السلام، بخير الحمدلله، كيف أقدر أساعدك؟» is warm and natural.
- Ticket submission: «رفعت التذكرة، رقمها …، الرد يوصل لك على إيميلك المسجل.» is clear and conversational.
- The draft-saving answer while a phone detail was pending answered the new question and then returned to the pending detail. That is useful conversational behavior.
- Arabic remained the conversation language after the customer supplied `Android 14`. Technical identifiers can stay unchanged without switching the surrounding prose to English.

**Where to improve, in priority order**

1. **Handle the customer's next meaning before polishing the accent.**

   In `second-request-queued`, after asking for the phone OS, the customer added «وبعد البحث في الأرشيف بطيء مرة». The next reply repeated the previous OS question **word for word**, without acknowledging the archive issue. In `answer-then-simplify`, «ما فهمت عليك، وضحها لي أكثر» received a ticket offer instead of simpler steps. That turn recorded `search_outcome=no_answer` and `stage=offer` under the stub backend.

   Inspect [router intent definitions and examples](../../src/ole5_chat/agent/prompts/router.py:9), [retained evidence selection](../../src/ole5_chat/agent/prompts/respond.py:252), and [response stage guidance](../../src/ole5_chat/agent/prompts/respond.py:174). The prompt already recognizes rephrasing; add regression assertions for the exact distinction between “I don't understand” and “I tried it and it failed.” Require recognition of a newly reported issue before repeating a pending detail question. Acknowledgements must reflect recorded workflow actions.

   For the simplification case, a suitable reply using the supplied evidence would be: «افتح إعدادات النظام وانزل لآخر الصفحة. هناك تلقى «العلامة المائية»؛ ارفع الشعار واضغط «حفظ».» Recheck the full conversation against real retrieval before concluding which production route needs changing.

2. **Remove repeated acknowledgements and duplicate detail requests.**

   Five sampled replies began with «أفهم إن». One answer to «ليش تحتاجها؟» said: «عشان نحدد إذا العطل يخص أندرويد أو iOS، نحتاج نعرف نظام تشغيل جوالك. نحتاج نظام تشغيل جوالك عشان نكمل التحضير.» Another repeated the request immediately after the customer explicitly said «لا تعيد علي نفس الكلام».

   Improve [persona question/voice rules](../../products/ole5/chat.yaml:19) and [detail-request guidance](../../src/ole5_chat/agent/prompts/respond.py:181). Ask for the detail once. If explaining why, attach the reason to that question rather than adding another reminder. For example: «عشان أوضح للفريق أي نسخة من التطبيق فيها المشكلة. جوالك آيفون ولا أندرويد؟» Use the first person for the assistant and refer to the support team separately. The current config already asks for this; the output does not consistently follow it.

3. **Keep the voice consistent through cancellation and the welcome.**

   Actual cancellation output: «تم إلغاء طلب وضع الليلي، وما تم رفع أي تذكرة.» This is formal, passive, and grammatically awkward. Another cancellation mixed colloquial speech with «وما تم رفع أي تذكرة».

   The [fixed Arabic welcome](../../products/ole5/chat.yaml:8) also uses «أجيب عن أسئلتك حول النظام» and «الاستعلام عن تذاكر الدعم المرفوعة», while later replies use spoken Najdi. A shorter opening could be: «هلا بك، أنا مساعد دعم Ole5. أجاوبك عن النظام وأساعدك في تذاكر الدعم. وش تحتاج؟»

   Review [cancellation guidance](../../src/ole5_chat/agent/prompts/respond.py:185) and [fallback text](../../src/ole5_chat/agent/ticket_action_guard.py:213). When no ticket was submitted for the current request, prefer: «تمام، وقفت تجهيز التذكرة وما أرسلت شي.» Keep exact screen names and technical terms formal where needed; natural Saudi support speech does not require slang in every sentence.

4. **Teach customary Saudi courtesy responses explicitly.**

   Customer: «يعطيك العافية». Actual response: «يعطيك العافية، كيف أقدر أساعدك؟» A more fitting response is «الله يعافيك، وش تحتاج؟» The existing [greeting rules](../../products/ole5/chat.yaml:9) cover السلام عليكم and صباح الخير but omit common courtesy exchanges. Add examples for «يعطيك العافية» → «الله يعافيك» and «ما قصرت» → a brief acknowledgement such as «حاضرين» or «العفو», chosen for context.

   Also correct the typo «يسلعدك» in the [first-detail example](../../src/ole5_chat/agent/prompts/respond.py:117). It did not appear in these sampled replies, but examples should be clean models of the desired voice.

5. **Use natural Arabic around technical facts and preserve their meaning.**

   Ticket lookup exposed the English description `Automatic translation of message text` and used «هالمعنى إن». Describe the ticket topic in Arabic, such as «ترجمة نص المراسلة تلقائيًا», while preserving the ticket identifier. A simpler status explanation is «يعني فريق الدعم استلمها، وما يحتاج منك شي الحين.» Review [ticket-status guidance](../../src/ole5_chat/agent/prompts/respond.py:178), which already requests customer-language descriptions but did not reliably produce them here.

   The urgent outage message «ولا موظف عندنا يقدر يسجل دخول» became «أفهم إن الموظف ما يقدر يسجل دخول»—losing the fact that all employees were affected. Preserve that scope when acknowledging the problem. In the support-hours reply, prefer «٦ المساء» to «٦ المغرب» when translating a configured 18:00 time.

   Five replies included an account-access limitation; some concerned an app crash or attachment problem rather than a request to inspect an account. Use that limitation only when it helps answer the actual request, as the existing response rules intend.

6. **Measure Saudi voice and naturalness explicitly.**

   [language_matches](../../evals/graders.py:70) only compares Arabic/English. [mini_replies](../../pyproject.toml:93) only checks `no_fallback`, and its cases have empty expected turn objects. A reply can therefore pass while being repetitive, overly formal, or unresponsive to the latest message—as these results demonstrate.

   Add offline evaluation criteria for register consistency, customary courtesy, repetition within and across turns, responding to the latest intent, and preserving facts. Keep dialect and naturalness separate from grounding and workflow correctness. Score whole conversations as well as individual replies; do not use the number of Saudi slang words as a quality score. Include native Saudi reviewer calibration before treating an automated style judge as authoritative.

   The highest-value regression cases from this run are the courtesy response, cancellation register, duplicate phone question, simplification follow-up, all-employees outage acknowledgement, and the newly reported archive problem. Keep the existing factual and ticket-action checks when evaluating any rewrite.

**Additional RiCH configuration issue**

The [RiCH persona](../../products/rich/chat.yaml:18) says “No slang,” but the [shared final instruction](../../src/ole5_chat/agent/prompts/respond.py:329) requires spoken Najdi for every Arabic reply. The two sampled RiCH replies mixed registers. Choose the intended RiCH Arabic register explicitly and make the shared instruction respect that product setting. The OLE5 persona already clearly requests a Riyadh/Najdi voice.

Only this review and the recorded transcripts were added. Application prompts and behavior were not changed.
