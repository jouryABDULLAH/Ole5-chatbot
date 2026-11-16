// Exercise the actual card renderer without a browser, network, or model calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; }
  append(...children) { this.children.push(...children); }
  setAttribute(key, value) { this.attrs[key] = value; }
  addEventListener() {}
  get childElementCount() { return this.children.filter(x => x instanceof Element).length; }
  get textContent() { return this.children.map(x => x instanceof Element ? x.textContent : String(x)).join('\n'); }
}
let Chat;
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../web/t2-chat.js'), 'utf8'), {
  HTMLElement: class {}, document: { createElement: tag => new Element(tag) },
  customElements: { define: (_, component) => { Chat = component; } },
});
const widget = Object.create(Chat.prototype);
widget.view = { language: 'ar', detail_labels: { page: 'اسم الصفحة', 'error text or screenshot': 'نص الخطأ أو لقطة شاشة' } };
const fields = {
  required: { page: 'Inbox', 'error text or screenshot': 'ERROR_load' },
  required_customer: { page: 'صفحة الوارد' },
  collected: { page: 'Wall page', 'error text or screenshot': 'ERROR_old', browser: 'Chrome' },
  collected_customer: { page: 'صفحة الجدار' },
};
for (const submitted of [null, { ticket_number: 'T-1' }]) {
  const text = widget.card(fields, [], submitted).textContent;
  assert.equal(text.match(/صفحة الوارد/g).length, 2);
  assert.equal(text.match(/ERROR_load/g).length, 2);
  assert.ok(text.includes('Chrome'));
  assert.ok(text.includes('اسم الصفحة'));
  assert.ok(!/Wall page|صفحة الجدار|ERROR_old|Inbox/.test(text));
}
const cleared = widget.card({ ...fields, required: { page: null, 'error text or screenshot': 'declined' } }, []).textContent;
assert.ok(!/صفحة الوارد|صفحة الجدار|ERROR_load|ERROR_old/.test(cleared));
console.log('Draft/submitted card consistency and clear/skip rendering passed.');
