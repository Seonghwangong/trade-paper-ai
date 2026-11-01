/* File contents are data: never interpolate them into HTML or execute formulas. */
(function () {
'use strict';
const config=JSON.parse(document.getElementById('import-config').textContent);
const ko=config.lang==='ko', $=id=>document.getElementById(id);
const words={
 eyebrow:['YOUR NEXT SHIPMENT, ONE STEP AT A TIME','다음 출하를, 차근차근'],
 stepFile:['Add your source','자료 입력'],stepReview:['Review the draft','초안 확인'],stepSaved:['Invoice saved','Invoice 저장'],
 dropHint:['Drop a file here, or choose one from your device.','파일을 끌어 놓거나 기기에서 선택하세요.'],browse:['Choose file','파일 선택'],sample:['Try a fictional sample','가상 예시로 먼저 보기'],shortLimits:['Excel · CSV · PDF · document photos / up to 8 MB','엑셀 · CSV · PDF · 서류 사진 / 최대 8MB'],fileDetails:['Supported files & how your data is handled','지원 파일과 자료 처리 안내'],
 replaceHint:['Your current draft has not been saved. If the new source cannot be read, we will keep this draft.','현재 초안은 저장되지 않았습니다. 새 자료를 읽지 못하면 기존 초안을 유지합니다.'],keepDraft:['Keep this draft','현재 초안 유지'],replaceDraft:['Use the new source','새 자료 사용'],
 oneFile:['Choose one file at a time.','파일을 한 번에 하나씩 선택하세요.'],sampleNotice:['Fictional sample — review it freely. Saving uses one document from your allowance.','가상 예시입니다. 자유롭게 확인해 보세요. 저장하면 문서 한도에서 1개를 사용합니다.'],missingFields:['Required fields still need attention: ','아직 확인할 필수 항목: '],reviewAll:['Required fields are filled. Compare every value with the original before saving.','필수 항목이 채워졌습니다. 저장 전 모든 값을 원본과 확인하세요.'],nextHint:['Next, prepare your Packing List from this Invoice. Check actual cartons and weights before saving it.','이 Invoice로 Packing List를 이어서 작성하세요. 저장 전에 실제 박스 수와 중량을 확인하면 됩니다.'],savedNumber:['Saved document','저장한 문서'],
 title:['Start with a file or a short order note','파일이나 짧은 주문 메모로 시작하세요'],
 intro:['Choose a file or enter a short order note. Review the Invoice draft, then continue to packing.','파일을 가져오거나 주문 내용을 적으세요. 송장 초안을 확인하고 포장명세서로 이어갑니다.'],
 choose:['Choose a file to read','읽어올 파일 선택'],limits:['XLSX / CSV / PDF / JPG / PNG / WebP · up to 8 MB, 5 PDF pages and 100 items. Printed English and Korean. Handwriting and complex layouts may need correction.','XLSX / CSV / PDF / JPG / PNG / WebP · 최대 8MB, PDF 5쪽, 품목 100개. 인쇄된 영문·한글을 읽습니다. 손글씨·복잡한 서식은 수정이 필요할 수 있습니다.'],
 privacy:['Original files are not kept. PDFs and photos are read on your device; extracted text and spreadsheets are analyzed by this site. Nothing is saved as a document or emailed until you choose to save.','원본 파일은 보관하지 않습니다. 사진·PDF는 기기에서 읽고, 추출한 글자와 엑셀 자료는 이 사이트에서 분석합니다. 저장 전에는 문서를 만들거나 이메일을 보내지 않습니다.'],
 sheet:['Worksheet (review every relevant sheet)','시트 선택 (필요한 시트를 각각 확인하세요)'],source:['Your source','가져온 원본'],readText:['Source text','원본 내용'],review:['Review your draft','자동으로 채운 초안 확인'],reviewHint:['Compare buyer, quantities and prices with the original. Highlighted fields need checking; an unmarked field can still be wrong. Existing company details are suggested only when the source has no seller.','거래처·수량·단가를 원본과 비교하세요. 노란 칸은 수정이 필요하며, 표시 없는 값도 확인해야 합니다. 수출자가 없으면 저장된 내 회사 정보를 제안합니다.'],items:['Items','품목'],add:['Add a missing item','누락된 품목 추가'],confirm:['I checked all items, quantities, prices and parties against the original.','원본과 모든 품목·수량·단가·거래처를 확인했습니다.'],save:['Save Invoice and continue','Invoice 저장하고 계속'],saveHint:['Saves one document against your plan allowance. Continue to Packing List after saving; cartons and weights require review. No charge or email is triggered.','저장 시 플랜의 문서 1개를 사용합니다. 저장 후 Packing List로 이어가며 박스·중량은 확인해야 합니다. 결제·이메일 발송은 하지 않습니다.'],
 seller:['Seller','수출자'],seller_address:['Seller address','수출자 주소'],buyer:['Buyer','수입자'],buyer_address:['Buyer address','수입자 주소'],buyer_email:['Buyer email','수입자 이메일'],invoice_date:['Invoice date','송장 날짜'],currency:['Currency (e.g. USD)','통화 (예: USD)'],trade_term:['Trade term','무역조건'],payment_term:['Payment terms','결제조건'],source_reference:['Original document number','원본 문서번호'],name:['Description','품명'],quantity:['Quantity','수량'],unit_price:['Unit price','단가'],unit:['Unit','단위'],hs_code:['HS code','HS 코드'],origin:['Origin','원산지'],remove:['Remove','삭제'],
 reading:['Reading your file…','파일을 읽고 있습니다…'],ocr:['Reading printed text… The first photo can take longer.','사진의 글자를 읽고 있습니다… 첫 사진은 시간이 조금 걸릴 수 있습니다.'],ready:['Draft ready. Check it against the original before saving.','초안을 채웠습니다. 저장 전 원본과 확인하세요.'],saving:['Saving Invoice…','Invoice 저장 중…'],saved:['Invoice saved.','Invoice를 저장했습니다.'],edit:['Open Invoice','Invoice 열기'],packing:['Continue to Packing List','Packing List로 이어가기'],pdf:['Open Invoice PDF','Invoice PDF 열기'],retrySave:['The save response was interrupted. Check your Invoice list before saving again to avoid duplicates.','저장 결과를 확인하지 못했습니다. 중복 저장을 피하려면 Invoice 목록을 먼저 확인하세요.'],checkInvoices:['Check Invoice list','Invoice 목록 확인'],saveFailed:['Could not save. Your draft remains here.','저장하지 못했습니다. 초안은 이 화면에 남아 있습니다.'],session:['Please sign in again, then reopen this page.','다시 로그인한 뒤 이 화면을 열어 주세요.'],limit:['Your document allowance is exhausted or billing status is unavailable. Your draft is still here.','문서 한도를 사용했거나 구독 상태를 확인할 수 없습니다. 초안은 그대로 있습니다.'],
 too_large:['Use a file up to 8 MB, 5 PDF pages and 100 items.','8MB·PDF 5쪽·품목 100개 이하의 파일을 선택하세요.'],too_many_items:['This file has more than 100 items. Split it into smaller files.','품목 100개를 초과합니다. 파일을 나누어 주세요.'],unsupported:['Use XLSX, CSV, PDF, JPG, PNG or WebP. Convert old XLS or HEIC first.','XLSX·CSV·PDF·JPG·PNG·WebP를 선택하세요. XLS·HEIC는 먼저 변환해 주세요.'],invalid_file:['We could not read this file. Try a clear, upright photo or an unprotected PDF/XLSX.','파일을 읽지 못했습니다. 글자가 선명하고 바르게 선 사진이나 암호 없는 PDF·XLSX로 다시 시도하세요.'],encoding:['Save the CSV as UTF-8 and retry.','CSV를 UTF-8로 저장한 뒤 다시 시도하세요.'],encrypted:['Remove the file password before importing.','암호를 해제한 파일로 다시 시도하세요.'],no_items:['Item columns were not recognized. Compare the source and add the missing items; nothing has been saved.','품목 열을 인식하지 못했습니다. 원본을 보며 누락 품목을 보완해 주세요. 아직 저장되지 않았습니다.'],formulas_skipped:['Formula cells were left empty. Use an Excel copy with values pasted, or check the empty values here.','수식 셀은 비웠습니다. 엑셀에서 값으로 붙여넣은 사본을 사용하거나 빈 값을 확인하세요.'],unrecognized_rows:['Some rows could not be assigned. Check the complete source for missing items.','일부 행을 품목에 연결하지 못했습니다. 원본 전체에서 누락 품목을 확인하세요.'],duplicate_headers:['Repeated column names were ambiguous. Check the item table.','같은 이름의 열이 여러 개여서 구분하지 못했습니다. 품목을 확인하세요.'],conflict:['The file contains conflicting fields. Check the source; values were not silently combined.','파일에 서로 다른 값이 있습니다. 원본을 확인하세요. 임의로 합치지 않았습니다.'],amount:['Quantity × unit price differs from the source amount. Check this item.','수량 × 단가가 원본 금액과 다릅니다. 이 품목을 확인하세요.'],partial:['Check every PDF page for omitted items, especially text inside images.','PDF의 모든 쪽에서 누락된 품목이 없는지 확인하세요. 이미지 안의 글자는 빠질 수 있습니다.'],total:['Calculated total','계산된 합계'],replace:['Replace this unsaved draft?','저장하지 않은 초안을 바꿀까요?'],ocrUnavailable:['Photo reading failed. Your file was not stored. Retry with a clearer photo or a PDF with selectable text.','사진 인식에 실패했습니다. 원본은 저장하지 않았습니다. 더 선명한 사진이나 글자를 선택할 수 있는 PDF로 시도하세요.']};
Object.assign(words,{
 draftFound:['Continue your unfinished draft?','작성하던 초안을 이어서 쓸까요?'],
 draftRestore:['Continue draft','초안 이어쓰기'],draftDiscard:['Start a new draft','새로 시작'],draftClear:['Remove browser copy','브라우저 보관본 지우기'],
 draftPrivacy:['Draft text is kept in this browser for recovery within 7 days. Original files and audio are not stored.','작성 중인 글자·초안은 이 브라우저에서 7일 이내 복구할 수 있습니다. 원본 파일·음성은 보관하지 않습니다.'],
 draftStored:['Draft kept in this browser.','이 브라우저에 초안을 보관했습니다.'],
 draftUnavailable:['Browser storage is unavailable. Keep this page open or copy your text before leaving.','브라우저에 보관하지 못했습니다. 이 화면을 유지하거나 이동 전 내용을 복사하세요.'],
 draftConflict:['This draft changed in another tab. Copy any new edits, then reload to continue with the latest draft.','다른 탭에서 초안이 변경되었습니다. 새로 적은 내용을 복사한 뒤 새로고침하여 최신 초안을 이어 쓰세요.'],
 draftConfirmClear:['Remove this browser copy and start over? Saved documents will stay unchanged.','브라우저 보관본을 지우고 새로 시작할까요? 저장된 문서는 유지됩니다.'],
 draftRestored:['Draft restored. Recheck all values before saving; original file previews are not retained.','초안을 복구했습니다. 저장 전에 내용을 다시 확인하세요. 원본 파일 미리보기는 보관하지 않습니다.'],
 draftTime:['Last kept: ','마지막 보관: '],draftPending:['A previous save may have completed. Check the Invoice list before creating another document.','이전 저장이 완료됐을 수 있습니다. 다른 문서를 만들기 전에 Invoice 목록을 확인하세요.'],

 sourceFile:['File or photo','파일·사진'],sourceNote:['Order note or voice','주문 메모·음성'],
 noteTitle:['Have an order note? Start here.','주문 메모가 있다면 바로 시작하세요'],
 noteHint:['Type or dictate one item per line, then review the draft. No document is saved at this step.','한 줄에 한 품목씩 적거나 말하고 초안을 확인하세요. 이 단계에서는 문서를 저장하지 않습니다.'],
 noteLabel:['Buyer and order details','거래처와 주문 내용'],
 noteExample:['Example: Buyer: ABC Ltd; Cotton shirts 100 PCS at USD 8.50; Steel bolts 50 PCS at USD 2','예: 거래처: ABC사; 머그컵 100개 개당 USD 3; 접시 50개 개당 USD 2'],
 noteBuild:['Create a draft from this note','메모로 초안 채우기'],noteSample:['Fill a sample note','예시 넣기'],
 noteReading:['Preparing your order draft…','주문 초안을 채우고 있습니다…'],
 noteSource:['Order note','주문 메모'],
 note_review:['Check the original note and every item. Only the shown formats are recognized; unsupported phrases are left for review.','원본 메모와 모든 품목을 확인하세요. 예시 형식으로 인식하며, 해석하지 못한 표현은 확인이 필요합니다.'],
 note_unmatched:['Some lines could not be read. Add missing details from the original note before saving.','읽지 못한 줄이 있습니다. 저장 전에 아래 원본에서 빠진 내용을 보완하세요.'],
 note_currency:['Currency is ambiguous or mixed. Confirm one currency and re-enter the unit prices; no conversion was performed.','통화가 불명확하거나 섞여 있습니다. 통화와 단가를 다시 입력하세요. 임의로 환산하지 않았습니다.'],
 empty_note:['Enter your order note first.','주문 내용을 먼저 입력하세요.'],note_too_large:['Keep your note within 8,000 characters.','메모는 8,000자 이내로 입력하세요.'],
 voiceStart:['Speak your order','말로 입력'],voiceStop:['Finish speaking','음성 입력 마치기'],voiceLanguage:['Spoken language','말할 언어'],
 voicePrivacy:['Voice input depends on your browser. After you allow the microphone, your browser may send audio to its speech service. You can always type or use keyboard dictation.','음성 입력은 브라우저 지원에 따라 달라집니다. 마이크를 허용하면 브라우저의 음성 서비스로 소리가 전달될 수 있습니다. 직접 입력하거나 키보드 받아쓰기도 사용할 수 있습니다.'],
 voiceListening:['Listening… Say one item at a time. Review the text before creating a draft.','듣고 있습니다… 품목을 하나씩 말하세요. 초안을 만들기 전에 인식된 글자를 확인하세요.'],
 voiceUnsupported:['Use typing or your keyboard’s dictation on this browser.','이 브라우저에서는 직접 입력하거나 키보드의 받아쓰기를 사용하세요.'],
 voiceDenied:['Microphone access was not allowed. You can keep typing.','마이크 사용이 허용되지 않았습니다. 직접 입력할 수 있습니다.'],
 voiceError:['Voice input stopped. Keep the text already recognized and continue typing.','음성 입력이 중단되었습니다. 인식된 내용은 유지되며 이어서 입력할 수 있습니다.'],
 voiceDone:['Check the recognized text, numbers and line breaks before creating a draft.','초안을 만들기 전에 인식된 글자·숫자와 품목별 줄바꿈을 확인하세요.'],
 voiceEmpty:['No speech was recognized. Try again or type your note.','말소리를 인식하지 못했습니다. 다시 시도하거나 직접 입력하세요.'],

 reviewIssues:['Check these fields','이 항목을 확인해 주세요'],
 reviewIssuesHint:['Choose an item to go straight to the field.','항목을 누르면 해당 입력칸으로 바로 이동합니다.'],
 requiredValue:['Enter a value.','값을 입력해 주세요.'],
 reviewNext:['Go to the first field to check','첫 확인 항목으로 이동'],
 fieldsToCheck:['Fields to check: ','확인할 항목: '],
 moreIssues:[' more to check. This list updates as you correct them.','개 항목이 더 있습니다. 수정하면 다음 항목이 표시됩니다.'],
 itemLabel:['Item','품목'],
 emptyItems:['Add at least one item.','품목을 하나 이상 추가해 주세요.']
});
const t=key=>(words[key]||words.invalid_file)[ko?1:0];
document.querySelectorAll('[data-i18n]').forEach(e=>e.textContent=t(e.dataset.i18n));
document.title=t('title')+' · Trade Paper AI';$('original').alt=t('source');
let file=null, busy=false, previewURL=null, draftReady=false, saved=false, saveUncertain=false, worker=null, saving=false, selectedSheet=0, voiceActive=false, noteOpening=false, recovering=false, pendingRecovery=false;
let activeWarnings=[], activeUnmatched=[], sourceLabel='', autoSaveTimer=null;
let browserStorage=null;try{browserStorage=window.localStorage;}catch(error){}
const draftStore=window.TPImportDraft?.create(browserStorage,config.draft_scope)||{load:()=>null,write:()=>({ok:false,reason:'unavailable'}),clear:()=>false};
const recoveredDraft=draftStore.load();

function setStep(step){document.querySelectorAll('[data-step]').forEach(el=>{const n=Number(el.dataset.step);el.classList.toggle('complete',n<step);if(n===step)el.setAttribute('aria-current','step');else el.removeAttribute('aria-current');});}
function setStatus(key,error=false){$('status').textContent=t(key);$('status').dataset.kind=error?'error':'info';}
function setBusy(value){value=value||pendingRecovery||saveUncertain;busy=value;$('file').disabled=value;$('sheet').disabled=value;$('sample').disabled=value;$('drop-zone').setAttribute('aria-busy',String(value));$('draft-clear').disabled=value||saving||saveUncertain;for(const id of ['note-build','note-sample','order-note','voice-language','source-file','source-note'])$(id).disabled=value;$('note-mic').disabled=value&&!voiceActive;}

const fields=['seller','seller_address','buyer','buyer_address','buyer_email','invoice_date','currency','trade_term','payment_term','source_reference'];
const itemFields=['name','quantity','unit','unit_price','hs_code','origin'];
const required=new Set(['seller','buyer','invoice_date','currency','name','quantity','unit_price']);
function makeInput(key,value=''){
 const label=document.createElement('label');label.textContent=t(key);const input=document.createElement('input');input.dataset.field=key;input.value=String(value??'');input.maxLength=key.includes('address')?1000:200;
 if(required.has(key))input.required=true;
 if(key==='invoice_date')input.type='date';
 if(key==='buyer_email')input.type='email';
 if(key==='currency'){input.pattern='[A-Z]{3}';input.maxLength=3;input.addEventListener('input',()=>input.value=input.value.toUpperCase());}
 if(['quantity','unit_price'].includes(key)){input.type='number';input.min='0';input.max='1000000000000';input.step='any';}
 input.addEventListener('input',()=>{$('confirmed').checked=false;updateTotal();});label.append(input);return label;
}
function addItem(values={}){
 if($('items').children.length>=100)return;
 const article=document.createElement('article');article.className='item';const top=document.createElement('div');top.className='item-top';const title=document.createElement('b');title.textContent=t('items')+' '+($('items').children.length+1);const remove=document.createElement('button');remove.type='button';remove.textContent=t('remove');remove.addEventListener('click',()=>{article.remove();$('confirmed').checked=false;updateTotal();});top.append(title,remove);article.append(top,makeInput('name',values.name));const grid=document.createElement('div');grid.className='grid';for(const key of itemFields.slice(1))grid.append(makeInput(key,values[key]));article.append(grid);
 if(values.check_amount){const p=document.createElement('p');p.textContent=t('amount');p.style.color='#875a13';article.append(p);} $('items').append(article);updateTotal();
}
function updateTotal(){let total=0;for(const row of $('items').children){const q=Number(row.querySelector('[data-field=quantity]').value),p=Number(row.querySelector('[data-field=unit_price]').value);if(Number.isFinite(q*p))total+=q*p;}const currency=$('fields').querySelector('[data-field=currency]');$('total').textContent=t('total')+': '+(currency?.value||'')+' '+total.toLocaleString(ko?'ko-KR':'en-US',{maximumFractionDigits:6});$('item-count').textContent=$('items').children.length+(ko?'개 품목':($('items').children.length===1?' item':' items'));[...$('items').children].forEach((row,i)=>row.querySelector('b').textContent=t('items')+' '+(i+1));updateReviewIssues();}
function reviewIssues(){
 const rows=[...$('items').children],issues=[];
 for(const input of $('review-form').querySelectorAll('input:not([type=checkbox])')){
  const blank=input.required&&!input.value.trim();
  input.setCustomValidity(blank?t('requiredValue'):'');
  const invalid=!input.validity.valid;
  input.classList.toggle('missing',invalid);
  if(invalid)input.setAttribute('aria-invalid','true');else input.removeAttribute('aria-invalid');
  if(!invalid)continue;
  const row=input.closest('.item');
  const prefix=row?t('itemLabel')+' '+(rows.indexOf(row)+1)+' · ':'';
  issues.push({label:prefix+t(input.dataset.field),target:input});
 }
 if(!rows.length)issues.push({label:t('emptyItems'),target:$('add')});
 return issues;
}
function focusIssue(target){
 target.scrollIntoView({block:'center',behavior:'auto'});
 target.focus({preventScroll:true});
}
function updateReviewIssues(){
 const issues=reviewIssues();
 $('review-state').textContent=issues.length?t('fieldsToCheck')+issues.length:t('reviewAll');
 $('review-issues').hidden=!issues.length;
 $('review-next').hidden=!issues.length;
 $('review-issue-list').replaceChildren();
 for(const issue of issues.slice(0,5)){
  const li=document.createElement('li'),button=document.createElement('button');
  button.type='button';button.className='review-issue-link';button.textContent=issue.label;
  button.addEventListener('click',()=>focusIssue(issue.target));
  li.append(button);$('review-issue-list').append(li);
 }
 $('review-more').hidden=issues.length<=5;
 $('review-more').textContent=issues.length>5?String(issues.length-5)+t('moreIssues'):'';
}
$('review-next').addEventListener('click',()=>{const issue=reviewIssues()[0];if(issue)focusIssue(issue.target);});
function renderDraft(result,extra=[]){
 activeWarnings=[...(result.warnings||[]),...extra];activeUnmatched=result.unmatched_lines||[];if(result.items.some(item=>item.check_amount))activeWarnings.push('amount');sourceLabel=result.source_label||file?.name||t('noteSource');
 const values=result.fields||{};if(!result.restored&&!values.seller&&!(result.warnings||[]).includes('conflicting_seller')){values.seller=config.company.name||'';values.seller_address=values.seller_address||config.company.address||'';}
 $('fields').replaceChildren();fields.forEach(key=>$('fields').append(makeInput(key,values[key])));$('items').replaceChildren();(result.items.length?result.items:[{}]).forEach(addItem);
 $('source-text').textContent=result.source_text||'';$('warnings').textContent=[...new Set([...(result.warnings||[]),...extra])].map(key=>t(key.startsWith('conflicting_')?'conflict':key)).join('\n');
 $('workspace').hidden=false;$('review-hint').hidden=false;$('review-heading').textContent=t('review');$('review-form').hidden=false;$('saved').hidden=true;$('confirmed').checked=false;$('save').disabled=false;draftReady=true;saved=false;saveUncertain=false;updateTotal();setStatus('ready');setStep(2);$('file-name').textContent=sourceLabel;$('file-name').hidden=false;$('review-heading').focus({preventScroll:true});scheduleDraft();
}
async function parseResponse(response){if(response.redirected||response.status===401||response.status===403)throw new Error('session');if(!response.headers.get('content-type')?.includes('application/json'))throw new Error('invalid_file');const data=await response.json();if(!response.ok)throw new Error(data.detail||'invalid_file');return data;}
const positionedText=window.TPImportLayout.positionedText;
async function loadOCR(){if(window.Tesseract)return;await new Promise((resolve,reject)=>{const s=document.createElement('script');s.src='/static/vendor/tesseract/tesseract.min.js';s.onload=resolve;s.onerror=()=>reject(new Error('ocrUnavailable'));document.head.append(s);});}
async function readImage(source){
 await loadOCR();if(!worker)worker=await Tesseract.createWorker('eng+kor',1,{workerPath:'/static/vendor/tesseract/worker.min.js',corePath:'/static/vendor/tesseract',langPath:'/static/vendor/tesseract',logger:m=>{$('status').textContent=t('ocr')+(m.status==='recognizing text'?' '+Math.round(m.progress*100)+'%':'');}});
 const result=await withTimeout(worker.recognize(source,{}, {text:true,blocks:true}),120000);const ws=[];for(const b of result.data.blocks||[])for(const p of b.paragraphs||[])for(const l of p.lines||[])for(const w of l.words||[])ws.push({text:w.text,x:w.bbox.x0,y:w.bbox.y0,w:w.bbox.x1-w.bbox.x0,h:w.bbox.y1-w.bbox.y0});return ws.length?positionedText(ws):result.data.text;
}
function withTimeout(promise,ms){let timer;return Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('ocrUnavailable')),ms);})]).finally(()=>clearTimeout(timer));}
async function imageCanvas(blob){const bitmap=await createImageBitmap(blob);try{if(bitmap.width*bitmap.height>30_000_000)throw new Error('too_large');const scale=Math.min(1,2400/Math.max(bitmap.width,bitmap.height));const canvas=document.createElement('canvas');canvas.width=Math.round(bitmap.width*scale);canvas.height=Math.round(bitmap.height*scale);canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height);return canvas;}finally{bitmap.close();}}
async function pdfText(blob){
 const pdfjs=await import('/static/vendor/pdfjs/build/pdf.mjs');pdfjs.GlobalWorkerOptions.workerSrc='/static/vendor/pdfjs/build/pdf.worker.mjs';
 const task=pdfjs.getDocument({data:new Uint8Array(await blob.arrayBuffer()),isEvalSupported:false,enableXfa:false,cMapUrl:'/static/vendor/pdfjs/cmaps/',cMapPacked:true,standardFontDataUrl:'/static/vendor/pdfjs/standard_fonts/',maxImageSize:12_000_000});
 task.onPassword=()=>{task.destroy();};let pdf;
 try{pdf=await task.promise;if(pdf.numPages>5)throw new Error('too_large');let text='',extra=[];
 for(let n=1;n<=pdf.numPages;n++){const page=await pdf.getPage(n);const content=await page.getTextContent();const ws=content.items.filter(i=>typeof i.str==='string').map(i=>({text:i.str,x:i.transform[4],y:-i.transform[5],w:i.width,h:i.height}));let lineText=positionedText(ws);
 if(n===1||lineText.trim().length<30){const base=page.getViewport({scale:1});const scale=Math.min(2,2000/Math.max(base.width,base.height));const viewport=page.getViewport({scale});const canvas=document.createElement('canvas');canvas.width=Math.ceil(viewport.width);canvas.height=Math.ceil(viewport.height);await page.render({canvasContext:canvas.getContext('2d'),viewport}).promise;if(n===1){$('original').src=canvas.toDataURL('image/jpeg',.8);$('original').hidden=false;}if(lineText.trim().length<30)lineText=await readImage(canvas);canvas.width=canvas.height=0;}
 text+=lineText+'\n';if(text.length>200000)throw new Error('too_large');page.cleanup();}
 extra.push('partial');return {text,extra};
 }finally{if(pdf)await pdf.destroy();else await task.destroy();}
}
async function readFile(sheet=0){
 if(!file||busy||saving)return false;const previous={src:$('original').getAttribute('src'),hidden:$('original').hidden,sheets:[...$('sheet').options].map(o=>({value:o.value,text:o.textContent})),sheetHidden:$('sheet-box').hidden};setBusy(true);$('workspace').hidden=true;setStatus('reading');
 try{if(file.size>8*1024*1024)throw new Error('too_large');let result,extra=[];const ext=file.name.split('.').pop().toLowerCase();
 if(['csv','xlsx'].includes(ext)){const form=new FormData();form.append('file',file);form.append('sheet',String(sheet));result=await parseResponse(await fetch('/import-document/extract',{method:'POST',body:form}));$('sheet-box').hidden=(result.sheets||[]).length<2;if(sheet===0){$('sheet').replaceChildren();(result.sheets||[]).forEach((name,i)=>{const o=document.createElement('option');o.value=i;o.textContent=name;$('sheet').append(o);});}$('original').hidden=true;}
 else{let text;$('sheet-box').hidden=true;if(ext==='pdf'){const data=await pdfText(file);text=data.text;extra=data.extra;}else if(['jpg','jpeg','png','webp'].includes(ext)){const canvas=await imageCanvas(file);previewURL=URL.createObjectURL(file);$('original').src=previewURL;$('original').hidden=false;text=await readImage(canvas);canvas.width=canvas.height=0;}else throw new Error('unsupported');result=await parseResponse(await fetch('/import-document/extract',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})}));}
 renderDraft(result,extra);if(previous.src?.startsWith('blob:')&&previous.src!==$('original').src)URL.revokeObjectURL(previous.src);selectedSheet=sheet;return true;
 }catch(e){setStatus(e.message,true);if($('original').src.startsWith('blob:')&&$('original').src!==previous.src)URL.revokeObjectURL($('original').src);$('workspace').hidden=!draftReady;if(previous.src)$('original').src=previous.src;else $('original').removeAttribute('src');$('original').hidden=previous.hidden;$('sheet').replaceChildren();previous.sheets.forEach(o=>{const option=document.createElement('option');option.value=o.value;option.textContent=o.text;$('sheet').append(option);});$('sheet').value=String(selectedSheet);$('sheet-box').hidden=previous.sheetHidden;return false;}finally{try{if(worker)await worker.terminate();}finally{worker=null;setBusy(false);}}
}
function confirmReplacement(){return new Promise(resolve=>{const dialog=$('replace-dialog');dialog.returnValue='cancel';dialog.addEventListener('close',()=>resolve(dialog.returnValue==='replace'),{once:true});dialog.showModal();});}
async function chooseFile(next){
 if(!next||busy||saving||saveUncertain||noteOpening)return false;
 if(draftReady&&!saved&&!await confirmReplacement())return false;
 const previous=file;file=next;const success=await readFile();if(!success)file=previous;return success;
}
for(const mode of ['file','note'])$('source-'+mode).addEventListener('click',()=>{
 if(busy||saving||noteOpening)return;
 $('drop-zone').hidden=mode!=='file';$('note-panel').hidden=mode!=='note';
 for(const value of ['file','note'])$('source-'+value).setAttribute('aria-pressed',String(value===mode));
});
$('order-note').placeholder=ko?'거래처: ABC사\n머그컵 100개 개당 USD 3\n접시 50개 개당 USD 2':'Buyer: ABC Ltd\nCotton shirts 100 PCS at USD 8.50\nSteel bolts 50 PCS at USD 2';
$('note-sample').addEventListener('click',()=>{if(!$('order-note').value.trim())$('order-note').value=$('order-note').placeholder;$('order-note').focus();scheduleDraft();});
$('note-build').addEventListener('click',async()=>{
 if(busy||saving||saveUncertain||noteOpening)return;
 const text=$('order-note').value.trim();if(!text){setStatus('empty_note',true);$('order-note').focus();return;}
 noteOpening=true;
 try{
  if(draftReady&&!saved&&!await confirmReplacement())return;
  if(busy||saving||saveUncertain)return;
  setBusy(true);setStatus('noteReading');
  const result=await parseResponse(await fetch('/import-document/extract',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:'order_note',text})}));
  if(draftReady&&!result.items.length)throw new Error('no_items');
  file=null;renderDraft(result);
  if(previewURL){URL.revokeObjectURL(previewURL);previewURL=null;}
  $('original').removeAttribute('src');$('original').hidden=true;$('sheet-box').hidden=true;
  if(result.unmatched_lines?.length){const p=document.createElement('p');p.textContent=result.unmatched_lines.join('\n');$('warnings').append(p);}
  $('review-heading').scrollIntoView({block:'start',behavior:'auto'});
 }catch(e){setStatus(e.message,true);}finally{noteOpening=false;setBusy(false);}
});
$('voice-language').value=ko?'ko-KR':'en-US';
const voice=(window.TPVoiceInput?.create||(()=>({supported:false})))({
 getLanguage:()=>$('voice-language').value,
 getText:()=>$('order-note').value,
 onText:text=>{$('order-note').value=text;scheduleDraft();},
 onState:active=>{voiceActive=active;setBusy(active);$('note-mic').textContent=t(active?'voiceStop':'voiceStart');$('note-mic').setAttribute('aria-pressed',String(active));},
 onMessage:key=>{$('voice-status').textContent=t(key);}
});
$('note-mic').hidden=!voice.supported;$('voice-language-label').hidden=!voice.supported;
if(!voice.supported)$('voice-status').textContent=t('voiceUnsupported');
$('note-mic').addEventListener('click',()=>{if(voiceActive)voice.stop();else if(!busy&&!saving&&!saveUncertain&&!noteOpening)voice.start();});
$('file').addEventListener('change',async()=>{await chooseFile($('file').files[0]);$('file').value='';});
$('sheet').addEventListener('change',async()=>{if(saveUncertain||saving||busy){$('sheet').value=String(selectedSheet);return;}if(draftReady&&!saved&&!await confirmReplacement()){$('sheet').value=String(selectedSheet);return;}readFile(Number($('sheet').value));});
$('sample').addEventListener('click',async()=>{
 const csv='Seller,Sample Export Ltd\nBuyer,Example Buyer Ltd\nInvoice Date,'+new Date().toISOString().slice(0,10)+'\nCurrency,USD\nDescription,Qty,Unit,Unit Price,Amount\nCotton shirts,100,PCS,8.50,850\n';
 const loaded=await chooseFile(new File([csv],'fictional-shipment.csv',{type:'text/csv'}));
 if(loaded&&draftReady&&!saved)setStatus('sampleNotice');
});
for(const event of ['dragenter','dragover'])$('drop-zone').addEventListener(event,e=>{e.preventDefault();if(!busy&&!saving)$('drop-zone').classList.add('drag-over');});
$('drop-zone').addEventListener('dragleave',e=>{if(!$('drop-zone').contains(e.relatedTarget))$('drop-zone').classList.remove('drag-over');});
$('drop-zone').addEventListener('drop',e=>{e.preventDefault();$('drop-zone').classList.remove('drag-over');if(e.dataTransfer.files.length===1)chooseFile(e.dataTransfer.files[0]);else setStatus('oneFile',true);});
$('add').addEventListener('click',()=>{const count=$('items').children.length;addItem();$('confirmed').checked=false;if($('items').children.length>count)focusIssue($('items').lastElementChild.querySelector('input'));});
$('review-form').addEventListener('submit',async event=>{
 event.preventDefault();if(saved||saveUncertain||saving||busy||!$('review-form').reportValidity())return;if(!$('items').children.length){$('status').textContent=t('no_items');return;}if(draftStore.conflict){showStorageResult({ok:false,reason:'conflict'});return;}saving=true;persistDraft('saving');if(draftStore.conflict){saving=false;return;}$('save').disabled=true;setStatus('saving');const payload={};$('fields').querySelectorAll('input').forEach(i=>payload[i.dataset.field]=i.value.trim());payload.items=[...$('items').children].map(row=>Object.fromEntries([...row.querySelectorAll('input')].map(i=>[i.dataset.field,i.value.trim()])));let response;const controls=[...$('review-form').querySelectorAll('input,button')];controls.forEach(el=>el.disabled=true);setBusy(true);
 try{response=await fetch('/invoice',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});}catch(e){saving=false;setBusy(false);saveUncertain=true;persistDraft('uncertain');setBusy(false);setStatus('retrySave',true);const a=document.createElement('a');a.href='/invoice-list';a.textContent=t('checkInvoices');$('status').append(document.createElement('br'),a);return;}
 if(response.status>=500&&!response.redirected){saving=false;showUncertainSave();persistDraft('uncertain');setBusy(false);return;}
 if(!response.ok||response.redirected){saving=false;setBusy(false);controls.forEach(el=>el.disabled=false);$('status').textContent=response.redirected?t('session'):([402,503].includes(response.status)?t('limit'):t('saveFailed'));$('save').disabled=false;persistDraft('draft');return;}
 try{const record=await response.json();if(!record.invoice_no)throw Error();saved=true;window.clearTimeout(autoSaveTimer);draftStore.clear();$('draft-storage-status').textContent='';if(window.tpMarkSaved)window.tpMarkSaved();const id=encodeURIComponent(record.invoice_no);$('review-form').hidden=true;$('review-hint').hidden=true;$('review-heading').textContent=t('stepSaved');$('saved').replaceChildren();const mark=document.createElement('span');mark.className='success-mark';mark.textContent='✓';mark.setAttribute('aria-hidden','true');const h=document.createElement('h3');h.textContent=t('saved');const number=document.createElement('p');number.textContent=t('savedNumber')+' · '+record.invoice_no;const hint=document.createElement('p');hint.textContent=t('nextHint');$('saved').append(mark,h,number,hint);const actions=document.createElement('div');actions.className='actions';for(const [label,url]of [['packing','/packing-page?invoice_no='+id],['pdf','/invoice-pdf/'+id],['edit','/edit-invoice/'+id]]){const a=document.createElement('a');a.textContent=t(label);a.className=label==='packing'?'button next-action':'button secondary';a.href=url+((url.includes('?')?'&':'?')+'lang='+config.lang);actions.append(a);}$('saved').append(actions);$('saved').hidden=false;setStatus('saved');setStep(3);$('saved').focus();window.location.assign('/packing-page?invoice_no='+id+'&lang='+encodeURIComponent(config.lang));}

 catch(e){saveUncertain=true;persistDraft('uncertain');$('save').disabled=true;setStatus('retrySave',true);const a=document.createElement('a');a.href='/invoice-list';a.textContent=t('checkInvoices');$('status').append(document.createElement('br'),a);}
 finally{saving=false;setBusy(false);if(!saveUncertain)controls.forEach(el=>el.disabled=false);}
});
function captureDraft(status='draft'){
 const result=draftReady?{
  fields:Object.fromEntries([...$('fields').querySelectorAll('input')].map(input=>[input.dataset.field,input.value])),
  items:[...$('items').children].map(row=>Object.fromEntries([...row.querySelectorAll('input')].map(input=>[input.dataset.field,input.value]))),
  source_text:$('source-text').textContent,warnings:activeWarnings,unmatched_lines:activeUnmatched
 }:null;
 return {status,note:$('order-note').value,mode:$('note-panel').hidden?'file':'note',label:sourceLabel,result};
}
function showStorageResult(result){
 $('draft-storage-status').textContent=t(result.ok?'draftStored':result.reason==='conflict'?'draftConflict':'draftUnavailable');
 $('draft-clear').hidden=false;
 if(result.reason==='conflict')$('save').disabled=true;
}
function persistDraft(status){
 window.clearTimeout(autoSaveTimer);
 if(saved||pendingRecovery||recovering)return true;
 if(!draftReady&&!$('order-note').value.trim()){const removed=draftStore.clear();$('draft-storage-status').textContent='';$('draft-clear').hidden=true;return removed;}
 const result=draftStore.write(captureDraft(status||(saveUncertain?'uncertain':saving?'saving':'draft')));showStorageResult(result);return result.ok;
}
function scheduleDraft(){
 if(saved||pendingRecovery||recovering)return;
 window.clearTimeout(autoSaveTimer);autoSaveTimer=window.setTimeout(()=>persistDraft(),350);
}
function showUncertainSave(){
 saveUncertain=true;$('save').disabled=true;
 $('review-form').querySelectorAll('input,button').forEach(input=>input.disabled=true);
 setStatus('draftPending',true);const link=document.createElement('a');link.href='/invoice-list';link.textContent=t('checkInvoices');$('status').append(document.createElement('br'),link);
}
function discardBrowserDraft(){
 if(!window.confirm(t('draftConfirmClear')))return;
 window.clearTimeout(autoSaveTimer);
 if(!draftStore.clear()){showStorageResult({ok:false,reason:draftStore.conflict?'conflict':'unavailable'});return;}
 saved=true;window.location.reload();
}
$('draft-clear').addEventListener('click',discardBrowserDraft);
$('draft-discard').addEventListener('click',discardBrowserDraft);
$('draft-restore').addEventListener('click',()=>{
 if(!recoveredDraft)return;
 recovering=true;pendingRecovery=false;setBusy(false);
 $('order-note').value=recoveredDraft.note;
 $('drop-zone').hidden=recoveredDraft.mode!=='file';$('note-panel').hidden=recoveredDraft.mode!=='note';
 for(const mode of ['file','note'])$('source-'+mode).setAttribute('aria-pressed',String(mode===recoveredDraft.mode));
 if(recoveredDraft.result){file=null;renderDraft({...recoveredDraft.result,source_label:recoveredDraft.label,restored:true});
  if(activeUnmatched.length){const p=document.createElement('p');p.textContent=activeUnmatched.join('\n');$('warnings').append(p);}
 }
 $('draft-recovery').hidden=true;recovering=false;
 if(recoveredDraft.status!=='draft')showUncertainSave();else setStatus('draftRestored');
 showStorageResult({ok:true});setBusy(false);
});
$('order-note').addEventListener('input',scheduleDraft);
$('review-form').addEventListener('input',scheduleDraft);
$('review-form').addEventListener('change',scheduleDraft);
$('review-form').addEventListener('click',event=>{if(event.target.closest('#add,.item-top button'))scheduleDraft();});
window.addEventListener('pagehide',()=>persistDraft());
document.addEventListener('visibilitychange',()=>{if(document.hidden)persistDraft();});
if(recoveredDraft){
 pendingRecovery=true;setBusy(true);$('draft-recovery').hidden=false;
 $('draft-recovery-info').textContent=t('draftTime')+new Date(recoveredDraft.updated).toLocaleString(ko?'ko-KR':'en-US')+(recoveredDraft.status==='draft'?'':' — '+t('draftPending'));
}
window.addEventListener('beforeunload',event=>{const kept=persistDraft();if((!kept||saving)&&!saved&&(draftReady||$('order-note').value.trim())){event.preventDefault();event.returnValue='';}});
})();
