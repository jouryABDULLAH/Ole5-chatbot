# Voice work log

What happened, in order: decisions, checkpoints, test results, timings. The plan is in [VOICE_PLAN.md](VOICE_PLAN.md).
Newest entries at the bottom of each section. Times are Riyadh time (AST).

## Decisions

| ID | Date | Decision | Why | Decided by |
|---|---|---|---|---|
| D1 | 2026-10-26 | Channel is the website (the existing widget) only. No phone calls planned. | Settled scope | User |
| D2 | 2026-10-26 | Cascade design, one brain: STT → Ole5 graph → TTS, with the voice shell as plain code. No second model in front of Ole5, no speech-to-speech. | Keeps Ole5's grounding, ticket flow and exact wording; zero extra model calls (learning guide + decision run) | User |
| D3 | 2026-10-26 | Base: branch `voice-cascade`, cut from `router-next-only-design` at `98558be`. | The branch has spoken-yes submit, per-session locks, a tuned router | User |
| D4 | 2026-10-26 | Confirm by explicit spoken yes: summary read back; "نعم أرسلها" / "yes, submit" (or a plain نعم/yes after the read-back) submits; a bare تمام/اوكي/ok gets "just to confirm, submit now?"; speech over the assistant never counts. The Submit button keeps working. | User's choice; the re-confirm step guards against backchannels | User |
| D5 | 2026-10-26 | Speech vendor for the demo: Groq. STT `whisper-large-v3-turbo`; TTS `canopylabs/orpheus-arabic-saudi` voice `noura`, and `canopylabs/orpheus-v1-english` for English. | Already keyed and measured. Data is stored in the US: fine for an internal demo, not yet cleared for customers. | User |
| D6 | 2026-10-26 | Knowledge base for the demo: real RAGent2 (Ole5 tenant). | Real answers; 17–20 s per search, covered by fillers | User |
| D7 | 2026-10-26 | Build the voice shell ourselves inside Ole5's FastAPI process (no Pipecat, LiveKit or the friend's repo tonight). | Shares the in-memory sessions; no new framework under time pressure; works on Windows; the friend's repo has no license and needs Python 3.14 | Proposed by Claude |
| D8 | 2026-10-26 | Voice turns take the language from STT (sticky, mapped to ar/en) and check the reply's language by script, instead of two model calls per turn. Text chat unchanged. | Two calls averaged ~8 s per turn on 2026-10-25; the script check kept replies identical on `main` | Proposed by Claude |
| D9 | 2026-10-26 | Voice turns put a random first line in each model prompt, to avoid Groq's slow cache path; plus a 15 s timeout and 1 retry. | Spike: slow cache hits still present today (6 of 13 cached calls, up to 10 s); the nonce avoided them 10 of 10 | Proposed by Claude, confirmed by spike |
| D10 | 2026-10-26 | Commit to `voice-cascade` at each checkpoint, locally. Never commit `sheets/tickets.xlsx`, `docker/` or `docker-compose.yml`. No push unless asked. | Safe checkpoints to return to | Proposed by Claude |
| D11 | 2026-10-26 | Groq transport errors (timeout, connection, rate limit, 5xx) become `ModelFailed` for every channel, so the existing apology answers instead of an HTTP 500. | Today they escape the graph and leave a message unanswered; on voice that is silence | Proposed by Claude |
| D12 | 2026-10-26 | The ticket number is spoken as "on the screen, ending in 0 0 0 1"; the full number stays on screen. | The mock number is 22 characters; the real OTRS format is unknown | Proposed by Claude |
| D13 | 2026-10-26 | Spoken replies change form, not content: every step and cause the passages give is still said. | Ole5's grounding rules treat a dropped cause as a wrong answer | Proposed by Claude |
| D14 | 2026-10-26 | English voice `autumn` (female, matching `noura`). Browser VAD: vad-web 0.0.31, model v5, vendored into `web/vendor` for the demo. WebSocket at `/chat/sessions/{id}/voice`. | Spike results; no CDN dependency at the venue | Proposed by Claude |
| D15 | 2026-10-26 | A second bare acknowledgement right after "just to confirm…" counts as yes. | Two acknowledgements in a row after an explicit question are a clear yes; avoids a loop | User |
| D16 | 2026-10-26 | Fixed lines and fillers in formal, plain MSA (e.g. "لحظات من فضلك، أتحقق من ذلك"). | Matches the persona and Ole5's written replies | User |
| D17 | 2026-10-26 | Silence prompt after 15–20 s, only after the assistant asked a question, never after a ticket is created | Avoids "are you still there?" during a demo explanation | Proposed by Claude |
| D18 | 2026-10-26 | Freeze: the demo runs from the tagged commit `voice-demo`; Joori's newer commits are merged after the demo, unless one is needed (then section 10 is re-run in full) | A late merge would void the rehearsals | Proposed by Claude |
| D19 | 2026-10-26 | The demo is online over Google Meet; the presenter wears a headset and shares the machine's audio. Real customers will use phones, headsets and laptop speakers, so echo handling must hold on all three (mobile tested later over HTTPS). | User's setup | User |
| D21 | 2026-10-26 | Groq limits are not a constraint (enterprise plan); the limits check is dropped from CP0. | User | User |
| D22 | 2026-10-26 | D13 confirmed: the full answer is spoken. | User | User |
| D20 | 2026-10-26 | Voice turns: at most 1 knowledge search, capped at 30 s; 45 s turn deadline; a late result is shown, never spoken | RAGent2 takes 17–20 s per search and the turn holds the session lock | Proposed by Claude (from review) |
| D23 | 2026-10-26 | CP1 is hands-free half-duplex instead of push-to-talk: the speech detector is always on, speech that starts while the assistant talks is not sent, and a Stop button interrupts. Barge-in is built but off (`?barge` in the page URL) until CP4. The voice timeout, the nonce and the search cap moved from CP2 into CP1. | The echo test showed Chrome cancels the assistant's voice on this laptop and the detector caught every take; half-duplex keeps phones and speakers safe until barge-in is tuned | Proposed by Claude |
| D24 | 2026-10-26 | Speech to text: each segment is transcribed twice at once, as Arabic with the hint "نظام Ole5 ودعم RiCH" and as English with "Ole5, RiCH". When both come back in the same script, that call is kept; otherwise the more confident one, with +0.3 for the conversation's language. No hint under 1.8 s; a transcript of hint words only is dropped. Replaces "auto-detect with the Arabic hint". | On the user's 35 segments: auto 23/35, auto + Arabic hint 31/35, forced language + its own hint 32/35; the Arabic hint turned English into Arabic ("Yes, submit" → "نعم، اشتركوا في القناة") and leaked into TTS clips ("نظام Ole5" for "Yes, I'm here"). The live check after the change: 31/35, the misses being a pause split in two (×2), "500" written as digits, and one "وقف" heard as "One gift" (now a stop variant). Median 242 ms | Proposed by Claude, from the data |
| D25 | 2026-10-26 | Speech audio is trimmed: 40 ms kept before speech, pauses inside cut to 400 ms, 80 ms after. Every cached fixed line is heard back by Whisper and synthesized again (up to 4 times) until it matches; "Still checking, thank you for waiting" never came out right in English and was rephrased to "I am still checking. Thank you for your patience." | Orpheus left 0.4–0.7 s of silence before speech (added to every first sound), up to 2 s pauses inside short lines, and produced unintelligible audio for 3 short English lines | Proposed by Claude, from the data |
| D26 | 2026-10-26 | Arabic speech says "Ole5" as "أولي فايف" (the screen text is unchanged). | The Saudi voice read it "ولا خمسة"; the user says "أولي فايف" in the mic-lab recording | Proposed by Claude, from the user's recording |
| D28 | 2026-10-26 | Voice searches use a lighter RAGent2 profile (8 passages, 8 graded at once, 2 rounds, its own 60 s limit; sharing the chat instance's Qdrant client and embedder) and a 35 s cap; past the cap the turn answers as for a question with no answer and offers a ticket, instead of the apology. Turn deadline 55 s; up to three progress lines. Text chat keeps its settings. | Measured today: answerable searches 20–28 s light vs 20–45 s plain; no-answer searches 33–84 s in both. With the 30 s cap and the apology, 8 of 15 voice turns apologized (chat: 0) | User, from measurements |
| D29 | 2026-10-26 | Male voices: `abdullah` for Arabic, `troy` for English (replacing noura and autumn, D14). | User's choice from samples; both heard back 0.97–1.00; all 30 fixed lines verified | User |
| D27 | 2026-10-26 | Rule exchanges (stop, wait, repeat, are you there, "I'll come to that") are shown as a caption in the voice bar, not in the transcript; the "just to confirm" exchange is written into the conversation history. A message typed while voice is on goes over the socket and is answered aloud. The language switches only on an utterance of 3+ words in the other script. | Keeps the transcript equal to Ole5's history; a product name said alone ("Outlook") must not switch the reply language | Proposed by Claude |

## Open items

| ID | Since | Item | Status |
|---|---|---|---|
| O1 | 2026-10-26 17:10 | A voice search past the 30 s cap answered with the apology. | Closed by D28 |
| O2 | 2026-10-26 15:30 | A sentence with a pause arrives as two utterances (mic lab `p_pause`). | Joined since CP1 (the browser reports speech start; the server waits for the rest). Open part: with the fake microphone (no noise suppression) the pause stayed inside one segment and Whisper dropped the first half; shortening the pause did not fix it reliably, so it was not kept. Check on the real microphone at the CP2 pass |
| O3 | 2026-10-26 17:40 | Groq occasionally returns speech cut short (seen while it was also returning 500s). Fixed lines are checked and re-made; live reply segments are only logged ("speech looks cut short"). | CP3: re-synthesize a cut segment |

## Checkpoints

| CP | Status | Finished | Notes |
|---|---|---|---|
| 0 Setup | done | 15:30 | plan, spikes, mic lab |
| 1 Hands-free half-duplex end to end | done | 17:55 | tag `voice-cp1`; 311 tests; real-KB browser check passed |
| 2 Ole5 voice mode | not started | | |
| 3 No dead air | not started | | |
| 4 Hands-free + barge-in | not started | | |
| 5 Quality pass + rehearsal | not started | | |

## Progress

- 2026-10-26 13:30 — Branch `voice-cascade` created from `router-next-only-design` (`98558be`). The working tree still
  has the user's `sheets/tickets.xlsx` change and untracked `docker/`, `docker-compose.yml`; they are left alone.
- 2026-10-26 13:40 — Decisions D4–D6 taken with the user. Plan drafted. Feasibility spikes started: Groq speech and cache
  today, the browser audio stack, LangGraph hooks on the current code.
- 2026-10-26 14:05 — Spikes done (details in VOICE_PLAN.md section 10). Baseline on `98558be`: 134 passed, 1 skipped
  (the live router test). Plan updated with the results; D9 confirmed; D11–D14 proposed.
- 2026-10-26 14:25 — Two independent reviews of the plan (engineering; demo quality) found 3 blockers and ~40 majors.
  Fixed in plan revision 2: a per-socket turn manager and speaker queue; bounds inside the graph on voice turns (1
  search, 30 s); a graph-side yes guard and a normalized confirm table; the read-back built from the draft; the ticket
  number changed in code, not by the model; error handling and the nonce also in `respond_agent._invoke`; Submit over
  the socket in voice mode; real-world tests (echo, recordings, Groq limits) moved to CP0; minimal fillers and the
  apology moved to CP1; tagged commits per checkpoint, a 04:00 hard stop and a code freeze.
- 2026-10-26 14:55 — Decisions D15, D16, D19, D21, D22 taken with the user. Mic lab built outside the repo at
  `OTRS-voice-agent/voice-lab/` (server.py + static page; recordings to `voice-lab/recordings/`), served at
  http://localhost:8770 from the project venv. Dry-run with a fake microphone in the built-in browser: recorder (save,
  speech-detector segments, Whisper with the product hint) and echo test (events, mic capture, result) both work.
  Waiting for the user's real-microphone run.
