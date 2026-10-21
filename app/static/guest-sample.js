(() => {
  'use strict';
  const form = document.getElementById('practice-form');
  const fields = document.getElementById('practice-fields');
  const status = document.getElementById('status');
  const results = document.getElementById('results');
  const names = ['quantity', 'unit_price', 'cartons', 'net_weight', 'gross_weight'];
  const links = ['invoice', 'packing'].map(name => document.getElementById(name + '-download'));
  let urls = [], started = false;
  const campaign = new URLSearchParams(location.search);
  const referral = (campaign.get('utm_source') || campaign.get('ref') || document.referrer).toLowerCase();
  const source = referral.includes('reddit') ? 'Reddit' : referral.includes('producthunt') ? 'Product Hunt' : referral.includes('disquiet') ? 'Disquiet' : referral.includes('google') ? 'Google' : referral ? 'Other' : 'Direct';
  function record(page) {
    fetch('/analytics/visit?' + new URLSearchParams({page, source}), {method: 'POST', credentials: 'omit', keepalive: true}).catch(() => {});
  }
  function clearDownloads() {
    results.hidden = true;
    urls.forEach(url => URL.revokeObjectURL(url)); urls = [];
    links.forEach(link => link.removeAttribute('href'));
  }
  function updateTotal() {
    const quantity = document.getElementById('quantity').valueAsNumber;
    const price = document.getElementById('unit_price').valueAsNumber;
    const total = quantity * price;
    document.getElementById('invoice-total').textContent = Number.isFinite(total) && total >= 0 ? 'USD ' + total.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}) : '—';
  }
  form.addEventListener('input', () => {
    const hadDownloads = urls.length > 0;
    clearDownloads(); updateTotal(); status.className = '';
    status.textContent = hadDownloads ? status.dataset.changed : '';
    if (!started) {started = true; record('Practice Started');}
  });
  form.addEventListener('reset', () => {
    clearDownloads(); status.textContent = ''; status.className = '';
    setTimeout(updateTotal, 0);
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (fields.disabled || !form.reportValidity()) return;
    clearDownloads();
    const payload = Object.fromEntries(names.map(name => [name, document.getElementById(name).valueAsNumber]));
    status.className = 'error';
    if (payload.gross_weight < payload.net_weight) {status.textContent = status.dataset.weightError; return;}
    if (payload.cartons > payload.quantity) {status.textContent = status.dataset.cartonError; return;}
    if (!started) {started = true; record('Practice Started');}
    fields.disabled = true; status.className = ''; status.textContent = status.dataset.busy;
    try {
      const query = new URLSearchParams({source});
      const response = await fetch('/try-sample/documents?' + query, {
        method: 'POST', credentials: 'omit', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
      });
      if (!response.ok) throw new Error('generation failed');
      const pair = await response.json();
      ['invoice', 'packing'].forEach((name, index) => {
        const bytes = Uint8Array.from(atob(pair[name]), ch => ch.charCodeAt(0));
        const url = URL.createObjectURL(new Blob([bytes], {type: 'application/pdf'}));
        urls.push(url); links[index].href = url;
      });
      results.hidden = false; status.textContent = status.dataset.ready;
      results.scrollIntoView({behavior: 'smooth', block: 'nearest'});
    } catch (_) {
      clearDownloads(); status.className = 'error'; status.textContent = status.dataset.error;
    } finally {fields.disabled = false;}
  });
  links.forEach((link, index) => link.addEventListener('click', () => {
    if (link.hasAttribute('href')) record(index === 0 ? 'Practice Invoice Download Click' : 'Practice Packing Download Click');
  }));
  window.addEventListener('pagehide', clearDownloads);
})();
