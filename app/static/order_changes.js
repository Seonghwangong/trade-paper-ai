/* Explicit, selective amendments. Imported text is rendered only as text. */
(function () {
'use strict';
window.TPOrderChanges={create({config,label,lock,complete}){
 const $=id=>document.getElementById(id),ko=config.lang==='ko',t=(en,kr)=>ko?kr:en;
 const base='/order-changes/'+encodeURIComponent(config.revise_invoice);
 const section=document.createElement('section');section.className='review-issues';section.hidden=true;section.tabIndex=-1;section.id='order-changes';
 $('review-form').after(section);
 let candidate=null,mapping={},result=null,pending=false,uncertain=false,externalBusy=false;
 const node=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
 const action=(text,run)=>{const b=node('button',text);b.type='button';b.addEventListener('click',run);return b;};
 function invalidate(){if(pending||uncertain)return;result=null;section.hidden=true;section.replaceChildren();}
 $('review-form').addEventListener('input',invalidate);
 for(const id of ['fields','items'])new MutationObserver(()=>{invalidate();const date=$('fields').querySelector('[data-field="invoice_date"]');if(date)date.parentElement.hidden=true;}).observe($(id),{childList:true});
 function message(text){$('status').textContent=text;}
 function error(e){
  const reason=e.message;
  if(reason==='changed_since_review')return t('The saved Invoice changed. Compare again before applying.','다른 곳에서 송장이 바뀌었습니다. 다시 비교한 뒤 반영하세요.');
  if(reason==='session')return t('Your session ended or access is unavailable. Sign in, then compare again.','로그인이 만료되었거나 접근 권한이 없습니다. 로그인 후 다시 비교하세요.');
  if(reason==='invalid_mapping')return t('Connect each revised item to a different saved item, or choose new / ignore.','각 품목을 서로 다른 기존 품목에 연결하거나 추가·건너뛰기를 선택하세요.');
  if(reason==='new_item_numbers_required'||reason==='invalid_number')return t('Check quantities and prices. New items need both; zero must be entered explicitly.','수량과 단가를 확인하세요. 새 품목에는 두 값이 필요하며, 0도 직접 입력해야 합니다.');
  if(reason==='revision_history_full')return t('This Invoice has reached its change-history limit. Export its records before continuing.','이 송장의 변경 기록 한도에 도달했습니다. 기록을 내려받아 보관해 주세요.');
  return t('Could not compare these values. Check the item names, numbers and currency, then compare again.','비교하지 못했습니다. 품명·수량·단가·통화를 확인한 뒤 다시 비교하세요.');
 }
 async function request(path,payload){
  const response=await fetch(base+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  if(response.redirected||response.status===401||response.status===403)throw Error('session');
  if(response.status>=500)throw Error('uncertain');
  const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'invalid_changes');return data;
 }
 function showValue(value){if(value&&typeof value==='object')return [value.name,value.quantity,value.unit,value.unit_price].filter(v=>v!==undefined&&v!=='').join(' · ');return String(value??'')||'—';}
 function draw(){
  section.replaceChildren(node('h2',t('Choose changes to apply','반영할 변경만 선택하세요')));
  section.append(node('p',t('Unchecked values stay unchanged. Missing items are never removed automatically.','선택하지 않은 값은 그대로 둡니다. 새 자료에 없는 품목도 자동으로 삭제하지 않습니다.')));
  for(const unresolved of result.unresolved){
   const row=node('label',unresolved.item.name+' · '+t('Match this item','어느 품목인가요?'));const select=node('select');select.dataset.source=String(unresolved.source);
   for(const [value,title] of [['',t('Choose a saved item','기존 품목 선택')],...result.existing_items.map((r,i)=>[String(i),(i+1)+'. '+r.name]),['new',t('Add as a new item','새 품목으로 추가')],['ignore',t('Ignore this source row','이 행은 반영하지 않음')]]){const option=node('option',title);option.value=value;select.append(option);}
   select.addEventListener('change',()=>{if(select.value==='')delete mapping[select.dataset.source];else mapping[select.dataset.source]=/^\d+$/.test(select.value)?Number(select.value):select.value;});row.append(select);section.append(row);
  }
  if(result.unresolved.length){section.append(action(t('Compare matched items','품목 연결 후 다시 비교'),compare));section.hidden=false;return;}
  if(!result.changes.length){section.append(node('p',t('No changes to apply. Your saved Invoice is unchanged.','반영할 변경이 없습니다. 기존 송장을 유지합니다.')));section.hidden=false;return;}
  const list=node('div');list.id='change-options';
  for(const c of result.changes){
   const row=node('label');row.className='check';const check=node('input');check.type='checkbox';check.value=c.id;check.dataset.kind=c.kind;
   const title=c.kind==='remove'?t('Remove saved item (optional)','기존 품목 삭제 (선택)'):c.kind==='add'?t('Add new item','새 품목 추가'):(c.name?c.name+' · ':'')+label(c.field);
   const copy=node('span',title+': '+showValue(c.before)+' → '+showValue(c.after));row.append(check,copy);list.append(row);
  }
  section.append(action(t('Select changes except removals','삭제를 제외한 변경 선택'),()=>{list.querySelectorAll('input').forEach(i=>i.checked=i.dataset.kind!=='remove');confirmation.checked=false;}),list);
  const confirmLabel=node('label');confirmLabel.className='check';const confirmation=node('input');confirmation.type='checkbox';confirmation.id='changes-confirmed';
  confirmLabel.append(confirmation,node('span',t('I checked the selected changes against the revised order. The previous signature will be removed; packing measurements need review.','수정 주문서와 선택한 변경을 확인했습니다. 기존 서명은 해제되며, 포장 실측값은 따로 확인합니다.')));
  list.addEventListener('change',()=>confirmation.checked=false);
  section.append(confirmLabel,action(t('Apply selected changes to Invoice','선택한 변경 송장에 반영'),()=>apply(confirmation,list)));
  section.hidden=false;
 }
 async function compare(){
  if(pending||uncertain||externalBusy)return;pending=true;lock(true);section.querySelectorAll('input,select,button').forEach(el=>el.disabled=true);
  try{result=await request('/preview',{candidate,mapping});draw();section.focus();message(t('Comparison ready. Nothing has been saved.','비교했습니다. 아직 저장한 내용은 없습니다.'));}
  catch(e){result=null;section.hidden=true;message(error(e));}
  finally{pending=false;lock(false);}
 }
 async function apply(confirmation,list){
  if(pending||uncertain||externalBusy||!result)return;
  const selected=[...list.querySelectorAll('input:checked')].map(i=>i.value);
  if(!selected.length||!confirmation.checked){message(t('Select changes and confirm your review first.','변경을 선택하고 원본 확인란을 체크하세요.'));return;}
  pending=true;lock(true);section.querySelectorAll('input,select,button').forEach(el=>el.disabled=true);
  try{
   const response=await request('/apply',{candidate,mapping,version:result.version,selected,confirmed:true});
   complete();window.location.assign(response.next_url+'?lang='+encodeURIComponent(config.lang));
  }catch(e){
   if(e.message==='uncertain'||e instanceof TypeError||e instanceof SyntaxError){uncertain=true;message(t('The save result could not be confirmed. Check the saved documents before continuing.','저장 결과를 확인하지 못했습니다. 저장된 내용을 먼저 확인하세요.'));const link=node('a',t('Check saved result','저장 결과 확인'));link.href=base+'/status?lang='+encodeURIComponent(config.lang);$('status').append(' ',link);}
   else{invalidate();message(error(e));section.hidden=true;}
  }finally{pending=false;if(!uncertain)lock(false);}
 }
 return {invalidate,setExternalBusy(value){externalBusy=value;section.querySelectorAll('input,select,button').forEach(el=>el.disabled=value||uncertain);},async review(draft){if(!draft||pending||uncertain||externalBusy)return;candidate={fields:{...draft.fields},items:draft.items.map(row=>({...row}))};mapping={};await compare();}};
}};
})();
