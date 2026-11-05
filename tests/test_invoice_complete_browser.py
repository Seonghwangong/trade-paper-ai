"""Exercise the single invoice action against isolated local storage."""
import json
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright
from tests.test_auth_browser import auth_server
from app.auth import _password_hash


@pytest.mark.browser
@pytest.mark.parametrize('lang', ['en', 'ko'])
def test_complete_invoice_saves_once_and_opens_saved_pdf(auth_server, lang):
    base_url, data_dir = auth_server
    email = 'complete-' + lang + '@example.com'
    users_file = data_dir / 'users.json'
    users = json.loads(users_file.read_text())
    users.append({'account_id':email, 'email':email, 'company':'Fictional Seller', 'password':_password_hash('test-password-9834'), 'role':'Owner', 'plan':'Free', 'subscription_status':'Trial'})
    users_file.write_text(json.dumps(users))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width':1280, 'height':900})
        try:
            response = page.request.post(base_url+'/login', form={'email':email, 'password':'test-password-9834'})
            assert response.ok
            page.goto(base_url+'/invoice?lang='+lang)
            for field, value in {'seller':'Fictional Seller', 'buyer':'Fictional Buyer', 'item1':'Test Mug', 'qty1':'2', 'price1':'6', 'unit1':'PCS'}.items():
                page.locator('#'+field).fill(value)
            assert page.locator('button.full').count() == 1
            page.locator('#complete-invoice').click()
            preview = page.locator('#completed-invoice-pdf')
            preview.wait_for(state='visible')
            assert preview.locator('iframe').get_attribute('src').startswith('blob:')
            saved = page.request.get(base_url+'/invoice-data').json()
            assert len(saved) == 1
            assert saved[0]['items'][0]['quantity'] == 2
            assert saved[0]['invoice_no'] in preview.inner_text()
            assert preview.locator('a[download]').get_attribute('download') == saved[0]['invoice_no']+'.pdf'
            assert page.locator('#qty1').is_disabled()
            page.locator('#complete-invoice').click()
            assert len(page.request.get(base_url+'/invoice-data').json()) == 1
        finally:
            browser.close()
