/* voice.js: the voice side of <t2-chat>, loaded only when the customer turns the microphone on. Plain JavaScript, no
   build step; exposes window.T2Voice.

   The microphone stays open while voice is on, with the browser's echo cancellation. A speech detector (Silero VAD,
   vad-web 0.0.31, served from web/vendor with the CDN as the fallback) cuts each utterance; it is sent as a 16 kHz WAV
   over the session's WebSocket. The assistant's speech
   comes back as 40 ms frames of 24 kHz PCM between tts_start and tts_end and is played without gaps. Speech that starts
   while the assistant is talking (and not in its last 0.6 s) is "over the assistant": the assistant's volume drops at
   once and the utterance is sent marked over_bot; the server decides (stop, wait and repeat act; "تمام", "ok" and the
   assistant's own echo are ignored and the volume comes back; other words stop it and are answered). With ?halfduplex
   in the page URL, speech over the assistant is dropped instead, unless it went on past the assistant's last word.
   The server is told when speech starts, so it can join a sentence split by a pause. Protocol: VOICE_PLAN.md
   section 5. */
(() => {
  // The speech detector's files: web/vendor next to this file first (VOICE_LOG.md D48), then the same versions on the
  // CDN. Absolute URLs: onnxruntime import()s its .mjs relative to the bundle, not the page.
  const HERE = new URL(".", document.currentScript?.src || location.href).href;
  const VAD_SOURCES = [
    { vad: HERE + "vendor/vad-web-0.0.31/", ort: HERE + "vendor/onnxruntime-web-1.22.0/" },
    { vad: "https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@0.0.31/dist/",
      ort: "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.22.0/dist/" },   // onnxruntime must match vad-web 0.0.31
  ];
  const VAD_FILES = { vad: ["bundle.min.js", "vad.worklet.bundle.min.js", "silero_vad_v5.onnx"],
                      ort: ["ort-wasm-simd-threaded.mjs", "ort-wasm-simd-threaded.wasm"] };
  // Tuned on the user's recordings (VOICE_LOG.md, CP0): every one-word answer was caught.
  const VAD = { model: "v5", positiveSpeechThreshold: 0.5, negativeSpeechThreshold: 0.35, redemptionMs: 850,
                preSpeechPadMs: 300, minSpeechMs: 160 };
  const TAIL_S = 0.6;          // speech starting in the assistant's last 0.6 s is an answer, not an interruption
  const JUDGED_MS = 2500;      // after speech over the assistant, it stays quiet this long for the server's verdict
  const INTERRUPT_REDEMPTION_MS = 500;   // speech over the assistant ends sooner: "stop" acts ~0.35 s faster
  const WHOLE_LEAD_S = 0.02;   // audio that arrives complete (a cached line) starts almost at once

  const scripts = {};
  function loadScript(src) {
    scripts[src] ||= new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src; s.onload = resolve; s.onerror = () => { delete scripts[src]; reject(new Error(`could not load ${src}`)); };
      document.head.append(s);
    });
    return scripts[src];
  }

  /** The sources to try, in order: the local copy only when every file is there and its scripts come as JavaScript
      (the worklet and the onnxruntime module are refused otherwise), then the CDN. */
  async function vadSources() {
    const [local, cdn] = VAD_SOURCES;
    const usable = (url) => fetch(url, { method: "HEAD" }).then((r) => {
      const ok = r.ok && (!/\.m?js$/.test(url) || /javascript/.test(r.headers.get("content-type") || ""));
      if (!ok) console.warn("voice: not usable", url, r.status, r.headers.get("content-type"));
      return ok;
    }, (err) => { console.warn("voice: not usable", url, err); return false; });
    const urls = [...VAD_FILES.vad.map((f) => local.vad + f), ...VAD_FILES.ort.map((f) => local.ort + f)];
    return (await Promise.all(urls.map(usable))).every(Boolean) ? [local, cdn] : [cdn];
  }

  /** A source's vad-web library. Each bundle carries its own onnxruntime, so after a failed start on one source the
      next one starts clean. */
  async function vadLibrary(source) {
    await loadScript(source.vad + "bundle.min.js");
    return (source.lib ||= window.vad);
  }

  /** Float32 samples (-1..1) to WAV file bytes, mono PCM16. */
  function encodeWav(samples, rate = 16000) {
    const buf = new ArrayBuffer(44 + samples.length * 2), v = new DataView(buf);
    const str = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
    str(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); str(8, "WAVE"); str(12, "fmt ");
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true);
    v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    str(36, "data"); v.setUint32(40, samples.length * 2, true);
    for (let i = 0, o = 44; i < samples.length; i++, o += 2) {
      const s = Math.max(-1, Math.min(1, samples[i]));
      v.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return buf;
  }

  /** Gapless player for streamed PCM16 mono frames. */
  class Player {
    constructor(sampleRate = 24000, leadSec = 0.12) {
      this.ctx = new AudioContext({ sampleRate, latencyHint: "interactive" });
      this.gain = this.ctx.createGain(); this.gain.connect(this.ctx.destination);
      this.rate = sampleRate; this.lead = leadSec;
      this.nextTime = 0; this.sources = new Set(); this.carry = null; this.timers = new Set();
      this.onIdle = null; this.idleAt = -1e9;   // when the last audio stopped (performance.now())
    }
    get playing() { return this.sources.size > 0; }
    /** Seconds of audio still to play. */
    remaining() { return this.playing ? Math.max(0, this.nextTime - this.ctx.currentTime) : 0; }
    async resume() { if (this.ctx.state !== "running") await this.ctx.resume(); }

    push(arrayBuffer, lead = this.lead) {
      let bytes = new Uint8Array(arrayBuffer);
      if (this.carry) { const m = new Uint8Array(this.carry.length + bytes.length); m.set(this.carry); m.set(bytes, this.carry.length); bytes = m; this.carry = null; }
      if (bytes.length % 2) { this.carry = bytes.slice(-1); bytes = bytes.subarray(0, bytes.length - 1); }
      if (!bytes.length) return;
      const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.length), n = bytes.length / 2;
      const buf = this.ctx.createBuffer(1, n, this.rate), ch = buf.getChannelData(0);
      for (let i = 0; i < n; i++) ch[i] = dv.getInt16(i * 2, true) / 32768;
      const src = this.ctx.createBufferSource(); src.buffer = buf; src.connect(this.gain);
      const now = this.ctx.currentTime;
      if (this.nextTime < now + 0.005) this.nextTime = now + lead;   // first frame, or fell behind: re-prime
      const startsIn = this.nextTime - now;
      src.start(this.nextTime); this.nextTime += buf.duration;
      this.sources.add(src);
      src.onended = () => {
        this.sources.delete(src);
        if (!this.sources.size) { this.idleAt = performance.now(); if (this.onIdle) this.onIdle(); }
      };
      // When this frame will be heard, the output device's own delay included (large on Bluetooth headsets).
      return performance.now() + (startsIn + (this.ctx.outputLatency || this.ctx.baseLatency || 0)) * 1000;
    }
    /** Calls back when everything pushed so far has played. */
    whenPlayed(callback) {
      const wait = Math.max(0, this.nextTime - this.ctx.currentTime) * 1000 + 30;
      const t = setTimeout(() => { this.timers.delete(t); callback(); }, this.playing ? wait : 0);
      this.timers.add(t);
    }
    /** Silence now, with a 5 ms fade so there is no click. */
    stop() {
      const now = this.ctx.currentTime, g = this.gain.gain, had = this.sources.size > 0;
      g.cancelScheduledValues(now); g.setValueAtTime(g.value, now); g.linearRampToValueAtTime(0, now + 0.005);
      for (const s of this.sources) { s.onended = null; try { s.stop(now + 0.006); } catch {} }
      this.sources.clear(); this.nextTime = 0; this.carry = null;
      for (const t of this.timers) clearTimeout(t); this.timers.clear();
      g.setValueAtTime(1, now + 0.01);
      if (had) this.idleAt = performance.now();
      if (this.onIdle) this.onIdle();
    }
    duck(level = 0.25) { const g = this.gain.gain; g.cancelScheduledValues(this.ctx.currentTime); g.setTargetAtTime(level, this.ctx.currentTime, 0.03); }
    unduck() { const g = this.gain.gain; g.cancelScheduledValues(this.ctx.currentTime); g.setTargetAtTime(1, this.ctx.currentTime, 0.08); }
    close() { this.stop(); this.ctx.close().catch(() => {}); }
  }

  /** One voice connection for one chat session. `on` receives: event(message) for the server's JSON events, stage(name)
      for what the assistant is doing (listening, thinking, searching, speaking), speech(bool) while the customer
      talks, closed(reason) when the connection ends without being stopped. */
  class VoiceLink {
    constructor({ api, sessionId, on, halfDuplex = false }) {
      this.api = api; this.sessionId = sessionId; this.on = on; this.halfDuplex = halfDuplex;
      this.item = null; this.stage = "listening"; this.stopped = false; this.overBot = false; this.unduckTimer = null;
      this.speaking = false;   // the customer is talking now
      this.marks = [];         // the assistant's items in play order: { id, end } (end in audio-clock seconds, once known)
      this.overId = null;      // the item that was audible when the customer started talking over it
      this.hush = false;   // after a local stop: drop the assistant's audio until the server's stop_audio
    }

    /** Must be called from a click: the audio output can only start in a user gesture. */
    async start() {
      this.player = new Player();
      await this.player.resume();
      this.player.onIdle = () => this.showStage();
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      this.vad = await this.detector();
      if (this.stopped) return;   // turned off while starting: no voice session (it would take the greeting)
      await this.connect();
      if (this.stopped) return;
      await this.vad.start();
      // The detector runs: show where things stand (with no greeting to play, the bar stayed on "Starting").
      this.showStage();
    }

    /** The speech detector on the microphone stream, from the first source whose library, model and onnxruntime load.
        The worklet loads later, in vad.start(), so only vadSources() checks it. */
    async detector() {
      let error;
      for (const source of await vadSources()) {
        if (this.stopped) throw new Error("voice stopped");
        try {
          const lib = await vadLibrary(source);
          const vad = await lib.MicVAD.new({
            ...VAD, startOnLoad: false, baseAssetPath: source.vad, onnxWASMBasePath: source.ort,
            getStream: async () => this.stream, pauseStream: async () => {}, resumeStream: async () => this.stream,
            onSpeechStart: () => this.speechStart(),
            onVADMisfire: () => this.misfire(),
            onSpeechEnd: (audio) => this.speechEnd(audio),
          });
          if (source !== VAD_SOURCES[0]) console.warn("voice: speech detector from the CDN, web/vendor was not usable");
          return vad;
        } catch (err) {
          error = err;
          console.warn(`voice: speech detector from ${source.vad} did not start`, err);
        }
      }
      throw error;
    }

    connect() {
      const url = new URL(`${this.api || location.origin}/chat/sessions/${encodeURIComponent(this.sessionId)}/voice`, location.href);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      const ws = this.ws = new WebSocket(url);
      ws.binaryType = "arraybuffer";
      ws.onmessage = (e) => (typeof e.data === "string" ? this.event(JSON.parse(e.data)) : this.frame(e.data));
      return new Promise((resolve, reject) => {
        ws.onopen = () => resolve();
        ws.onerror = () => reject(new Error("voice connection failed"));
        ws.onclose = (e) => {
          reject(new Error("voice connection closed"));
          if (!this.stopped) { this.stop(); this.on.closed?.(e.code); }
        };
      });
    }

    send(data) { if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(typeof data === "string" ? data : data instanceof ArrayBuffer ? data : JSON.stringify(data)); }

    // ---- the customer's speech ----

    speechStart() {
      this.speaking = true;
      this.pendingFrom = this.waitFrom;   // a misfire gives the measurement back
      this.waitFrom = null;
      this.overBot = this.player.remaining() > TAIL_S;
      // How long after the assistant's last sound this speech began: the server checks a quick start for echo.
      this.sinceBot = this.player.playing ? 0 : Math.round(performance.now() - this.player.idleAt);
      this.overId = this.overBot ? this.audibleId() : null;
      this.send({ type: "speech", on: true });
      this.on.speech?.(true);
      if (this.overBot && !this.halfDuplex) {   // the customer is heard at once: the assistant gets quieter
        clearTimeout(this.unduckTimer);
        this.player.duck();
        this.vad.setOptions({ redemptionMs: INTERRUPT_REDEMPTION_MS });
      }
    }

    /** Speech ended. The assistant's volume comes back now, or after `judgedMs` when the server is still deciding
        whether the speech over it was an interruption. */
    speechDone(judgedMs = 0) {
      this.speaking = false;
      this.vad.setOptions({ redemptionMs: VAD.redemptionMs });
      this.on.speech?.(false);
      clearTimeout(this.unduckTimer);
      if (judgedMs) this.unduckTimer = setTimeout(() => this.player.unduck(), judgedMs);
      else this.player.unduck();
    }

    unduck() { clearTimeout(this.unduckTimer); if (!this.speaking) this.player.unduck(); }

    /** The detector heard a sound too short to be speech. */
    misfire() {
      this.overBot = false;
      if (this.pendingFrom != null) this.waitFrom = this.pendingFrom;
      this.speechDone();
      this.send({ type: "speech", on: false });
    }

    speechEnd(audio) {
      const overBot = this.overBot;
      this.overBot = false;
      if (overBot && this.halfDuplex && this.player.playing) {   // half-duplex: all of it was over the assistant
        this.speechDone();
        this.send({ type: "speech", on: false });
        return;
      }
      this.speechDone(overBot && !this.halfDuplex ? JUDGED_MS : 0);
      // The customer stopped speaking about one redemption window ago: the silence they hear is timed from there.
      // Speech over the assistant is timed too; if it is only a backchannel, the server's "ignored" clears it.
      this.waitFrom = performance.now() - (overBot && !this.halfDuplex ? INTERRUPT_REDEMPTION_MS : VAD.redemptionMs);
      this.pendingFrom = null; this.longest = 0;
      this.send({ type: "utterance", over_bot: overBot, ms: Math.round(audio.length / 16), over_id: this.overId,
                  since_bot_ms: this.sinceBot < 5000 ? this.sinceBot : null });
      this.send(encodeWav(audio));
    }

    // ---- the assistant's speech ----

    event(m) {
      if (m.type === "tts_start") {
        // after: announced once the customer stopped, so it can be the answer to what they said (an item that was
        // already playing when they spoke over it cannot).
        this.item = { id: m.id, kind: m.kind, whole: !!m.whole, dropped: this.hush,
                      after: this.waitFrom != null || this.pendingFrom != null };
        if (!this.hush) this.marks.push({ id: m.id, end: null });
        if (!this.hush) this.showStage("speaking");
        if (m.kind !== "reply" || !this.player.playing) this.unduck();   // something new to say: full volume
      } else if (m.type === "tts_end") {
        const item = this.item;
        if (item && item.id === m.id) {
          this.item = null;
          const mark = this.marks.find((x) => x.id === m.id);
          if (mark) mark.end = this.player.nextTime;
          if (!m.cancelled && !item.dropped) this.player.whenPlayed(() => this.send({ type: "played", id: m.id }));
          else this.marks = this.marks.filter((x) => x.id !== m.id);
          if (!this.player.playing) this.showStage();   // an item with no audio must not leave "speaking" up
        }
      } else if (m.type === "stop_audio") {
        // A stop is followed by silence on purpose: nothing after it is timed as the answer to it.
        if (this.item) this.item.after = false;
        this.waitFrom = null; this.pendingFrom = null;
        this.silence();
        this.hush = false;
      } else if (m.type === "status") {
        this.stage = m.stage;
        this.showStage();
      } else if (m.type === "ignored") {
        this.waitFrom = null;   // nothing will answer it: no silence to time
        this.unduck();   // a backchannel or the assistant's own echo: carry on at full volume
      }
      this.on.event?.(m);
    }

    frame(buffer) {
      const item = this.item;
      if (!item || item.dropped) return;
      const idle = !this.player.playing, idleAt = this.player.idleAt;
      const heardAt = this.player.push(buffer, item.whole && idle ? WHOLE_LEAD_S : undefined);
      if (heardAt == null) return;
      const from = this.waitFrom != null ? this.waitFrom : this.pendingFrom;   // pending: a detection under way
      const lag = (this.player.ctx.outputLatency || this.player.ctx.baseLatency || 0) * 1000;
      if (from != null && item.after && !item.started) {   // the first sound after the customer stopped
        this.send({ type: "latency", gap_ms: Math.max(0, Math.round(heardAt - from)), id: item.id, kind: item.kind });
        this.longest = 0; this.timing = true; this.waitFrom = null; this.pendingFrom = null;
      } else if (this.timing && idle && !item.started) {   // a later silence while the answer is awaited
        this.longest = Math.max(this.longest, heardAt - lag - idleAt);   // both ends on the output side
      }
      if (this.timing && item.kind === "reply" && !item.started) {   // the answer: its longest silence before it
        this.send({ type: "latency", longest_ms: Math.round(this.longest), id: item.id });
        this.timing = false;
      }
      item.started = true;
    }

    showStage(forced) {
      this.on.stage?.(forced || (this.player && this.player.playing ? "speaking" : this.muted ? "muted" : this.stage));
    }

    /** Mute: the detector stops listening (the assistant can still be heard and stopped); unmute starts it again. */
    async setMuted(on) {
      if (!this.vad || this.muted === on) return;
      this.muted = on;
      if (on) {
        this.vad.pause();
        if (this.speaking) { this.speechDone(); this.send({ type: "speech", on: false }); }
      } else {
        await this.vad.start();
      }
      this.send({ type: "mute", on });
      this.showStage();
    }

    silence() {
      if (this.item) this.item.dropped = true;
      this.player.stop();
      this.marks = [];
    }

    /** The id of the assistant's item being heard now: the server judges speech over it by what it was saying. */
    audibleId() {
      const now = this.player.ctx.currentTime;
      this.marks = this.marks.filter((x) => x.end === null || x.end > now);
      return this.marks.length ? this.marks[0].id : null;
    }

    /** The Stop button: silence at once, and the server drops what it was about to say. Audio already on its way is
        dropped until the server answers with stop_audio. */
    interrupt(kind = "stop") {
      this.waitFrom = null; this.pendingFrom = null;
      this.silence();
      this.hush = true;
      this.send({ type: kind });
    }

    // The Submit button and typed text get no acknowledgement (D38): their silence is timed from the click.
    submit() { this.waitFrom = performance.now(); this.send({ type: "submit" }); }
    text(text) { this.waitFrom = performance.now(); this.send({ type: "text", text }); }

    stop() {
      if (this.stopped) return;
      this.stopped = true;
      clearTimeout(this.unduckTimer);
      try { this.vad && this.vad.destroy(); } catch {}
      if (this.stream) for (const t of this.stream.getTracks()) t.stop();
      if (this.player) this.player.close();
      if (this.ws && this.ws.readyState <= WebSocket.OPEN) this.ws.close();
    }
  }

  window.T2Voice = { VoiceLink, encodeWav, Player };
})();
