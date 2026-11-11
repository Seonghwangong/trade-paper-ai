import asyncio
from html.parser import HTMLParser
from urllib.robotparser import RobotFileParser
import pytest
from app import auth, main, seo
from app.search_submission import INDEXNOW_KEY, INDEXNOW_PATH
from tests.test_seo import _anonymous_get

class Metadata(HTMLParser):
    def __init__(self):
        super().__init__(); self.meta, self.titles = {}, []; self.in_title = False
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta':
            key = attrs.get('name', attrs.get('property'))
            if key: self.meta.setdefault(key, []).append(attrs.get('content'))
        if tag == 'title': self.in_title = True
    def handle_endtag(self, tag):
        if tag == 'title': self.in_title = False
    def handle_data(self, data):
        if self.in_title: self.titles.append(data)

@pytest.fixture
def anonymous(monkeypatch):
    monkeypatch.setenv('TRADE_PAPER_PUBLIC_BASE_URL', 'https://www.tradepaper.ai')
    monkeypatch.setattr(auth, 'current_user', lambda request: None)
    monkeypatch.setattr(main.analytics_module, 'record_visit', lambda *a, **kw: None)

@pytest.mark.parametrize('lang', ['en', 'ko'])
def test_public_metadata_is_unique_localized_and_not_on_private_pages(anonymous, lang):
    async def check():
        for path, copy in seo.PAGE_COPY.items():
            response = await _anonymous_get(path + ('?lang=ko' if lang == 'ko' else ''))
            assert response.status_code == 200
            parsed = Metadata(); parsed.feed(response.text)
            assert ''.join(parsed.titles) == copy[lang][0]
            for tag in ('og:title', 'twitter:title'): assert parsed.meta[tag] == [copy[lang][0]]
            for tag in ('description', 'og:description', 'twitter:description'): assert parsed.meta[tag] == [copy[lang][1]]
            assert response.text.count('rel="canonical"') == 1
            assert 'noindex' not in response.headers.get('x-robots-tag', '')
            assert 'keywords' not in parsed.meta
        login = await _anonymous_get('/login')
        assert 'noindex' in login.headers['x-robots-tag'] and 'og:title' not in login.text
    asyncio.run(check())

@pytest.mark.parametrize('agent', ['OAI-SearchBot', 'Claude-SearchBot', 'PerplexityBot', 'bingbot', 'Yeti', 'Googlebot'])
def test_search_agents_public_access_and_private_guards(anonymous, agent):
    async def check():
        robots = await _anonymous_get('/robots.txt', agent)
        parser = RobotFileParser(); parser.parse(robots.text.splitlines())
        assert parser.can_fetch(agent, 'https://www.tradepaper.ai/?lang=ko')
        assert parser.can_fetch(agent, 'https://www.tradepaper.ai/getting-started')
        for path in ('/admin/dashboard','/api/private','/analytics/visit'):
            assert not parser.can_fetch(agent, 'https://www.tradepaper.ai' + path)
        for path in ('/', '/?lang=ko', '/getting-started', '/try-sample?lang=ko'):
            assert (await _anonymous_get(path, agent)).status_code == 200
        for path in ('/invoice', '/packing-data', '/admin/dashboard'):
            response = await _anonymous_get(path, agent)
            assert response.status_code == 303 and response.headers['location'].startswith('/login?next=')
    asyncio.run(check())

def test_only_exact_indexnow_file_is_public_and_not_in_sitemap(anonymous):
    async def check():
        response = await _anonymous_get(INDEXNOW_PATH)
        assert response.status_code == 200 and response.text.strip() == INDEXNOW_KEY
        assert response.headers['content-type'].startswith('text/plain')
        assert response.headers['x-robots-tag'] == 'noindex'
        assert INDEXNOW_PATH not in (await _anonymous_get('/sitemap.xml')).text
        assert (await _anonymous_get('/other-key.txt')).status_code == 303
    asyncio.run(check())
