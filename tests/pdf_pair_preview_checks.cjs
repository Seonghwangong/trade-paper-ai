const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const code=fs.readFileSync('app/static/pdf-pair-preview.js','utf8').replace("import('/static/vendor/pdfjs/build/pdf.mjs')",'Promise.resolve(pdfjsFixture)');
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.events={};this.style={};this.hidden=false;}
 append(...nodes){this.children.push(...nodes);}
 setAttribute(k,v){this[k]=v;}
 getAttribute(k){return this[k];}
 addEventListener(k,v){this.events[k]=v;}
 getBoundingClientRect(){return {width:500};}
 getContext(){return {};}
 before(panel){this.panel=panel;}
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
async function run(failure=false,external=false){
 const frame=new Element('iframe');frame.title='Invoice.pdf';frame.src=external?'https://other.example/private.pdf':'/email-pair/pdf/token/0#view=FitH';
 let loads=0;const rendered=[];
 const fixture={numPages:2,getPage:async n=>{assert(n>=1&&n<=2);return {getViewport:({scale})=>({width:600*scale,height:800*scale}),render:()=>({promise:failure?Promise.reject(Error()):Promise.resolve(rendered.push(n))})};}};
 const context={URL,location:{href:'https://tradepaper.test/email-pair/review/token',origin:'https://tradepaper.test'},window:{devicePixelRatio:2},document:{documentElement:{lang:'ko'},querySelectorAll:()=>[frame],createElement:tag=>new Element(tag)},pdfjsFixture:{GlobalWorkerOptions:{},getDocument:opts=>{loads++;assert(opts.url.startsWith('https://tradepaper.test/'));assert.equal(opts.isEvalSupported,false);return {promise:Promise.resolve(fixture)};}}};
 await vm.runInNewContext(code,context);return {frame,rendered,loads};
}
(async()=>{
 let h=await run();const [status,canvas,controls]=h.frame.panel.children;const [prev,next]=controls.children;
 assert(h.frame.hidden);assert(!canvas.hidden);assert.equal(status.textContent,'1 / 2 페이지');assert(prev.disabled);assert(!next.disabled);
 next.events.click();await settle();assert.equal(status.textContent,'2 / 2 페이지');assert(next.disabled);assert(!prev.disabled);
 prev.events.click();await settle();assert.deepEqual(h.rendered,[1,2,1]);
 h=await run(true);assert(!h.frame.hidden);assert(h.frame.panel.children[1].hidden);assert(h.frame.panel.children[0].textContent.includes('PDF 열기'));
 h=await run(false,true);assert.equal(h.loads,0);assert(!h.frame.hidden);
 console.log('PDF pair previews: two pages, navigation, failed rendering fallback and same-origin restriction passed.');
})().catch(e=>{console.error(e);process.exit(1);});
