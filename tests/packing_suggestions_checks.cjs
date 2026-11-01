const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const code=fs.readFileSync('app/static/packing-assistant.js','utf8');
class Element {
 constructor(tag='div'){this.tag=tag;this.children=[];this.events={};this.dataset={};this.style={};this._value='';this.isConnected=true;}
 get value(){return this._value;} set value(v){this._value=String(v);}
 append(...nodes){this.children.push(...nodes);} appendChild(node){this.append(node);}
 addEventListener(name,handler){(this.events[name]??=[]).push(handler);}
 dispatchEvent(event){for(const f of this.events[event.type]||[]) f(event);}
 setAttribute(k,v){this[k]=v;}
 querySelectorAll(){return [];}
 remove(){this.isConnected=false;}
}
const defaults={name:'Shirt',hs_code:'6109',origin:'KR',unit:'PCS',units_per_carton:20,net_per_unit:'.2',tare_per_carton:'.5'};
const settle=()=>new Promise(resolve=>setImmediate(resolve));
async function run(records, beforeResolve){
 const row=new Element();const fields={};for(const [k,v] of Object.entries({...defaults,quantity:'100',carton:'',net_weight:'',gross_weight:''})){fields[k]=new Element('input');fields[k].value=v;}
 row.querySelector=selector=>fields[selector.match(/name="([^"]+)/)[1].replace('item_name','name')];
 const document=new Element();document.body=new Element();document.documentElement={lang:'en'};document.querySelectorAll=()=>[row];document.createElement=tag=>new Element(tag);
 let resolveFetch;const requests=[];const pending=new Promise(resolve=>resolveFetch=resolve);
 vm.runInNewContext(code,{document,fetch:(url)=>{requests.push(url);return pending;},MutationObserver:class{observe(){}},Event:class{constructor(type){this.type=type;}}});
 const all=(node)=>[node,...node.children.flatMap(all)];const inputs=()=>Object.fromEntries(all(row).filter(n=>n.dataset.calculator).map(n=>[n.dataset.calculator,n]));
 if(beforeResolve)beforeResolve(inputs(),fields,row);
 resolveFetch({ok:true,json:async()=>records});await settle();
 return {row,fields,inputs:inputs(),requests,all:()=>all(row)};
}
(async()=>{
 let h=await run([defaults]);assert.equal(h.inputs.units_per_carton.value,'20');assert.equal(h.fields.carton.value,'');assert.deepEqual(h.requests,['/packing-defaults']);
 h.fields.name.value='Other';h.row.dispatchEvent({type:'change'});await settle();assert.equal(h.inputs.units_per_carton.value,'');
 h=await run([defaults],inputs=>{inputs.units_per_carton.value='42';inputs.units_per_carton.dispatchEvent({type:'input'});});assert.equal(h.inputs.units_per_carton.value,'42');assert.equal(h.inputs.net_per_unit.value,'');
 for(const records of [[],[defaults,{...defaults,units_per_carton:50}],[{...defaults,origin:'US'}],[{...defaults,units_per_carton:0}],[{...defaults,net_per_unit:null}]]){h=await run(records);assert.equal(h.inputs.units_per_carton.value,'');}
 h=await run([defaults]);h.inputs.net_per_unit.value='.7';h.inputs.net_per_unit.dispatchEvent({type:'input'});h.fields.name.value='Different';h.row.dispatchEvent({type:'change'});await settle();assert.equal(h.inputs.net_per_unit.value,'.7');
 console.log('Packing suggestions: unique match, ambiguity, numeric validation, late response, manual edits and item changes passed.');
 const source=fs.readFileSync('app/main.py','utf8');const start=source.indexOf('function showPackingNextActions(');const end=source.indexOf('</script>',start);
 const document=new Element();document.body=new Element();document.documentElement={lang:'ko'};document.createElement=tag=>new Element(tag);document.getElementById=()=>null;
 const context={document,window:{setTimeout:()=>assert.fail('Next actions must remain visible')},encodeURIComponent};vm.createContext(context);vm.runInContext(source.slice(start,end),context);context.showPackingNextActions('PK-3','INV-2');
 const all=node=>[node,...node.children.flatMap(all)];const links=all(document.body).filter(n=>n.tag==='a');
 assert(links.some(a=>a.href==='/email-pair/prepare/PK-3?lang=ko'));
 assert(links.some(a=>a.href==='/invoice-pdf/INV-2?lang=ko'&&a.download==='INV-2.pdf'));
 assert(links.some(a=>a.href==='/packing-list-pdf/PK-3?lang=ko'&&a.download==='PK-3.pdf'));
 assert(links.every(a=>a.href.includes('lang=ko')));console.log('Saved pair actions: exact document links, filenames, language and persistence passed.');
})().catch(error=>{console.error(error);process.exit(1);});
