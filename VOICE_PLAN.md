# Voice for Ole5: implementation plan

Branch `voice-cascade`, cut from `router-next-only-design` at `98558be` on 2026-10-26.
Companion file: [VOICE_LOG.md](VOICE_LOG.md) (decisions, progress, checkpoints, runbook). This file says what we are
building and how; the log says what happened. Revision 2: two independent reviews of revision 1 found 52 issues; the
fixes are folded in below.

## 1. Goal for the demo

In the existing web widget, a signed-in customer turns on the microphone and talks to the Ole5 assistant in Arabic or
English:

- The assistant answers aloud from the real knowledge base (RAGent2), in the language the customer spoke: Arabic by
  Groq's Saudi voice `abdullah`, English by the Orpheus voice `troy` (male voices, D29).
- No dead air: an acknowledgement as soon as the question is understood (D38), "let me check" when a search starts,
  progress lines while it runs (D39), and a spoken apology on any error or timeout.
- The customer can interrupt. A "تمام" or "ok" said over the assistant is ignored; real words stop it.
- A ticket can be opened by voice: offer → details → summary read aloud → explicit yes → ticket confirmed aloud. A bare
  "تمام" or "ok" at that point gets "just to confirm…". The on-screen card and the Submit button keep working.
- "Repeat that", "wait", "are you there?" and "stop" are answered at once, without calling Ole5.
- The text chat behaves exactly as before, and every existing test stays green.

**Definition of done:** the scenarios in section 10 pass twice in a row on the frozen demo commit, in Arabic and English.

**Channel:** the website only. Phone calls are not planned (D1).

**Not in scope:** Pipecat or LiveKit, in-Kingdom speech vendors, streaming the model's tokens into speech, diacritics,
login and rate limits on the voice route, persistent sessions, production deployment (section 13).

## 2. Design: one brain, a voice shell around it

Ole5 stays the only brain. Everything voice-specific is plain code around it, inside the same FastAPI process, so the
in-memory sessions are shared. Voice turns make no extra model calls; they make two fewer (D8).

```
Browser (t2-chat widget)
  mic kept open all session, echo cancellation on; Silero VAD in the browser (vad-web 0.0.31)
  push-to-talk or hands-free (same VAD; only the gating differs)
  utterance → 16 kHz PCM16 WAV ──── WebSocket /chat/sessions/{id}/voice ────┐
                                                                           │
Server, per socket (src/ole5_chat/voice/)                                  ▼
  1. STT (AsyncGroq whisper-large-v3-turbo, verbose_json), two calls at once (D24):
       Arabic + "نظام Ole5 ودعم RiCH", English + "Ole5, RiCH"; same script → that one,
       else the more confident (+0.3 for the conversation's language); no hint under 1.8 s
  2. Filters: empty / punctuation / self-echo (matches what the assistant just said)
       / known hallucinations, only on short or over-the-assistant clips
  3. Turn manager: at most ONE Ole5 turn in flight
       rules first (whole utterance, ≤ 3 words): stop > wait > repeat > are-you-there
       backchannel over the assistant → ignored (un-duck)
       during a turn: rules answered at once; backchannels dropped;
         one substantive utterance held ("I'll come to that next") and run after
       confirm phase: the confirm table (section 6)
  4. Ole5 turn: asyncio.to_thread(sessions.send, id, words, channel="voice", heard_language, on_event)
       on voice turns, inside the graph: 1 search at most, 30 s cap on the search,
       15 s model timeout, no retries, prompt nonce (Groq slow-cache path)
       events: search_started, search_done → progress lines
  5. Speaker (one ordered output queue per socket): fillers, fixed lines, reply segments;
       a reply cancels pending fillers; stop/barge-in purges the queue and closes TTS
  6. Speakable: markdown and "←" out, lines joined, ticket number → "on your screen, ending in…"
  7. TTS (AsyncGroq Orpheus): sentence segments, pipelined; WAV header parsed (78 bytes);
       40 ms PCM16 24 kHz frames; one voice per reply, by the reply's language (D35)
  8. View push: the SessionView after every Ole5 turn (transcript, phase, card)
  Turn deadline 45 s → spoken apology; a late result is shown, never spoken
                                                                           │
Browser ◄────── tts_start · PCM frames · tts_end · JSON events ────────────┘
  gapless player (24 kHz, 120 ms lead); ducks on speech start; stops on barge-in, drops late frames
```

