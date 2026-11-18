# Focused capability-overview correction

The user flagged an unnecessary explanation of stopping ticket preparation in a general “what else can you do?” reply. Cancellation had been listed alongside the assistant's core capabilities, encouraging the model to advertise it.

The introduction now lists product help, ticket preparation and ticket lookup. Workflow controls and account-access limitations are explained only when specifically asked about or relevant to the current action. Their actual behavior and the facts available to the response guards are preserved.

## The reported example

Customer: طيب تقدر تعمل ايه تاني؟

Previous reply, supplied by the user:

> أقدر أجاوبك عن أي سؤال بخصوص نظام Ole5، أساعدك في خطوات الاستخدام، أستعلم لك عن حالة تذاكرك الحالية، وأجهز لك تذكرة دعم جديدة إذا عندك مشكلة. إذا غيرت رأيك قبل الإرسال أقدر أوقف تجهيز التذكرة. ما أقدر أغيّر إعدادات حسابك أو أطلع بياناتك مباشرة، لكن أوجهك للخطوات اللازمة.

New recorded reply to that exact question:

> أقدر أساعدك في استخدام نظام Ole5: أشرح لك الإجراءات، أوجهك لحل المشكلات، وإذا ما انحلت أقدر أساعدك في رفع تذكرة دعم وأستعلم عن حالتها.

The new response is a live frozen-state probe of the exact question, not a replay of the user's full history, which was not supplied.

## Verification

- All 259 offline tests pass (two existing dependency deprecation warnings).
- All 10 automated live checks pass: six general capability questions across Arabic/English and OLE5/RiCH, two explicit preparation-control questions, and two submitted-ticket cancellation controls.
- The general capability cases now reject unsolicited preparation-control descriptions and account disclaimers. Direct preparation-control questions still receive an affirmative answer.
- [Exact model replies and checks](results.md), [raw results and source hashes](results.json).

Manual review still matters: the Arabic submitted-ticket control correctly refuses direct cancellation but appends unverified advice about contacting support or opening another ticket. That control passes its existing lexical checks; it is a separate finding requiring follow-up, not a clean factual-quality result. The direct preparation-control answers also explain that no draft is active, which could be shorter. Neither issue is hidden by the automated pass count.
