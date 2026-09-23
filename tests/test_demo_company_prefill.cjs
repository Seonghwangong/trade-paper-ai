const assert = require('node:assert/strict');
const { test } = require('node:test');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');

function source(file, name) {
  const html = readFileSync(join(__dirname, '../app/static', file), 'utf8');
  const match = html.match(new RegExp(`function ${name}\\([^]*?\\n}`, 'm'));
  assert.ok(match, `${name} exists`);
  return match[0];
}
function fixture(file, search = '?demo=1') {
  const fields = {};
  const ctx = vm.createContext({
    URLSearchParams,
    window: { location: { search } },
    document: { getElementById: id => fields[id] ||= { value: '', style: {}, textContent: '' } },
    companies: [], buyers: [], products: [],
    getPiNoFromUrl: () => '', calculateTotal: () => {},
  });
  vm.runInContext(source(file, 'applyDemoPrefill'), ctx);
  if (file === 'invoice.html') vm.runInContext(source(file, 'selectSeller'), ctx);
  return {ctx, fields, field: id => ctx.document.getElementById(id)};
}

test('company demo preserves all existing fields, including partially configured companies', () => {
  for (const saved of [{name:'Real Exporter', address:'Seoul', email:'owner@example.com', phone:'123'}, {address:'Existing address'}]) {
    const {ctx, field} = fixture('company.html');
    for (const key of ['name','address','email','phone']) field(key).value = saved[key] || '';
    ctx.applyDemoPrefill(saved);
    for (const key of ['name','address','email','phone']) assert.equal(field(key).value, saved[key] || '');
    assert.match(field('demo-preview').textContent, /saved company details are kept/);
  }
});
test('empty company receives complete sample; ordinary page receives no prefill', () => {
  const {ctx, field} = fixture('company.html');
  ctx.applyDemoPrefill({});
  assert.equal(field('name').value, 'Busan Comfort Trading');
  assert.equal(field('address').value, 'Busan, Korea');
  assert.equal(field('email').value, 'trade@example.com');
  const normal = fixture('company.html', '');
  normal.ctx.applyDemoPrefill({});
  assert.equal(normal.field('name').value, '');
});
test('invoice sample keeps seller identity and contacts from the same saved company', () => {
  const {ctx, field} = fixture('invoice.html');
  ctx.companies = [{name:'Real Exporter', address:'Real address', email:'owner@example.com', phone:'123'}];
  ctx.applyDemoPrefill();
  assert.equal(field('sellerSelect').value, '0');
  for (const [id, expected] of Object.entries({seller:'Real Exporter',seller_address:'Real address',seller_email:'owner@example.com',seller_phone:'123'})) assert.equal(field(id).value, expected);
  assert.equal(field('qty1').value, '1');
});
test('invoice without company replaces stale seller contacts with a coherent sample', () => {
  const {ctx, field} = fixture('invoice.html');
  field('seller_email').value = 'stale@example.com';
  ctx.applyDemoPrefill();
  assert.equal(field('seller').value, 'Busan Comfort Trading');
  assert.equal(field('seller_address').value, 'Busan, Korea');
  assert.equal(field('seller_email').value, 'trade@example.com');
  assert.equal(field('seller_phone').value, '+82-51-000-0000');
});
test('proforma invoice prefill is never replaced by demo data', () => {
  const {ctx, field} = fixture('invoice.html');
  ctx.getPiNoFromUrl = () => 'PI-1';
  field('seller').value = 'Proforma Seller';
  ctx.applyDemoPrefill();
  assert.equal(field('seller').value, 'Proforma Seller');
  assert.equal(field('qty1').value, '');
});