Why not the alternatives: speech-to-speech paraphrases Ole5 and skips its grounding checks; a second "front" model
states unchecked facts and keeps a second memory; the friend's repo has no license and needs Python 3.14; Pipecat is a
new framework and protocol to learn under time pressure (it is the path after the demo, section 13).

## 3. Changes inside Ole5 (guarded hooks; text chat unchanged)

Joori is still changing these files (19 commits on 2026-10-26), so they get small hooks only. **Each hook is applied
and the full suite re-run before anything builds on it.** (Revision 1 claimed they had been tried in a scratch copy;
only some had.)

| File | Change | Text chat |
|---|---|---|
| `agent/state.py` | `channel: str` ("chat" or "voice") and `heard_language: str \| None`, defaulted in `initial_state` | Unchanged |
| `sessions.py` | `send(..., *, channel="chat", heard_language=None, on_event=None)`: with `on_event` it streams the graph and forwards node names and custom events (callback errors are logged, never abort the turn). `submit(session_id, *, channel="chat")`. Both set the voice context for llm.py. `channel` is passed on every turn, because LangGraph keeps the last value. | Unchanged |
| `agent/respond_agent.py` | `search`: guarded `search_started` and `search_done` events; voice turns search once, skip the errored-search retry, and cap the search at 30 s (a timeout becomes an error result, so the turn ends and the lock is released). `respond_agent`: voice turns take the language from STT and check the reply's language by script. `_invoke`: same error handling and nonce as llm.py. | Unchanged |
| `agent/handlers.py` | Voice tasks are built from the draft: summary, priority (in Arabic words for Arabic: منخفضة/متوسطة/عالية/حرجة), urgent. Applies to `done`, `change`, the urgent branch of `next_issue`, and the submit-failure path. **Graph-side yes guard** in `submit`: on a voice turn whose last message is the customer's, the normalized text must contain an explicit-yes word, otherwise the re-confirm task is returned and nothing is created. Button turns are unaffected. | Unchanged |
| `agent/prompt.py` | Voice overlays: spoken style in the respond prompt's note slot; read-back wording for confirm, urgent_ready and changed; "at most two short questions" for accept and details; a one-line note in the router prompt that messages are speech transcripts | Unchanged |
| `llm.py` | Transport errors become `ModelFailed` (D11): `BadRequestError` handling stays as it is; `APIConnectionError` (including timeouts), `RateLimitError` and `InternalServerError` raise `ModelFailed` at once. Voice turns (context variable): 15 s timeout, `max_retries=0`, and a random first prompt line (D9), applied by one helper also used by `_invoke` | Errors answered with the apology instead of an HTTP 500 |
| `config.py` | Voice settings (models, voices, timers, caps), declared before they go in `.env` | None |
| `api.py` | `app.include_router(voice_router)`, before `main.py` mounts `web/` at `/`; the Submit endpoint passes `channel="chat"` | None |
| `main.py` | Background warm-up for the voice clients and the fixed-line audio cache, like `warm_knowledge` (never at import, so tests stay offline) | None |
| `web/t2-chat.js` | Mic control outside the re-rendered area, voice mode, transcript sync incl. fixed-line exchanges; in voice mode the Submit button goes through the socket | Text UI unchanged |

New files: `voice/api.py` (WebSocket, config), `voice/session.py` (per-socket turn manager and speaker),
`voice/stt.py`, `voice/tts.py`, `voice/speakable.py`, `voice/rules.py` (normalization, rules, confirm table),
`voice/fillers.py` (fixed-line audio, disk cache keyed by model, voice and text), `web/voice.js` (VAD, WAV, socket,
player), `web/vendor/` (vad-web, onnxruntime and the model, so the demo needs no CDN), `tests/test_voice_*.py`,
`scripts/try_voice.py`.

RAGent2 makes its own Groq calls with its own client, so the voice timeout and nonce do not reach it. The 30 s search
cap is what bounds it.

## 4. The turn manager and the speaker

These two pieces carry most of the quality, so they get the most tests.

**Turn manager**, one per socket:
- At most one Ole5 turn in flight.
- While it runs, a new utterance is transcribed at once:
  - a rule ("stop", "wait", "are you there") is answered at once; "are you there" gets "I'm still checking your question";
  - a backchannel is dropped;
  - a substantive utterance is held (only the latest one), acknowledged ("I'll answer that right after"), and sent when
    the current turn ends.
- "stop" or a barge-in during a turn means its reply is shown on screen but not spoken.
- At the 45 s deadline the customer hears the apology; the late result is shown when it arrives, never spoken.