- 2026-10-26 15:30 — **CP0 results: mic lab** (Chrome 155 on the user's Windows laptop; 7 echo runs, 64 takes of 27
  phrases; files in `voice-lab/recordings/`).
  - **Echo:** laptop speakers, silent: 0 detections in 3 runs (max speech probability 0.003–0.061). Headset, silent:
    0. Interrupt runs: speech detected 0.1–0.3 s after the user started, on speakers and on the headset. Chrome's echo
    cancellation works on this machine, so hands-free with barge-in is feasible. The guards stay for phones and other
    devices.
  - **Speech detector:** an offline replay of Silero v5 with the planned settings (0.5 / 0.35, minSpeech 160 ms,
    redemption 850 ms) found exactly one clean segment in every take, including one-word answers (7–16 speech frames,
    well above the 5-frame minimum). The lab's "nothing caught" rows came from the lab page cutting the take off ~1 s
    after Stop, not from the detector. One take with a mid-sentence pause over ~0.85 s split into two segments, so the
    server joins utterances that arrive back to back.
  - **Whisper:** VAD-trimmed segments are far better than the untrimmed takes (raw takes with ~1 s of silence were
    detected as Icelandic or Spanish and transcribed as gibberish; the trimmed versions were correct). Long phrases
    are good; Ole5 and RiCH are spelled right with the hint. Short words are weak: ايه → "E"/"y"/"أي"; عيد →
    "Reid"/"Rit"; وقف → "وغف"/"وغفت"; معي → "معين"; تمام → "تَمَامُ"/"Tama"; ok → "Okay"/"حسنا". One segment
    ("Yes, submit") came back as "نعم، اشتركوا في القناة" (a known hallucination appended), and one untrimmed take of
    "وقف" came back as "ودعم RiCH" (the hint leaking into the output). The detected `language` field is unreliable
    (Arabic text tagged "en").
  - **Consequences for the build:** always send the VAD segment, never padded audio; language from the transcript's
    script; for segments under ~1.5 s, no hint (prevents leaks) and the session language forced; strip known
    hallucination phrases rather than dropping the whole text; command and acknowledgement matching uses the observed
    variants; the read-back asks for "نعم أرسلها" (recognized correctly in 4 of 4 trimmed takes); on-screen Stop and
    Repeat buttons as the reliable path for those two commands. The recordings become test fixtures.
