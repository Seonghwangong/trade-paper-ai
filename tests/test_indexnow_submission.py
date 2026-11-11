import json
import pytest
from scripts import submit_indexnow as tool

@pytest.mark.parametrize('url', [
    'https://example.com/', 'https://www.tradepaper.ai.evil.test/',
    'https://www.tradepaper.ai/admin/dashboard', 'https://www.tradepaper.ai/invoice/INV-1',
    'https://www.tradepaper.ai/?utm_source=email', 'https://www.tradepaper.ai/?lang=en',
    'https://www.tradepaper.ai/?lang=ko&customer=secret', 'https://www.tradepaper.ai/#private',
    'http://www.tradepaper.ai/', 'https://www.tradepaper.ai/%2fadmin',
    'https://www.tradepaper.ai/login', 'https://www.tradepaper.ai/?lang=ko&lang=ko',
])
def test_rejects_private_external_noncanonical_and_tracking_urls(url):
    with pytest.raises(ValueError): tool.payload_for([url])

def test_selects_requested_canonical_urls_and_deduplicates():
    urls = [tool.ORIGIN + '/', tool.ORIGIN + '/?lang=ko']
    assert tool.payload_for(urls + urls)['urlList'] == urls
    with pytest.raises(ValueError): tool.payload_for([])
    assert len(tool.allowed_urls()) == 17

def test_redirects_are_refused():
    with pytest.raises(ValueError): tool.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example/')

def test_key_must_be_deployed_before_submission(tmp_path, monkeypatch):
    calls = []
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, *args): return b'wrong-key'
    class Opener:
        def open(self, request, **kwargs):
            calls.append(request); return Response()
    monkeypatch.setattr(tool, 'build_opener', lambda *args: Opener())
    record = tool.submit(tool.payload_for([tool.ORIGIN + '/']), tmp_path / 'receipt.json')
    assert record['state'] == 'not_sent' and len(calls) == 1

@pytest.mark.parametrize('code,state', [(200, 'received_not_indexed'), (202, 'key_validation_pending')])
def test_once_only_receipt_not_indexing(tmp_path, monkeypatch, code, state):
    calls = []
    class Response:
        def __init__(self, status, body): self.status, self.body = status, body
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, *args): return self.body
    class Opener:
        def open(self, request, **kwargs):
            calls.append(request)
            return Response(200, tool.INDEXNOW_KEY.encode()) if len(calls) == 1 else Response(code, b'')
    monkeypatch.setattr(tool, 'build_opener', lambda *args: Opener())
    output = tmp_path / 'receipt.json'; payload = tool.payload_for([tool.ORIGIN + '/'])
    record = tool.submit(payload, output)
    assert record['state'] == state and record['indexed'] == 'not verified'
    assert json.loads(output.read_text()) == record
    assert calls[1].full_url == tool.ENDPOINT and calls[1].method == 'POST'
    assert json.loads(calls[1].data)['urlList'] == [tool.ORIGIN + '/']
    with pytest.raises(FileExistsError): tool.submit(payload, output)
    assert len(calls) == 2
