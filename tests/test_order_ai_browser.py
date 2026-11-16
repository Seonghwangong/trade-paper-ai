from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen
from uuid import uuid4

import pytest
from playwright.sync_api import sync_playwright, expect
from reportlab.pdfgen.canvas import Canvas
from tests.test_auth_browser import _free_port
from tests.test_order_ai import SOURCE


@pytest.mark.browser
@pytest.mark.parametrize('browser_name,width', [('chromium',1280),('webkit',390)])
def test_authenticated_pdf_review_and_allowance(browser_name, width, tmp_path):
    base = 'http://127.0.0.1:' + str(_free_port())
    data = tmp_path / 'data'; data.mkdir()
    env = dict(os.environ, TRADE_PAPER_ENV='test', TRADE_PAPER_DATA_DIR=str(data),
        TRADE_PAPER_ORDER_AI_PUBLIC='1', TRADE_PAPER_ORDER_AI_ENABLED='1',
        TRADE_PAPER_ORDER_AI_API_KEY='browser-offline-placeholder',
        TRADE_PAPER_PADDLE_LIVE_ACCESS='0', TRADE_PAPER_EMAIL_BACKEND='disabled')
    log = (tmp_path/'server.log').open('w')
    process = subprocess.Popen([sys.executable,'-m','uvicorn','tests.order_ai_browser_server:app',
        '--host','127.0.0.1','--port',base.rsplit(':',1)[1]], env=env, stdout=log, stderr=log)
    try:
        for _ in range(100):
            if process.poll() is not None:
                pytest.fail((tmp_path/'server.log').read_text())
            try:
                urlopen(base+'/health', timeout=.2).close(); break
            except OSError:
                time.sleep(.1)
        with sync_playwright() as pw:
            browser = getattr(pw,browser_name).launch(headless=True)
            page = browser.new_page(viewport={'width':width,'height':900})
            errors=[];page.on('pageerror',lambda err:errors.append(str(err)))
            page.goto(base+'/register?lang=en')
            page.get_by_label('Company Name').fill('Fictional Exporter')
            email = 'qa-'+str(uuid4())+'@example.com'
            page.get_by_label('Email',exact=True).fill(email)
            password='Fictional-QA!7319'
            page.get_by_label('Password',exact=True).fill(password)
            page.get_by_label('Confirm Password').fill(password)
            page.get_by_role('button',name='Register',exact=True).click()
            page.wait_for_url(lambda url:'/register' not in str(url))
            page.goto(base+'/login?next=%2Fimport-document&lang=en')
            page.get_by_label('Email',exact=True).fill(email)
            page.get_by_label('Password',exact=True).fill(password)
            page.get_by_role('button',name='Login',exact=True).click()
            page.wait_for_url(lambda url:'/login' not in str(url))
            if '/company' in page.url:
                page.locator('#address').fill('10 Fictional Road, Example City')
                page.locator('#phone').fill('000-0000-0000')
                page.get_by_role('button',name='Save Company',exact=True).click()
                page.wait_for_url(lambda url:'/company' not in str(url))
            page.goto(base+'/import-document?lang=ko')
            expect(page.locator('#ai-option')).to_be_visible()
            expect(page.locator('#ai-consent')).not_to_be_checked()
            expect(page.locator('#ai-usage')).to_contain_text('1 / 1회 남음')
            page.locator('#ai-consent').check()
            pdf=BytesIO(); c=Canvas(pdf)
            for i,line in enumerate(SOURCE.splitlines()):
                c.drawString(40,800-i*20,line)
            c.save()
            page.locator('#file').set_input_files({'name':'fictional-order.pdf','mimeType':'application/pdf','buffer':pdf.getvalue()})
            expect(page.locator('#workspace')).to_be_visible(timeout=20000)
            expect(page.locator('#fields [data-field=buyer]')).to_have_value('ABC Trading USA')
            expect(page.locator('#items [data-field=quantity]')).to_have_value('500')
            expect(page.locator('#items [data-field=unit_price]')).to_have_value('8.00')
            expect(page.locator('#ai-usage')).to_contain_text('0 / 1회 남음')
            expect(page.locator('#confirmed')).not_to_be_checked()
            assert (data/'fake_provider_calls.txt').read_text().count('fictional call')==1
            # HTTP guard uses authenticated session; no second provider call.
            result=page.evaluate('''async () => {const r=await fetch('/import-document/extract',{
              method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
              mode:'ai_order',ai_consent:'openai-order-text-v1',request_id:crypto.randomUUID(),text:'Buyer: ABC'})});
              return {status:r.status,body:await r.json()};}''')
            assert result['status']==429 and result['body']['detail']=='ai_allowance'
            assert (data/'fake_provider_calls.txt').read_text().count('fictional call')==1
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(tmp_path/f'ai-review-{browser_name}.png'),full_page=True)
            assert not errors
            # Extraction does not save a document or send mail.
            assert not (data/'invoices.json').exists() or json.loads((data/'invoices.json').read_text())==[]
            browser.close()
    finally:
        process.terminate();process.wait(timeout=10);log.close()
