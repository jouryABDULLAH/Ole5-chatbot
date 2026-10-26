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

## Checkpoints

| CP | Status | Finished | Notes |
|---|---|---|---|
| 0 Setup | in progress | | branch created; plan drafted; spikes running |
| 1 Push-to-talk end to end | not started | | |
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
