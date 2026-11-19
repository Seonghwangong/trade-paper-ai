"""Offline fictional-order flow: no AI provider, mail or payment calls."""
import json
import os
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest
from playwright.sync_api import sync_playwright, expect
from tests.test_auth_browser import _free_port


@pytest.mark.browser
@pytest.mark.parametrize('browser_name,width',[('chromium',1280),('webkit',390)])
def test_revised_order_selective_save_and_packing_review(browser_name,width,tmp_path):
    base='http://127.0.0.1:'+str(_free_port());data=tmp_path/'data';data.mkdir()
    env=dict(os.environ,TRADE_PAPER_ENV='test',TRADE_PAPER_DATA_DIR=str(data),
        TRADE_PAPER_ORDER_AI_ENABLED='0',TRADE_PAPER_ORDER_AI_PUBLIC='0',
        TRADE_PAPER_EMAIL_BACKEND='disabled',TRADE_PAPER_PADDLE_LIVE_ACCESS='0')
    log=(tmp_path/'server.log').open('w')
    process=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',base.rsplit(':',1)[1]],env=env,stdout=log,stderr=log)
    try:
        for _ in range(100):
            if process.poll() is not None:pytest.fail((tmp_path/'server.log').read_text())
            try:urlopen(base+'/health',timeout=.2).close();break
            except OSError:time.sleep(.1)
        with sync_playwright() as pw:
            browser=getattr(pw,browser_name).launch(headless=True)
            page=browser.new_page(viewport={'width':width,'height':900});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(base+'/register?lang=en')
            page.get_by_label('Company Name').fill('Fictional Exporter')
            page.get_by_label('Email',exact=True).fill('fictional-revision@example.com')
            page.get_by_label('Password',exact=True).fill('Fictional-QA!7319')
            page.get_by_label('Confirm Password').fill('Fictional-QA!7319')
            page.get_by_role('button',name='Register',exact=True).click()
            page.wait_for_url(lambda u:'/register' not in str(u))
            page.goto(base+'/login?next=%2Fimport-document&lang=en')
            page.get_by_label('Email',exact=True).fill('fictional-revision@example.com')
            page.get_by_label('Password',exact=True).fill('Fictional-QA!7319')
            page.get_by_role('button',name='Login',exact=True).click()
            page.wait_for_url(lambda u:'/login' not in str(u))
            if '/company' in page.url:
                page.locator('#address').fill('10 Fictional Road')
                page.locator('#phone').fill('000-0000-0000')
                page.get_by_role('button',name='Save Company',exact=True).click()
                page.wait_for_url(lambda u:'/company' not in str(u))
            # Save fictional original through the regular authenticated API.
            original=page.evaluate('''async()=>{const r=await fetch('/invoice',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({seller:'Fictional Exporter',buyer:'Sample Buyer',currency:'USD',invoice_date:'2026-11-19',items:[{name:'Cotton shirts',quantity:500,unit_price:8,unit:'PCS',hs_code:'6109'}]})});if(!r.ok)throw Error(await r.text());return r.json();}''')
            number=original['invoice_no']
            page.goto(base+'/import-document?revise_invoice='+number+'&lang=ko')
            csv='Buyer,Changed Buyer\nCurrency,USD\nDescription,Qty,Unit,Unit Price\nCotton shirts,600,PCS,8\n'
            page.locator('#file').set_input_files({'name':'fictional-revised-order.csv','mimeType':'text/csv','buffer':csv.encode()})
            expect(page.locator('#workspace')).to_be_visible()
            expect(page.locator('#save')).to_have_text('기존 송장과 비교')
            page.locator('#save').click()
            expect(page.locator('#order-changes')).to_be_visible()
            expect(page.locator('#change-options')).to_contain_text('500 → 600')
            assert not page.locator('#change-options input:checked').count()
            # Editing after comparison invalidates the old selection.
            page.locator('#items [data-field=quantity]').fill('650')
            expect(page.locator('#order-changes')).not_to_be_visible()
            page.locator('#items [data-field=quantity]').fill('600');page.locator('#save').click()
            page.locator('#change-options input[value="item:0:quantity"]').check()
            page.locator('#changes-confirmed').check()
            page.screenshot(path=str(tmp_path/f'order-changes-{browser_name}.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.get_by_role('button',name='선택한 변경 송장에 반영').click()
            page.wait_for_url('**/order-changes/*/status?lang=ko')
            rows=json.loads((data/'invoices.json').read_text())
            assert len(rows)==1 and rows[0]['items'][0]['quantity']==600
            assert rows[0]['buyer']=='Sample Buyer'
            assert rows[0]['order_change_history'][0]['document']['items'][0]['quantity']==500
            assert not errors
            browser.close()
    finally:
        process.terminate();process.wait(timeout=10);log.close()
