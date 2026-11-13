const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/guest-sample.js', 'utf8');

async function exercise(search, referrer, internal = false) {
  const calls = [], nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {
      dataset: id === 'practice-form' ? {traffic: internal ? 'internal' : ''} : {},
      valueAsNumber: {quantity: 100, unit_price: 8, cartons: 5, net_weight: 20, gross_weight: 25}[id],
      handlers: {}, attrs: {}, hidden: true, disabled: false,
      addEventListener(event, handler) { this.handlers[event] = handler; },
      removeAttribute(key) { delete this.attrs[key]; },
      hasAttribute(key) { return key in this.attrs; },
      set href(value) { this.attrs.href = value; },
      get href() { return this.attrs.href; },
      reportValidity() { return true; }, scrollIntoView() {}
    });
    return nodes.get(id);
  };
  const languageLink = {href: 'https://trade.example/try-sample?lang=ko'};
  class URLMock extends URL {}
  URLMock.createObjectURL = () => 'blob:test';
  URLMock.revokeObjectURL = () => {};
  vm.runInNewContext(source, {
    document: {getElementById: node, querySelectorAll: () => [languageLink], referrer},
    location: {search, href: 'https://trade.example/try-sample' + search},
    URLSearchParams, URL: URLMock, Blob, Uint8Array, atob,
    window: {addEventListener() {}}, setTimeout,
    fetch: async (url, options) => {
      calls.push({url: new URL(url, 'https://trade.example'), options});
      return {ok: true, json: async () => ({invoice: 'UERG', packing: 'UERG'})};
    }
  });
  await node('practice-form').handlers.submit({preventDefault() {}});
  node('invoice-download').handlers.click();
  node('packing-download').handlers.click();
  assert.equal(node('results').hidden, false);
  node('practice-form').handlers.input();
  assert.equal(node('results').hidden, true);
  assert.equal(node('invoice-download').hasAttribute('href'), false);
  assert.equal(calls.filter(c => c.url.searchParams.get('page') === 'Practice Started').length, 1);
  return {calls, languageLink};
}

(async () => {
  for (const [query, referrer, expected] of [
    ['?utm_source=linkedin', '', 'LinkedIn'],
    ['', 'https://www.youtube.com/watch?v=abc', 'YouTube'],
    ['', 'https://linkedin.com.attacker.example/x', 'Other'],
    ['?utm_source=unknown', 'https://www.google.com/', 'Other'],
    ['', '', 'Direct']
  ]) {
    const {calls} = await exercise(query, referrer);
    assert(calls.every(c => c.url.searchParams.get('source') === expected));
    assert(calls.every(c => c.url.searchParams.get('traffic') === ''));
  }
  const qa = await exercise('?utm_source=linkedin&traffic=internal', '', true);
  assert(qa.calls.every(c => c.url.searchParams.get('traffic') === 'internal'));
  assert.equal(new URL(qa.languageLink.href).searchParams.get('traffic'), 'internal');
  assert.equal(new URL(qa.languageLink.href).searchParams.get('utm_source'), 'linkedin');
  assert.equal(qa.calls.filter(c => c.url.pathname === '/try-sample/documents').length, 1);
  console.log('6 traffic/source and QA frontend scenarios passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
