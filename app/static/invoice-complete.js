/* One action saves the invoice, then previews that exact saved document. */
(function () {
  let busy = false, savedNumber = '', uncertain = false, previewURL = '';
  const label = (en, ko) => document.documentElement.lang === 'ko' ? ko : en;
  const fieldIds = ['seller', 'seller_address', 'seller_email', 'seller_phone', 'currency', 'buyer', 'buyer_address', 'buyer_email'];
  function buttonState() {
    const button = document.getElementById('complete-invoice');
    button.disabled = busy || uncertain;
    button.textContent = busy ? label('Completing…', '완성하는 중…') : uncertain ? label('Check saved invoices', '저장 여부 확인 필요') : savedNumber ? label('Show saved PDF', '저장된 PDF 보기') : label('Complete Invoice', '송장 완성');
  }
  function showUncertain() {
    uncertain = true;
    showWorkflowError(label('The save result could not be confirmed. Check your invoice list before creating another invoice.', '저장 결과를 확인하지 못했습니다. 중복 생성을 막기 위해 송장 목록에서 저장 여부를 먼저 확인해 주세요.'));
    const link = document.createElement('a'); link.href = '/invoice-list'; link.textContent = label('Check invoice list', '송장 목록 확인');
    workflowMessageArea().appendChild(link);
  }
  async function showSavedPDF() {
    const area = document.getElementById('completed-invoice-pdf');
    if (previewURL) { area.hidden = false; area.scrollIntoView({behavior:'smooth', block:'start'}); return; }
    const response = await fetch('/invoice-pdf/' + encodeURIComponent(savedNumber), {headers:{Accept:'application/pdf, application/json'}});
    if (!response.ok) throw new Error(await workflowErrorMessage(response));
    if (!(response.headers.get('content-type') || '').toLowerCase().includes('application/pdf')) throw new Error(label('Sign in again to open the saved PDF.', '저장된 PDF를 열려면 다시 로그인해 주세요.'));
    const blob = await response.blob();
    if (!blob.size) throw new Error(label('The PDF was empty.', 'PDF를 받지 못했습니다.'));
    previewURL = window.URL.createObjectURL(blob);
    const heading = document.createElement('h2'); heading.textContent = label('Invoice complete · ', '송장 완성 · ') + savedNumber;
    const download = document.createElement('a'); download.href = previewURL; download.download = savedNumber + '.pdf'; download.textContent = label('Download PDF', 'PDF 받기');
    const edit = document.createElement('a'); edit.href = '/edit-invoice/' + encodeURIComponent(savedNumber); edit.textContent = label('Edit saved invoice', '저장된 송장 수정'); edit.style.marginLeft = '20px';
    const frame = document.createElement('iframe'); frame.src = previewURL; frame.title = label('Saved invoice PDF', '저장된 송장 PDF'); frame.style.cssText = 'display:block;width:100%;height:65vh;border:1px solid #ddd;margin-top:16px';
    area.replaceChildren(heading, download, edit, frame); area.hidden = false;
    area.scrollIntoView({behavior:'smooth', block:'start'});
  }
  window.saveInvoice = async function () {
    if (busy || uncertain) return;
    busy = true; buttonState(); clearWorkflowMessage();
    try {
      if (!savedNumber) {
        const params = new URLSearchParams(window.location.search);
        const data = Object.fromEntries(fieldIds.map(id => [id, document.getElementById(id).value]));
        data.items = getItems();
        for (const key of ['pi_no', 'shipment_no']) { const value = (params.get(key) || '').trim(); if (value) data[key] = value; }
        let response;
        try { response = await fetch('/invoice', {method:'POST', headers:{'Content-Type':'application/json', Accept:'application/json'}, body:JSON.stringify(data)}); }
        catch (error) { showUncertain(); return; }
        // A server error or missing acknowledgement may follow a committed write.
        if (response.status >= 500) { showUncertain(); return; }
        if (!response.ok) { showWorkflowError(await workflowErrorMessage(response)); return; }
        let result;
        try { result = await response.json(); } catch (error) { showUncertain(); return; }
        if (!result || !result.invoice_no) { showUncertain(); return; }
        savedNumber = result.invoice_no;
        if (data.shipment_no) showShipmentReturn(data.shipment_no, 'Invoice', savedNumber);
        showInvoiceNextActions(savedNumber);
        // The form now represents a saved snapshot; edits use the existing editor.
        document.querySelectorAll('.container input, .container select, .container textarea').forEach(field => { field.disabled = true; });
      }
      try { await showSavedPDF(); }
      catch (error) { showWorkflowError(label('Invoice saved. PDF could not be opened. Press “Show saved PDF” to retry without saving again. ', '송장은 저장됐습니다. PDF를 열지 못했어요. ‘저장된 PDF 보기’를 누르면 중복 저장 없이 다시 엽니다. ') + error.message); }
    } finally { busy = false; buttonState(); }
  };
})();
