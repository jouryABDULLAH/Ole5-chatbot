"""One voice conversation: the turn manager and the speaker behind one WebSocket (VOICE_PLAN.md section 4).

Turn manager: at most one Ole5 turn in flight. Utterances are transcribed one at a time, in the order they arrive. While
a turn runs, a rule (stop, wait, repeat, are you there) is answered at once, a backchannel is dropped, and one
substantive utterance is held (several are joined) and sent when the turn ends. A sentence split by a pause arrives
as two utterances: when the customer is already speaking again as the first is transcribed, it waits and the two are
sent as one. A turn past the deadline gets "this is taking longer"; its late result is shown, never spoken. "stop"
during a turn means its reply is shown but not spoken. Speech that began over the assistant counts as an answer when
the assistant had just asked a question; a yes over the confirm read-back is asked again (D4).

Speaker: one ordered queue of things to say. Each item is announced (tts_start), streamed as 40 ms frames and closed
(tts_end). The browser buffers and plays them in order and reports each one played, so the speaker knows when the
assistant has finished talking. A reply drops the fillers still waiting; stop, wait and repeat purge everything,
including what the browser holds."""

import asyncio
import itertools
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass, field

from ole5_chat import sessions
from ole5_chat.agent.validate import script_language
from ole5_chat.config import get_settings
from ole5_chat.tickets.submit import SubmitRefused
from ole5_chat.voice import fillers, rules, speakable, stt, tts

log = logging.getLogger(__name__)

SendJson = Callable[[dict], Awaitable[None]]
SendBytes = Callable[[bytes], Awaitable[None]]
ECHO_WINDOW_S = 60.0
PROGRESS = ("still_checking", "still_looking", "still_checking")
JOIN_GRACE_S = 0.15  # after a transcript, how long to look for the customer speaking again before starting the turn
JOIN_LIMIT_S = 15.0  # a partial sentence waits at most this long for the rest
QUESTION_END = ("?", "؟")
GREETED: set[str] = set()  # sessions whose greeting was already spoken, so a reconnect does not repeat it


def _view(session_id: str, state: dict) -> dict:
    from ole5_chat.api import _view as view  # imported late: api.py includes this package's router
    return view(session_id, state).model_dump(mode="json")


@dataclass
class Backend:
    """What a voice session calls. Tests replace each part with a fake."""

    transcribe: Callable[[bytes, str], Awaitable[stt.Heard]] = stt.transcribe
    speech: Callable[[str, str], AsyncIterator[bytes]] = tts.stream
    cached: Callable[[str, str], Awaitable[bytes]] = fillers.pcm
    send: Callable[..., dict] = sessions.send
    submit: Callable[..., dict] = sessions.submit
    note: Callable[[str, str, str], dict] = sessions.note
    get: Callable[[str], dict] = sessions.get
    view: Callable[[str, dict], dict] = _view
    warm: Callable[[], Awaitable[None]] | None = None


@dataclass
class Item:
    """One thing to say."""

    id: str
    kind: str  # "reply", "filler" or "line"
    text: str
    language: str
    turn: int | None = None
    pcm: bytes | None = None  # audio already made: a cached fixed line, or a reply said again
    audio: bytearray = field(default_factory=bytearray)  # what was streamed, kept for "repeat"
    complete: bool = False


async def _frames(pcm: bytes) -> AsyncIterator[bytes]:
    for frame in tts.frames_of(pcm):
        yield frame


