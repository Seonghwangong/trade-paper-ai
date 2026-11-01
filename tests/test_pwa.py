import asyncio
import json
from pathlib import Path

import pytest
from types import SimpleNamespace
from urllib.parse import urlsplit
from PIL import Image

from app import auth, main
from app.pwa import app_head

ROOT = Path(__file__).parents[1]


async def get_page(url):
    parsed = urlsplit(url)
    messages = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(message):
        messages.append(message)
    scope = {"type": "http", "method": "GET", "path": parsed.path,
             "raw_path": parsed.path.encode(), "query_string": parsed.query.encode(),
             "headers": [], "scheme": "https", "server": ("testserver", 443),
             "client": ("127.0.0.1", 1), "http_version": "1.1"}
    await main.app(scope, receive, send)
    content = b"".join(m.get("body", b"") for m in messages)
    return SimpleNamespace(status_code=messages[0]["status"], content=content,
                           text=content.decode(), headers={k.decode(): v.decode() for k, v in messages[0]["headers"]})


def test_manifest_icons_and_shortcuts_are_usable():
    manifest = json.loads((ROOT / 'app/static/site.webmanifest').read_text())
    assert manifest['scope'] == '/' and manifest['id'] == '/'
    assert manifest['display'] == 'standalone'
    sizes = set()
    for icon in manifest['icons']:
        with Image.open(ROOT / 'app' / icon['src'].lstrip('/')) as image:
            assert icon['sizes'] == f'{image.width}x{image.height}'
            sizes.add(image.width)
    assert {192, 512} <= sizes
    routes = {route.path for route in main.app.routes}
    for shortcut in manifest['shortcuts']:
        assert shortcut['url'] in routes
        assert shortcut['url'] not in auth.PUBLIC_PATHS


@pytest.mark.parametrize('lang,heading', [('en', 'Your workspace, one tap away.'), ('ko', '한 번 눌러, 내 작업공간으로.')])
def test_public_install_does_not_expose_private_workspace(monkeypatch, lang, heading):
    monkeypatch.setattr(auth, 'current_user', lambda request: None)
    monkeypatch.setattr(main.analytics_module, 'record_visit', lambda *a, **kw: None)
    async def check():
        page = await get_page('/install?lang=' + lang)
        assert page.status_code == 200
        assert heading in page.text
        assert '__' not in page.text
        assert page.text.count('rel="manifest"') == 1
        assert 'apple-touch-icon' in page.text
        assert 'id="app-install" hidden' in page.text
        assert 'data-trade-paper-ux="true"' not in page.text
        assert 'noindex,follow' in page.text
        assert int(page.headers['content-length']) == len(page.content)
        for path in ['/import-document', '/invoice-list', '/shipment-list']:
            response = await get_page(path)
            assert response.status_code == 303
            assert response.headers['location'].startswith('/login?next=')
    asyncio.run(check())


def test_public_home_and_workspace_metadata_are_idempotent(monkeypatch):
    monkeypatch.setattr(auth, 'current_user', lambda request: None)
    monkeypatch.setattr(main.analytics_module, 'record_visit', lambda *a, **kw: None)
    async def check():
        home = (await get_page('/?lang=ko')).text
        assert '/install?lang=ko' in home
        assert home.count('rel="manifest"') == 1
        assert 'rel="apple-touch-icon"' in home
    asyncio.run(check())
    page = '<html><head><title>Document</title></head><body></body></html>'
    assert app_head(app_head(page)) == app_head(page)
