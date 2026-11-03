const assert=require('node:assert/strict');
const {test}=require('node:test');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const vm=require('node:vm');
const html=readFileSync(join(__dirname,'../app/static/invoice.html'),'utf8');
function fixture(search='?flow=setup'){
 const fields={};const field=id=>fields[id]??=( {value:'',hidden:true,replaceChildren(){}} );
 field('currency').value='USD';
 const ctx=vm.createContext({URLSearchParams,window:{location:{search}},
  document:{getElementById:field,dispatchEvent(){}},Event:class{},
  setupEdited:new Set(),calculateTotal(){},
  buyers:[{name:'Buyer A',address:'Saved address',email:'buyer@example.com',default_currency:'EUR'}],
  products:[{name:'Cup',hs_code:'691110',unit_price:'0',origin:'KR',unit:'PCS'}],
 });
 for(const name of ['getPiNoFromUrl','applyProduct','selectProduct','selectBuyerCompany','applySetupPrefill']){
  const source=html.match(new RegExp(`function ${name}\\([^]*?\\n}`, 'm'));assert.ok(source,name);vm.runInContext(source[0],ctx);
 }
 return {ctx,field,apply:()=>ctx.applySetupPrefill()};
}
test('first setup fills the sole saved buyer/product and leaves quantity unconfirmed',()=>{
 const f=fixture();f.apply();
 for(const [id,value] of Object.entries({buyer:'Buyer A',buyer_address:'Saved address',buyer_email:'buyer@example.com',currency:'EUR',item1:'Cup',hs1:'691110',price1:'0',origin1:'KR',unit1:'PCS'}))assert.equal(String(f.field(id).value),value,id);
 assert.equal(f.field('qty1').value,'');assert.equal(f.field('setup-preview').hidden,false);
});
test('multiple or absent master records are never chosen by ordering',()=>{
 for(const count of [0,2]){
  const f=fixture();f.ctx.buyers=Array(count).fill(f.ctx.buyers[0]);f.ctx.products=Array(count).fill(f.ctx.products[0]);f.apply();
  assert.equal(f.field('buyer').value,'');assert.equal(f.field('item1').value,'');assert.equal(f.field('setup-preview').hidden,true);
 }
});
test('entered buyer contacts and product fields are not overwritten',()=>{
 const f=fixture();f.field('buyer_email').value='custom@example.com';f.field('price1').value='12.5';f.apply();
 assert.equal(f.field('buyer_email').value,'custom@example.com');assert.equal(f.field('buyer').value,'');assert.equal(f.field('price1').value,'12.5');assert.equal(f.field('item1').value,'');
});
test('deliberately cleared fields stay blank while other untouched sections can fill',()=>{
 const f=fixture();f.ctx.setupEdited.add('buyer');f.apply();assert.equal(f.field('buyer').value,'');assert.equal(f.field('item1').value,'Cup');
 const g=fixture();g.ctx.setupEdited.add('hs1');g.apply();assert.equal(g.field('item1').value,'');assert.equal(g.field('buyer').value,'Buyer A');
});
test('entered quantities and chosen currency survive asynchronous initial loading',()=>{
 for(const currency of ['JPY','USD','']){
  const f=fixture();f.field('qty1').value='23';f.field('currency').value=currency;f.ctx.setupEdited.add('currency');f.apply();
  assert.equal(f.field('qty1').value,'23');assert.equal(f.field('currency').value,currency);
 }
});
test('ordinary invoices, demo and proforma-prefill retain their existing behavior',()=>{
 for(const search of ['', '?demo=1&flow=setup','?flow=setup&pi_no=PI-1']){
  const f=fixture(search);f.apply();assert.equal(f.field('buyer').value,'');assert.equal(f.field('item1').value,'');
 }
});
test('re-running setup does not replace already loaded or edited values',()=>{
 const f=fixture();f.apply();f.field('price1').value='99';f.ctx.products[0].name='New';f.apply();
 assert.equal(f.field('item1').value,'Cup');assert.equal(f.field('price1').value,'99');
});