class Speaker:
    def __init__(self, send_json: SendJson, send_bytes: SendBytes, backend: Backend,
                 on_failure: Callable[[Item], None] | None = None):
        self._send_json, self._send_bytes, self._backend = send_json, send_bytes, backend
        self._on_failure = on_failure
        self._queue: list[Item] = []
        self._wake = asyncio.Event()
        self._ids = itertools.count(1)
        self._playing: asyncio.Task | None = None
        self.current: Item | None = None
        self.unplayed: dict[str, Item] = {}  # streamed (or streaming) and not yet reported played by the browser
        self.recent: list[tuple[float, str]] = []  # (when, text) of what was said, for the echo filter
        self._task = asyncio.create_task(self._run(), name="voice-speaker")

    @property
    def busy(self) -> bool:
        """Something is waiting, being streamed, or still playing in the browser."""
        return bool(self._queue or self.current or self.unplayed)

    def say(self, kind: str, text: str, language: str, *, turn: int | None = None, pcm: bytes | None = None) -> Item:
        item = Item(f"s{next(self._ids)}", kind, text, language, turn, pcm)
        self._queue.append(item)
        self._wake.set()
        return item

    def drop_waiting(self, kinds: set[str], turn: int | None = None) -> None:
        """Removes waiting items of these kinds (of one turn, if given); what is being said now finishes."""
        self._queue = [i for i in self._queue if i.kind not in kinds or (turn is not None and i.turn != turn)]

    @property
    def last_said(self) -> str:
        return self.recent[-1][1] if self.recent else ""

    async def purge(self, always: bool = False) -> bool:
        """Silence now: nothing waiting, the item being streamed stopped, and the browser told to drop what it holds
        (always, when the browser asked: it keeps dropping audio until it hears this). True when something stopped."""
        had = self.busy
        self._queue.clear()
        if self._playing and not self._playing.done():
            self._playing.cancel()
        self.unplayed.clear()
        if had or always:
            await self._send_json({"type": "stop_audio"})
        return had

    def played(self, item_id: str) -> None:
        self.unplayed.pop(item_id, None)

    async def close(self) -> None:
        for task in (self._playing, self._task):
            if task and not task.done():
                task.cancel()

    async def _run(self) -> None:
        while True:
            if not self._queue:
                self._wake.clear()
                await self._wake.wait()
                continue
            item = self.current = self._queue.pop(0)
            self._playing = asyncio.create_task(self._stream(item), name=f"voice-say-{item.id}")
            try:
                await asyncio.wait({self._playing})
            finally:
                if not self._playing.done():
                    self._playing.cancel()
            failed = not self._playing.cancelled() and self._playing.exception() is not None
            if failed:
                log.error("voice  speech failed for %s %r", item.kind, item.text[:80], exc_info=self._playing.exception())
                if self._on_failure:
                    self._on_failure(item)
            cancelled = self._playing.cancelled() or failed
            if cancelled:
                self.unplayed.pop(item.id, None)
            self.current = None
            await self._send_json({"type": "tts_end", "id": item.id, "cancelled": cancelled})

    async def _stream(self, item: Item) -> None:
        await self._send_json({"type": "tts_start", "id": item.id, "kind": item.kind, "text": item.text})
        self.unplayed[item.id] = item
        now = time.monotonic()
        self.recent = [(at, text) for at, text in self.recent if now - at < ECHO_WINDOW_S] + [(now, item.text)]
        source = _frames(item.pcm) if item.pcm is not None else self._backend.speech(item.text, item.language)
        async with aclosing(source) as frames:
            async for frame in frames:
                item.audio += frame
                await self._send_bytes(frame)
        item.complete = True


