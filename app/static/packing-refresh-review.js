/* Review existing values without estimating or changing physical measurements. */
(function () {
  'use strict';
  function bind(form, language) {
    const checks = Array.from(form.querySelectorAll('.packing-sync-item')).map(row => {
      const net = row.querySelector('[name="net_weight"]');
      const gross = row.querySelector('[name="gross_weight"]');
      const check = () => {
        gross.setCustomValidity('');
        if (net.value !== '' && gross.value !== '' && net.validity.valid && gross.validity.valid &&
            gross.valueAsNumber < net.valueAsNumber) {
          gross.setCustomValidity(language === 'ko'
            ? '총중량은 순중량 이상이어야 합니다. 실제 포장 중량을 확인해 주세요.'
            : 'Gross weight must be at least net weight. Check the actual packed weights.');
        }
      };
      for (const input of [net, gross]) {
        input.addEventListener('input', check);
        input.addEventListener('change', check);
      }
      check();
      return check;
    });
    form.addEventListener('submit', event => {
      checks.forEach(check => check());
      if (!form.reportValidity()) event.preventDefault();
    });
  }
  if (typeof module !== 'undefined') module.exports = {bind};
  if (typeof document !== 'undefined') {
    document.querySelectorAll('[data-packing-review]').forEach(form => bind(form, form.dataset.reviewLang));
  }
})();
