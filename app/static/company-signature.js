(function () {
  const label = (en, ko) => document.documentElement.lang === 'ko' ? ko : en;
  const fileInput = document.getElementById('signature-file');
  const preview = document.getElementById('signature-preview');
  const button = document.getElementById('signature-save');
  const status = document.getElementById('signature-status');
  let selected = '', busy = false, loading = false, changed = false;
  const show = image => { preview.src = image; preview.hidden = !image; };
  fetch('/company-signature').then(r => { if (!r.ok) throw Error(); return r.json(); }).then(data => { if (!changed) show(data.image || ''); }).catch(() => { if (!changed) status.textContent = label('Could not load the registered signature.', '등록된 서명을 불러오지 못했습니다.'); });
  fileInput.addEventListener('change', async () => {
    changed = true; selected = ''; const file = fileInput.files[0]; show('');
    if (!file) return;
    if (!['image/png', 'image/jpeg'].includes(file.type) || file.size > 1024 * 1024) { status.textContent = label('Choose a PNG or JPEG up to 1 MB.', '1MB 이하 PNG·JPEG 이미지를 선택하세요.'); return; }
    loading = true; button.disabled = true;
    try { selected = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(file); }); show(selected); status.textContent = label('Preview only. Press Register to save.', '미리보기입니다. 등록 버튼을 누르면 저장됩니다.'); }
    catch (e) { status.textContent = label('Could not read the image.', '이미지를 읽지 못했습니다.'); }
    finally { loading = false; button.disabled = false; }
  });
  button.addEventListener('click', async () => {
    if (busy || loading) return;
    if (!selected) { status.textContent = label('Choose an image first.', '먼저 이미지를 선택하세요.'); return; }
    busy = true; button.disabled = true; fileInput.disabled = true;
    try {
      const response = await fetch('/company-signature', {method:'POST', headers:{'Content-Type':'application/json', Accept:'application/json'}, body:JSON.stringify({image:selected})});
      if (!response.ok) throw Error();
      const data = await response.json(); show(data.image); selected = '';
      status.textContent = label('Registered. Select it when completing an invoice. Existing invoices are unchanged.', '등록했습니다. 송장 완성 전에 선택해 넣으세요. 기존 송장은 바뀌지 않습니다.');
    } catch (e) { status.textContent = label('Registration failed. Check the image and try again.', '등록하지 못했습니다. 이미지 형식을 확인하고 다시 시도하세요.'); }
    finally { busy = false; button.disabled = false; fileInput.disabled = false; if (window.tpRestoreSavingButtons) window.tpRestoreSavingButtons(); }
  });
  if (window.location.hash === '#company-signature') document.getElementById('company-signature').open = true;
})();
