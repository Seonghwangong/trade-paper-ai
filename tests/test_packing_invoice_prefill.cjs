const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('app/static/packing.html', 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

test('Invoice prefill keeps full specifications, quantities and even blank shared fields despite master defaults', async () => {
  const items = [
    {name:'Mug 450 ml / 80 × 110 mm', quantity:'12.5', hs_code:'', origin:'', unit:''},
    {name:'Mug 300 ml / 70 × 90 mm', quantity:24, hs_code:'069110', origin:'Korea', unit:'PCS'}
  ];
  const invoice = {invoice_no:'INV-TEST', seller:'Seller', buyer:'Buyer', seller_address:'Original address', items};
  const fields = {}, cards = [];
  const field = () => ({value:''});
  ['invoice_no','seller','seller_address','seller_email','seller_phone','buyer','buyer_address','buyer_email'].forEach(id => fields[id] = field());
  fields.invoice_no.value = invoice.invoice_no;
  fields.items_area = {set innerHTML(value) { cards.length = 0; }};
  const events = [];
  const context = {
    window:{}, Event:class {constructor(type) {this.type=type;}},
    document:{getElementById:id => fields[id], querySelectorAll:() => cards, dispatchEvent:e => events.push(e.type)},
    fetch:async () => ({json:async () => [invoice]})
  };
  vm.createContext(context); vm.runInContext(script, context);
  vm.runInContext('products = [{name:"Mug 450 ml / 80 × 110 mm",hs_code:"999999",origin:"Other",unit:"BOX"}]', context);
  context.addItem = () => {
    const values = {};
    for(const key of ['item','quantity','hs_code','origin','unit','productSelect','carton','net_weight','gross_weight']) values['.'+key] = field();
    cards.push({querySelector:key=>values[key]});
  };
  await context.loadInvoiceData();
  assert.equal(cards.length,2);
  for(let i=0;i<items.length;i++) {
    for(const [selector,key] of [['item','name'],['quantity','quantity'],['hs_code','hs_code'],['origin','origin'],['unit','unit']])
      assert.equal(cards[i].querySelector('.'+selector).value,items[i][key]);
    for(const key of ['carton','net_weight','gross_weight']) assert.equal(cards[i].querySelector('.'+key).value,'');
  }
  assert.equal(fields.seller_address.value,'Original address');
  assert.equal(fields.buyer.value,'Buyer');
  assert.deepEqual(events,['tp:prefill-complete']);
});