class VoiceSession:
    def __init__(self, session_id: str, send_json: SendJson, send_bytes: SendBytes, backend: Backend | None = None, *,
                 deadline: float | None = None, progress_every: float | None = None):
        s = get_settings()
        self.id = session_id
        self.backend = backend or Backend()
        self._out = send_json
        self.closed = False
        self.speaker = Speaker(self.send_json, send_bytes, self.backend, on_failure=self._speech_failed)
        self.deadline = deadline or s.voice_turn_deadline
        self.progress_every = progress_every or s.voice_progress_every
        self.max_wav = int(s.voice_max_utterance_s * 16000 * 2) + 44
        self.language = self.backend.get(session_id)["identity"].language
        self.turn: asyncio.Task | None = None
        self.turn_no = 0
        self.muted: set[int] = set()  # turns whose reply is shown and not spoken
        self.searched: set[int] = set()
        self.held: tuple[str, str | None] | None = None
        self.partial: tuple[str, str | None, float] | None = None  # the first half of a sentence split by a pause
        self.talking = False  # the browser's speech detector hears the customer now
        self.confirm_asked = False  # "just to confirm" was said and not yet answered (D15)
        self.last_reply: list[Item] = []
        self._inbox: asyncio.Queue = asyncio.Queue()
        self._tasks: set[asyncio.Task] = set()
        self._worker = asyncio.create_task(self._work(), name="voice-worker")

    # ---- called by the socket ----

    async def start(self) -> None:
        """Ready; the greeting is spoken once when the conversation has nothing else yet."""
        if self.backend.warm:
            self._spawn(self.backend.warm())
        await self.send_json({"type": "ready", "language": self.language})
        state = self.backend.get(self.id)
        messages = state["messages"]
        if self.id not in GREETED and messages and not any(m.type == "human" for m in messages):
            GREETED.add(self.id)
            text = speakable.speakable(messages[-1].text, self.language)  # the same text fillers.warm caches
            self.last_reply = [self.speaker.say("reply", text, self.language, pcm=await self._cached(text))]

    def utterance(self, meta: dict, wav: bytes) -> None:
        self.talking = False
        self._inbox.put_nowait(("speech", meta, wav))

    def typed(self, text: str) -> None:
        self._inbox.put_nowait(("typed", text))

    def submit(self) -> None:
        self._inbox.put_nowait(("submit",))

    def played(self, item_id: str) -> None:
        self.speaker.played(item_id)

    async def stop(self) -> None:
        """The Stop button, or "stop" said: silence now; a turn still running is shown, not spoken. The turn is muted
        before the first await, so a turn finishing meanwhile cannot queue its reply after the purge."""
        if self.turn is not None:
            self.muted.add(self.turn_no)
        self.partial = None
        await self.speaker.purge(always=True)
        await self._status()

    async def barge_in(self) -> None:
        """Speech over the assistant went on past the backchannel limit: it stops talking. What was said is judged
        when the utterance arrives."""
        await self.speaker.purge(always=True)

    def speech(self, on: bool) -> None:
        """The browser's speech detector: the customer started speaking (on), or what it heard was not speech or will
        not be sent (off). Off with a partial sentence waiting sends the partial."""
        self.talking = on
        if not on and self.partial:
            self._inbox.put_nowait(("flush",))

    async def close(self) -> None:
        self.closed = True
        for task in (self._worker, self.turn, *self._tasks):
            if task and not task.done():
                task.cancel()
        await self.speaker.close()

    async def send_json(self, data: dict) -> None:
        if self.closed:
            return
        try:
            await self._out(data)
        except Exception:
            self.closed = True

    # ---- inbound work, one item at a time ----

    async def _work(self) -> None:
        while True:
            job = await self._inbox.get()
            try:
                if job[0] == "speech":
                    await self._hear(job[1], job[2])
                elif job[0] == "typed":
                    text = job[1].strip()
                    if text:
                        await self._words(text, None, typed=True)
                elif job[0] == "submit":
                    await self._submit_button()
                elif job[0] == "flush":
                    self._flush_partial()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("voice  could not handle %s", job[0])
                await self.send_json({"type": "error", "text": "Something went wrong with the voice channel."})

    async def _hear(self, meta: dict, wav: bytes) -> None:
        over_bot = bool(meta.get("over_bot"))
        if len(wav) > self.max_wav or not wav.startswith(b"RIFF"):
            await self.send_json({"type": "ignored", "reason": "bad_audio"})
            return
        try:
            heard = await self.backend.transcribe(wav, self.language)
        except Exception:
            log.exception("voice  speech to text failed")
            if not over_bot:
                await self._say_line("unclear")
            await self.send_json({"type": "ignored", "reason": "stt_failed"})
            return
        if not heard.text:
            await self.send_json({"type": "ignored", "reason": "empty"})
            return
        await self.send_json({"type": "heard", "text": heard.text, "language": heard.language or self.language,
                              "over_bot": over_bot})
        await self._words(heard.text, heard.language, over_bot=over_bot)

    async def _words(self, text: str, language: str | None, *, over_bot: bool = False, typed: bool = False) -> None:
        command = None if typed else rules.command(text, talking=self.speaker.busy)
        # Speech that began in the assistant's question and went on after it is the answer to it.
        answering = over_bot and self.speaker.last_said.rstrip().endswith(QUESTION_END)
        if over_bot:
            if command == "stop":
                await self.stop()
                return
            reason = None if answering else (
                "backchannel" if rules.is_backchannel(text) or rules.is_thanks(text) or rules.is_fragment(text)
                else "echo" if self._echo(text) else None)
            if reason:
                await self.send_json({"type": "ignored", "reason": reason})
                return
            await self.speaker.purge()  # real words over the assistant: it stops, and they are answered below
        if command:
            if command == "stop":
                self.partial = None
            await self._rule(command)
            return
        language = None if typed else self._language(text, language)
        if self.turn is not None:
            if rules.is_backchannel(text) or rules.is_fragment(text) or rules.is_thanks(text):
                await self.send_json({"type": "ignored", "reason": "backchannel"})
                return
            first = not self.held
            self.held = (f"{self.held[0]} {text}" if self.held else text, language)
            if first:
                await self._say_line("held")
            return
        if not typed:
            text, language = self._joined(text, language)
            if await self._still_talking():
                self.partial = (text, language, time.monotonic())
                self._spawn(self._flush_later(self.partial))
                return
        if not typed and rules.is_fragment(text):
            await self._say_line("just_confirm" if self._phase() == "confirm" else "unclear")
            return
        if not typed and self._phase() == "confirm":
            kind = rules.confirm_kind(text)
            if kind == "ack" and self.confirm_asked:
                self.confirm_asked = False
                self._start("submit", text, language)  # D15: a second acknowledgement is a yes
                return
            if kind == "ack":
                self.confirm_asked = True
                line = rules.line("just_confirm", self.language)
                state = await asyncio.to_thread(self.backend.note, self.id, text, line)
                await self._push_view(state)
                self.speaker.say("line", line, self.language, pcm=await self._cached(line))
                return
            if kind == "unclear" or (kind == "submit" and over_bot):  # D4: a yes over the read-back is asked again
                await self._say_line("just_confirm")
                return
        self.confirm_asked = False
        self._start("send", text, language)

    def _joined(self, text: str, language: str | None) -> tuple[str, str | None]:
        """The text with the first half of a split sentence before it, if one is waiting."""
        if not self.partial:
            return text, language
        first, first_language, _ = self.partial
        self.partial = None
        return f"{first} {text}", language or first_language

    async def _still_talking(self) -> bool:
        if not self.talking:
            await asyncio.sleep(JOIN_GRACE_S)
        return self.talking

    async def _flush_later(self, partial: tuple) -> None:
        await asyncio.sleep(JOIN_LIMIT_S)
        if self.partial is partial:
            self._inbox.put_nowait(("flush",))

    def _flush_partial(self) -> None:
        """The rest of a split sentence never came: the first half is sent on its own."""
        if not self.partial or self.talking:
            return
        text, language, _ = self.partial
        self.partial = None
        if self.turn is not None:
            self.held = (f"{self.held[0]} {text}" if self.held else text, language)
        else:
            self.confirm_asked = False
            self._start("send", text, language)

    async def _rule(self, command: str) -> None:
        if command == "stop":
            await self.stop()
        elif command == "wait":
            await self.speaker.purge()
            await self._say_line("wait")
        elif command == "repeat":
            if not self.last_reply:
                await self._say_line("nothing_to_repeat")
                return
            await self.speaker.purge()
            self.last_reply = [self.speaker.say("reply", i.text, i.language, turn=i.turn,
                                                pcm=bytes(i.audio) if i.complete else None) for i in self.last_reply]
        elif command == "there":
            await self._say_line("there_busy" if self.turn is not None else "there")

    async def _submit_button(self) -> None:
        if self.turn is not None:
            await self.send_json({"type": "error", "text": "Still working on the last message."})
            return
        self.confirm_asked = False
        self._start("button", None, None)

    # ---- one Ole5 turn ----

    def _start(self, kind: str, text: str | None, language: str | None) -> None:
        self.turn_no += 1
        self.turn = asyncio.create_task(self._turn(self.turn_no, kind, text, language), name=f"voice-turn-{self.turn_no}")

    def _call(self, kind: str, text: str | None, language: str | None, on_event: Callable[[dict], None]) -> tuple[int, dict]:
        """Runs in a worker thread: the graph is synchronous and holds the session lock for the whole turn. Returns
        where this turn's reply starts: after the customer's words, or for the Submit button after the messages there
        were (read before the lock: the button is refused while another turn runs)."""
        before = len(self.backend.get(self.id)["messages"])
        if kind == "send":
            state = self.backend.send(self.id, text, channel="voice", heard_language=language, on_event=on_event)
        else:
            # The Submit button has no words to hear: the conversation's language stands in for them.
            state = self.backend.submit(self.id, channel="voice", said=text, heard_language=language or self.language,
                                        on_event=on_event)
        return before, state

    async def _turn(self, no: int, kind: str, text: str | None, language: str | None) -> None:
        """language: what the customer spoke, or None for a typed message or the Submit button (Ole5 then judges it
        as it does in the text chat)."""
        loop = asyncio.get_running_loop()
        started = time.perf_counter()

        def on_event(event: dict) -> None:
            loop.call_soon_threadsafe(self._graph_event, no, event)

        await self.send_json({"type": "turn", "text": text})
        await self._status("thinking")
        progress = asyncio.create_task(self._progress(no))
        work = asyncio.ensure_future(asyncio.to_thread(self._call, kind, text, language, on_event))
        late, outcome, shown = False, "spoken", False
        try:
            try:
                before, state = await asyncio.wait_for(asyncio.shield(work), self.deadline)
            except TimeoutError:
                late, outcome = True, "late"
                log.warning("voice  turn %d past the %.0f s deadline", no, self.deadline)
                self.muted.add(no)  # no more fillers for it; its reply is shown when it comes
                progress.cancel()
                self.speaker.drop_waiting({"filler"})
                await self._say_line("late")
                before, state = await work
            await self._push_view(state)
            shown = True
            if late or no in self.muted:
                outcome = "late" if late else "muted"
                return
            messages = state["messages"]
            if text:
                before = max((i + 1 for i, m in enumerate(messages) if m.type == "human"), default=before)
            reply = "\n".join(m.text for m in messages[before:] if m.type == "ai").strip()
            numbers = [t["ticket_number"] for t in state.get("created_tickets") or []]
            self._speak_reply(reply, script_language(reply) or language or self.language, numbers, turn=no)
        except SubmitRefused as exc:
            outcome = "refused"
            await self.send_json({"type": "error", "text": str(exc)})
        except Exception:
            outcome = "failed"
            log.exception("voice  turn %d failed", no)
            if not late:
                self.speaker.drop_waiting({"filler"})
                await self._say_line("apology")
        finally:
            progress.cancel()
            log.info("voice  turn no=%d kind=%s outcome=%s ms=%d", no, kind, outcome,
                     int((time.perf_counter() - started) * 1000))
            self.muted.discard(no)
            self.searched.discard(no)
            # No await between clearing the turn and starting the held one: an utterance handled in between would
            # start a turn of its own.
            self.turn = None
            following = self._start_held() if not self.closed else False
            if not self.closed:
                if not shown:  # the page shows the customer's words with a "writing" mark until a view arrives
                    await self._push_view()
                if not following:
                    await self._status()

    def _start_held(self) -> bool:
        if not self.held:
            return False
        text, language = self.held
        self.held = None
        self._start("send", text, language)
        return True

    def _speech_failed(self, item: Item) -> None:
        """A reply segment could not be spoken (Groq refused or failed): the rest of that reply is dropped and the
        customer is told it is on the screen, once."""
        if item.kind != "reply":
            return
        self.speaker.drop_waiting({"reply"}, turn=item.turn)
        self._spawn(self._say_line("speech_failed"))

    def _speak_reply(self, reply: str, language: str, numbers: list[str], turn: int | None) -> None:
        if not reply:
            return
        self.speaker.drop_waiting({"filler"})
        spoken = speakable.speakable(reply, language, numbers)
        self.last_reply = [self.speaker.say("reply", part, language, turn=turn) for part in speakable.segments(spoken)]

    def _graph_event(self, no: int, event: dict) -> None:
        """Runs in the event loop, called from the graph's worker thread."""
        if no != self.turn_no or self.closed:
            return
        if event.get("type") == "search_started" and no not in self.searched and no not in self.muted:
            self.searched.add(no)
            self._spawn(self._searching())

    async def _searching(self) -> None:
        await self._status("searching")
        await self._say_line("searching", kind="filler")

    async def _progress(self, no: int) -> None:
        """While a turn runs and nothing is being said: a progress line every progress_every seconds, three at most."""
        for key in PROGRESS:
            await asyncio.sleep(self.progress_every)
            if no != self.turn_no or no in self.muted:
                return
            if not self.speaker.busy:
                await self._say_line(key, kind="filler")

    # ---- helpers ----

    def _language(self, text: str, found: str | None) -> str:
        """The conversation's language after this utterance (sticky): it follows the letters of what was said, but a
        short utterance in the other script (a product or screen name said on its own) does not switch it."""
        if found and found != self.language and len(text.split()) >= 3:
            self.language = found
        return self.language

    def _phase(self) -> str:
        return self.backend.get(self.id)["phase"]

    def _echo(self, text: str) -> bool:
        """The microphone picked up the assistant: most of the words heard are words it just said."""
        words = rules.normalize(text).split()
        if len(words) < 2:
            return False
        now = time.monotonic()
        said = set(" ".join(rules.normalize(t) for at, t in self.speaker.recent if now - at < ECHO_WINDOW_S).split())
        return sum(w in said for w in words) / len(words) >= 0.7

    async def _cached(self, text: str) -> bytes | None:
        try:
            return await self.backend.cached(text, self.language)
        except Exception:
            log.exception("voice  no audio for the fixed line %r", text)
            return None

    async def _say_line(self, key: str, kind: str = "line") -> None:
        text = rules.line(key, self.language)
        self.speaker.say(kind, text, self.language, pcm=await self._cached(text))

    async def _status(self, stage: str | None = None) -> None:
        if stage is None:
            stage = "thinking" if self.turn is not None else "listening"
        await self.send_json({"type": "status", "stage": stage})

    async def _push_view(self, state: dict | None = None) -> None:
        try:
            view = self.backend.view(self.id, state if state is not None else self.backend.get(self.id))
        except Exception:
            log.exception("voice  could not build the view")
            return
        await self.send_json({"type": "view", "session": view})

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
