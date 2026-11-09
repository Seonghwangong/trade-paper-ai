(() => {
  'use strict';
  const rows = Array.from(document.querySelectorAll('.invoice-item'));
  const currency = document.querySelector('input[name="currency"]');
  const total = document.getElementById('total');
  const field = (row, key) => row.querySelector('[data-field="' + key + '"]');
  function calculateTotal() {
    const amount = rows.reduce((sum, row) => {
      const quantity = Number(field(row, 'quantity').value || 0);
      const price = Number(field(row, 'unit_price').value || 0);
      return sum + quantity * price;
    }, 0);
    total.textContent = (currency.value.trim().toUpperCase() || 'USD') + ' ' +
      (Number.isFinite(amount) ? amount.toLocaleString(undefined, {maximumFractionDigits: 6}) : '—');
  }
  rows.forEach(row => row.addEventListener('input', calculateTotal));
  currency.addEventListener('input', calculateTotal);
  calculateTotal();
  async function loadProducts() {
    try {
      const response = await fetch('/product-data');
      if (!response.ok) return;
      const products = await response.json();
      if (!Array.isArray(products)) return;
      rows.forEach(row => {
        const select = row.querySelector('.saved-product');
        products.forEach((product, index) => {
          const option = document.createElement('option');
          option.value = String(index);
          option.textContent = product.name || '';
          select.appendChild(option);
        });
        // Loading saved products never replaces the document's snapshot.
        select.addEventListener('change', () => {
          if (select.value === '') return;
          const product = products[Number(select.value)];
          if (!product) return;
          for (const key of ['name', 'hs_code', 'origin', 'unit', 'unit_price']) {
            field(row, key).value = product[key] ?? '';
            field(row, key).dispatchEvent(new Event('input', {bubbles: true}));
          }
          calculateTotal();
        });
      });
    } catch (_) { /* Manual editing remains available if the product list cannot load. */ }
  }
  loadProducts();
  const form = document.getElementById('invoice-edit-form');
  const error = document.getElementById('edit-error');
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    if (button.disabled) return;
    button.disabled = true;
    error.hidden = true;
    try {
      const response = await fetch(form.action, {method: 'POST', body: new FormData(form)});
      const saved = response.ok && response.redirected && new URL(response.url, window.location.href).pathname === '/invoice-list';
      if (!saved) {
        const ko = document.documentElement.lang === 'ko';
        const needsLogin = response.status === 401 || (response.redirected && new URL(response.url, window.location.href).pathname === '/login');
        error.textContent = needsLogin
          ? (ko ? '로그인이 만료되었습니다. 입력은 유지됩니다. 다른 창에서 로그인한 뒤 다시 저장하세요.'
                : 'Your session expired. Your entries remain here. Sign in in another window, then save again.')
          : response.status === 409
          ? (ko ? '다른 화면에서 이 송장이 변경되었습니다. 현재 입력은 유지됩니다. 변경 내용을 따로 기록하고 최신 송장을 다시 열어 확인하세요.'
                : 'This Invoice changed in another window. Your entries remain here. Keep a copy of your edits and reopen the latest Invoice before saving.')
          : (ko ? '저장하지 못했습니다. 입력한 품목과 수량·단가를 확인해 주세요. 현재 입력은 유지됩니다.'
                : 'Unable to save. Review item names, quantities and prices. Your entries remain here.');
        error.hidden = false;
        error.scrollIntoView({block: 'center'});
        return;
      }
      if (window.tpMarkSaved) window.tpMarkSaved();
      // Continue only after the save is confirmed. The review route rechecks
      // ownership/linkage and requires comparison if invoice fields changed.
      const next = new URL(form.dataset.continueUrl || response.url, window.location.href);
      const current = new URL(window.location.href);
      window.location.href = next.origin === current.origin &&
        /^\/email-pair\/prepare\/[^/]+$/.test(next.pathname)
        ? next.href : response.url;
    } catch (_) {
      error.textContent = document.documentElement.lang === 'ko'
        ? '연결을 확인하고 다시 저장해 주세요. 입력한 내용은 유지됩니다.'
        : 'Check your connection and save again. Your entries remain here.';
      error.hidden = false;
    } finally {
      button.disabled = false;
    }
  });
})();
