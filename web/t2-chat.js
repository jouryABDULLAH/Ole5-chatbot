/* <t2-chat api="https://chat-service">: the support chat widget. Plain JavaScript in one file, no build step. It knows only the chat service's HTTP API; the api attribute defaults to the page's own origin. When a cited source is clicked it fires a cancelable t2-document event {product, title} so the host page can show the document in place. */
(() => {
  const TEXT = {
    en: {
      open: "Chat", close: "Close chat", title: "Support",
      signinTitle: "Chat with support", signinBody: "Enter the email you use for support.",
      email: "Email", cont: "Continue", checking: "Checking",
      placeholder: "Write a message", send: "Send", writing: "Writing a reply",
      source: "Source", sources: "Sources",
      summary: "Ticket summary", submit: "Submit ticket", submitting: "Submitting", submitted: "Ticket {number} submitted",
      fields: { queue: "Queue", type: "Type", subtype: "Subtype", priority: "Priority", title: "Title", summary: "Summary",
                attachments: "Attachments" },
      notSet: "Not set", failed: "The message was not sent. Try again.",
      unreachable: "The chat service cannot be reached. Check that it is running, then try again.",
      restart: "Start a new chat",
      attach: "Attach a file", remove: "Remove", uploading: "Uploading",
      tooBig: "The file is too large.", badType: "This file type cannot be attached.",
      tooMany: "No more files can be attached to this ticket.",
    },
    ar: {
      open: "محادثة", close: "إغلاق المحادثة", title: "الدعم",
      signinTitle: "تحدث مع الدعم", signinBody: "أدخل البريد الإلكتروني الذي تستخدمه للدعم.",
      email: "البريد الإلكتروني", cont: "متابعة", checking: "جارٍ التحقق",
      placeholder: "اكتب رسالتك", send: "إرسال", writing: "جارٍ كتابة الرد",
      source: "المصدر", sources: "المصادر",
      summary: "ملخص التذكرة", submit: "إرسال التذكرة", submitting: "جارٍ الإرسال", submitted: "تم إرسال التذكرة {number}",
      fields: { queue: "القائمة", type: "النوع", subtype: "النوع الفرعي", priority: "الأولوية", title: "العنوان", summary: "الملخص",
                attachments: "المرفقات" },
      notSet: "غير محدد", failed: "لم تُرسل الرسالة. حاول مرة أخرى.",
      unreachable: "تعذّر الوصول إلى خدمة المحادثة. تأكد من تشغيلها ثم حاول مرة أخرى.",
      restart: "بدء محادثة جديدة",
      attach: "إرفاق ملف", remove: "إزالة", uploading: "جارٍ الرفع",
      tooBig: "حجم الملف كبير جداً.", badType: "لا يمكن إرفاق هذا النوع من الملفات.",
      tooMany: "لا يمكن إرفاق ملفات أخرى بهذه التذكرة.",
    },
  };
  const KEY = "t2-chat-session";

  const CSS = `
    :host { --paper:#FBFCFB; --ink:#1E2A2C; --muted:#5F6D6B; --line:#DCE3E0; --soft:#EAF1EE;
            --accent:#0E5A4C; --accent-dark:#0A463B; --error:#9B2C22;
            font-family:"IBM Plex Sans Arabic", system-ui, sans-serif; color:var(--ink); }
    * { box-sizing:border-box; }
    button, input, textarea { font:inherit; color:inherit; }
    :focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
    /* The launcher and the panel sit at the bottom right in both languages, so a host page can keep that side clear. */
    .launcher { position:fixed; bottom:24px; right:24px; display:flex; align-items:center; gap:10px;
                padding:12px 20px 12px 16px; border:0; border-radius:999px; background:var(--accent); color:#fff;
                font-size:15px; font-weight:500; cursor:pointer; box-shadow:0 6px 20px rgba(14,90,76,.28); }
    .launcher:hover { background:var(--accent-dark); }
    .launcher svg { width:20px; height:20px; }
    .panel { position:fixed; bottom:24px; right:24px; width:min(392px, calc(100vw - 32px));
             height:min(620px, calc(100vh - 48px)); display:flex; flex-direction:column; background:var(--paper);
             border:1px solid var(--line); border-radius:16px; overflow:hidden;
             box-shadow:0 18px 48px rgba(30,42,44,.16); transform-origin:bottom right; animation:open .16s ease-out; }
    @keyframes open { from { opacity:0; transform:translateY(8px) scale(.98); } }
    .head { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:14px 16px 14px 20px;
            border-bottom:1px solid var(--line); }
    .head strong { font-weight:600; font-size:15px; }    .icon { display:grid; place-items:center; width:32px; height:32px; border:0; border-radius:8px; background:none;
            color:var(--muted); cursor:pointer; }
    .icon:hover { background:var(--soft); color:var(--ink); }
    .signin { flex:1; display:flex; flex-direction:column; justify-content:center; gap:16px; padding:32px 28px; }
    .signin h2 { margin:0; font-size:22px; line-height:1.3; font-weight:600; }
    .signin p { margin:0; color:var(--muted); font-size:15px; line-height:1.6; }
    label { display:flex; flex-direction:column; gap:6px; font-size:14px; font-weight:500; }
    input { padding:11px 14px; border:1px solid var(--line); border-radius:10px; background:#fff; font-size:15px; }
    input:focus { border-color:var(--accent); outline:none; box-shadow:0 0 0 3px var(--soft); }
    .primary { padding:11px 18px; border:0; border-radius:10px; background:var(--accent); color:#fff; font-weight:500;
               cursor:pointer; }
    .primary:hover:not(:disabled) { background:var(--accent-dark); }
    .primary:disabled { opacity:.55; cursor:default; }
    .error { margin:0; color:var(--error); font-size:14px; line-height:1.5; }
    .log { flex:1; overflow-y:auto; display:flex; flex-direction:column; gap:18px; padding:20px; }
    .log > * { flex-shrink:0; }
    .bot { max-width:92%; font-size:15px; line-height:1.65; white-space:pre-wrap; }
    .me { align-self:flex-end; max-width:82%; padding:9px 14px; border-radius:14px; border-end-end-radius:4px;
          background:var(--soft); font-size:15px; line-height:1.6; white-space:pre-wrap; }
    .src a { color:var(--accent); text-decoration:underline; text-underline-offset:3px; }
    .src { margin-top:8px; padding-inline-start:10px; border-inline-start:2px solid var(--accent);
           color:var(--muted); font-size:13px; line-height:1.5; white-space:normal; }
    .writing { color:var(--muted); font-size:14px; display:flex; align-items:center; gap:8px; }
    .dots { display:inline-flex; gap:3px; }
    .dots i { width:5px; height:5px; border-radius:50%; background:var(--muted); animation:pulse 1.2s infinite; }
    .dots i:nth-child(2) { animation-delay:.2s; } .dots i:nth-child(3) { animation-delay:.4s; }
    @keyframes pulse { 0%,80%,100% { opacity:.25; } 40% { opacity:1; } }
    .card { border:1px solid var(--line); border-radius:12px; background:#fff; overflow:hidden; }
    .card h3 { margin:0; padding:12px 16px; font-size:14px; font-weight:600; border-bottom:1px solid var(--line); }
    .card.done { background:var(--paper); color:var(--muted); }
    .card.done h3 { display:flex; align-items:center; gap:8px; color:var(--accent); }
    .card.done h3 svg { width:16px; height:16px; flex:none; }
    .card dl { margin:0; padding:6px 16px; display:grid; grid-template-columns:auto 1fr; gap:6px 16px; font-size:14px; }
    .card dt { color:var(--muted); }
    .card dd { margin:0; line-height:1.5; white-space:pre-line; overflow-wrap:anywhere; }
    .card dd.missing { color:var(--muted); font-style:italic; }
    .card .actions { padding:12px 16px 16px; display:flex; flex-direction:column; gap:8px; }
    .card .note { margin:0; color:var(--muted); font-size:13px; }
    .compose { display:flex; align-items:flex-end; gap:8px; padding:12px; border-top:1px solid var(--line); }
    textarea { flex:1; resize:none; max-height:132px; padding:10px 14px; border:1px solid var(--line); border-radius:12px;
               background:#fff; font-size:15px; line-height:1.5; }
    textarea:focus { border-color:var(--accent); outline:none; box-shadow:0 0 0 3px var(--soft); }
    .send { width:42px; height:42px; display:grid; place-items:center; border:0; border-radius:12px; background:var(--accent);
            color:#fff; cursor:pointer; flex:none; }
    .send:disabled { opacity:.45; cursor:default; }
    :host([dir=rtl]) .send svg { transform:scaleX(-1); }
    .clip { width:42px; height:42px; display:grid; place-items:center; border:1px solid var(--line); border-radius:12px;
            background:#fff; color:var(--muted); cursor:pointer; flex:none; }
    .clip:hover:not(:disabled) { color:var(--ink); background:var(--soft); }
    .clip:disabled { opacity:.45; cursor:default; }
    .me .sent { display:flex; align-items:center; gap:6px; margin-top:6px; padding-top:6px; border-top:1px solid rgba(14,90,76,.18);
                color:var(--accent); font-size:13px; }
    .me .sent svg { width:14px; height:14px; flex:none; }
    .files { display:flex; flex-wrap:wrap; gap:6px; padding:10px 12px 0; border-top:1px solid var(--line); }
    .files + .compose { border-top:0; }
    .chip { display:inline-flex; align-items:center; gap:4px; max-width:100%; padding:4px 4px 4px 10px;
            border:1px solid var(--line); border-radius:999px; background:#fff; font-size:13px; }
    :host([dir=rtl]) .chip { padding:4px 10px 4px 4px; }
    .chip span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:220px; }
    .chip button { width:22px; height:22px; display:grid; place-items:center; border:0; border-radius:50%;
                   background:none; color:var(--muted); cursor:pointer; }
    .chip button:hover:not(:disabled) { background:var(--soft); color:var(--ink); }
    .restart { align-self:center; border:0; background:none; color:var(--accent); font-size:14px; cursor:pointer;
               text-decoration:underline; text-underline-offset:3px; }
    @media (prefers-reduced-motion: reduce) { .panel { animation:none; } .dots i { animation:none; opacity:.6; } }
    /* On a phone the panel is a bottom sheet, so the top of the page (a document, say) stays readable above it. */
    @media (max-width: 480px) { .panel { inset:auto 0 0 0; width:100%; height:60vh; height:60dvh;
                                         border-radius:16px 16px 0 0; border-bottom:0; }
                                .launcher { bottom:16px; right:16px; } }
  `;

  const ICON_CHAT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 5h16v11H9l-5 4z"/></svg>';
  const ICON_CLOSE = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  const ICON_SEND = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h13M13 6l6 6-6 6"/></svg>';

  const ICON_CLIP = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 11.5l-7.8 7.8a5 5 0 01-7.1-7.1l8.5-8.5a3.3 3.3 0 014.7 4.7l-8.5 8.5a1.7 1.7 0 01-2.4-2.4L15 6.9"/></svg>';
  const ICON_CHECK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12l5 5L20 7"/></svg>';
  const ICON_X ='<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  const ACCEPT = ".png,.jpg,.jpeg,.pdf,.docx,.xlsx,.txt,.log";
  const MAX_MB = 10;

  const el = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "html") node.innerHTML = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else if (v !== false && v != null) node.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children) if (c != null) node.append(c);
    return node;
  };

  const store = {
    get() { try { return sessionStorage.getItem(KEY); } catch { return null; } },
    set(v) { try { v ? sessionStorage.setItem(KEY, v) : sessionStorage.removeItem(KEY); } catch {} },
  };

  async function call(base, method, path, body) {
    let res;
    try {
      res = await fetch(base + path, { method, headers: { "Content-Type": "application/json" },
                                body: body ? JSON.stringify(body) : undefined });
    } catch { throw { kind: "unreachable" }; }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw { kind: "http", status: res.status, detail: data.detail };
    return data;
  }

  async function upload(base, path, file) {
    const form = new FormData();
    form.append("file", file);
    let res;
    try { res = await fetch(base + path, { method: "POST", body: form }); } catch { throw { kind: "unreachable" }; }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw { kind: "http", status: res.status, detail: data.detail };
    return data;
  }

  class T2Chat extends HTMLElement {
    constructor() {
      super();
      this.root = this.attachShadow({ mode: "open" });
      this.view = null;
      this.open = false;
      this.busy = false;
      this.error = "";
      this.pending = [];
    }

    get api() { return (this.getAttribute("api") || "").replace(/\/$/, ""); }
    get lang() { return (this.view && this.view.language) || this.getAttribute("lang") || "en"; }
    get t() { return TEXT[this.lang] || TEXT.en; }

    async connectedCallback() {
      const saved = store.get();
      if (saved) {
        try { this.view = await call(this.api, "GET", `/chat/sessions/${saved}`); } catch { store.set(null); }
      }
      this.render();
    }

    static get observedAttributes() { return ["lang"]; }
    attributeChangedCallback() { if (this.isConnected) this.render(); }

    render() {
      /* Keep what was typed across a re-render, but not from the email field into the message box or back. */
      const typed = this.root.querySelector("textarea, input:not([type=file])");
      if (typed && typed.tagName === (this.view ? "TEXTAREA" : "INPUT")) this.draft = typed.value;
      this.setAttribute("dir", this.lang === "ar" ? "rtl" : "ltr");
      this.root.replaceChildren(el("style", {}, CSS), this.open ? this.panel() : this.launcher());
      const field = this.root.querySelector("textarea, input:not([type=file])");
      if (field && this.draft) field.value = this.draft;
    }

    launcher() {
      return el("button", { class: "launcher", type: "button", "aria-label": this.t.open,
                            onclick: () => { this.open = true; this.render(); this.focusInput(); } },
                el("span", { html: ICON_CHAT }), this.t.open);
    }

    panel() {
      const head = el("div", { class: "head" },
        el("strong", {}, this.view ? this.view.product_name : this.t.title),
        el("button", { class: "icon", type: "button", "aria-label": this.t.close, html: ICON_CLOSE,
                       onclick: () => { this.open = false; this.render(); } }));
      return el("section", { class: "panel", role: "dialog", "aria-label": this.t.title },
                head, ...(this.view ? this.chat() : [this.signin()]));
    }

    signin() {
      const input = el("input", { type: "email", name: "email", autocomplete: "email", required: true,
                                  dir: "ltr", disabled: this.busy });
      const form = el("form", { class: "signin", onsubmit: (e) => { e.preventDefault(); this.start(input.value); } },
        el("h2", {}, this.t.signinTitle),
        el("p", {}, this.t.signinBody),
        el("label", {}, this.t.email, input),
        this.error ? el("p", { class: "error", role: "alert" }, this.error) : null,
        el("button", { class: "primary", type: "submit", disabled: this.busy },
           this.busy ? this.t.checking : this.t.cont));
      return form;
    }

    chat() {
      const log = el("div", { class: "log", "aria-live": "polite" });
      const lines = this.view.messages;
      lines.forEach((m, i) => {
        for (const t of this.view.created_tickets) if (t.message_index === i) log.append(this.card(t, t.attachments, t));
        if (m.author === "customer") { log.append(this.customerBubble(m.text)); return; }
        const bubble = el("div", { class: "bot", dir: "auto" }, m.text);
        if (m.sources.length) {
          const label = m.sources.length > 1 ? this.t.sources : this.t.source;
          const links = m.sources.flatMap((s, j) => [j ? ", " : null, this.documentLink(s)]);
          bubble.append(el("div", { class: "src" }, `${label}: `, ...links));
        }
        log.append(bubble);
      });
      if (this.view.draft_ready && this.view.ticket_draft) {
        log.append(this.card(this.view.ticket_draft, (this.view.attachments || []).map((f) => f.file_name)));
      }
      if (this.busy) log.append(el("div", { class: "writing" }, el("span", { class: "dots", "aria-hidden": "true" },
                                    el("i"), el("i"), el("i")), this.t.writing));
      if (this.error) log.append(el("p", { class: "error", role: "alert" }, this.error));
      log.append(el("button", { class: "restart", type: "button", onclick: () => this.restart() }, this.t.restart));

      const box = el("textarea", { rows: 1, placeholder: this.t.placeholder, "aria-label": this.t.placeholder, dir: "auto" });
      const sendBtn = el("button", { class: "send", type: "submit", "aria-label": this.t.send, html: ICON_SEND,
                                     disabled: this.busy });
      box.addEventListener("input", () => { box.style.height = "auto"; box.style.height = box.scrollHeight + "px"; });
      box.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
      });
      const picker = el("input", { type: "file", accept: ACCEPT, hidden: true,
                                   onchange: () => { if (picker.files[0]) this.stage(picker.files[0]); } });
      const clip = el("button", { class: "clip", type: "button", "aria-label": this.t.attach, title: this.t.attach,
                                  html: ICON_CLIP, disabled: this.busy, onclick: () => picker.click() });
      const form = el("form", { class: "compose", onsubmit: (e) => { e.preventDefault(); this.send(box.value); } },
                      picker, clip, box, sendBtn);
      const files = this.pending.length ? el("div", { class: "files" },
        ...this.pending.map((f, i) => el("span", { class: "chip", dir: "auto" }, el("span", { title: f.name }, f.name),
          el("button", { type: "button", "aria-label": `${this.t.remove} ${f.name}`, html: ICON_X,
                         disabled: this.busy, onclick: () => { this.pending.splice(i, 1); this.render(); } }))))
        : null;
      queueMicrotask(() => { log.scrollTop = log.scrollHeight; });
      return [log, files, form];
    }

    /* A link to a cited knowledge base document. The host page may listen for the t2-document event and show the
       document itself; if it doesn't, or the click has a modifier key, the link opens the API's page in a new tab. */
    documentLink(title) {
      const href = `${this.api}/chat/kb/${encodeURIComponent(this.view.product)}/${encodeURIComponent(title)}`;
      return el("a", { href, target: "_blank", rel: "noopener", onclick: (e) => {
        if (e.ctrlKey || e.metaKey || e.shiftKey) return;
        const event = new CustomEvent("t2-document", { bubbles: true, composed: true, cancelable: true,
                                                        detail: { product: this.view.product, title } });
        if (!this.dispatchEvent(event)) e.preventDefault();
      } }, title);
    }

    /* The ticket card: the draft with its Submit button, or, once submitted, the same fields greyed under the ticket
       number, kept in the chat where the summary was. */
    card(fields, files, submitted = null) {
      const rows = el("dl");
      for (const key of ["title", "summary", "queue", "type", "subtype", "priority"]) {
        const value = fields[key];
        rows.append(el("dt", {}, this.t.fields[key]),
                    el("dd", { class: value ? null : "missing", dir: "auto" }, value || this.t.notSet));
      }
      if (files.length) rows.append(el("dt", {}, this.t.fields.attachments), el("dd", { dir: "auto" }, files.join("\n")));
      if (submitted) {
        return el("div", { class: "card done" },
                  el("h3", {}, el("span", { html: ICON_CHECK }), this.t.submitted.replace("{number}", submitted.ticket_number)),
                  rows);
      }
      return el("div", { class: "card" }, el("h3", {}, this.t.summary), rows,
        el("div", { class: "actions" },
           el("button", { class: "primary", type: "button", disabled: this.busy, onclick: () => this.submitTicket() },
              this.busy ? this.t.submitting : this.t.submit)));
    }

    focusInput() {
      queueMicrotask(() => {
        const target = this.root.querySelector("textarea, input:not([type=file])");
        if (target) target.focus();
      });
    }

    explain(err) {
      if (err.kind === "unreachable") return this.t.unreachable;
      if (err.status === 403 && err.detail) return err.detail;
      return this.t.failed;
    }

    async start(email) {
      if (!email.trim() || this.busy) return;
      this.busy = true; this.error = ""; this.render();
      try {
        this.view = await call(this.api, "POST", "/chat/sessions", { email: email.trim() });
        store.set(this.view.session_id);
        this.draft = "";
      } catch (err) { this.error = this.explain(err); }
      this.busy = false; this.render(); this.focusInput();
    }

    customerBubble(text) {
      const m = text.match(/^([\s\S]*?)\n?\[attached: (.+)\]$/);
      const bubble = el("div", { class: "me", dir: "auto" }, m ? m[1] : text);
      if (m) bubble.append(el("div", { class: "sent", html: ICON_CLIP }, el("span", {}, m[2])));
      return bubble;
    }

    stage(file) {
      this.error = "";
      const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
      if (!ACCEPT.split(",").includes(ext)) this.error = this.t.badType;
      else if (file.size > MAX_MB * 1024 * 1024) this.error = this.t.tooBig;
      else this.pending.push(file);
      this.render(); this.focusInput();
    }

    async send(text) {
      text = text.trim();
      if ((!text && !this.pending.length) || this.busy) return;
      const before = this.view;
      const shown = this.pending.length ? `${text}\n[attached: ${this.pending.map((f) => f.name).join(", ")}]` : text;
      this.view = { ...before, messages: [...before.messages, { author: "customer", text: shown, sources: [] }] };
      this.root.querySelector("textarea").value = "";
      this.busy = true; this.error = ""; this.render();
      try {
        const ids = [];
        for (const file of this.pending) {
          const added = await upload(this.api, `/chat/sessions/${before.session_id}/attachments`, file);
          ids.push(added.id);
        }
        this.view = await call(this.api, "POST", `/chat/sessions/${before.session_id}/messages`,
                               { text, attachment_ids: ids });
        this.pending = [];
      } catch (err) {
        if (err.status === 404) { this.restart(); return; }
        this.view = before;
        this.draft = text;
        this.error = err.status === 413 ? this.t.tooBig : err.status === 415 ? this.t.badType
                   : err.status === 409 ? this.t.tooMany : this.explain(err);
      }
      this.busy = false; this.render(); this.focusInput();
    }

    async submitTicket() {
      if (this.busy) return;
      this.busy = true; this.error = ""; this.render();
      try {
        this.view = await call(this.api, "POST", `/chat/sessions/${this.view.session_id}/submit`);
      } catch (err) {
        if (err.status === 404) { this.restart(); return; }
        this.error = err.detail || this.explain(err);
      }
      this.busy = false; this.render();
    }

    restart() {
      store.set(null);
      this.view = null; this.busy = false; this.error = ""; this.draft = "";
      this.render(); this.focusInput();
    }
  }

  customElements.define("t2-chat", T2Chat);
})();
