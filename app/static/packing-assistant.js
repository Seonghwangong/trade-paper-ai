(() => {
  'use strict';
  const ko = () => document.documentElement.lang === 'ko';
  const text = (en, kr) => ko() ? kr : en;
  const find = (row, name) => row.querySelector(`[name="${name === 'name' ? 'item_name' : name}"], .${name === 'name' ? 'item' : name}`);
  const identity = row => Object.fromEntries(['name','hs_code','origin','unit'].map(k => [k, (find(row,k)?.value || '').trim()]));
  let saved = [];
  const ready = fetch('/packing-defaults').then(r => r.ok ? r.json() : []).then(data => { saved = data; }).catch(() => {});
  async function post(url, data) {
    const response = await fetch(url, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : text('Check the entered numbers.','입력한 숫자를 확인해 주세요.'));
    return result;
  }
  const boundRows = new WeakSet();
  function attach(row) {
    if (boundRows.has(row) || !find(row,'quantity') || !find(row,'carton')) return;
    boundRows.add(row);
    row.querySelectorAll('.packing-calculator').forEach(el=>el.remove());
    const box = document.createElement('fieldset');
    box.className = 'packing-calculator';
    box.style.cssText = 'margin:18px 0;padding:16px;border:1px solid #93c5fd;border-radius:12px;background:#eff6ff';
    const legend = document.createElement('legend');
    legend.textContent = text('Packing calculator (kg)','포장 계산기 (kg)'); box.append(legend);
    const note = document.createElement('p');
    note.textContent = text('Use your actual packing specification. Each carton uses the same packaging weight, including the last partial carton. Recalculate after quantity changes.','실제 포장 기준을 입력하세요. 마지막 미완성 박스도 동일한 포장재 무게로 계산합니다. 수량 변경 후 다시 계산해 주세요.'); box.append(note);
    const inputs = {};
    for (const [key, en, kr] of [['units_per_carton','Units per carton','박스당 수량'],['net_per_unit','Net kg per unit','단위당 순중량 (kg)'],['tare_per_carton','Packaging kg per carton','박스당 포장재 무게 (kg)']]) {
      const label = document.createElement('label'); label.textContent = text(en, kr);
      const input = document.createElement('input'); input.type='number'; input.min='0'; input.step='any'; input.dataset.calculator=key;
      input.setAttribute('aria-label',label.textContent); label.append(input); box.append(label); inputs[key]=input;
    }
    const status = document.createElement('p'); status.setAttribute('role','status');
    const filledMessage = () => text('Totals filled. Check against the actual packed goods before saving.','합계를 채웠습니다. 실제 포장 결과와 비교한 후 저장하세요.');
    const values = () => ({...identity(row), quantity:find(row,'quantity').value, ...Object.fromEntries(Object.entries(inputs).map(([k,v])=>[k,v.value]))});
    const totals = () => Object.fromEntries(['carton','net_weight','gross_weight'].map(k=>[k,find(row,k).value]));
    let lastCalculation = null;
    const review = document.createElement('p');
    review.className='packing-calculation-warning'; review.hidden=true;
    review.setAttribute('role','status'); review.setAttribute('aria-live','polite');
    review.style.cssText='padding:12px;border:1px solid #dfd3b9;border-radius:8px;background:#fffaf0;color:#705222';
    box.append(review);
    function refreshReview() {
      let message='';
      if(lastCalculation) {
        if(lastCalculation.inputs!==JSON.stringify(values())) {
          message=text('Quantity, item or packing settings changed after calculation. The totals have not been recalculated. Calculate again, or check and enter the actual packing totals before saving.','계산 후 수량·품목 또는 포장 기준이 바뀌었습니다. 합계는 다시 계산되지 않았습니다. 다시 계산하거나 실제 박스·중량을 확인해 직접 입력한 뒤 저장하세요.');
        } else if(lastCalculation.totals!==JSON.stringify(totals())) {
          message=text('The packing totals were edited after calculation. Check the entered values against the actual packed goods before saving.','계산 후 박스·중량을 직접 수정했습니다. 저장 전에 입력한 값을 실제 포장 결과와 확인하세요.');
        }
      }
      review.hidden=!message;
      if(review.textContent!==message)review.textContent=message;
      if(message && status.textContent===filledMessage())status.textContent='';
    }
    row.addEventListener('input',refreshReview);
    row.addEventListener('change',refreshReview);
    const button = (en, kr, action) => {
      const b = document.createElement('button'); b.type='button'; b.textContent=text(en,kr); b.style.margin='6px';
      b.addEventListener('click',async()=>{ b.disabled=true; try {await action();} catch(error) {status.textContent=error.message;} finally {b.disabled=false;} }); box.append(b);
    };
    button('Load saved packing settings','저장한 포장 기준 불러오기',async()=>{
      await ready; const key=identity(row);
      const match=saved.find(s=>Object.keys(key).every(k=>String(s[k]||'').trim()===key[k]));
      if(!match) {status.textContent=text('No saved settings for this item.','이 품목에 저장된 포장 기준이 없습니다.');return;}
      Object.keys(inputs).forEach(k=>{inputs[k].value=match[k];});
      refreshReview();
      status.textContent=text('Settings loaded. Check them, then calculate.','기준을 불러왔습니다. 확인한 후 계산해 주세요.');
    });
    button('Calculate and fill totals','계산해서 박스·중량 채우기',async()=>{
      const sent=values(), priorTotals=JSON.stringify(totals()), result=await post('/packing-calculate',sent);
      if(JSON.stringify(sent)!==JSON.stringify(values()) || priorTotals!==JSON.stringify(totals())) throw new Error(text('Values changed; calculate again.','입력값이 바뀌었습니다. 다시 계산해 주세요.'));
      for(const k of ['carton','net_weight','gross_weight']) {const input=find(row,k); input.value=result[k]; input.dispatchEvent(new Event('input',{bubbles:true}));}
      lastCalculation={inputs:JSON.stringify(sent),totals:JSON.stringify(totals())};
      refreshReview();
      status.textContent=filledMessage();
    });
    button('Save packing settings for this item','이 품목의 포장 기준 저장',async()=>{
      const data=values(); await post('/packing-defaults',data);
      saved=await fetch('/packing-defaults').then(r=>r.json());
      status.textContent=text('Packing settings saved in your account.','계정에 포장 기준을 저장했습니다.');
    });
    box.append(status); row.append(box);
  }
  function scan(){document.querySelectorAll('.item-card,.item-row,.packing-sync-item').forEach(attach);}
  scan(); new MutationObserver(scan).observe(document.body,{childList:true,subtree:true});
})();
