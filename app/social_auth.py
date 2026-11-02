"""Opt-in OAuth sign-in. Disabled until each provider is configured and enabled.

No automatic email-based account linking. Provider subjects are the identity;
email is contact data. Provider tokens are discarded after this sign-in.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import logging
import os
import secrets
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit
from urllib.request import Request as URLRequest, build_opener, HTTPRedirectHandler

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.storage import data_path, locked_json_mutation

router = APIRouter()
STATE_FILE = data_path("oauth_states.json")
TTL = 600
PROVIDERS = {
    "google": {"label":"Google", "authorize":"https://accounts.google.com/o/oauth2/v2/auth", "token":"https://oauth2.googleapis.com/token", "scope":"openid email", "jwks":"https://www.googleapis.com/oauth2/v3/certs", "issuer":["https://accounts.google.com", "accounts.google.com"]},
    "kakao": {"label":"Kakao", "authorize":"https://kauth.kakao.com/oauth/authorize", "token":"https://kauth.kakao.com/oauth/token", "scope":"account_email", "profile":"https://kapi.kakao.com/v2/user/me"},
    "naver": {"label":"NAVER", "authorize":"https://nid.naver.com/oauth2.0/authorize", "token":"https://nid.naver.com/oauth2.0/token", "profile":"https://openapi.naver.com/v1/nid/me"},
    "apple": {"label":"Apple", "authorize":"https://appleid.apple.com/auth/authorize", "token":"https://appleid.apple.com/auth/token", "scope":"email", "jwks":"https://appleid.apple.com/auth/keys", "issuer":"https://appleid.apple.com"},
}
PUBLIC_PATHS = frozenset(f"/auth/{provider}/{action}" for provider in PROVIDERS for action in ("start", "callback"))


class OAuthAccessLogFilter(logging.Filter):
    """Uvicorn access logs must not retain authorization codes or state."""
    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            path = str(args[2])
            if path.split("?", 1)[0] in PUBLIC_PATHS:
                args[2] = path.split("?", 1)[0]
                record.args = tuple(args)
        return True


logging.getLogger("uvicorn.access").addFilter(OAuthAccessLogFilter())


def config(provider):
    if provider not in PROVIDERS:
        return None
    prefix = "TRADE_PAPER_" + provider.upper() + "_"
    if os.getenv(prefix + "ENABLED") != "1":
        return None
    client = os.getenv(prefix + "CLIENT_ID", "").strip()
    secret = os.getenv(prefix + "CLIENT_SECRET", "").strip()
    origin = os.getenv("TRADE_PAPER_OAUTH_ORIGIN", "").strip().rstrip("/")
    parsed = urlsplit(origin)
    if not client or not secret or parsed.scheme != "https" or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
        return None
    return {**PROVIDERS[provider], "client_id":client, "client_secret":secret, "origin":origin, "redirect_uri":origin+f"/auth/{provider}/callback"}


def buttons(next_path="/", lang="en"):
    links = []
    for provider in PROVIDERS:
        settings = config(provider)
        if not settings:
            continue
        label = {"kakao":"카카오", "naver":"네이버"}.get(provider, settings["label"]) if lang == "ko" else settings["label"]
        text = f"{label}로 계속하기" if lang == "ko" else f"Continue with {label}"
        # Absolute canonical origin avoids losing host-only state cookies between www/apex.
        url = settings["origin"]+f"/auth/{provider}/start?"+urlencode({"next":next_path, "lang":lang})
        links.append(f'<a class="social-login" href="{html.escape(url, quote=True)}">{html.escape(text)}</a>')
    if not links:
        return ""
    note = '처음 가입하면 <a href="/terms">이용약관</a>에 동의하며 <a href="/privacy">개인정보 처리방침</a>을 확인한 것으로 간주합니다.' if lang == "ko" else 'First-time sign-in creates a free account under our <a href="/terms">Terms</a> and <a href="/privacy">Privacy Policy</a>.'
    return '<div class="social-logins">'+''.join(links)+'</div><p class="field-help">'+note+'</p><style>.social-logins{display:grid;gap:8px;margin-bottom:12px}.social-login{display:block;text-align:center;padding:13px;border:1px solid #cfc9bd;border-radius:10px;text-decoration:none;color:#292824;font-weight:700}.social-logins~form{margin-top:22px}</style>'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def local_next(value):
    from app.auth import safe_next_path
    value = str(value or "")
    if len(value)>2048 or any(ord(c)<32 for c in value) or value.startswith('/auth/'):
        return "/"
    return safe_next_path(value)


def create_state(provider, next_path, lang, settings):
    state, browser, verifier, nonce = (secrets.token_urlsafe(32) for _ in range(4))
    record = {"state":digest(state), "browser":digest(browser), "provider":provider, "next":local_next(next_path), "lang":"ko" if lang=="ko" else "en", "expires":time.time()+TTL, "verifier":verifier, "nonce":nonce, "client_id":settings["client_id"], "redirect_uri":settings["redirect_uri"]}
    def add(rows):
        rows[:] = [r for r in rows if isinstance(r,dict) and r.get("expires",0)>time.time()][-999:]
        rows.append(record)
    locked_json_mutation(STATE_FILE, [], add, list)
    return state, browser, record


def consume_state(provider, state, browser):
    if not state or not browser or len(state)>128 or len(browser)>128:
        raise ValueError("Invalid state")
    found=[]
    def consume(rows):
        keep=[]
        for row in rows:
            if not isinstance(row,dict) or row.get("expires",0)<=time.time():
                continue
            matches = row.get("provider")==provider and hmac.compare_digest(str(row.get("state","")), digest(state))
            if matches and hmac.compare_digest(str(row.get("browser","")), digest(browser)):
                found.append(row)
            else:
                keep.append(row)
        rows[:] = keep
    locked_json_mutation(STATE_FILE, [], consume, list)
    if len(found)!=1:
        raise ValueError("Expired or mismatched state")
    return found[0]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_json(url, form=None, bearer=None):
    allowed={v[k] for v in PROVIDERS.values() for k in ('token','profile','jwks') if k in v}
    if url not in allowed:
        raise ValueError("Unapproved identity endpoint")
    headers={"Accept":"application/json"}
    if bearer:
        if len(bearer)>16384 or '\r' in bearer or '\n' in bearer:
            raise ValueError("Invalid token")
        headers['Authorization']='Bearer '+bearer
    data=None
    if form is not None:
        headers['Content-Type']='application/x-www-form-urlencoded'
        data=urlencode(form).encode()
    with build_opener(NoRedirect()).open(URLRequest(url,data=data,headers=headers),timeout=10) as response:
        body=response.read(262145)
        if len(body)>262144:
            raise ValueError("Identity response too large")
        result=json.loads(body)
    if not isinstance(result,dict) or result.get('error'):
        raise ValueError("Identity response failed")
    return result


def verify_id_token(token, settings, record):
    import jwt
    if not isinstance(token,str) or len(token)>32768:
        raise ValueError("Invalid ID token")
    header=jwt.get_unverified_header(token)
    if header.get('alg')!='RS256' or not header.get('kid'):
        raise ValueError("Unexpected signing algorithm")
    keys=fetch_json(settings['jwks']).get('keys',[])
    key=next((k for k in keys if k.get('kid')==header['kid'] and k.get('kty')=='RSA' and k.get('use','sig')=='sig'),None)
    if not key:
        raise ValueError("Unknown signing key")
    public=jwt.PyJWK.from_dict(key,algorithm='RS256').key
    claims=jwt.decode(token,public,algorithms=['RS256'],audience=settings['client_id'],issuer=settings['issuer'],options={'require':['exp','iat','iss','aud','sub','nonce']})
    if not hmac.compare_digest(str(claims.get('nonce','')),record['nonce']):
        raise ValueError("Invalid nonce")
    if claims.get('azp',settings['client_id'])!=settings['client_id']:
        raise ValueError("Unexpected authorized party")
    if claims.get('email_verified') not in (True, 'true'):
        raise ValueError("Email not verified")
    return str(claims['sub']), str(claims.get('email',''))


def exchange_identity(provider, code, state, record, settings):
    fields={"grant_type":"authorization_code", "client_id":settings['client_id'], "client_secret":settings['client_secret'], "redirect_uri":settings['redirect_uri'], "code":code}
    if provider=='google': fields['code_verifier']=record['verifier']
    if provider=='naver': fields['state']=state
    token=fetch_json(settings['token'],fields)
    if provider in ('google','apple'):
        return verify_id_token(token.get('id_token'),settings,record)
    access=token.get('access_token')
    if not isinstance(access,str) or not access:
        raise ValueError('Missing access token')
    profile=fetch_json(settings['profile'],bearer=access)
    if provider=='kakao':
        account=profile.get('kakao_account') or {}
        if account.get('is_email_valid') is not True or account.get('is_email_verified') is not True:
            raise ValueError('Email consent/verification required')
        subject=profile.get('id')
        if not isinstance(subject,int) or subject<=0: raise ValueError('Missing subject')
        return str(subject), str(account.get('email',''))
    if profile.get('resultcode')!='00': raise ValueError('Profile request failed')
    account=profile.get('response') or {}
    return str(account.get('id','')), str(account.get('email',''))


def account_for(provider, subject, email):
    from app import auth
    if not subject or len(subject)>256:
        raise ValueError('Missing subject')
    email=auth._normalized_email(email)
    selected=[]
    def find_or_create(users):
        matches=[u for u in users if isinstance(u,dict) and u.get('social_provider')==provider and u.get('social_subject')==subject]
        if len(matches)>1: raise ValueError('Ambiguous identity')
        if matches:
            selected.append(matches[0]); return
        if not auth._EMAIL_PATTERN.fullmatch(email) or len(email)>254 or '|' in email:
            raise ValueError('Email consent required')
        # Contact email never authorizes linking, admin status or password recovery.
        if email in auth._admin_emails() or any(isinstance(u,dict) and auth._normalized_email(u.get('email'))==email for u in users):
            raise ValueError('Use existing sign-in method')
        user={'account_id':str(uuid.uuid4()), 'company':'', 'email':email, 'password':'', 'session_version':0, 'plan':'Free', 'subscription_status':'Trial', 'registered_at':datetime.now(timezone.utc).isoformat(), 'role':'Owner', 'social_only':True, 'social_provider':provider, 'social_subject':subject}
        users.append(user); selected.append(user)
    locked_json_mutation(auth.USERS_FILE,[],find_or_create,list)
    auth.load_users()  # Provision the same isolated company record used by password signup.
    return selected[0]


def failure(lang='en', disabled=False):
    from app.auth import _auth_page
    message=('간편가입 연결이 준비되지 않았습니다. 이메일로 가입하거나 로그인해 주세요.' if disabled else '간편로그인을 완료하지 못했습니다. 다시 시도해 주세요. 이미 가입한 이메일이면 기존 로그인 방법을 이용해 주세요.') if lang=='ko' else ('This sign-in option is not connected yet. Please use email sign-in.' if disabled else 'Sign-in could not be completed. Try again; if this email already has an account, use its existing sign-in method.')
    response=_auth_page('login',error=message,lang=lang,status_code=503 if disabled else 400)
    response.headers['Cache-Control']='no-store'
    response.headers['Referrer-Policy']='no-referrer'
    return response


@router.get('/auth/{provider}/start')
def start(provider: str, request: Request, next: str='/', lang: str='en'):
    settings=config(provider)
    if not settings: return failure(lang,True)
    if request.url.netloc!=urlsplit(settings['origin']).netloc:
        return RedirectResponse(settings['origin']+f'/auth/{provider}/start?'+urlencode({'next':local_next(next),'lang':lang}),status_code=303)
    from app import auth
    rate_key = "oauth:" + str(request.client.host if request.client else "unknown")
    retry = auth._login_rate_retry_after(rate_key)
    if retry:
        response = failure(lang)
        response.status_code = 429
        response.headers["Retry-After"] = str(retry)
        return response
    auth._record_login_failure(rate_key)
    state,browser,record=create_state(provider,next,lang,settings)
    params={'response_type':'code','client_id':settings['client_id'],'redirect_uri':settings['redirect_uri'],'state':state}
    if settings.get('scope'):params['scope']=settings['scope']
    if provider in ('google','apple'):params['nonce']=record['nonce']
    if provider=='google':
        params['code_challenge']=base64.urlsafe_b64encode(hashlib.sha256(record['verifier'].encode()).digest()).decode().rstrip('=')
        params['code_challenge_method']='S256'
    if provider=='apple':params['response_mode']='form_post'
    response=RedirectResponse(settings['authorize']+'?'+urlencode(params),status_code=303)
    response.set_cookie('tp_oauth_'+provider,browser,max_age=TTL,httponly=True,secure=True,samesite='none' if provider=='apple' else 'lax',path='/')
    response.headers['Cache-Control']='no-store'
    response.headers['Referrer-Policy']='no-referrer'
    return response


def complete(provider, request, code, state, error=''):
    from app import auth
    settings=config(provider)
    if not settings: return failure(disabled=True)
    lang='en'
    try:
        record=consume_state(provider,state,request.cookies.get('tp_oauth_'+provider,''))
        lang=record['lang']
        if error or not code or len(code)>8192: raise ValueError('Cancelled or invalid code')
        if record['client_id']!=settings['client_id'] or record['redirect_uri']!=settings['redirect_uri']: raise ValueError('Configuration changed')
        subject,email=exchange_identity(provider,code,state,record,settings)
        user=account_for(provider,subject,email)
        auth._clear_login_failures('oauth:' + str(request.client.host if request.client else 'unknown'))
        response=RedirectResponse(record['next'],status_code=303)
        response.set_cookie(auth.SESSION_COOKIE,auth._session_token(user['email'],user.get('session_version',0)),max_age=auth.SESSION_MAX_AGE,httponly=True,secure=True,samesite='lax',path='/')
        from app.audit_log import record_audit
        record_audit(user['account_id'],user['email'],'Login','Account','',path=auth.USERS_FILE.with_name('audit_log.json'))
    except Exception:
        # Never render provider responses, codes, tokens or client secrets in errors/logs.
        response=failure(lang)
    response.delete_cookie('tp_oauth_'+provider,path='/',httponly=True,secure=True,samesite='none' if provider=='apple' else 'lax')
    response.headers['Cache-Control']='no-store'
    response.headers['Referrer-Policy']='no-referrer'
    return response


@router.get('/auth/{provider}/callback')
def callback(provider: str, request: Request, code: str='', state: str='', error: str=''):
    if provider=='apple': return failure()
    return complete(provider,request,code,state,error)


@router.post('/auth/apple/callback')
async def apple_callback(request: Request):
    body=bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body)>32768: return failure()
    from urllib.parse import parse_qs
    try:
        params=parse_qs(body.decode('utf-8',errors='strict'),max_num_fields=10)
    except (ValueError, UnicodeError):
        return failure()
    return await run_in_threadpool(complete,'apple',request,params.get('code',[''])[0],params.get('state',[''])[0],params.get('error',[''])[0])
