/* Small, source-linked HS6 guide. Stored product reuse stays within the signed-in account. */
(() => {
  'use strict';
  const form = document.querySelector('form[action="/save-product"],form[action^="/update-product/"]');
  if (!form) return;
  const name = form.elements.namedItem('name'), hs = form.elements.namedItem('hs_code');
  if (!name || !hs) return;
  const ko = document.documentElement.lang === 'ko' || new URLSearchParams(location.search).get('lang') === 'ko' || document.cookie.includes('tp_language=ko');
  const t = (en, kr) => ko ? kr : en;
  const node = (tag, text) => { const el = document.createElement(tag); if(text) el.textContent=text; return el; };
  const savedArea = node('div'); savedArea.setAttribute('aria-live','polite'); name.after(savedArea);
  let serial = 0, timer;
  const set = (field, value) => { field.value=String(value ?? ''); field.dispatchEvent(new Event('input',{bubbles:true})); field.dispatchEvent(new Event('change',{bubbles:true})); };
  function reuse(product) {
    const fields=['name','hs_code','unit_price','origin','unit'];
    const changed=fields.some(key => key!=='name' && form.elements.namedItem(key)?.value && String(form.elements.namedItem(key).value)!==String(product[key]??''));
    if(changed && !window.confirm(t('Replace the entered details with this saved product?', '입력한 내용을 이 저장 품목의 정보로 바꿀까요?'))) return;
    fields.forEach(key => {const field=form.elements.namedItem(key); if(field) set(field,product[key]);});
    serial++; savedArea.replaceChildren();
    savedArea.append(node('small', t('Saved product details loaded. Review the price and HS code for this order.', '저장한 품목 정보를 불러왔습니다. 이번 거래의 단가와 HS 코드를 확인하세요.')));
  }
  name.addEventListener('input', () => {
    clearTimeout(timer); const version=++serial; savedArea.replaceChildren(); result.replaceChildren(); select.value="";
    const query=name.value.trim(); if(query.length<2) return;
    timer=setTimeout(async () => {
      try {
        const response=await fetch('/product-suggestions?q='+encodeURIComponent(query));
        if(!response.ok) return;
        const products=await response.json(); if(version!==serial || !Array.isArray(products)) return;
        products.slice(0,5).forEach(product => {
          const button=node('button',t('Use saved: ','저장 품목 불러오기: ')+product.name+(product.hs_code?' · HS '+product.hs_code:''));
          button.type='button'; button.className='hs-saved'; button.addEventListener('click',()=>reuse(product)); savedArea.append(button);
        });
      } catch (_) { /* The ordinary form remains usable without suggestions. */ }
    },200);
  });
  const panel=node('details'); panel.className='hs-assistant';
  panel.append(node('summary',t('Find an HS code','HS 코드 찾기')));
  panel.append(node('p',t('Choose the description that matches the actual product. This guide currently covers laptops and plain ceramic or plastic tableware.','실제 품목에 맞는 설명을 선택하세요. 현재 노트북과 일반 도자기·플라스틱 식기를 안내합니다.')));
  const select=node('select'); select.id='hs-profile'; select.setAttribute('aria-label',t('Product characteristics','품목 특징'));
  const profiles=[
    {id:'',en:'Choose product characteristics',ko:'품목 특징 선택'},
    {id:'laptop',code:'847130',en:'Portable computer, up to 10 kg, with processor, keyboard and display',ko:'처리장치·키보드·화면이 있는 10kg 이하 휴대용 컴퓨터',query:'8471.30',source:'https://ids.usitc.gov/case/8315/investigation/8818'},
    {id:'porcelain',code:'691110',en:'Porcelain/china tableware or kitchenware, without electrical or vacuum features',ko:'자기·포슬린 식기 (전기·진공보온 기능 없음)',query:'6911.10',source:'https://rulings.cbp.gov/docs/hq/2025/h320944'},
    {id:'ceramic',code:'691200',en:'Other ceramic tableware, such as stoneware; not porcelain, electrical or vacuum-insulated',ko:'자기가 아닌 도기·석기 식기 (전기·진공보온 기능 없음)',query:'6912.00',source:'https://rulings.cbp.gov/docs/hq/2025/h320944'},
    {id:'plastic',code:'392410',en:'Plastic tableware or kitchenware, without electrical or vacuum features',ko:'플라스틱 식기 (전기·진공보온 기능 없음)',query:'3924.10',source:'https://www.gov.br/receitafederal/pt-br/assuntos/aduana-e-comercio-exterior/classificacao-fiscal-de-mercadorias/nesh-2022.pdf'},
    {id:'other',en:'Other / unsure',ko:'다른 품목 / 잘 모르겠음'}
  ];
  profiles.forEach(profile=>{const option=node('option',ko?profile.ko:profile.en); option.value=profile.id; select.append(option);});
  panel.append(select);
  const result=node('div'); result.setAttribute('aria-live','polite'); panel.append(result);
  select.addEventListener('change',()=>{
    result.replaceChildren(); const profile=profiles.find(p=>p.id===select.value); if(!profile?.id) return;
    if(!profile.code) {
      result.append(node('p',t('No verified candidate in this guide. Use a previously confirmed code or check the official tariff search.','이 안내에서 확인된 후보가 없습니다. 기존에 확인한 코드를 입력하거나 공식 품목분류 검색을 이용하세요.')));
    } else {
      result.append(node('strong',t('HS6 candidate: ','HS 6자리 후보: ')+profile.code));
      result.append(node('p',t('Confirm the description before applying. Check destination-country extensions separately. Source checked 2 Nov 2026; this is not a customs classification ruling.','품목 특징을 확인한 뒤 적용하세요. 목적국의 추가 세부 코드는 별도 확인이 필요합니다. 2026-11-02 근거 확인 · 세관의 품목분류 결정은 아닙니다.')));
      const source=node('a',t('Classification source','분류 근거')); source.href=profile.source; source.target='_blank'; source.rel='noopener noreferrer'; result.append(source);
      const apply=node('button',t('Matches my product — use this candidate','내 품목 특징과 일치 — 후보 적용')); apply.type='button';
      apply.addEventListener('click',()=>{
        if(hs.value && hs.value!==profile.code && !window.confirm(t('Replace the current HS code?', '입력된 HS 코드를 바꿀까요?'))) return;
        set(hs, profile.code); result.append(node('p',t('Candidate entered. Save the product to reuse it next time.','후보를 입력했습니다. 품목을 저장하면 다음에 다시 불러올 수 있습니다.'))); apply.disabled=true;
      }); result.append(apply);
    }
    const lookup=node('a',t('Official tariff search (USITC)','공식 품목분류 검색 (USITC)'));
    lookup.href='https://hts.usitc.gov/search?query='+encodeURIComponent(profile.query||'');lookup.target='_blank';lookup.rel='noopener noreferrer';result.append(lookup);
  });
  hs.after(panel);
  const style=node('style');style.textContent='.hs-assistant{border:1px solid #d8d2c7;border-radius:10px;padding:12px;margin:-4px 0 18px;background:#faf8f4}.hs-assistant summary{cursor:pointer;font-weight:700}.hs-assistant p{font-size:14px;line-height:1.6}.hs-assistant a{display:block;margin:8px 0}.hs-assistant button,.hs-saved{font-size:14px!important;padding:10px!important;margin:5px 0!important;width:100%}.hs-saved{background:#ebe7df!important;color:#292824!important}';document.head.append(style);
})();
