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
| D30 | 2026-10-26 | Spoken interruptions are on by default (replaces D23's half-duplex): speech over the assistant lowers its volume at once and is always sent; the server decides — stop and wait (heard anywhere in the utterance) act, a backchannel, the assistant's echo or a very short sound (≤ 2 words, < 1.6 s, not a command or a no) is ignored and the volume comes back, other words stop it and are answered. While the customer talks over the assistant the speech detector waits 500 ms instead of 850 ms for the end. In a short clip, a transcript made only of known phrases beats one that is not. `?halfduplex` brings back D23. | The user's real-microphone try: "Stop" and "وقف" did nothing (half-duplex dropped them). After the change, on the user's recordings: "Stop." stops ~1 s after the word, "وقف" too; "تمام" (heard "تاما", "Time out.") is ignored | User report; fix by Claude |
| D31 | 2026-10-26 | CP2 voice mode in Ole5 (text chat unchanged): the confirm step is read back from the draft in the reply's language (title and summary restated, priority, Arabic priority word) and ends with fixed closing words, «…ولإرسالها قل: نعم أرسلها. هل تريد إرسالها الآن؟» / "…to send it, say: yes, submit. Would you like it submitted now?" — a closing question with no yes or send word, so the assistant's echo cannot answer it. "just to confirm" ends the same way. A change is said back as what really changed (worked out in code); a failed change or a failed submit never invites a submit; the ticket number's sentence stays where it was said. | Design reviewed by three critics and a verifier before coding: 31 findings, 22 real, all folded in | Proposed by Claude, from the review |
| D32 | 2026-10-26 | Graph-side yes guard (D4) on spoken voice turns only: the router's yes stands only when the words are an explicit submit — a yes or send word (not a polite word alone), no negation, and nothing else but words with no content ("نعم أرسلها، شكراً", "Yes, go ahead and submit it" pass; "yes but make it high", "تمام كذا" do not). Otherwise nothing is created and the customer is asked again (the fixed line when they said nothing else, so D15 counts it). The Submit button and D15 arrive confirmed; typed messages and the text chat are not guarded. | Typed text cannot be misheard; a strict word list refused clear yeses and looped | Proposed by Claude, from the review |
| D33 | 2026-10-26 | At the confirm step, speech over the assistant is never a decision: an acknowledgement over the read-back is ignored (and does not count for D15), a yes is asked again. D15 counts only an acknowledgement right after "just to confirm" has played (within 30 s, nothing said in between); thanks alone goes to Ole5 (it can mean "no thanks"). A yes or acknowledgement held during a turn is dropped when that turn ends at the confirm step. The browser now says which item was audible when speech over the assistant began, and the server checks speech starting within 1 s after the assistant for echo. | A test run created the urgent ticket from a "نعم أرسلها" said before its summary existed; review: D15 survived wait/repeat, thanks twice submitted | Proposed by Claude |
| D34 | 2026-10-26 | Spoken replies keep the passages' own order and words: no added "first… then…" and no turning alternatives into steps (replaces the plan's "steps signposted"). | With signposting, the grounding check rejected the first draft of 3–4 of 10 voice answers ("أولاً، افتح المراسلة… ثم إما…" ordered what the passages give as alternatives); without it, 0 of 10 (chat: 3) | Proposed by Claude, from the data |
| D35 | 2026-10-26 | One voice per reply: a reply is spoken in its language's voice from start to end (replaces "voice chosen by the reply text's script" per sentence). An English reply's sentence that is mostly an Arabic screen name stays with `troy`; an Arabic reply's sentence that is mostly an English label stays with `abdullah`. | The user's real-mic pass: Arabic words in an English answer "sound like a different person". Heard back by Whisper: `troy` says the Arabic names recognisably ("Inche Murasila Jadida"); `abdullah` says English labels inside Arabic well ("اضغط على create new correspondence ثم save") while `troy` garbled that sentence. `abdullah` for all English too was tried and rejected: it stumbled on an English sentence with an Arabic name ("Open the. The. The.") | User report; fix by Claude |
| D36 | 2026-10-26 | A question held during a turn is answered after a bridge line, "Now, about your other question." / «والآن، بخصوص سؤالك الآخر.» ("…about what you added" when it was not a question), queued before the held turn says anything. | The user's real-mic pass: the held answer ran straight on from the first one and "the two answers mix" | User report; fix by Claude |
| D37 | 2026-10-26 | "Ole5" is spoken as "Oh-lee five" by `troy` and «أُولِي فايف» (with diacritics) by `abdullah`; the screen keeps "Ole5". One voice per reply (D35) stays for now; the user may revisit the English voice reading Arabic names. | The user's picks (E4, A3) from six spellings per voice; the earlier «أولي فايف» and the bare "Ole5" sounded "a bit off". Three takes of each greeting heard back the same (0.95–0.96) | User |
| D38 | 2026-10-26 | A first acknowledgement as soon as what the customer said goes to Ole5, chosen by the step: «حسناً.» / "Okay." by default; «يسعدني ذلك.» / "Certainly." for a yes to the ticket offer; «فهمت.» / "Got it." and "Okay." in turn while details are collected; «حسناً، سأرسل التذكرة الآن.» / "Okay, sending the ticket now." for an explicit submit (and D15). None for a greeting or how-are-you, thanks, a bare no, a yes or acknowledgement alone in phase none, typed messages and the Submit button (answered directly, ~3 s). "Let me check" only when the search starts (0.5-0.8 s later), never before Ole5 decides. The assistant's acknowledgement words are listening sounds when their echo comes back. | Baseline: first sound median 3.9 s, max 7.2 s. Review: "let me check" before the router is untrue for greetings, "I need help" and urgent problems; «طيب/تمام» are the customer's words and colloquial (D16); «بكل سرور» came back «بكل سرق» on every take | Proposed by Claude, from the design review |
| D39 | 2026-10-26 | Progress by silence: while Ole5 works, a line after 5.5 s in which nothing was heard (assistant, customer, or speech being transcribed), in the order still_looking, still_checking, «يستغرق هذا وقتاً أطول قليلاً، وما زلت أعمل عليه.» / "This is taking a little longer, but I'm still working on it.", «لم أنتهِ بعد، أواصل البحث.» / "Not done yet, I'm still searching.", then the first two in turn at twice the silence; none in the 3 s after the search returns (the answer is being written, and a line that has started cannot be withdrawn). No "I found something" line. | Baseline: 7.0-7.3 s of silence after "let me check" (a fixed 10 s clock), lines repeated word for word with three thank-yous; a progress line that began 0.2 s before Ole5 finished held the English answer back 3 s; a long-wait line timed from the turn start (28 s) left 9.2 s of silence before it; "found" is also true for answers that end in "no answer" | Proposed by Claude, from the design review |
| D40 | 2026-10-26 | No fixed line may contain a command, a yes or a send word (a test checks every line), and the echo of any fixed line, at any length, is ignored when the utterance began while it played or within a second after it. English "One moment please" became "Let me check that for you." | The review found "one moment" is a wait command: the echo of "let me check" stopped the search line with "Of course, take your time"; a one-word "Okay." passes every other echo filter and came back as a new turn (at the confirm step it set up D15) | From the design review |
| D41 | 2026-10-26 | The voice channel's Groq clients keep connections open for 5 minutes; when the customer starts talking, or the search returns, a connection idle for 40 s is reopened. HTTP/2 not used. | Measured: httpx closes idle connections after 5 s, so the two "parallel" Whisper calls took about the sum of both (0.67-1.25 s; 0.41-0.71 s kept open) and a reply's first sound after a search took 1.1-1.3 s (0.23-0.25 s kept open, after up to 45 s idle). HTTP/2 was no faster | Proposed by Claude, from measurements |
| D42 | 2026-10-26 | Later parts of a reply are made in full and checked before they are sent, when the browser has enough audio queued to hide it; a part that came back cut short is made again unheard while the queue lasts, else streamed fresh. Cached lines and checked parts go out in one piece and start after a 20 ms lead instead of 120 ms. | O3: a 163-character sentence came back as 3.7 s of audio in a live run, its question lost. Without the queue check, part gaps of 0.3-1.7 s after a short first sentence | Proposed by Claude, from the review |
| D43 | 2026-10-26 | CP3 exit target, restated from the measurements: first sound within 2.0 s of the customer stopping (median ~1.6 s) for every utterance that gets an acknowledgement or a rule line; the D38 exceptions about 3 s; the reply about 0.4 s after Ole5 is done; no silence over about 6 s while it works. A 1.5 s median would need a shorter detector wait (it would split the user's 0.83 s pauses), an acknowledgement before speech-to-text (it would answer commands, echo and mid-sentence pauses) or speculative speech-to-text (after the demo). | 0.85 s detector wait + 0.4-0.7 s speech-to-text + 0.15 s join grace + lead | From the latency review |
| D44 | 2026-10-27 | Silence prompt (D17, CP4): when a reply that ends on a question has had 18 s of real quiet (nothing said by either side, no turn, nothing being transcribed, the microphone not muted), «أنا هنا إن احتجت إلى أي مساعدة.» / "I'm here whenever you're ready.", once; a statement, so it asks nothing twice and its echo cannot answer anything. Never once a ticket exists; the customer starting to speak, a stop or any turn cancels it. | D17; "are you still there?" holds the "there" command words, so its echo would be heard as the customer asking it | Proposed by Claude |
| D45 | 2026-10-27 | Mute (CP4): a Mute/Unmute button in the voice bar and the M key (by key position, so an Arabic keyboard works; not while typing) stop the speech detector; the assistant can still be heard and stopped; the server is told, for D44. Push-to-talk is not brought back (D23). | A presenter over Meet can talk to the audience without the assistant answering | Proposed by Claude |
| D46 | 2026-10-27 | A clip of 2.5 s or more whose kept transcript is less sure than -0.7 (Whisper's mean log-probability) is not acted on: «عذراً، لم أسمع ذلك جيداً. هل تعيده من فضلك؟». Shorter clips are judged as before. | In the browser check, a replayed Arabic question came back as "Kæf og erjist möraslaði fannig nígama?" (-0.85, even from the Arabic call) and was answered in English. On all 36 of the user's recordings, real speech of 2 s or more kept -0.55 or better; one-word commands go down to -1.07 | Proposed by Claude, from the data |
| D47 | 2026-10-27 | Hesitations («امم», "hmm", "umm") are listening sounds but not acknowledgements: at the confirm step they are asked again and never count as the D15 yes; the assistant's own acknowledgement words («فهمت», "Got it", «يسعدني ذلك», "Certainly") are listening sounds only, never the customer's yes. | D15 is "a second bare تمام/اوكي/ok"; the review found «امم» after "just to confirm" submitted the ticket, and «فهمت» would have too | From the code review |
| D27 | 2026-10-26 | Rule exchanges (stop, wait, repeat, are you there, "I'll come to that") are shown as a caption in the voice bar, not in the transcript; the "just to confirm" exchange is written into the conversation history. A message typed while voice is on goes over the socket and is answered aloud. The language switches only on an utterance of 3+ words in the other script. | Keeps the transcript equal to Ole5's history; a product name said alone ("Outlook") must not switch the reply language | Proposed by Claude |

## Open items

| ID | Since | Item | Status |
|---|---|---|---|
| O1 | 2026-10-26 17:10 | A voice search past the 30 s cap answered with the apology. | Closed by D28 |
| O2 | 2026-10-26 15:30 | A sentence with a pause arrives as two utterances (mic lab `p_pause`). | Joined since CP1 (the browser reports speech start; the server waits for the rest). Open part: with the fake microphone (no noise suppression) the pause stayed inside one segment and Whisper dropped the first half; shortening the pause did not fix it reliably, so it was not kept. Check on the real microphone at the CP2 pass |
| O3 | 2026-10-26 17:40 | Groq occasionally returns speech cut short (seen while it was also returning 500s). Fixed lines are checked and re-made; live reply segments are only logged ("speech looks cut short"). | Mostly closed by D42: later parts are checked and made again; the first part of a reply still streams unchecked (latency) |
| O4 | 2026-10-26 23:40 | In one live run Ole5 said "I have opened a support ticket for the app closing…" while still collecting details (the accept/details tasks say "Don't say a ticket is opened"), and took two extra collect turns, so the en_ticket flow missed its expected phases. Model variance in the graph, not the voice shell; text chat has the same prompts. | CP5: a code check on collect-phase replies, or a prompt fix with Joori |

## Checkpoints

| CP | Status | Finished | Notes |
|---|---|---|---|
| 0 Setup | done | 15:30 | plan, spikes, mic lab |
| 1 Hands-free half-duplex end to end | done | 17:55 | tag `voice-cp1`; 311 tests; real-KB browser check passed |
| 2 Ole5 voice mode | done | 21:55 | tag `voice-cp2`; 413 tests; both demo scenarios pass live; grounding gate passes |
| 3 No dead air | done | 00:50 | tag `voice-cp3`; first sound median 1.6 s, max 1.8 s (was 3.9 / 7.2); see Progress |
| 4 Hands-free + barge-in | done | 00:50 | most of it came early (D23, D30, CP2); added the silence prompt (D44) and mute (D45); in the `voice-cp3` commit |
| 5 Quality pass + rehearsal | not started | | |

## Demo runbook

**Before (morning, about 15 minutes):**
1. Close Excel (it locks `sheets/tickets.xlsx`). The demo runs on a copy of `sheets/`, so the real file is never written.
2. Check `Ole5-chatbot/.env`: `GROQ_API_KEY`, `KB_BACKEND=ragent2` and the Qdrant URL on port 443 (the knowledge base is
   the remote one); `TRACING=false` unless Phoenix is running.
3. Start the server from `Ole5-chatbot/` in Git Bash, on a fresh copy of the sheets and the mock ticket API:
   ```
   T=$(mktemp -d) && cp -r sheets "$T/" && W=$(cygpath -w "$T") && PYTHONIOENCODING=utf-8 PYTHONUTF8=1 \
     SHEETS_DIR="$W\sheets" ATTACHMENTS_DIR="$W\uploads" TRACING=false TICKET_API_URL= \
     venv/Scripts/python.exe -m uvicorn ole5_chat.main:app --app-dir src --host 127.0.0.1 --port 8000 \
     > ../voice-lab/server8000.log 2>&1
   ```
4. Wait for `voice  fixed lines ready` in the log: every fixed line is made and heard back once (new ones take a
   minute).
5. Chrome (not Edge) at `http://localhost:8000` (the microphone needs localhost or HTTPS, not the LAN address). Sign in
   with the demo customer's email, turn the microphone on, and ask one question in Arabic and one in English: the first
   search and the first Groq calls of the day are slow.
6. Headset on. In Meet, share the Chrome tab with its audio.

**During:**
- The five scenarios are VOICE_PLAN.md section 10. Phrases that work: «كيف أؤرشف مراسلة في النظام؟»,
  «نسيت كلمة المرور كيف أستعيدها؟»; "The mobile app closes by itself when I open the inbox." → "Yes." → details →
  "That's all I know." → "Okay." (it asks again) → "Yes, submit"; «ولا موظف عندنا يقدر يسجل دخول من الصبح» → «نعم أرسلها».
- **M** (or the Mute button) mutes the microphone while you talk to the audience; the assistant can still be stopped.
- Stop: say «وقف» / "stop", or press Stop. Typing in the box always works, also in voice mode.
- Scenario 5 (failure): `venv/Scripts/python.exe scripts/try_voice.py kb-down on`, ask a question (the apology is
  spoken), then `... kb-down off` and ask again. No restart.

**If something goes wrong:** the text chat keeps working with the microphone off. Restart the server (step 3). Fall back
to the last tagged build: `git checkout voice-cp3` (or `voice-cp2`). The log's `voice  latency` and
`voice  heard silence` lines show where the time went.

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
- 2026-10-26 19:20 — The user tried CP1 on the real microphone: spoken "Stop" and "وقف" did not interrupt (half-duplex
  threw away all speech over the assistant). Fixed (D30) and checked in the browser on the real knowledge base with the
  user's recordings: "Stop." and "وقف" over a long answer stop it (stop_audio 1.7–1.9 s after the clip starts, about
  1 s after the word); "تمام" over it is ignored and the volume comes back. Live speech-to-text check 33/36 (only the
  two pause halves and "500" in digits). Tests: 334 passed, 1 skipped.
- 2026-10-26 20:00 — **CP2 design review** (three critics, then a verifier, before any code): 31 findings, 22 real,
  folded into the design (D31–D33): an allowlist yes guard instead of the strict word rule; the read-back built in
  code in one language, ending on a closing question with no trigger word; the browser says what was audible when
  speech over the assistant began; D15 only right after "just to confirm"; held and split utterances through the
  confirm table. A baseline run of the demo scenarios on the old code also showed a held "نعم أرسلها" creating the
  urgent ticket before its summary was read (fixed).
- 2026-10-26 21:00 — **CP2 code review** (four reviewers, a skeptic per finding): 28 findings; the 16 most severe
  verified, 16 confirmed, none refuted; all 28 fixed with tests. The main ones: the echo check right after playback
  dropped a prompt "نعم أرسلها" (the closing words contain it) — echo is now matched in order, three or more words from
  the end of what was said; D15 stayed armed after a stop, and after an apology; "changed" could claim a change
  classify had only reworded; words after "stop" were forwarded normalized, and "التطبيق توقف فجأة" counted as a stop
  (a stop now counts at the start of an utterance, or later only when the other words are the assistant's echo);
  failed submits were counted across tickets; the urgent sentence came after the closing words.
- 2026-10-26 21:45 — **CP2 live checks.** Both demo scenarios pass end to end on the real knowledge base with the
  user's recordings (`scripts/try_voice.py flow`): English no-answer → offer → details → read-back → a bare "Okay."
  re-confirmed → "Yes, submit" → ticket, number spoken; Arabic urgent → read-back at once (priority حرجة) → "نعم
  ارسلها" → ticket. Browser (fake microphone): the same urgent flow; "Naam er Silha." (Whisper's Latin spelling of
  "نعم أرسلها" for the replayed clip) now counts as a yes and does not switch the conversation to English;
  interruptions still work ("تمام" ignored, "وقف" stops in ~1.5 s). **Grounding gate (plan 8.3) passes:** 10 turns per
  channel — voice answered 6, offered 4, apologised 0, grounding retries 0, median 15.5 s; chat 6, 4, 0, 3, 24.6 s.
  Live speech-to-text check 33/36. Tests: 413 passed, 1 skipped.
- 2026-10-26 22:30 — **The user's real-microphone pass (CP2) and fixes.** Notes: a question held during an answer ran
  straight on from it ("the two answers mix") → D36 bridge line; Arabic names in an English answer switched voice → D35
  one voice per reply; "Ole5" sounded off → D37, the user's picks from six spellings per voice (E4, A3). Commit 7b30c37.
- 2026-10-26 23:00 — **CP3 baseline** (`scripts/try_voice.py flow` now plays the audio out in real time and prints each
  step's stages and silences; `voice-lab/cp3_baseline.txt`): all four demo flows pass; first sound after the customer
  stops median 3.9 s, max 7.2 s; longest silence per step median 4.9 s, max 7.3 s. Probes: the two "parallel" Whisper
  calls cost about their sum because httpx closes idle connections after 5 s (D41); a reply's first sound after a
  search 1.1-1.3 s for the same reason.
- 2026-10-26 23:15 — **CP3 design review** (three critics — conversation, concurrency, latency — and a verifier): 21
  findings, 18 real, 3 partly; folded in before and while coding (D38-D43). The main ones: "let me check" before Ole5
  decides is untrue for greetings and urgent problems; one-word acknowledgements pass every echo filter; the English
  "one moment please" lines were a wait command when echoed (an existing bug); progress lines played over the customer
  and repeated word for word; a failed second take dropped the rest of a reply; 1.5 s is not reachable as a median.
- 2026-10-27 00:00 — **CP3 live run** (`voice-lab/cp3_after.txt`): first sound median 1.6 s, max 1.8 s (was 3.9 / 7.2);
  speech-to-text 0.4-0.7 s; the reply about 0.4 s after Ole5 is done. One 9.2 s silence before a long-wait line timed
  from the turn start → fixed (D39, the line is now third in the order). en_ticket missed its phases once: Ole5 asked two
  extra questions and said "I have opened a support ticket" mid-collect (O4).
- 2026-10-27 00:30 — **CP3 code review** (three reviewers — turn manager, browser and connections, measurement — each
  with a skeptic): 16 findings; all fixed with tests. The main ones: the assistant's own «فهمت» / "Got it" had been added
  to the customer's acknowledgement words, so «فهمت» right after "just to confirm" would have submitted the ticket
  (D15) — they are now listening sounds only; «شكراً» over «سأرسل التذكرة الآن» stopped that line; a later reply part
  made whole had no deadline; a stream cut off by a stop closed its connection without the next turn reopening it;
  "repeat" replayed a cut-short first part; "still looking through the documentation" on turns that never searched;
  cached audio now goes out in 250 ms pieces (one large message was slow on a phone link); the browser's silence
  report counted frames of the reply already playing. CP4 (D44, D45) added; tests: 447 passed, 1 skipped.
- 2026-10-27 00:45 — **CP3 final live run** (`voice-lab/cp3_final.txt`, all CP3 and CP4 code): all four flows pass.
  First sound after a spoken turn 1.3-2.0 s (median 1.8 s with the typed step's 4.1 s, which gets no acknowledgement by
  D38); longest silence per step median 3.3 s, max 7.4 s (the held question: the 3 s hold after the search added to the
  5.5 s quiet; the line still came before the answer). Sounds as designed: «حسناً.» → «لحظات من فضلك، أتحقق من ذلك.» →
  progress → answer; "Certainly." → the questions; "Okay, sending the ticket now." → "The ticket was created…".
  Seen in the logs: Groq's tool_use_failed on Ole5's search call (several queries in one call), retried by the graph;
  that turn took 24 s.
- 2026-10-27 01:00 — **CP5 so far.** Scenario 5 had no switch: `scripts/try_voice.py kb-down on|off` (a flag file every
  search checks; no restart) — a question then gets «حسناً.», "let me check" and the spoken apology, and the next one
  after `off` is answered. New harness flows `interrupt` and `failure` cover scenarios 4 and 5 live: «تمام» over the
  answer ignored, «عيد» (heard «ريد») repeats it, «وقف» (heard «لغير») over the repeat stops it. Demo runbook above.
- 2026-10-27 01:10 — **Browser check on the real page** (fake microphone playing a recording): the voice bar showed
  "null" next to its buttons and stayed on "Starting the microphone" when there was no greeting to play (both fixed);
  Mute and the M key work, with an Arabic keyboard too. End to end: «حسناً.»/"Okay." heard 1.87 s after the speech
  ended (the browser's own report), "let me check" 0.9 s later, progress lines, the reply; longest silence before it
  6.0 s. The replayed clip came back from Whisper as gibberish → D46. Rehearsal 1 of the six flows (scenarios 1-5) on
  0bacd17: all pass.

