const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const code = fs.readFileSync('app/static/prepare-pdf-pair.js', 'utf8');
function setup({missing = false, throws = false, lang = 'ko'} = {}) {
  const events = {}, windowEvents = {};
  const button = {disabled: false}, status = {textContent: ''};
  let submissions = 0;
  const form = {
    querySelector: () => button,
    addEventListener: (name, handler) => events[name] = handler,
    requestSubmit() {
      if (throws) throw Error('unavailable');
      const event = {preventDefault() { this.prevented = true; }};
      events.submit(event);
      if (!event.prevented) submissions++;
    }
  };
  vm.runInNewContext(code, {
    document: {documentElement: {lang}, getElementById: id => id === 'prepare-pdf-pair' ? (missing ? null : form) : status},
    window: {addEventListener: (name, handler) => windowEvents[name] = handler}
  });
  return {form, button, status, windowEvents, submissions: () => submissions};
}
test('one automatic preparation, duplicate submit blocked, no send action', () => {
  const h = setup();
  assert.equal(h.submissions(), 1);
  assert.equal(h.button.disabled, true);
  assert.match(h.status.textContent, /준비하고/);
  h.form.requestSubmit();
  assert.equal(h.submissions(), 1);
});
test('back navigation does not resubmit and restores manual action', () => {
  const h = setup();
  h.windowEvents.pageshow({persisted: true});
  assert.equal(h.submissions(), 1);
  assert.equal(h.button.disabled, false);
  assert.equal(h.status.textContent, '');
});
test('unsupported automatic submit leaves fallback available', () => {
  const h = setup({throws: true});
  assert.equal(h.button.disabled, false);
  assert.match(h.status.textContent, /아래 버튼/);
  assert.equal(h.submissions(), 0);
});
test('mismatch page has no automatic action', () => {
  assert.equal(setup({missing: true}).submissions(), 0);
});
