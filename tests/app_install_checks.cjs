// Browser prompt outcomes and event ordering, without installing anything.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync('app/static/app-install.js', 'utf8');
function setup({standalone=false, lang='en', agent='Chrome', platform='Linux', touch=0}={}) {
  const events = {}, clicks = {}, nodes = {};
  for (const name of ['app-install','app-install-status','install-ios','install-android','install-desktop','options']) nodes[name] = {hidden:name==='app-install', addEventListener:(type,cb)=>clicks[type]=cb};
  const document = {documentElement:{lang},getElementById:id=>nodes[id],querySelector:()=>nodes.options};
  const window = {matchMedia:()=>({matches:standalone,addEventListener(){}}),addEventListener:(name,cb)=>events[name]=cb};
  vm.runInNewContext(code,{window,document,navigator:{userAgent:agent,platform,maxTouchPoints:touch}});
  return {nodes, events, click:()=>clicks.click()};
}
async function run(){
  let s=setup(); assert.equal(s.nodes['app-install'].hidden,true); assert.equal(s.nodes['install-desktop'].open,true);
  s=setup({platform:'MacIntel',touch:5}); assert.equal(s.nodes['install-ios'].open,true);
  s=setup({standalone:true,lang:'ko'}); assert.equal(s.nodes.options.hidden,true); assert.match(s.nodes['app-install-status'].textContent,/앱으로/);
  for(const outcome of ['dismissed','accepted']){
    s=setup();let used=0,prevented=false;
    s.events.beforeinstallprompt({preventDefault(){prevented=true;},prompt:async()=>used++,userChoice:Promise.resolve({outcome})});
    assert.ok(prevented);assert.equal(s.nodes['app-install'].hidden,false);
    await s.click();await s.click();assert.equal(used,1);assert.equal(s.nodes['app-install'].hidden,true);
    assert.match(s.nodes['app-install-status'].textContent,outcome==='accepted'?/requested/:/Not installed/);
  }
  s=setup();s.events.beforeinstallprompt({preventDefault(){},prompt:async()=>{throw Error('not available')},userChoice:Promise.resolve({outcome:'accepted'})});
  await s.click();assert.match(s.nodes['app-install-status'].textContent,/Could not/);
  s=setup();s.events.beforeinstallprompt({preventDefault(){},prompt:async()=>s.events.appinstalled(),userChoice:Promise.resolve({outcome:'accepted'})});
  await s.click();assert.match(s.nodes['app-install-status'].textContent,/App installed/);assert.equal(s.nodes.options.hidden,true);
  console.log('PASS: install availability, iPad detection, standalone mode, dismiss, accept, error, one-use prompt and install event race');
}
run().catch(error=>{console.error(error);process.exitCode=1});
