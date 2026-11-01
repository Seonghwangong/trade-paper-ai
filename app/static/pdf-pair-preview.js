/* Render the same private PDF bytes as the download links; never auto-send. */
(async () => {
  const frames = [...document.querySelectorAll('iframe[data-pdf-preview]')];
  if (!frames.length) return;
  const ko = document.documentElement.lang === 'ko';
  const text = (en, kr) => ko ? kr : en;
  let pdfjs;
  try {
    pdfjs = await import('/static/vendor/pdfjs/build/pdf.mjs');
    pdfjs.GlobalWorkerOptions.workerSrc = '/static/vendor/pdfjs/build/pdf.worker.mjs';
  } catch (_) { return; } // Native frames and direct-download links remain usable.
  for (const frame of frames) {
    const panel = document.createElement('div'); panel.className = 'pdf-pair-preview';
    const status = document.createElement('p'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    status.textContent = text('Loading PDF…', 'PDF를 불러오는 중…');
    const canvas = document.createElement('canvas'); canvas.style.cssText = 'display:block;width:100%;height:auto;background:white';
    canvas.hidden = true; canvas.setAttribute('role','img');
    const controls = document.createElement('div'); controls.style.cssText='display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:12px 0';
    const previous = document.createElement('button'), next = document.createElement('button');
    previous.type = next.type = 'button'; previous.disabled = next.disabled = true;
    previous.textContent = text('Previous page', '이전 페이지'); next.textContent = text('Next page', '다음 페이지');
    controls.append(previous, next); panel.append(status, canvas, controls); frame.before(panel);
    let doc, current = 1, busy = false;
    const fallback = () => {
      frame.hidden = false; canvas.hidden = true; controls.hidden = true;
      status.textContent = text('Preview unavailable. Use “Open this PDF” below to review the file.', '미리보기를 표시하지 못했습니다. 아래 PDF 열기 링크로 확인해 주세요.');
    };
    const render = async (number) => {
      if (busy) return;
      busy = true; previous.disabled = next.disabled = true;
      try {
        const page = await doc.getPage(number);
        const original = page.getViewport({scale:1});
        const width = Math.min(1400, Math.max(320, panel.getBoundingClientRect().width) * Math.min(window.devicePixelRatio || 1,2));
        const viewport = page.getViewport({scale:width/original.width});
        canvas.width = Math.ceil(viewport.width); canvas.height = Math.ceil(viewport.height);
        await page.render({canvasContext:canvas.getContext('2d'), viewport}).promise;
        current = number; canvas.hidden = false; frame.hidden = true;
        status.textContent = text(`Page ${current} of ${doc.numPages}`, `${current} / ${doc.numPages} 페이지`);
        canvas.setAttribute('aria-label', `${frame.title} — ${status.textContent}`);
        previous.disabled = current === 1; next.disabled = current === doc.numPages;
        controls.hidden = doc.numPages === 1;
      } catch (_) { fallback(); }
      finally { busy = false; }
    };
    previous.addEventListener('click', () => render(current - 1));
    next.addEventListener('click', () => render(current + 1));
    try {
      const url = new URL(frame.getAttribute('src'), location.href); url.hash = '';
      if (url.origin !== location.origin) throw Error('Only same-origin document previews');
      doc = await pdfjs.getDocument({url:url.href,isEvalSupported:false,enableXfa:false,
        cMapUrl:'/static/vendor/pdfjs/cmaps/',cMapPacked:true,
        standardFontDataUrl:'/static/vendor/pdfjs/standard_fonts/',maxImageSize:12_000_000}).promise;
      await render(1);
    } catch (_) { fallback(); }
  }
})();
