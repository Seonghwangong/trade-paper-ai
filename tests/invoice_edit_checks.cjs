const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync('app/static/invoice-edit.js', 'utf8');
function element(value = '') {
  return {value, textContent:'', listeners:{}, options:[], addEventListener(name, fn){this.listeners[name]=fn}, appendChild(option){this.options.push(option)},dispatchEvent(){},scrollIntoView(){}};
}
async function setup(products, failed=false, result={ok:true,redirected:true,url:"http://example.test/invoice-list"}) {
  const rows = [[2,3],[4,5],[1.5,2.25]].map(([quantity,price],index) => {
    const row = element();
    row.fields = Object.fromEntries(Object.entries({name:'Original '+index, hs_code:'123456', origin:'KR',unit:'PCS',quantity:String(quantity),unit_price:String(price)}).map(([key,value])=>[key,element(value)]));
    row.select = element();
    row.querySelector = selector => selector==='.saved-product' ? row.select : row.fields[selector.match(/"(.*?)"/)[1]];
    return row;
  });
  const currency = element('USD'), total = element(), form = element(), error = element(), button = element();
  form.action = '/update-invoice/INV-1'; form.querySelector = () => button;
  const window = {location:{href:'http://example.test/edit-invoice/INV-1'},tpMarkSaved(){this.saved=true}};
  const document = {querySelectorAll:()=>rows,querySelector:()=>currency,documentElement:{lang:'ko'},getElementById:id=>({'total':total,'invoice-edit-form':form,'edit-error':error}[id]),createElement:()=>element()};
  vm.runInNewContext(code,{document,window,URL,Event:class{},FormData:class{},fetch:async(url, options)=>{if(failed)throw Error('offline');return options ? result : {ok:true,json:async()=>products}}});
  await new Promise(resolve=>setImmediate(resolve));
  return {rows,currency,total,form,error,button,window};
}
test('total includes every edited row and fractional quantities', async()=>{
 const {rows,total,currency}=await setup([]);
 assert.equal(total.textContent,'USD 29.375');
 rows[2].fields.quantity.value='2'; rows[2].listeners.input();
 assert.equal(total.textContent,'USD 30.5');
 currency.value='eur'; currency.listeners.input(); assert.equal(total.textContent,'EUR 30.5');
});
test('loading masters preserves all snapshots; selection changes only intended row and preserves quantity',async()=>{
 const {rows,total}=await setup([{name:'Replacement',unit_price:0,hs_code:'222222',origin:'JP',unit:'KG'}]);
 assert.equal(rows[1].fields.name.value,'Original 1');
 rows[1].select.value='0'; rows[1].select.listeners.change();
 assert.equal(rows[1].fields.name.value,'Replacement');
 assert.equal(rows[1].fields.unit_price.value,0);
 assert.equal(rows[1].fields.quantity.value,'4');
 assert.equal(rows[0].fields.name.value,'Original 0');
 assert.equal(rows[2].fields.name.value,'Original 2');
 assert.equal(total.textContent,'USD 9.375');
});
test('failed product fetch leaves manual editing and totals usable',async()=>{
 const {rows,total}=await setup([],true);
 rows[1].fields.quantity.value='0';rows[1].listeners.input();
 assert.equal(total.textContent,'USD 9.375');
});

test('conflict leaves inputs intact and explains how to recover',async()=>{
 const {rows,form,error,button,window}=await setup([],false,{ok:false,status:409});
 await form.listeners.submit({preventDefault(){}});
 assert.equal(rows[1].fields.quantity.value,'4');
 assert.equal(error.hidden,false);assert.match(error.textContent,/다른 화면/);
 assert.equal(button.disabled,false);assert.equal(window.saved,undefined);
});
test('network error leaves input available for retry',async()=>{
 const {form,error,button}=await setup([],true);
 await form.listeners.submit({preventDefault(){}});
 assert.equal(error.hidden,false);assert.match(error.textContent,/연결/);assert.equal(button.disabled,false);
});
test('expired-session redirect does not claim success or discard input',async()=>{
 const {form,error,window}=await setup([],false,{ok:true,redirected:true,url:'http://example.test/login?next=/invoice'});
 await form.listeners.submit({preventDefault(){}});
 assert.match(error.textContent,/로그인/);assert.equal(window.saved,undefined);
 assert.equal(window.location.href,'http://example.test/edit-invoice/INV-1');
});
test('confirmed save clears dirty state and opens invoice list',async()=>{
 const {form,window}=await setup([]);
 await form.listeners.submit({preventDefault(){}});
 assert.equal(window.saved,true);assert.equal(window.location.href,'http://example.test/invoice-list');
});
