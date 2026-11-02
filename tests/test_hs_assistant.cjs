const assert = require('node:assert/strict');
const { test } = require('node:test');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');

function fixture() {
  const nodes=[];
  function element(tag) {
    const el={tag,value:'',children:[],events:{},textContent:'',
      setAttribute(){}, append(...items){this.children.push(...items);},
      after(){}, replaceChildren(){this.children=[];},
      addEventListener(type,fn){this.events[type]=fn;},
      dispatchEvent(event){this.events[event.type]?.(event);},
    };
    nodes.push(el); return el;
  }
  const fields=Object.fromEntries(['name','hs_code','unit_price','origin','unit'].map(k=>[k,element('input')]));
  const form={elements:{namedItem:key=>fields[key]}};
  let scheduled, products=[], answer=false, confirmCount=0;
  const context=vm.createContext({
    document:{querySelector:()=>form,documentElement:{lang:'ko'},cookie:'',createElement:element,head:element('head')},
    location:{search:''},URLSearchParams,Event:class {constructor(type){this.type=type;}},
    window:{confirm:()=>{confirmCount++;return answer;}},
    setTimeout:fn=>{scheduled=fn;return 1;},clearTimeout:()=>{scheduled=null;},
    fetch:async()=>({ok:true,json:async()=>products}),
  });
  vm.runInContext(readFileSync(join(__dirname,'../app/static/hs-assistant.js'),'utf8'),context);
  return {fields,nodes,setAnswer:x=>{answer=x;},getConfirms:()=>confirmCount,
    choose(value){const select=nodes.find(n=>n.tag==='select');select.value=value;select.events.change();},
    apply(){const button=nodes.filter(n=>n.tag==='button'&&n.textContent.includes('후보 적용')).at(-1);button.events.click();},
    async suggest(rows){products=rows;fields.name.value='Notebook';fields.name.dispatchEvent({type:'input'});await scheduled();},
  };
}

test('HS characteristics never enter a code until confirmed; cancel preserves an existing code',()=>{
  const f=fixture();f.choose('laptop');assert.equal(f.fields.hs_code.value,'');
  f.apply();assert.equal(f.fields.hs_code.value,'847130');
  f.choose('porcelain');f.apply();assert.equal(f.getConfirms(),1);assert.equal(f.fields.hs_code.value,'847130');
  f.setAnswer(true);f.apply();assert.equal(f.fields.hs_code.value,'691110');
  f.choose('other');assert.equal(f.fields.hs_code.value,'691110');
});

test('saved product reuse preserves zero prices and requires confirmation before overwriting entered values',async()=>{
  const f=fixture();
  await f.suggest([{name:'Notebook sample',hs_code:'847130',unit_price:0,origin:'Korea',unit:'PCS'}]);
  const use=f.nodes.find(n=>n.tag==='button'&&n.textContent.includes('저장 품목 불러오기'));
  f.fields.unit_price.value='50';use.events.click();
  assert.equal(f.fields.unit_price.value,'50');assert.equal(f.fields.hs_code.value,'');
  f.setAnswer(true);use.events.click();
  assert.equal(f.fields.unit_price.value,'0');assert.equal(f.fields.hs_code.value,'847130');
  assert.equal(f.fields.origin.value,'Korea');assert.equal(f.fields.unit.value,'PCS');
});
