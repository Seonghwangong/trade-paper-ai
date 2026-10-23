(async () => {
  'use strict';
  const selected = document.currentScript?.dataset.language || 'en';
  document.documentElement.lang = selected;
  const nav = document.createElement('nav');
  nav.setAttribute('aria-label','Language'); nav.setAttribute('translate','no');
  nav.style.cssText='max-width:1180px;margin:12px auto;padding:0 18px;text-align:right;font:14px Arial';
  for(const [code,label] of [['en','English'],['ko','한국어']]) {
    const link=document.createElement('a'), url=new URL(location.href); url.searchParams.set('lang',code);
    link.href=url.pathname+url.search+url.hash; link.textContent=label; link.style.marginLeft='12px';
    if(code===selected) link.setAttribute('aria-current','true'); nav.append(link);
  }
  document.body.prepend(nav);
  if(selected!=='ko') return;
  const response=await fetch('/static/workspace-ko.json');
  if(!response.ok) return;
  const dictionary=await response.json();
  if(dictionary[document.title]) document.title=dictionary[document.title];
  const blocked = 'script,style,textarea,pre,code,[translate="no"],.signed-in-user';
  const eligible='h1,h2,h3,h4,th,label,button,a,summary,p,legend,small,b,strong,span,option,li,div';
  function translate(root) {
    if(root.nodeType===Node.TEXT_NODE) {
      const parent=root.parentElement;
      if(!parent || parent.closest(blocked) || !parent.matches(eligible)) return;
      if(parent.closest('td') && !parent.closest('a,button,label')) return;
      if(parent.tagName==='OPTION' && parent.value) return;
      const raw=root.textContent, key=raw.trim().replace(/\s+/g,' ');
      const control=parent.closest('button,a');
      if(control && !control.dataset.tpOriginalLabel) control.dataset.tpOriginalLabel=control.textContent.trim();
      if(/^([✓•] )/.test(key) && dictionary[key.slice(2)]) {root.textContent=key.slice(0,2)+dictionary[key.slice(2)];return;}
      if(dictionary[key]) root.textContent=raw.replace(raw.trim(),dictionary[key]);
      else if(/^\d+% Complete$/.test(key)) root.textContent=key.replace('Complete','완료');
      else if(/^Trial · \d+ \/ \d+ documents this month$/.test(key)) root.textContent=key.replace('Trial ·','무료 이용 ·').replace('documents this month','문서 / 이번 달');
      else if(/^Ready to save · \d+ \/ \d+$/.test(key)) root.textContent=key.replace('Ready to save','저장 준비 완료');
      else if(/^\d+ \/ \d+ complete$/.test(key)) root.textContent=key.replace(' complete',' 완료');
      else if(/^Required fields: \d+ \/ \d+$/.test(key)) root.textContent=key.replace('Required fields:','필수 항목:');
      else if(/^Total (Products|Buyers|Invoices|Packing Lists)\s*:\s*\d+$/.test(key)) root.textContent=key.replace(/^Total (Products|Buyers|Invoices|Packing Lists)\s*:/,'전체 개수:');
      return;
    }
    if(root.nodeType!==Node.ELEMENT_NODE || root.matches(blocked)) return;
    for(const attr of ['placeholder','aria-label','title']) {
      if(dictionary[root.getAttribute(attr)]) root.setAttribute(attr,dictionary[root.getAttribute(attr)]);
    }
    // Deliberately never modify values, option values, names, IDs or event handlers.
    Array.from(root.childNodes).forEach(translate);
  }
  translate(document.body);
  let pending=false;
  const observer=new MutationObserver(()=>{
    if(pending) return; pending=true;
    requestAnimationFrame(()=>{observer.disconnect();translate(document.body);pending=false;observer.observe(document.body,{childList:true,subtree:true,characterData:true});});
  });
  observer.observe(document.body,{childList:true,subtree:true,characterData:true});
})();
