/* One recoverable draft per signed-in user, isolated by the server's opaque scope. */
(function(root){
'use strict';
const VERSION=1, TTL=7*24*60*60*1000, MAX_BYTES=1000000;
const object=value=>Boolean(value)&&typeof value==='object'&&!Array.isArray(value);
const strings=(value,max)=>object(value)&&Object.keys(value).length<=20&&Object.values(value).every(v=>typeof v==='string'&&v.length<=max);
function valid(value,now){
 if(!object(value)||value.version!==VERSION||!Number.isFinite(value.updated)||value.updated>now+60000||now-value.updated>TTL)return false;
 if(!['draft','saving','uncertain'].includes(value.status)||typeof value.note!=='string'||value.note.length>8000)return false;
 if(!['file','note'].includes(value.mode)||typeof value.label!=='string'||value.label.length>1000)return false;
 if(value.result===null)return value.status==='draft';
 const result=value.result;
 return object(result)&&strings(result.fields,1000)&&Array.isArray(result.items)&&result.items.length<=100&&result.items.every(item=>strings(item,1000))&&typeof result.source_text==='string'&&result.source_text.length<=200000&&Array.isArray(result.warnings)&&result.warnings.length<=120&&result.warnings.every(s=>typeof s==='string'&&s.length<=100)&&Array.isArray(result.unmatched_lines)&&result.unmatched_lines.length<=2000&&result.unmatched_lines.every(s=>typeof s==='string'&&s.length<=8000);
}
function create(storage,scope,now=Date.now){
 const key='tradepaper-import-draft-v1:'+scope;
 let expected=null, available=Boolean(storage)&&/^[a-f0-9]{64}$/.test(scope), conflict=false;
 function load(){
  if(!available)return null;
  try{expected=storage.getItem(key);if(!expected)return null;
   if(expected.length>MAX_BYTES)throw new Error('invalid');
   const value=JSON.parse(expected);if(!valid(value,now()))throw new Error('invalid');return value;
  }catch(error){try{if(storage.getItem(key)===expected)storage.removeItem(key);expected=null;}catch(ignore){available=false;}return null;}
 }
 function write(data){
  if(!available)return {ok:false,reason:'unavailable'};
  try{
   if(storage.getItem(key)!==expected){conflict=true;return {ok:false,reason:'conflict'};}
   if(expected){const previous=JSON.parse(expected);if(JSON.stringify({...data,version:VERSION,updated:previous.updated})===expected)return {ok:true};}
   const value={...data,version:VERSION,updated:now()};const raw=JSON.stringify(value);
   if(raw.length>MAX_BYTES||!valid(value,now()))return {ok:false,reason:'unavailable'};
   storage.setItem(key,raw);expected=raw;return {ok:true};
  }catch(error){return {ok:false,reason:'unavailable'};}
 }
 function clear(){
  if(!available)return false;
  try{if(storage.getItem(key)!==expected){conflict=true;return false;}storage.removeItem(key);expected=null;return true;}catch(error){return false;}
 }
 return {load,write,clear,get conflict(){return conflict;}};
}
root.TPImportDraft={create};if(typeof module!=='undefined')module.exports={create,valid};
})(typeof window==='undefined'?globalThis:window);
