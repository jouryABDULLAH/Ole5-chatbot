**Bilingual chat validation**
Live model calls with fixed evidence, isolated sheets and mock tickets. Checks cover facts, routing, language and workflow behavior. Passing them is not a native-speaker naturalness score.
| Suite | Passed | Scored | Unscored |
|---|---:|---:|---:|
| bilingual_voice | 10 | 10 | 0 |

**Response-stage coverage**
| Stage | Arabic | English |
|---|---:|---:|
| other | 5 | 5 |

**Cases needing review**

**Recorded responses and conversations**

**bilingual_voice / ar-capabilities — PASS**

Customer: وش تقدر تساعدني فيه؟

Assistant: أقدر أساعدك في استخدام نظام Ole5، أشرح لك الإجراءات والوظايف، أرفع لك تذكرة دعم إذا واجهتك مشكلة، وأستعلم لك عن حالة تذاكرك.

**bilingual_voice / ar-capabilities-followup — PASS**

Customer: طيب تقدر تعمل ايه تاني؟

Assistant: أقدر أساعدك في استخدام نظام Ole5: أشرح لك الإجراءات، أوجهك لحل المشكلات، وإذا ما انحلت أقدر أساعدك في رفع تذكرة دعم وأستعلم عن حالتها.

**bilingual_voice / ar-preparation-control-question — PASS**

Customer: أقدر أوقف تجهيز التذكرة قبل ما أرسلها؟

Assistant: نعم، تقدر توقف تجهيز التذكرة قبل ما تُرسلها. حالياً ما فيه أي تجهيز قيد تنفيذ، فما في شيء نوقفه الآن.

**bilingual_voice / ar-rich_capabilities — PASS**

Customer: هلا، وش تسوي منصة RiCH؟

Assistant: منصة RiCH تمكّنك من إرسال رسائل SMS وتوليد رموز OTP لعملائك عبر بوابة الويب أو باستخدام الـ API. تقدر تدير حسابك، تراقب الإرسال، تتحقق من حالة الرسائل. بالإضافة إلى ذلك أقدر أساعدك في استخدام البوابة أو الـ API، أفتح لك تذكرة دعم إذا احتجت، وأتحقق من حالة أي تذكرة سبق إنشاؤها.

**bilingual_voice / ar-submitted_cancellation — PASS**

Customer: ألغ التذكرة المرفوعة T-1001

Assistant: ما أقدر ألغي تذكرة تم رفعها من هنا. التذكرة T-1001 صارت في نظام الدعم، وفريق الدعم هو المسؤول عن أي تعديل أو إلغاء. إذا تحتاج إلغاء أو تعديل، تواصل معهم عبر القناة المعتادة أو افتح تذكرة جديدة توضح طلبك.

**bilingual_voice / en-capabilities — PASS**

Customer: What can you help me with?

Assistant: I can help you troubleshoot Ole5 features, guide you through actions, prepare a support ticket when needed, and look up the status of any tickets you’ve opened.

**bilingual_voice / en-capabilities-followup — PASS**

Customer: Okay, what else can you do?

Assistant: I can answer questions about Ole5, help you troubleshoot actions, rules and failures, prepare a support ticket for you, and look up the status of any tickets you’ve opened. Let me know what you need help with.

**bilingual_voice / en-preparation-control-question — PASS**

Customer: Can I stop preparing a ticket before I send it?

Assistant: Yes, you can cancel a ticket while it’s being prepared, but there’s no ticket in preparation right now, so there’s nothing to stop.

**bilingual_voice / en-rich_capabilities — PASS**

Customer: Hi, what can you help me with in RiCH?

Assistant: Hello, I can guide you on using the RiCH portal and API, help you send SMS or OTP messages, troubleshoot delivery issues, and assist with creating or checking the status of support tickets.

**bilingual_voice / en-submitted_cancellation — PASS**

Customer: Cancel the submitted ticket T-1001.

Assistant: I’m sorry, but I can’t cancel a submitted ticket from here.
