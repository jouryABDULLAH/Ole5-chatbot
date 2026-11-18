**Bilingual chat validation**
Live model calls with fixed evidence, isolated sheets and mock tickets. Checks cover facts, routing, language and workflow behavior. Passing them is not a native-speaker naturalness score.
| Suite | Passed | Scored | Unscored |
|---|---:|---:|---:|
| bilingual_conversations | 3 | 4 | 0 |
| bilingual_voice | 3 | 3 | 0 |
| reg_respond | 8 | 9 | 0 |
| reg_router | 6 | 9 | 1 |

**Response-stage coverage**
| Stage | Arabic | English |
|---|---:|---:|
| already_submitted | 1 | 1 |
| other | 1 | 0 |

**Cases needing review**
- bilingual_conversations/en-second-issue: turn 2: stage: expected ['detail_requested'], got 'summary_shown'; turn 5: stage: expected ['submitted'], got 'offer'; turn 6: stage: expected ['preparation_stopped'], got 'already_submitted'
- reg_respond/submitted: missing one of ['البريد', 'بريدك', 'الإيميل']
- reg_router/composite-enquiry-plus-question: Expected behavior has not been decided; this case is unscored.
- reg_router/enquiry-capability-question: intent: expected 'other', got 'ticket_enquiry'; next_target: expected 'respond', got 'ticket_enquiry'
- reg_router/enquiry-oldest-with-greeting: intent: expected 'ticket_enquiry', got 'other'; next_target: expected 'ticket_enquiry', got 'respond'
- reg_router/repeated-earlier-question: intent: expected 'other', got 'question'; next_target: expected 'respond', got 'search'

**Recorded responses and conversations**

**bilingual_conversations / ar-simplification — PASS**

Customer: كيف انشئ علامة مائية في النظام

Assistant: ادخل إعدادات النظام، انزل لأسفل الصفحة، اضغط على «العلامة المائية»، ارفع الشعار اللي تبي تستخدمه، ثم احفظ.

Customer: اشرحها لي خطوة خطوة بدون تفاصيل زيادة

Assistant: 1. افتح إعدادات النظام.
2. انزل لأسفل الصفحة.
3. اضغط على العلامة المائية.
4. ارفع الشعار اللي تبي تستخدمه.
5. احفظ.

**bilingual_conversations / en-mobile-submit — PASS**

Customer: The Ole5 mobile app closes every time I open attachments.

Assistant: The Ole5 mobile app closing whenever you open attachments sounds serious. Would you like me to open a ticket with the technical support team about this?

Customer: Yes, open a ticket.

Assistant: Sure, to prepare the ticket about the app closing when you open attachments, could you tell me your phone’s operating system? This helps the team reproduce the crash.

Customer: Why do you need that?

Assistant: Knowing the phone’s operating system lets the support team reproduce the crash in the same environment, which speeds up finding a fix. Could you tell me which OS your phone is running?

Customer: Android 14

Assistant: Got Android 14. The ticket’s ready—shall I send it?

Customer: [submit]

Assistant: I’ve submitted the ticket T-MOCK-20261118095205-0001. Keep this number for any follow‑up, and the support team will reply through your registered email address.

Customer: Thanks, that is all.

Assistant: You're welcome. If anything else comes up, just let me know.

**bilingual_conversations / en-second-issue — REVIEW**

Customer: The Ole5 mobile app cannot open attachments.

Assistant: I see the Ole5 mobile app can’t open attachments. Would you like me to open a ticket for this?

Customer: Yes.

Assistant: Got it, the mobile app can’t open attachments. I’ve prepared a ticket with that info—should I send it?

Customer: Also, archive search is very slow.

Assistant: I’ve noted the archive search is slow and will handle that after we finish the attachment issue. For the mobile app not opening attachments, I’ve prepared a ticket—should I submit it?

Customer: iPhone

Assistant: Got it, iPhone. I’ve updated the ticket with that detail. The ticket’s ready—should I submit it?

Customer: [submit]

