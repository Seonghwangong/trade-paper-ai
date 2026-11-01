const assert = require('node:assert/strict');
const {create} = require('../app/static/voice_input.js');
function fixture(){
 let text='Buyer: ABC', state=false, message='', instance, sequence=0;
 const timers=new Map();
 class Recognition{
  constructor(){instance=this;}
  start(){this.started=true;}
  stop(){this.stopped=true;}
  abort(){this.aborted=true;}
 }
 const env={SpeechRecognition:Recognition,setTimeout:fn=>{timers.set(++sequence,fn);return sequence;},clearTimeout:id=>timers.delete(id),addEventListener(){}};
 const voice=create({getText:()=>text,getLanguage:()=> 'ko-KR',onText:value=>text=value,onState:value=>state=value,onMessage:value=>message=value},env);
 return {voice,env,timers,get instance(){return instance;},get text(){return text;},get state(){return state;},get message(){return message;}};
}
function result(text){const r=[{transcript:text}];r.isFinal=true;return r;}
{
 const f=fixture();assert.equal(f.instance,undefined);f.voice.start();
 assert.equal(f.instance.lang,'ko-KR');assert.equal(f.state,true);
 f.instance.onresult({resultIndex:0,results:[result('Shirts 5 PCS at USD 3')]});
 f.instance.onresult({resultIndex:0,results:[result('Shirts 5 PCS at USD 3')]});
 assert.equal(f.text,'Buyer: ABC\nShirts 5 PCS at USD 3');
 f.voice.stop();assert.equal(f.instance.stopped,true);f.instance.onend();
 assert.equal(f.state,false);assert.equal(f.timers.size,0);assert.equal(f.message,'voiceDone');
}
{
 const f=fixture();f.voice.start();const stale=f.instance;
 stale.onerror({error:'not-allowed'});assert.equal(f.message,'voiceDenied');assert.equal(f.text,'Buyer: ABC');assert.equal(f.state,false);
 stale.onend();assert.equal(f.message,'voiceDenied');
 f.voice.start();stale.onresult({resultIndex:0,results:[result('stale data')]});assert.equal(f.text,'Buyer: ABC');f.voice.cancel();assert.equal(f.instance.aborted,true);
}
{
 const f=fixture();f.voice.start();f.instance.onresult({resultIndex:0,results:[result('x'.repeat(8001))]});
 assert.equal(f.text,'Buyer: ABC');assert.equal(f.message,'note_too_large');assert.equal(f.state,false);assert.equal(f.instance.aborted,true);
}
{
 const f=fixture();f.voice.start();const timeout=[...f.timers.values()][0];timeout();assert.equal(f.instance.stopped,true);
 [...f.timers.values()][0]();assert.equal(f.instance.aborted,true);assert.equal(f.state,false);
}
assert.equal(create({},{}).supported,false);
console.log('Voice input: explicit start, transcript preservation, duplicates, denial, stale events, limits, timeout and fallback passed.');
