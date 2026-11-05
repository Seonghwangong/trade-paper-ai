/* Prepare private previews only. Sending still requires the review form. */
(() => {
  const form = document.getElementById('prepare-pdf-pair');
  if (!form) return;
  const button = form.querySelector('button[type="submit"]');
  const status = document.getElementById('prepare-pdf-status');
  const ko = document.documentElement.lang === 'ko';
  let pending = false;
  form.addEventListener('submit', event => {
    if (pending) { event.preventDefault(); return; }
    pending = true;
    button.disabled = true;
    status.textContent = ko ? '두 PDF를 준비하고 있습니다…' : 'Preparing both PDFs…';
  });
  // Returning from review must not create another pair automatically.
  window.addEventListener('pageshow', event => {
    if (!event.persisted) return;
    pending = false;
    button.disabled = false;
    status.textContent = '';
  });
  try { form.requestSubmit(); }
  catch (_) {
    pending = false;
    button.disabled = false;
    status.textContent = ko ? '아래 버튼으로 PDF 준비를 계속해 주세요.' : 'Use the button below to continue.';
  }
})();