Assistant: I’ve noted the archive search is slow. Should I open a ticket for it?

Customer: No thanks, leave the archive issue.

Assistant: Got it—the mobile app can’t open attachments, and that problem is already in ticket T-MOCK-20261118095338-0001. Keep that number handy; the team’s reply will be sent to your registered email. Let me know if what you see now differs from what was reported in that ticket.

**bilingual_conversations / language-switch — PASS**

Customer: Hi

Assistant: Hello, how can I help you?

Customer: يعطيك العافية

Assistant: الله يعافيك

Customer: What are your support hours?

Assistant: Our support hours are Sunday to Thursday, 09:00 – 18:00 Riyadh time.

Customer: مشكور

Assistant: العفو

**bilingual_voice / ar-already_submitted — PASS**

Customer: نفس المشكلة للحين ما انحلت

Assistant: المشكلة ما زالت موجودة، وهي نفسها اللي رفعت لك تذكرة عنها برقم T-1001 بخصوص إغلاق التطبيق عند فتح المرفقات. الرد يوصل لك على بريدك الإلكتروني المسجل. هل في شيء جديد أو مختلف عن اللي تم ذكره في التذكرة؟

**bilingual_voice / ar-submitted_cancellation — PASS**

Customer: ألغ التذكرة المرفوعة T-1001

Assistant: ما أقدر ألغي التذكرة T-1001 من هالدردشة. التذكرة تم رفعها وفريق الدعم هو المسؤول عنها. إذا تحتاج تعديل أو إضافة تفاصيل، أقدر أساعدك.

**bilingual_voice / en-already_submitted — PASS**

Customer: The same problem is still happening.

Assistant: You’re still seeing the app close when you open attachments. That problem is already recorded in ticket T-1001 (app closes when opening attachments). Keep that number for any follow‑up—the support team will reply to you through the email address you used. Is there anything different about what you’re seeing now?

**reg_respond / first-detail — PASS**

Customer: نعم

Assistant: عشان أجهز التذكرة، ممكن تذكر لي اسم الصفحة اللي كان فيها زر الأرشفة؟

**reg_respond / no-answer — PASS**

Customer: هل يدعم النظام الوضع الليلي؟

Assistant: ما لقيت معلومات عن دعم الوضع الليلي في النظام.

**reg_respond / offer — PASS**

Customer: زر الأرشفة لا يعمل

Assistant: زر الأرشفة ما يشتغل. ودك أرفع لك تذكرة للفريق الفني يشيكون عليها؟

**reg_respond / offer-after-cancel — PASS**

Customer: لا الغها

Assistant: تم إلغاء طلب زر الأرشفة. تبي أفتح لك تذكرة بخصوص تنبيهات الواتساب ما توصل؟

**reg_respond / reply-time — PASS**

Customer: متى يوصلني الرد؟

Assistant: الفريق راح يرد بأسرع وقت ممكن، والرد يوصل على بريدك الإلكتروني المسجل.

**reg_respond / search-failed — PASS**

Customer: هل يدعم النظام الوضع الليلي؟

Assistant: ما قدرنا نلقى إجابة عن وضع الليل في النظام. ممكن تعيد إرسال سؤالك أو توضح أكثر؟

**reg_respond / search-failed-pending — PASS**

Customer: هل يدعم النظام الوضع الليلي؟

Assistant: ما قدرت ألقى إجابة عن الوضع الليلي، ممكن تعيد رسالتك وتوضح إذا تبي أرفع لك تذكرة بخصوص زر الأرشفة لا يعمل؟

**reg_respond / submitted — REVIEW**

Customer: نعم ارسلها

Assistant: رفعت التذكرة، رقمها T-1001. احتفظ بالرقم للمتابعة، والرد بيجي على إيميلك المسجل.

**reg_respond / summary-after-edit — PASS**

Customer: خل الأولوية عالية

Assistant: غيّرت الأولوية إلى عالية، التذكرة جاهزة بأولوية عالية. تبي أرسلها للفريق الآن؟