- 2026-10-26 17:10 — **CP1 built** (not yet tagged).
  - **Inside Ole5** (guarded hooks; text chat unchanged): `channel`/`heard_language` in the state; `sessions.send`
    streams the graph to a listener; `sessions.submit` takes the customer's words (D15) and the heard language;
    `sessions.note` writes a voice-only exchange into the history; search progress events; one search capped at 30 s
    on voice; language from speech and a script check instead of two model calls; Groq transport errors become the
    apology (D11); voice model calls get a 15 s timeout, no retries and a fresh first prompt line (D9).
  - **Voice package** `src/ole5_chat/voice/`: rules, speech to text (D24), speech (streamed WAV, 40 ms frames,
    trimmed, D25), the spoken form of replies (markdown, menu arrows, ticket number D12, Ole5 D26), fixed-line audio
    cached and checked on disk, the per-socket turn manager and speaker, the WebSocket route.
  - **Browser**: `web/voice.js` (speech detector from the CDN, WAV, socket, gapless player) and a mic button and
    voice bar in `t2-chat.js`.
  - **Tests**: 274 passed, 1 skipped (baseline 134 + 140 new: rules and the confirm table, speech helpers, the
    graph hooks, the turn manager with fake speech, the WebSocket end to end).
  - **Live checks** (`scripts/try_voice.py`): speech to text 31/35 on the user's recordings, median 242 ms; long
    replies spoken and heard back 0.99–1.00, first sound 220–310 ms; all 24 fixed lines heard back right.
  - **Browser check** (built-in browser, the user's recordings played into a fake microphone, stub knowledge base):
    mic on in 1.7 s; cached greeting; Arabic question heard right → "let me check" on the search → answer spoken in two
    segments with the source on screen; "عيد" (heard "ريت") repeats; "تمام" over the assistant is not sent; the Stop
    button silences at once; an English question switches the conversation to English and the English voice. No
    console errors, no server errors.
  - **Real knowledge base, demo questions ×2 so far** (`.voice_cache/kb_timings.json`): voice turns 27–32 s; chat
    45–206 s. **5 of 10 voice turns hit the 30 s search cap and answered with the apology**, every English question
    among them; chat answered or offered a ticket for the same questions after 77–171 s. RAGent2 is also failing
    intermittently today ("Failed to parse tool call arguments as JSON", 3 failed finds after 35–76 s). Open: O1.
- 2026-10-26 17:15 — **Independent review of CP1** (one reviewer, code only): 5 major and 11 minor findings, all fixed
  the same hour, each with a test: half-duplex dropped an answer that began just before the assistant finished (now
  sent when it outlasts the assistant, and counted as an answer after a question; a yes over the confirm read-back is
  asked again, D4); a sentence split by a pause is now joined, and utterances held during a turn are joined instead of
  replaced; the ticket number removal no longer drops the noun ("Ticket {N} was created" → "the ticket was created");
  multi-word acknowledgements and repeated or polite commands now match ("all right", "ok thanks", "نعم من فضلك",
  "وقف وقف", "Okay, stop"; "خلاص" stops only while the assistant talks); Stop now wins over a turn finishing during it;
  no window for two turns at once; only this turn's reply is spoken; the deadline says "this is taking longer" and
  mutes the late turn; one failed Whisper call keeps the other take; a reply that cannot be spoken says it is on the
  screen; per-writer temp files for the line cache; consistent RAGent2 cache keys; the browser drops audio after a
  local Stop until the server confirms; "Hi", "بس", "اه" are no longer "didn't catch that".
- 2026-10-26 17:30 — Decisions D28 (light search profile, 35 s cap, ticket offer) and D29 (male voices abdullah and
  troy) taken with the user. Speech-to-text live check after the changes: 32/36 (new spelling "تامام" added).
  All 30 fixed lines rebuilt in the new voices and heard back right. Tests: 311 passed, 1 skipped.
- 2026-10-26 17:55 — **CP1 done**, tagged `voice-cp1`. Browser check on the real knowledge base (fake microphone with
  the user's recordings): the English no-answer question → "let me check" at 1.4 s, progress lines at 11, 21, 31 s,
  then "I'm sorry, I don't have an answer for this. Would you like me to open a support ticket?" at 37.6 s, phase
  offer_ticket; the Arabic password question answered at 20.7 s with its source on screen.
  Next: CP2 (voice overlays with the draft fields, graph-side yes guard, read-back, ticket number), then the user's
  10-minute real-microphone pass.