**Speaker**, one ordered queue per socket:
- Each item has an id and a kind: filler, progress, fixed line or reply segment.
- A reply cancels any filler still waiting; a fixed line ("stop", "wait") purges the queue.
- On a purge the TTS request is closed, queued frames are dropped, and `tts_end` carries `cancelled`.
- STT and TTS use the async Groq client, so nothing blocks the socket's event loop.

## 5. The WebSocket protocol

One socket per session at `/chat/sessions/{session_id}/voice`. The session comes from the existing email sign-in. Raw
graph updates are never forwarded.

| Direction | Frame | Meaning |
|---|---|---|
| browser → server | `{"type":"utterance","over_bot":bool,"ms":int}` then binary WAV (16 kHz mono PCM16) | One utterance. `over_bot` is set when speech started while the assistant was talking, and not in its last ~0.6 s |
| browser → server | `{"type":"barge_in"}` | Speech over the assistant passed ~1.2 s; playback was stopped |
| browser → server | `{"type":"submit"}` | The Submit button, in voice mode |
| browser → server | `{"type":"played","id"}` | A spoken item finished playing |
| server → browser | `{"type":"heard","text","language"}` | What STT heard |
| server → browser | `{"type":"ignored","reason"}` | Backchannel, echo or noise: un-duck |
| server → browser | `{"type":"status","stage"}` | `listening`, `thinking`, `searching`, `speaking` |
| server → browser | `{"type":"line","author","text"}` | A fixed-line exchange to show in the transcript |
| server → browser | `{"type":"tts_start","id","kind","text"}` · binary 40 ms frames · `{"type":"tts_end","id","cancelled"}` | Audio to play in order |
| server → browser | `{"type":"view","session":{…}}` | The SessionView after an Ole5 turn |
| server → browser | `{"type":"error","text"}` | Shown; a spoken apology follows |

## 6. Conversation rules for voice

**Normalization** (before any matching): strip diacritics, tatweel and punctuation; unify أ/إ/آ→ا, ة→ه, ى→ي;
lowercase Latin.

**Rules** fire only when the whole utterance is three words or fewer and is made only of rule phrases, with the
priority stop > wait > repeat > are-you-there. "النظام توقف" or "I've been waiting an hour" go to Ole5. "wait" pauses the
silence prompt. Filler words are never rule words.

**Language:** sticky per voice session. The first substantive utterance sets it, then it follows the transcript's
script (Arabic or Latin letters). Clips under 1 s are transcribed with the session language forced. A detected
language other than Arabic or English is retried with the session language. It is always exactly `ar` or `en`.

**Style** (voice overlay): short spoken sentences in the passages' own order and words (no added "first… then…": D34), at most two short questions per turn, the question last, no markdown, links or sources. D13: the spoken reply changes form, not content; long answers are split into sentences for speech, and the full text is also on screen.

**Confirm table** (D4). Text is normalized first. The graph-side guard enforces the same rule.

| What the customer says (at confirm, after the read-back) | What happens |
|---|---|
| A submit word (ارسل/أرسل، ارسلها، قدمها، submit, send) | To Ole5 → router `yes` → ticket created |
| A plain yes: نعم، ايه، ايوه، اكيد، yes, yes please, sure | To Ole5 → `yes` → ticket created |
| Only an acknowledgement: تمام، طيب، اوكي، ok, okay, alright | Fixed line: "Just to confirm: shall I submit it? Say yes, submit, or no." The exchange goes into the transcript and the history |
| An acknowledgement right after that line | **Decision D15** |
| Anything with content ("نعم بس غير الأولوية", "cancel", a question) | To Ole5 (change, cancel, question) |
| Spoken over the read-back | Ignored, unless it started in the read-back's last ~0.6 s; then it is treated as an answer |

**Ticket number** (D12): the model's text keeps the full number, so the on-screen bubble shows it. The spoken version
is changed in code: "your ticket number is on your screen, ending in zero zero zero one" (from `created_tickets`).

**Silence** (D17, D44): «أنا هنا إن احتجت إلى أي مساعدة.» / "I'm here whenever you're ready." once after 18 s of quiet,
and only after the assistant asked a question; never once a ticket exists, nor while the microphone is muted (D45).

## 7. What you need to do or decide

**Now (CP0, about 15 minutes):**
1. **Mic lab.** I'll give you a local page (no repo change). In Chrome on the demo laptop, run the echo test once on the
   laptop speakers and once on a headset, then record about 20 phrases on the demo mic: the scenario lines; نعم، ايه،
   لا، تمام، اوكي، ارسلها، وقف، عيد، yes, ok, stop; one sentence with a pause in it; "thank you". These become the test
   fixtures, the fake-mic input for browser tests, and the VAD tuning data. Real Saudi speech has not been tested yet.
