# Bilingual voice and workflow review — November 18, 2026

The assistant is more conversational in both languages, with shorter introductions, more direct questions, customary Saudi courtesy replies and clearer descriptions of its own actions. The improvement is uneven: some Arabic status/transition replies remain formal, and some English replies repeat the problem or offer further help unnecessarily. Automated passes do not establish native-speaker naturalness.

## Read the conversations

- [Previous and updated replies, side by side](recheck/comparison.html): exact output from the November 17 broad run and November 18 targeted recheck. This selected comparison includes failures and rate-limit fallbacks; it is not an unbiased quality benchmark.
- [Original Saudi before/after conversations](../saudi_voice_comparison_2026-11-17/after/comparison.html): 51 replies from the original baseline and the earlier persona revision, with review notes.
- [All 223 original cases and transcripts](../bilingual_validation_2026-11-17/results.md), [raw JSON](../bilingual_validation_2026-11-17/results.json).
- [Targeted recheck and transcripts](recheck/results.md), [raw JSON](recheck/results.json).
- [Final two-issue workflow replay](final_queue_recheck/results.md), [comparison](final_queue_recheck/comparison.html). The [intermediate replay](queue_recheck/results.md) is also preserved.

The original Saudi comparison predates the latest bilingual and workflow fixes. None of these historical model replies were edited to make them sound better. The full conversations evolve independently, so workflow states and mock ticket numbers can differ.

## What changed

- OLE5 uses everyday Saudi/Najdi Arabic without requiring slang in every sentence. RiCH retains its configured professional Arabic. Both use conversational English, active verbs and shorter openings while identifying themselves as support assistants.
- Responses ask for each detail once, avoid routine empathy preambles and unnecessary account-access disclaimers, preserve the scope of an outage, and distinguish explanation requests from failed troubleshooting.
- Cancellation transitions identify the stopped issue separately from the next offered issue. Timing replies use the actual registered-email channel, without a made-up deadline or the internal label “Reply channel”.
- Known ticket numbers retain their exact ASCII hyphens even when the model produces typographic hyphens; unrelated codes remain untouched.
- A completed submission or cancellation now takes precedence over an incorrect model interpretation of the next issue or an older ticket. The submission receipt also retains the submitted issue's identity separately from the queued issue.
- The router enforces the documented return route from worker facts: a completed search with a partial or missing answer goes to the ticket operator, while answered/failed searches go to Respond. This prevents a mistaken model route from silently dropping the newly reported issue.
- `.gitignore` covers credentials, environments, runtime workbooks, uploads, logs and generated duplicates. Existing ticket and attachment workbooks were backed up and preserved locally; fresh installations create empty runtime workbooks on first use.
- Offline tests reject unscripted model calls. Stale router/queue tests were updated for the existing split router and consent-based ticket operator, with coverage for urgent, ordinary and empty queues.

## Coverage and results

The broad live evaluation uses `openai/gpt-oss-120b` through the deployment's KSA endpoint, synthetic customers, temporary sheets, mock tickets and fixed/stub knowledge passages. It tests response behavior and workflow, not production retrieval quality. Response generation uses temperature 0.1; structured first attempts use 0, with correction retries as configured.

| Coverage | Cases |
|---|---:|
| Routing and scope | 76 |
| Grounding and ticket-truth guards | 52 |
| Existing response regressions | 25 |
| Mirrored bilingual voice/workflow states | 60: 30 Arabic, 30 English |
| Full conversations | 10 conversations, 36 customer turns |

All **16 response stages** appear in both languages. Scenarios include greetings, courtesy, product answers, missing answers, failures, ticket offers, detail collection, edits, summaries, submission, cancellation, queued issues, existing tickets, status explanations, support hours, capability questions, unsupported requests, hidden-instruction requests, language switches and bare technical values.

- **259 offline tests pass**, including five new regressions for the submission/cancellation handoff, four for worker return routing, and three for identifier fidelity. Both UI checks pass. See [checks.json](checks.json).
- Initial live run: **197/223 cases passed its original assertions**. All **52/52 guard cases passed**. Four conversations encountered provider rate limits.
- Targeted live recheck: **20/25 scored cases passed**, with one additional case left unscored because its expected behavior is undecided. The four rate-limited conversations were replayed with one worker; three passed, and the two-issue case exposed a real workflow problem subsequently fixed.
- One remaining recheck failure is a substring-check omission: “إيميلك المسجل” correctly expresses registered email. Rescoring that saved reply with the corrected synonym passes; the raw result remains unchanged. See [fixture_review.json](fixture_review.json).
- **The final two-issue conversation passes all six turn checks.** It collects the phone OS, keeps the archive issue queued, confirms the submitted mobile ticket by number, offers the archive ticket, and then stops only the archive preparation when declined. This follows two preserved failed replays; it is one successful sample, not proof that every paraphrase will follow the same path.

This is a broad initial run followed by targeted verification, not a claim that all 223 cases were rerun on the final source. [Source hashes](source_hashes.json) identify the targeted-recheck source before the final workflow fix; the final workflow replay records its own source and dataset hashes in its results JSON.

## Remaining findings and suggestions

1. **Arabic routing:** “مساء الخير، ابي أعرف أقدم تذكرة لي” can be misread as submitting a ticket, and “هل أقدر أشوف تذاكري؟” can trigger a lookup instead of explaining the capability. Both remain flagged after the prompt refinement. Avoid solving these with literal phrase overrides; simplify the competing routing instructions and validate on paraphrases.
2. **Multiple requests:** A ticket-status question combined with a product how-to still has no agreed multi-intent behavior. That fixture is explicitly unscored. An older question repeated after another topic triggers a new search because only the latest answer's evidence is retained; its existing fixture intentionally expects behavior deferred by the current design.
3. **Classification:** In the two-issue replay, a mobile attachment problem was classified without requiring the phone OS, so the summary appeared earlier than expected. The submission/cancellation reporting defect was fixed separately; classification consistency still needs a dedicated review.
4. **Arabic voice:** “تم إلغاء طلب…” still appears in a transition even though the stopped topic is now correct. Search-failure wording can use “ما قدرنا” instead of “ما قدرت”, and can blur an interrupted lookup with not finding an answer. Capability overviews can become longer and more formal than needed.
5. **English voice:** Phrases such as “I see…” and “Let me know…” still repeat in some replies. Even the final workflow pass repeats the reason for the OS question and mentions ordinary priority unnecessarily. Prefer a short acknowledgement only when it adds something, and let a closing “thanks” end the chat.

For the next quality pass, have a Saudi reviewer and an English reviewer independently score a held-out set of realistic conversations for natural wording, contextual response, brevity, factual accuracy and workflow accuracy. Keep technical names and ticket facts literal. Use those ratings to shorten the persona instructions and improve a few stage-specific examples; adding more slang or a mandatory empathy sentence would not address these problems.

The live evaluator now defaults to one worker to reduce shared token-limit interruptions. Run new evaluations into a fresh directory; `--rerun-from` preserves historical results and creates a side-by-side comparison. Do not hide provider failures or undecided cases inside a pass rate.
