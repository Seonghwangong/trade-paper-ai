// Execute the actual file-reading controller with a mock transport, no API cost.
const assert=require('node:assert/strict'),{test}=require('node:test');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../app/static/document_import.js'),'utf8');
const controller=source.slice(source.indexOf('function useAI()'),source.indexOf('function confirmReplacement()'));
function environment(enabled,checked,fail=false){
 const nodes={};
 const $=id=>nodes[id]||(nodes[id]={checked,disabled:false,hidden:false,src:'old-preview',options:[],getAttribute(){return this.src;},removeAttribute(){this.src='';},replaceChildren(){this.options=[];}});
 const calls=[],statuses=[],rendered=[];
 const context={crypto:{randomUUID:()=> '00000000-0000-4000-8000-000000000000'},showAIUsage(){},config:{ai_enabled:enabled},$,file:{name:'order.pdf',size:128},busy:false,saving:false,draftReady:true,
  worker:null,selectedSheet:0,previewURL:null,URL:{revokeObjectURL(){}},
  setBusy(v){context.busy=v;},setStatus(...x){statuses.push(x);},
  async pdfText(){return {text:'Buyer: ABC\nQuantity: 500\nUnit Price: USD 8.00',extra:['partial']};},
  async parseResponse(response){return response;},
  async fetch(url,options){calls.push({url,options});if(fail)throw new Error('ai_unavailable');return {fields:{buyer:'ABC'},items:[{quantity:'500',unit_price:'8.00'}]};},
  renderDraft(result){rendered.push(result);},
 };
 vm.createContext(context);vm.runInContext(controller,context);
 return {context,calls,statuses,rendered,nodes};
}
test('consented PDF text calls the AI mode without uploading PDF or account records',async()=>{
 const e=environment(true,true);assert.equal(await e.context.readFile(),true);
 assert.equal(e.calls.length,1);const body=JSON.parse(e.calls[0].options.body);
 assert.deepEqual(Object.keys(body).sort(),['ai_consent','mode','request_id','text']);
 assert.equal(body.mode,'ai_order');assert.equal(body.ai_consent,'openai-order-text-v1');
 assert.equal(e.rendered[0].items[0].quantity,'500');assert.equal(e.context.busy,false);
});
for(const [enabled,checked] of [[false,true],[true,false]])test(`no AI transmission enabled=${enabled} consent=${checked}`,async()=>{
 const e=environment(enabled,checked);await e.context.readFile();
 assert.deepEqual(Object.keys(JSON.parse(e.calls[0].options.body)),['text']);
});
test('AI error keeps existing draft and preview and permits explicit retry',async()=>{
 const e=environment(true,true,true);assert.equal(await e.context.readFile(),false);
 assert.equal(e.rendered.length,0);assert.equal(e.context.draftReady,true);
 assert.equal(e.nodes.original.src,'old-preview');assert.equal(e.nodes.workspace.hidden,false);
 assert.equal(e.context.busy,false);assert.equal(e.calls.length,1);
 assert.deepEqual(e.statuses.at(-1),['ai_unavailable',true]);
});
