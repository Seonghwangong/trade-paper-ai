const assert = require('node:assert/strict');
const {test} = require('node:test');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const html = readFileSync(join(__dirname, '../app/templates/public_home.html'), 'utf8');
const source = html.match(/function source\(\)\{[\s\S]*?\n  \}/)[0];
for (const [search, referrer, expected] of [
  ['?ref=producthunt', '', 'Product Hunt'],
  ['?ref=ProductHunt', '', 'Product Hunt'],
  ['?utm_source=&ref=producthunt', '', 'Product Hunt'],
  ['?utm_source=reddit&ref=producthunt', '', 'Reddit'],
  ['', '', 'Direct'],
  ['?utm_source=youtube', 'https://google.com/', 'YouTube'],
  ['', 'https://l.instagram.com/', 'Instagram'],
  ['', 'https://blog.naver.com/', 'Naver'],
  ['', 'https://youtube.com.attacker.example/', 'Other'],
  ['', 'https://example.com/youtube.com', 'Other'],
  ['', 'https://www.producthunt.com/', 'Product Hunt'],
  ['?ref=unknown', '', 'Other'],
]) {
  test(`landing source ${search || referrer || 'direct'}`, () => {
    const ctx = vm.createContext({URL, URLSearchParams, location:{search}, document:{referrer}});
    assert.equal(vm.runInContext(source + ';source()', ctx), expected);
  });
}
