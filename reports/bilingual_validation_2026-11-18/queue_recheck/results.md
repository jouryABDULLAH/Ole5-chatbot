**Bilingual chat validation**
Live model calls with fixed evidence, isolated sheets and mock tickets. Checks cover facts, routing, language and workflow behavior. Passing them is not a native-speaker naturalness score.
| Suite | Passed | Scored | Unscored |
|---|---:|---:|---:|
| bilingual_conversations | 0 | 1 | 0 |

**Response-stage coverage**
| Stage | Arabic | English |
|---|---:|---:|

**Cases needing review**
- bilingual_conversations/en-second-issue: turn 3: stage: expected ['queued', 'detail_requested'], got 'no_answer'; turn 6: stage: expected ['preparation_stopped'], got 'other'

**Recorded responses and conversations**

**bilingual_conversations / en-second-issue — REVIEW**

Customer: The Ole5 mobile app cannot open attachments.

Assistant: Sorry to hear the Ole5 mobile app can’t open attachments. Would you like me to open a ticket for this?

Customer: Yes.

Assistant: Could you let me know your phone’s operating system so we can reproduce the problem?

Customer: Also, archive search is very slow.

Assistant: I couldn’t find details on why the archive search is slow. Could you let me know your phone’s operating system so we can continue with the attachment issue?

Customer: iPhone

Assistant: I've noted your iPhone. The ticket is ready—should I submit it?

Customer: [submit]

Assistant: I’ve submitted the ticket. Its number is T-MOCK-20261118095813-0001. The support team will reply through your registered email address—keep this number for any follow‑up.

Customer: No thanks, leave the archive issue.

Assistant: Got it—I won’t open a ticket for the archive issue. The attachment‑problem ticket is already raised as T-MOCK-20261118095813-0001, and the support team will reply through your registered email.
