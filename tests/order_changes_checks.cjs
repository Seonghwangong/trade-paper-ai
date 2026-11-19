const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const code=fs.readFileSync('app/static/order_changes.js','utf8');
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.listeners={};this.dataset={};this.checked=false;this.disabled=false;this.hidden=false;this.textContent='';}
 append(...children){this.children.push(...children.filter(c=>c instanceof Element));}
 after(el){this.sibling=el;}
 replaceChildren(...children){this.children=[];this.append(...children);}
 addEventListener(name,fn){this.listeners[name]=fn;}
 focus(){}
 querySelectorAll(selector){
  const all=this.children.flatMap(c=>[c,...c.querySelectorAll('*')]);
  if(selector==='*')return all;
  return all.filter(c=>selector.split(',').some(s=>s==='input:checked'?c.tag==='input'&&c.checked:c.tag===s));
 }
 querySelector(){return null;}
}
function setup(responseMode='ok',unresolved=false){
 const nodes=Object.fromEntries(['review-form','fields','items','status'].map(id=>[id,new Element('div')]));
 const observers=[],requests=[],locks=[],state={};
 const document={getElementById:id=>nodes[id],createElement:tag=>new Element(tag)};
 const window={location:{assign:url=>state.url=url}};
 const changes=[{id:'item:0:quantity',kind:'item',name:'Cotton shirts',field:'quantity',before:500,after:600},{id:'field:buyer',kind:'field',field:'buyer',before:'Buyer',after:'New Buyer'},{id:'remove:1',kind:'remove',before:{name:'Bowl',quantity:2},after:''}];
 const result={version:'review-1',changes,unresolved:unresolved?[{source:0,item:{name:'New name'}}]:[],existing_items:[{name:'Cotton shirts'}]};
 const fetch=async(url,options)=>{const payload=JSON.parse(options.body);requests.push({url,payload});if(url.endsWith('/apply')&&responseMode==='offline')throw new TypeError('offline');return {ok:!url.endsWith('/apply')||responseMode==='ok',status:url.endsWith('/apply')&&responseMode==='server'?503:responseMode==='conflict'?409:200,json:async()=>url.endsWith('/preview')?result:responseMode==='conflict'?{detail:'changed_since_review'}:{next_url:'/order-changes/INV-1/status'}};};
 vm.runInNewContext(code,{window,document,fetch,TypeError,SyntaxError,MutationObserver:class{constructor(fn){observers.push(fn);}observe(){}}});
 const controller=window.TPOrderChanges.create({config:{lang:'ko',revise_invoice:'INV-1'},label:s=>s,lock:b=>locks.push(b),complete:()=>state.completed=true});
 const section=nodes['review-form'].sibling;
 const find=(tag,text)=>section.querySelectorAll(tag).find(el=>el.textContent===text);
 return {controller,section,nodes,requests,locks,state,observers,find};
}
const draft={fields:{buyer:'New Buyer'},items:[{name:'Cotton shirts',quantity:'600',unit_price:'8'}]};
test('comparison is read-only, unselected by default, and bulk selection excludes removals',async()=>{
 const x=setup();await x.controller.review(draft);
 assert.equal(x.requests.length,1);assert.match(x.requests[0].url,/preview$/);
 assert.equal(x.section.querySelectorAll('input:checked').length,0);
 x.find('button','삭제를 제외한 변경 선택').listeners.click();
 assert.deepEqual(x.section.querySelectorAll('input:checked').map(i=>i.value),['item:0:quantity','field:buyer']);
 assert.deepEqual(x.locks,[true,false]);
});
test('applying needs confirmation and sends only selected changes and the reviewed version',async()=>{
 const x=setup();await x.controller.review(draft);
 const inputs=x.section.querySelectorAll('input');inputs.find(i=>i.value==='item:0:quantity').checked=true;
 const apply=x.find('button','선택한 변경 송장에 반영');
 await apply.listeners.click();assert.equal(x.requests.length,1);
 inputs.find(i=>i.id==='changes-confirmed').checked=true;
 await apply.listeners.click();
 assert.deepEqual(x.requests[1].payload.selected,['item:0:quantity']);
 assert.equal(x.requests[1].payload.version,'review-1');assert.equal(x.requests[1].payload.confirmed,true);
 assert.equal(x.state.completed,true);assert.match(x.state.url,/status\?lang=ko$/);
});
test('editing values or adding/removing rows invalidates the old comparison',async()=>{
 const x=setup();await x.controller.review(draft);x.nodes['review-form'].listeners.input();assert.equal(x.section.hidden,true);
 await x.controller.review(draft);x.observers[1]();assert.equal(x.section.hidden,true);
});
test('ambiguous items must be matched before apply is offered',async()=>{
 const x=setup('ok',true);await x.controller.review(draft);
 assert.equal(x.find('button','선택한 변경 송장에 반영'),undefined);
 const select=x.section.querySelectorAll('select')[0];select.value='0';select.listeners.change();
 await x.find('button','품목 연결 후 다시 비교').listeners.click();
 assert.equal(x.requests[1].payload.mapping['0'],0);
});
test('reading a replacement source prevents applying an old comparison',async()=>{
 const x=setup();await x.controller.review(draft);
 const inputs=x.section.querySelectorAll('input');inputs.find(i=>i.value==='item:0:quantity').checked=true;inputs.find(i=>i.id==='changes-confirmed').checked=true;
 x.controller.setExternalBusy(true);
 await x.find('button','선택한 변경 송장에 반영').listeners.click();
 assert.equal(x.requests.length,1);
 x.controller.invalidate();assert.equal(x.section.hidden,true);
 x.controller.setExternalBusy(false);
});
for(const mode of ['offline','server','conflict'])test(mode+' never clears the draft or reports success',async()=>{
 const x=setup(mode);await x.controller.review(draft);
 const inputs=x.section.querySelectorAll('input');inputs.find(i=>i.value==='item:0:quantity').checked=true;inputs.find(i=>i.id==='changes-confirmed').checked=true;
 await x.find('button','선택한 변경 송장에 반영').listeners.click();
 assert.equal(x.state.completed,undefined);assert.equal(x.state.url,undefined);
 if(mode==='conflict'){assert.match(x.nodes.status.textContent,/다시 비교/);assert.equal(x.locks.at(-1),false);}
 else{assert.match(x.nodes.status.textContent,/저장 결과/);assert.equal(x.locks.at(-1),true);await x.controller.review(draft);assert.equal(x.requests.length,2);}
});