2. ~~Groq limits~~: not needed, enterprise plan (D21). Decisions D15, D16, D19, D22 are answered (see the log).

**Demo setup (D19):** online over Google Meet, presenter on a headset, sharing the machine's audio. Customers will use
phones, headsets and laptop speakers, so the echo guards (Chrome's echo cancellation, the self-echo filter, barge-in
confirmed by speech-to-text, the half-duplex switch) stay in scope. Phones need HTTPS for the microphone, so mobile is
tested after the demo through an HTTPS tunnel.

**After CP2 (~22:00, 10 minutes):** a real-mic pass of the scenarios.
**Morning:** one rehearsal on the frozen commit.

## 8. Quality strategy

Every checkpoint ends with this gate:

1. **The existing suite** stays green: baseline 134 passed, 1 skipped (re-run in the working tree with its `.env` at the
   start of CP1). Tests never touch the network: Groq clients are created lazily and are faked in tests.
2. **New tests:**
   - unit: normalization, rules, the confirm table (one test per row), language choice, speakable, the WAV parser and
     framing
   - graph, with the existing `ScriptedModel`/`ToolModel` and unchanged signatures: voice flag on and off, overlays with
     the draft fields, the yes guard (including "تمام كذا"), no `language_of` call on voice, transport errors →
     apology, the one-search cap and the search timeout
   - WebSocket, with fake STT/TTS and injectable timers: event order, held utterances, superseded and late turns,
     backchannel over the assistant, an 800 ms "تمام", stop, deadline → apology → the next utterance answered
3. **Live pipeline check** (`scripts/try_voice.py`): your recordings through real STT → Ole5 → TTS on a temp copy of
   the sheets. The ~10 knowledge-base questions run with channel chat and with channel voice; voice must not have more
   grounding retries or "no answer" outcomes. It also round-trips long replies through Whisper to catch TTS dropouts.
4. **Browser check:** I drive the widget with your recordings fed in as the mic, because the built-in browser blocks
   real microphones.
5. **Independent review** of each checkpoint's diff before the next one starts.
6. **Log entry** and a **tagged commit** at every checkpoint, so there is always a demoable build to fall back to.

## 9. Checkpoints

Hard stop at 04:00: after it, only fixes to the last tagged build. Joori's branch is not merged in before the demo
unless one of his fixes is needed, and then section 10 is re-run in full (D18).

| CP | What | Exit criteria | Until |
|---|---|---|---|
| 0 | Plan agreed; mic lab (echo test, recordings); Groq limits; open decisions; baseline re-run | Audio setup decided; recordings in the fixture folder | ~15:30 |
| 1 | Hands-free half-duplex end to end on MicVAD (D23; was push-to-talk): STT, TTS (segments), speakable v1, socket, speaker queue, turn manager (single turn + deadline + apology), `sessions.send` hook, one cached "let me check" on `search_started`, the widget's mic control and player; demo questions timed 3× each on real RAGent2 | Hold to talk, ask the demo questions in Arabic and English, hear the answers; tests green; tag `voice-cp1` | ~19:00 |
| 2 | Ole5 voice mode: channel flag, overlays with draft fields, yes guard and confirm table, language from STT, transport errors and voice timeouts, 1-search cap and 30 s search cap, Submit over the socket, ticket number | A full ticket by voice; chat-vs-voice grounding comparison passes; tests green; tag `voice-cp2`; your 10-minute real-mic pass | ~22:00 |
| 3 | No dead air: filler policy (first acknowledgement, search started/done, rotating progress lines, a long-wait line), fixed-line disk cache, per-stage latency report | No silence over ~1.5 s after the customer stops, in the demo scenarios | ~23:30 |
| 4 | Hands-free: VAD gating, ducking, barge-in with backchannel ignore and self-echo filter, rules, silence prompt, held utterances, mute hotkey; push-to-talk kept | Hands-free conversation with working interruptions on your recordings | ~02:00 |
| 5 | Quality pass: full review and fixes, vendored VAD assets, live pipeline run, rehearsal of section 10 twice, runbook | Section 10 passes twice; tag `voice-demo` | 04:00 |

## 10. Demo scenarios (the acceptance test)

1. **Arabic question answered from the KB** (chosen from the questions timed in CP1 that RAGent2 answers outright):
   acknowledgement, "let me check", progress, the spoken answer, and the text and sources on screen.
