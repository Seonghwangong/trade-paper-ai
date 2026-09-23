const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const source=fs.readFileSync(path.join(__dirname,'../app/main.py'),'utf8');
const code=source.slice(source.indexOf('async function previewWorkflowPDF('),source.indexOf('function showShipmentReturn('));
async function run(mode){
  const errors=[],opened=[],links=[],area={appendChild:node=>links.push(node),style:{}};
  let request;
  const context=vm.createContext({
    clearWorkflowMessage:()=>{},showWorkflowError:message=>errors.push(message),workflowErrorMessage:async()=> 'Quantity must be a valid number.',
    workflowMessageArea:()=>area,document:{createElement:tag=>({tag})},
    window:{URL:{createObjectURL:()=> 'blob:test'},open:url=>{opened.push(url);return mode==='blocked'?null:{};}},
    fetch:async(url,options)=>{request={url,options};if(mode==='network')throw Error('offline');return {
      ok:mode!=='invalid',headers:{get:()=>mode==='html'?'text/html':'application/pdf'},
      blob:async()=>{if(mode==='interrupted')throw Error('failed');return {size:mode==='empty'?0:10};}
    };}
  });
  vm.runInContext(code,context);
  await context.previewWorkflowPDF('/invoice/pdf',{currency:'EUR'});
  assert.equal(JSON.parse(request.options.body).currency,'EUR');
  assert(request.options.headers.Accept.includes('application/json'));
  if(['network','invalid','html','interrupted','empty'].includes(mode)){
    assert.equal(errors.length,1,mode);assert.equal(opened.length,0,mode);
  }else{assert.equal(errors.length,0);assert.equal(opened.length,1);}
  if(mode==='blocked')assert(links.some(link=>link.tag==='a'&&link.href==='blob:test'&&link.rel==='noopener'));
}
(async()=>{for(const mode of ['network','invalid','html','interrupted','empty','success','blocked'])await run(mode);console.log('7 PDF preview scenarios passed.');})().catch(error=>{console.error(error);process.exit(1);});
