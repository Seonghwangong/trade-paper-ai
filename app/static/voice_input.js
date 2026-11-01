/* Explicit push-to-talk; transcription remains editable and never submits a form. */
(function(root){
'use strict';
function create(options, environment=root){
 const Recognition=environment.SpeechRecognition||environment.webkitSpeechRecognition;
 let session=null;
 function finish(current,message){
  if(session!==current)return;
  environment.clearTimeout(current.timer);session=null;options.onState(false);
  options.onMessage(message||(current.received?'voiceDone':'voiceEmpty'));
 }
 function start(){
  if(!Recognition||session)return;
  const current={recognition:null,baseline:options.getText().trim(),parts:new Map(),received:false,timer:null};
  session=current;
  try{
   const recognition=current.recognition=new Recognition();
   recognition.lang=options.getLanguage();recognition.continuous=true;recognition.interimResults=false;
   recognition.onresult=event=>{
    if(session!==current)return;
    for(let i=event.resultIndex;i<event.results.length;i++)if(event.results[i].isFinal)current.parts.set(i,event.results[i][0].transcript.trim());
    // Separate utterances into editable lines, keeping the pre-existing note intact.
    const text=[current.baseline,...[...current.parts.entries()].sort((a,b)=>a[0]-b[0]).map(entry=>entry[1])].filter(Boolean).join('\n');
    if(text.length>8000){finish(current,'note_too_large');recognition.abort();return;}
    current.received=current.parts.size>0;options.onText(text);
   };
   recognition.onerror=event=>{finish(current,['not-allowed','service-not-allowed','audio-capture'].includes(event.error)?'voiceDenied':event.error==='no-speech'?'voiceEmpty':'voiceError');try{recognition.abort();}catch(error){/* already stopped */}};
   recognition.onend=()=>finish(current);
   options.onState(true);options.onMessage('voiceListening');recognition.start();
   if(session===current)current.timer=environment.setTimeout(stop,60000);
  }catch(error){finish(current,'voiceError');}
 }
 function stop(){
  const current=session;if(!current)return;
  environment.clearTimeout(current.timer);
  try{current.recognition.stop();if(session===current)current.timer=environment.setTimeout(()=>{finish(current);current.recognition.abort();},3000);}
  catch(error){finish(current,'voiceError');}
 }
 function cancel(){const current=session;if(!current)return;finish(current);try{current.recognition.abort();}catch(error){/* already stopped */}}
 environment.addEventListener?.('pagehide',cancel);
 environment.document?.addEventListener('visibilitychange',()=>{if(environment.document.hidden)cancel();});
 return {supported:Boolean(Recognition),start,stop,cancel};
}
root.TPVoiceInput={create};
if(typeof module!=='undefined')module.exports={create};
})(typeof window==='undefined'?globalThis:window);
