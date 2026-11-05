/* One action saves the packing, then previews that exact saved document. */
(function () {
  let busy = false, savedNumber = '', uncertain = false, previewURL = '';
  const label = (en, ko) => document.documentElement.lang === 'ko' ? ko : en;
  function buttonState() {
    const button = document.getElementById('complete-packing');
    button.disabled = busy || uncertain;
    button.textContent = busy ? label('Completing…', '완성하는 중…') : uncertain ? label('Check saved packing lists', '저장 여부 확인 필요') : savedNumber ? label('Show saved PDF', '저장된 PDF 보기') : label('Complete Packing List', '포장명세서 완성');
  }
  function showUncertain() {
    uncertain = true;
    showWorkflowError(label('The save result could not be confirmed. Check your packing list before creating another packing list.', '저장 결과를 확인하지 못했습니다. 중복 생성을 막기 위해 포장명세서 목록에서 저장 여부를 먼저 확인해 주세요.'));
    const link = document.createElement('a'); link.href = '/packing-list'; link.textContent = label('Check packing list', '포장명세서 목록 확인');
    workflowMessageArea().appendChild(link);
  }
  async function showSavedPDF() {
    const area = document.getElementById('completed-packing-pdf');
    if (previewURL) { area.hidden = false; area.scrollIntoView({behavior:'smooth', block:'start'}); return; }
    const response = await fetch('/packing-list-pdf/' + encodeURIComponent(savedNumber), {headers:{Accept:'application/pdf, application/json'}});
    if (!response.ok) throw new Error(await workflowErrorMessage(response));
    if (!(response.headers.get('content-type') || '').toLowerCase().includes('application/pdf')) throw new Error(label('Sign in again to open the saved PDF.', '저장된 PDF를 열려면 다시 로그인해 주세요.'));
    const blob = await response.blob();
    if (!blob.size) throw new Error(label('The PDF was empty.', 'PDF를 받지 못했습니다.'));
    previewURL = window.URL.createObjectURL(blob);
    const heading = document.createElement('h2'); heading.textContent = label('Packing List complete · ', '포장명세서 완성 · ') + savedNumber;
    const download = document.createElement('a'); download.href = previewURL; download.download = savedNumber + '.pdf'; download.textContent = label('Download PDF', 'PDF 받기');
    const edit = document.createElement('a'); edit.href = '/edit-packing/' + encodeURIComponent(savedNumber); edit.textContent = label('Edit saved packing', '저장된 포장명세서 수정'); edit.style.marginLeft = '20px';
    const frame = document.createElement('iframe'); frame.src = previewURL; frame.title = label('Saved packing PDF', '저장된 포장명세서 PDF'); frame.style.cssText = 'display:block;width:100%;height:65vh;border:1px solid #ddd;margin-top:16px';
    area.replaceChildren(heading, download, edit, frame); area.hidden = false;
    area.scrollIntoView({behavior:'smooth', block:'start'});
  }
  window.savePacking = async function () {
    if (busy || uncertain) return;
    busy = true; buttonState(); clearWorkflowMessage();
    try {
      if (!savedNumber) {
        const params = new URLSearchParams(window.location.search);
        const data = getPackingData();
        if (!data.invoice_no) { showWorkflowError(label('Select an invoice first.', '송장을 먼저 선택하세요.')); return; }
        const shipmentNo = (params.get('shipment_no') || '').trim();
        if (shipmentNo) data.shipment_no = shipmentNo;
        let response;
        try { response = await fetch('/packing-list', {method:'POST', headers:{'Content-Type':'application/json', Accept:'application/json'}, body:JSON.stringify(data)}); }
        catch (error) { showUncertain(); return; }
        // A server error or missing acknowledgement may follow a committed write.
        if (response.status >= 500) { showUncertain(); return; }
        if (!response.ok) { showWorkflowError(await workflowErrorMessage(response)); return; }
        let result;
        try { result = await response.json(); } catch (error) { showUncertain(); return; }
        if (!result || !result.packing_no) { showUncertain(); return; }
        savedNumber = result.packing_no;
        if (data.shipment_no) showShipmentReturn(data.shipment_no, 'Packing List', savedNumber);
        showPackingNextActions(savedNumber, result.invoice_no || data.invoice_no);
        document.getElementById('packing_no').value = savedNumber;
        // The form now represents a saved snapshot; edits use the existing editor.
        document.querySelectorAll('.container input, .container select, .container textarea, .container button:not(#complete-packing)').forEach(field => { field.disabled = true; });
      }
      try { await showSavedPDF(); }
      catch (error) { showWorkflowError(label('Packing List saved. PDF could not be opened. Press “Show saved PDF” to retry without saving again. ', '포장명세서는 저장됐습니다. PDF를 열지 못했어요. ‘저장된 PDF 보기’를 누르면 중복 저장 없이 다시 엽니다. ') + error.message); }
    } finally { busy = false; buttonState(); }
  };
})();