2. **English question with no answer → ticket:** offer, details, summary read back, a bare "ok" re-confirmed, then "yes,
   submit", and the number spoken.
3. **Urgent problem in Arabic** ("ولا موظف عندنا يقدر يسجل دخول"): the summary read back at once, then "نعم أرسلها".
4. **Interruptions:** "تمام" over the assistant is ignored; "stop" stops it; "repeat that" repeats it; a question asked
   while it is still searching is held and answered next.
5. **Failure:** with the knowledge base switched to "down" (a runtime switch that needs no restart and fails fast), a
   spoken apology, and the conversation goes on.

## 11. Measurements and targets

Every voice turn logs one `voice  latency` line: seconds after the utterance arrived for STT done, turn start, first
sound (and its kind), search started and done, Ole5 done and the reply's first sound. The browser reports the silence
the customer heard (`voice  heard silence gap_ms`, and `longest_ms` before the reply), and
`scripts/try_voice.py flow` plays whole conversations out in real time and prints each step's timeline. Targets (D43,
reset from the CP3 measurements): first sound within 2.0 s of the end of speech (median ~1.6 s) for every utterance
that gets an acknowledgement or a rule line; greetings, thanks, a bare no, typed messages and the Submit button about
3 s; the reply about 0.4 s after Ole5 is done; no silence over about 6 s while Ole5 works.

## 12. What the spikes established (2026-10-26)

- **Groq STT:** `whisper-large-v3-turbo` with the hint wrote "Ole5" correctly 4 of 4 times (without it, "أولي خمسة"
  and "Ol5"). `verbose_json` returns `language` as "Arabic"/"English". It takes 170–500 ms warm and about 1 s cold. It
  invents text on silence. All of this was tested on synthetic audio only.
- **Groq TTS:** the input limit is 4000 characters (not 200). First audio arrives in about 0.18–0.29 s warm, and it
  generates about 5× faster than real time. The WAV header is 78 bytes; chunks can be odd-sized; `sample_rate` only
  changes the header. Arabic output is deterministic. English voices: autumn, diana, hannah, austin, daniel, troy.
- **Groq cache:** the slow cache hits are still there today (6 of 13 cached calls slow, up to 10 s). A random first
  prompt line avoided them 10 of 10 times.
- **Browser:** vad-web 0.0.31 runs with no build step (model v5; onnxruntime must be 1.22.0). End of speech is
  detected 0.6–0.7 s after it stops. The Web Audio player is gapless and goes silent within ~45 ms. Chrome on Windows
  cancels the page's own playback by design, but this is not yet measured on your machine. The mic needs `localhost`
  or HTTPS, in Chrome.
- **Graph:** streaming in a worker thread works; the custom event arrives while the search runs; new state fields
  persist; the voice route must be registered in `api.py`. Baseline: 134 passed, 1 skipped.

## 13. Risks and fallbacks

| Risk | Guard |
|---|---|
| Echo in the room | Echo test now; headset or speakers decided now; self-echo filter; half-duplex switch plus an Interrupt button |
| A slow or hung knowledge-base turn | 1 search, 30 s cap, 45 s turn deadline, turn manager, warm-up question on demo morning |
| Real Saudi speech (VAD drops one-word answers, Whisper mishears) | Your recordings drive tuning and tests; short clips get the language forced; the text box stays for emails and codes |
| The confirm step derails | Confirm table with a test per row, the graph-side guard, read-back from the draft |
| Groq quota or a 429 on demo morning | Limits checked now; fixed-line audio cached on disk, including the apology; overnight runs budgeted |
| English replies with Arabic screen names, read by `troy` | Tested: `troy` says them recognisably, and switching to `abdullah` for those sentences sounded like a second speaker, so `troy` keeps the whole reply (D35) |
| Joori's changes | Frozen demo commit; merge after the demo |
| Environment traps (Excel holding `tickets.xlsx`, `TRACING` without Phoenix, Edge, LAN IP, CDN) | Demo runbook in the log; demo runs on a copy of `sheets/`; VAD assets vendored |

## 14. After the demo

Pipecat for full-duplex with Smart Turn (Arabic end-of-turn); streaming STT (Deepgram ar-SA or Speechmatics ar_en)
benchmarked on recorded Saudi speech; history trimmed to what the customer heard after a barge-in; data-residency
sign-off and an in-Kingdom vendor if required; login and rate limits on the voice route; persistent sessions; the
real OTRS ticket-number format.
