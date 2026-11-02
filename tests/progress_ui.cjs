// Exercise the actual widget send path while the response is pending.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let Widget;
global.HTMLElement = class { attachShadow() { return {}; } };
global.customElements = { define(name, cls) { Widget = cls; } };
vm.runInThisContext(fs.readFileSync('web/t2-chat.js', 'utf8'));
(async () => {
  const widget = new Widget();
  widget.getAttribute = () => null;
  widget.view = { session_id: 'test', language: 'en', messages: [] };
  widget.root.querySelector = () => ({ value: '' });
  widget.render = () => {};
  widget.focusInput = () => {};
  let finish;
  const requested = [];
  global.fetch = async (url) => {
    requested.push(url);
    if (url.endsWith('/progress')) return { ok: true, json: async () => ({ key: 'searching', language: 'ar' }) };
    return new Promise(resolve => { finish = () => resolve({ ok: true, json: async () => widget.view }); });
  };
  const sent = widget.send('Hello');
  await new Promise(resolve => setImmediate(resolve));
  assert(requested.includes('/chat/sessions/test/progress'));
  assert.equal(widget.progress.key, 'searching');
  // Progress follows the outgoing message immediately, even if the API still reports
  // the previous session language while routing is starting.
  assert.equal(widget.progress.language, 'en');
  assert.equal(widget.busy, true);
  finish();
  await sent;
  assert.equal(widget.progressTimer, null);
  assert.equal(widget.busy, false);
  console.log('Progress UI send/poll/cleanup simulation passed');
})().catch(error => { console.error(error); process.exit(1); });
