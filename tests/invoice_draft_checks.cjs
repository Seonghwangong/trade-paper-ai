const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const helper=fs.readFileSync('app/invoice_draft.py','utf8').match(/INVOICE_DRAFT_SCRIPT = r"""([\s\S]*?)"""/)[1];
function storage(){const data=new Map();return {data,getItem:k=>data.get(k)??null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)}}
function store(backing,context='edit/A',revision='r1'){return vm.runInNewContext(helper+';invoiceDraftStore(storage,context,revision)',{storage:backing,context,revision})}
test('drafts for different invoices and proformas remain independent',()=>{
 const s=storage(),a=store(s,'edit/A'),b=store(s,'edit/B'),n=store(s,'new-invoice:null'),p=store(s,'new-invoice:PI-001');
 for(const instance of [a,b,n,p])assert.equal(instance.read().status,'empty');
 a.write({item_name:['A']});b.write({item_name:['B']});n.write({item1:['New']});p.write({item1:['PI']});
 assert.equal(store(s,'edit/A').read().draft.values.item_name[0],'A');
 a.clear();assert.equal(b.read().draft.values.item_name[0],'B');assert.equal(n.read().draft.values.item1[0],'New');assert.equal(p.read().draft.values.item1[0],'PI');
});
test('matching revision resumes; changed server revision only exposes saved values for review',()=>{
 const s=storage(),a=store(s);a.read();a.write({extra_quantity:['2.5','0']});
 assert.equal(store(s).read().status,'ready');const changed=store(s,'edit/A','r2').read();
 assert.equal(changed.status,'stale');assert.equal(changed.draft.values.extra_quantity[0],'2.5');
});
test('an older window cannot overwrite or clear a newer draft',()=>{
 const s=storage(),a=store(s),b=store(s);a.read();b.read();a.write({item_name:['Recent']});
 assert.equal(b.write({item_name:['Stale']}),'conflict');assert.equal(b.clear(),'conflict');
 assert.equal(store(s).read().draft.values.item_name[0],'Recent');
});
test('legacy unscoped draft is not adopted or deleted',()=>{
 const s=storage();s.setItem('trade-paper-ai-invoice-draft','old private draft');const a=store(s);a.read();a.write({item_name:['New']});a.clear();
 assert.equal(s.getItem('trade-paper-ai-invoice-draft'),'old private draft');
});
test('unavailable browser storage never reports saved',()=>{
 const a=store({getItem(){throw Error()},setItem(){throw Error()},removeItem(){throw Error()}});
 assert.equal(a.read().status,'unavailable');assert.equal(a.write({}),'unavailable');assert.equal(a.clear(),'unavailable');
});
test('malformed draft values cannot be restored',()=>{
 const s=storage(),a=store(s);a.read();a.write({item_name:['Okay']});const key=[...s.data.keys()][0];
 s.setItem(key,JSON.stringify({version:2,context:'edit/A',revision:'r1',values:{item_name:'not an array'}}));
 assert.equal(store(s).read().status,'invalid');
});
const ui=fs.readFileSync('app/ui.py','utf8').split('__INVOICE_DRAFT_STORE__\n')[1].split("  const templateKey=")[0];
function editor(s,{path='/edit-invoice/A',revision='r1',lang='ko'}={}){
 const listeners={},windowListeners={},timers=new Map();let seq=0;
 const root={children:[],querySelectorAll:()=>fields,querySelector:()=>({value:revision}),appendChild(el){this.children.push(el)},insertBefore(el){this.children.unshift(el)},firstChild:null};
 function node(tag){return {tag,children:[],hidden:false,textContent:'',listeners:{},setAttribute(){},appendChild(el){this.children.push(el)},addEventListener(n,fn){this.listeners[n]=fn},remove(){root.children=root.children.filter(n=>n!==this)}}}
 function field(name,value){return {name,value,type:'text',closest:()=>null,dispatchEvent(event){for(const fn of listeners[event.type]||[])fn({target:this})}}}
 const fields=[field('item_name','First'),field('extra_name','Second'),field('extra_quantity','5')];
 const document={documentElement:{lang},visibilityState:'visible',createElement:node,addEventListener(n,fn){(listeners[n]??=[]).push(fn)}};
 const window={location:{pathname:path,search:''},confirm:()=>true,clearTimeout(id){timers.delete(id)},setTimeout(fn){const id=++seq;timers.set(id,fn);return id},addEventListener(n,fn){windowListeners[n]=fn}};
 const context=vm.createContext({document,window,editingRoot:root,workLabel:'Commercial Invoice',localStorage:s,URLSearchParams,Event:class{constructor(type){this.type=type}}});
 vm.runInContext(helper+ui,context);
 return {fields,root,window,document,context,input(index,value){fields[index].value=value;listeners.input.forEach(fn=>fn({target:fields[index]}))},flush(){for(const fn of timers.values())fn();timers.clear()},hide(){document.visibilityState='hidden';listeners.visibilitychange.forEach(fn=>fn())},leave(){windowListeners.pagehide()},save(){window.tpClearInvoiceDraft()},buttons(){return root.children.flatMap(el=>el.children).flatMap(el=>el.children).filter(el=>el.tag==='button')}};
}
test('rapid navigation flushes pending input only for the edited document',()=>{
 const s=storage(),a=editor(s);a.input(2,'7.5');a.leave();
 assert.equal(store(s,'/edit-invoice/A').read().draft.values.extra_quantity[0],'7.5');
 const b=editor(s,{path:'/edit-invoice/B'});b.input(0,'Other');b.hide();
 assert.equal(store(s,'/edit-invoice/B').read().draft.values.item_name[0],'Other');
});
test('restoration is explicit and restores all repeated fields',()=>{
 const s=storage(),a=editor(s);a.input(2,'7.5');a.flush();
 const resumed=editor(s);assert.equal(resumed.fields[2].value,'5');
 const restore=resumed.buttons().find(b=>b.textContent==='임시저장 불러오기');assert.ok(restore);restore.listeners.click();
 assert.equal(resumed.fields[2].value,'7.5');
});
test('stale draft offers review but never a restore action',()=>{
 const s=storage(),a=editor(s);a.input(2,'7.5');a.flush();const newer=editor(s,{revision:'r2'});
 assert.ok(!newer.buttons().some(b=>b.textContent==='임시저장 불러오기'));
 assert.ok(newer.root.children.some(c=>c.children.some(n=>n.tag==='details')));
 assert.equal(newer.fields[2].value,'5');
});
test('new typing cannot overwrite an undecided previous draft',()=>{
 const s=storage(),a=editor(s);a.input(2,'7.5');a.flush();const resumed=editor(s);resumed.input(2,'99');resumed.flush();
 assert.equal(store(s,'/edit-invoice/A').read().draft.values.extra_quantity[0],'7.5');
});
test('successful save clears only this draft and cancels pending writes',()=>{
 const s=storage(),a=editor(s),b=editor(s,{path:'/edit-invoice/B'});a.input(2,'7');b.input(2,'9');b.flush();a.save();a.leave();a.flush();
 assert.equal(store(s,'/edit-invoice/A').read().status,'empty');assert.equal(store(s,'/edit-invoice/B').read().status,'ready');
});

test('broken JSON is retained as an invalid draft until deliberate discard',()=>{
 const s=storage(),a=store(s);a.read();a.write({item_name:['Okay']});const key=[...s.data.keys()][0];
 s.setItem(key,'{broken');assert.equal(store(s).read().status,'invalid');assert.equal(s.getItem(key),'{broken');
});
