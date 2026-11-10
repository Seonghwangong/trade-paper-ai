const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
const source=fs.readFileSync('app/static/invoice-complete.js','utf8');
function setup(responses,lang='ko'){
 const calls=[],errors=[],next=[],fields={};const node=()=>({style:{},appendChild(){},replaceChildren(...children){this.children=children;},scrollIntoView(){}});
 for(const id of ['seller','seller_address','seller_email','seller_phone','currency','buyer','buyer_address','buyer_email','complete-invoice','completed-invoice-pdf','include-signature'])fields[id]={...node(),value:id};
 const context={URLSearchParams,Object,Error,document:{documentElement:{lang},getElementById:id=>fields[id],createElement:node,querySelectorAll:()=>[fields.seller,fields.buyer]},getItems:()=>[{name:'Mug',quantity:2,unit_price:6}],clearWorkflowMessage(){},showWorkflowError:m=>errors.push(m),workflowErrorMessage:async()=> 'Request failed',workflowMessageArea:node,showShipmentReturn(){},showInvoiceNextActions:n=>next.push(n),window:{location:{search:'?pi_no=PI-001&shipment_no=SHP-001'},URL:{createObjectURL:()=> 'blob:pdf'}},fetch:async(url,opts)=>{calls.push({url,opts});const r=responses.shift();if(r instanceof Error)throw r;if(typeof r==='function')return r();return r;}};
 vm.runInNewContext(source,context);return {...context,fields,calls,errors,next};
}
const saved=()=>({ok:true,status:200,json:async()=>({invoice_no:'INV-123'})});
const pdf=()=>({ok:true,status:200,headers:{get:()=> 'application/pdf'},blob:async()=>({size:42})});
test('one click saves once and previews the saved invoice; repeat does not create or export again',async()=>{const c=setup([saved(),pdf()]);await c.window.saveInvoice();await c.window.saveInvoice();assert.deepEqual(c.calls.map(x=>x.url),['/invoice','/invoice-pdf/INV-123']);const payload=JSON.parse(c.calls[0].opts.body);assert.equal(payload.pi_no,'PI-001');assert.equal(payload.shipment_no,'SHP-001');assert.equal(payload.items[0].quantity,2);assert.equal(c.fields['completed-invoice-pdf'].hidden,false);assert.equal(c.fields.seller.disabled,true);assert.equal(c.fields['complete-invoice'].textContent,'저장된 PDF 보기');assert.equal(c.next[0],'INV-123');});
test('double clicks while save is pending cannot create duplicates',async()=>{let resolve;const c=setup([()=>new Promise(r=>resolve=r),pdf()]);const pending=c.window.saveInvoice();await c.window.saveInvoice();assert.equal(c.calls.length,1);resolve(saved());await pending;assert.equal(c.calls.length,2);});
test('PDF failure retries only the saved document',async()=>{const c=setup([saved(),new Error('offline'),pdf()]);await c.window.saveInvoice();assert.match(c.errors[0],/송장은 저장/);await c.window.saveInvoice();assert.deepEqual(c.calls.map(x=>x.url),['/invoice','/invoice-pdf/INV-123','/invoice-pdf/INV-123']);});
for(const mode of ['network','server','invalid-json','missing-number'])test('uncertain save blocks automatic resubmission: '+mode,async()=>{const r=mode==='network'?new Error('offline'):mode==='server'?{status:500,ok:false}:mode==='invalid-json'?{status:200,ok:true,json:async()=>{throw Error('bad')}}:{status:200,ok:true,json:async()=>({})};const c=setup([r]);await c.window.saveInvoice();await c.window.saveInvoice();assert.equal(c.calls.length,1);assert.equal(c.fields['complete-invoice'].disabled,true);assert.match(c.errors[0],/저장 결과/);});
test('validation error leaves form editable and permits correction',async()=>{const c=setup([{status:422,ok:false},saved(),pdf()]);await c.window.saveInvoice();assert.equal(c.fields['complete-invoice'].disabled,false);assert.equal(c.fields.seller.disabled,undefined);await c.window.saveInvoice();assert.equal(c.next[0],'INV-123');});
test('HTML login response and empty PDF never become previews',async()=>{for(const response of [{ok:true,headers:{get:()=> 'text/html'}},{ok:true,headers:{get:()=> 'application/pdf'},blob:async()=>({size:0})}]){const c=setup([saved(),response]);await c.window.saveInvoice();assert.equal(c.fields['completed-invoice-pdf'].children,undefined);assert.equal(c.errors.length,1);}});

test('signature inclusion is an explicit unchecked choice',async()=>{for(const checked of [false,true]){const c=setup([saved(),pdf()]);c.fields['include-signature'].checked=checked;await c.window.saveInvoice();assert.equal(JSON.parse(c.calls[0].opts.body).include_signature,checked);}});

test('pending save locks the submitted fields before acknowledgement',async()=>{
 let resolve;const c=setup([()=>new Promise(r=>resolve=r),pdf()]);
 const pending=c.window.saveInvoice();
 assert.equal(c.fields.seller.disabled,true);
 assert.equal(c.fields.buyer.disabled,true);
 resolve(saved());await pending;
 assert.equal(c.fields.seller.disabled,true);
});
test('definite validation rejection restores only previously editable fields',async()=>{
 let resolve;const c=setup([()=>new Promise(r=>resolve=r)]);
 c.fields.seller.disabled=false;c.fields.buyer.disabled=true;
 const pending=c.window.saveInvoice();
 assert.equal(c.fields.seller.disabled,true);
 resolve({status:422,ok:false});await pending;
 assert.equal(c.fields.seller.disabled,false);
 assert.equal(c.fields.buyer.disabled,true);
});
test('uncertain save keeps the submitted fields locked for checking the invoice list',async()=>{
 const c=setup([new Error('offline')]);await c.window.saveInvoice();
 assert.equal(c.fields.seller.disabled,true);
 assert.equal(c.fields['complete-invoice'].disabled,true);
});
