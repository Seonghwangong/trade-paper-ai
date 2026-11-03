import asyncio
import json

from app.ui import ReleaseFooterMiddleware, _ux_script
from app.workspace_storage import storage_prefix


def test_storage_prefix_is_stable_and_does_not_embed_identity_or_markup():
    assert storage_prefix("account-a") == storage_prefix("account-a")
    assert storage_prefix("account-a") != storage_prefix("account-b")
    assert storage_prefix("") == ""
    assert "</script>" not in storage_prefix("</script>")
    assert "account-a" not in storage_prefix("account-a")


def test_workspace_storage_is_bound_before_editor_code():
    script = _ux_script("/invoice", "account-a")
    assert '__WORKSPACE_STORAGE__' not in script
    assert '__INVOICE_DRAFT_STORE__' not in script
    assert json.dumps(storage_prefix("account-a")) in script
    assert script.index('const localStorage=') < script.index('function invoiceDraftStore(')
    assert script.index('const sessionStorage=') < script.index('const lastWorkKey=')
    assert 'const workspaceStoragePrefix="";' in _ux_script('/register')


def test_response_uses_authenticated_account_not_url_query():
    async def render(account_id):
        messages=[]
        async def app(scope, receive, send):
            await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'text/html')]})
            await send({'type':'http.response.body','body':b'<html><head></head><body><main>Invoice</main></body></html>'})
        async def receive(): return {'type':'http.request','body':b''}
        async def send(message): messages.append(message)
        scope={'type':'http','method':'GET','path':'/invoice','headers':[], 'query_string':b'account_id=attacker', 'trade_paper_user':{'account_id':account_id,'email':'qa@example.com'}}
        await ReleaseFooterMiddleware(app)(scope,receive,send)
        return b''.join(m.get('body',b'') for m in messages).decode()
    for account in ['account-a','account-b']:
        body=asyncio.run(render(account))
        assert storage_prefix(account) in body
        assert storage_prefix('attacker') not in body
