import json
import time
from urllib.parse import urlsplit, parse_qs

import pytest
from starlette.requests import Request
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

from app import auth, social_auth as social


def request(path='/auth/google/start', cookie='', host='app.example.com'):
    return Request({'type':'http','scheme':'https','method':'GET','path':path,'query_string':b'', 'headers':[(b'host',host.encode()),(b'cookie',cookie.encode())], 'server':(host,443), 'client':('127.0.0.1',1)})


@pytest.fixture
def setup(tmp_path, monkeypatch):
    auth._LOGIN_RATE_ENTRIES.clear()
    monkeypatch.setattr(auth,'USERS_FILE',tmp_path/'users.json')
    monkeypatch.setattr(social,'STATE_FILE',tmp_path/'oauth_states.json')
    monkeypatch.setenv('TRADE_PAPER_OAUTH_ORIGIN','https://app.example.com')
    for provider in social.PROVIDERS:
        prefix='TRADE_PAPER_'+provider.upper()+'_'
        monkeypatch.setenv(prefix+'ENABLED','1')
        monkeypatch.setenv(prefix+'CLIENT_ID',provider+'-client')
        monkeypatch.setenv(prefix+'CLIENT_SECRET','test-secret')
    return tmp_path


def test_config_gate_and_safe_origin(setup, monkeypatch):
    assert all(social.config(p) for p in social.PROVIDERS)
    for p in social.PROVIDERS:
        assert p in social.buttons()
        monkeypatch.delenv('TRADE_PAPER_'+p.upper()+'_ENABLED')
    assert social.buttons()==''
    assert social.start('google',request()).status_code==503
    monkeypatch.setenv('TRADE_PAPER_GOOGLE_ENABLED','1')
    for origin in ['http://app.example.com','https://user:pass@app.example.com','https://app.example.com/path','https://app.example.com?bad']:
        monkeypatch.setenv('TRADE_PAPER_OAUTH_ORIGIN',origin)
        assert social.config('google') is None


def test_start_pkce_cookie_and_state_binding(setup):
    response=social.start('google',request(),next='//evil.invalid',lang='ko')
    params=parse_qs(urlsplit(response.headers['location']).query)
    cookie=response.headers['set-cookie'].split(';')[0].split('=',1)[1]
    assert params['code_challenge_method']==['S256']
    assert params['scope']==['openid email']
    assert all(x in response.headers['set-cookie'].lower() for x in ['httponly','secure','samesite=lax'])
    with pytest.raises(ValueError):social.consume_state('google',params['state'][0],'other-browser')
    with pytest.raises(ValueError):social.consume_state('naver',params['state'][0],cookie)
    row=social.consume_state('google',params['state'][0],cookie)
    assert row['next']=='/' and row['lang']=='ko'
    with pytest.raises(ValueError):social.consume_state('google',params['state'][0],cookie)
    apple=social.start('apple',request('/auth/apple/start'))
    assert 'samesite=none' in apple.headers['set-cookie'].lower()
    assert 'response_mode=form_post' in apple.headers['location']
    other=social.start('google',request(host='www.app.example.com'))
    assert other.headers['location'].startswith('https://app.example.com/auth/google/start')
    assert 'set-cookie' not in other.headers


def test_state_expiration(setup, monkeypatch):
    state,browser,_=social.create_state('google','/','en',social.config('google'))
    now=time.time(); monkeypatch.setattr(social.time,'time',lambda:now+601)
    with pytest.raises(ValueError):social.consume_state('google',state,browser)


def test_subject_identity_no_email_linking_no_admin_or_password_recovery(setup, monkeypatch):
    user=social.account_for('naver','subject-a','first@example.com')
    assert user['plan']=='Free' and user['social_only'] and user['password']==''
    again=social.account_for('naver','subject-a','changed@example.com')
    assert again['account_id']==user['account_id'] and again['email']=='first@example.com'
    with pytest.raises(ValueError):social.account_for('google','different-subject','first@example.com')
    monkeypatch.setenv('TRADE_PAPER_ADMIN_EMAILS','first@example.com,admin@example.com')
    assert not auth.user_is_admin(user)
    with pytest.raises(ValueError):social.account_for('naver','bad','admin@example.com')
    user.update(password_reset_token_hash=auth._password_reset_token_hash('reset'),password_reset_expires_at=time.time()+300)
    auth.USERS_FILE.write_text(json.dumps([user]))
    assert auth._valid_password_reset_record('reset') is None


@pytest.mark.parametrize('provider',['google','apple'])
def test_id_token_signature_claims_and_nonce(setup, monkeypatch, provider):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()));jwk.update(kid='test',use='sig')
    monkeypatch.setattr(social,'fetch_json',lambda *a,**kw:{'keys':[jwk]})
    cfg=social.config(provider)
    claims={'sub':'subject','email':'hello@example.com','email_verified':True,'iss':'https://accounts.google.com' if provider=='google' else 'https://appleid.apple.com','aud':cfg['client_id'],'iat':int(time.time()),'exp':int(time.time())+600,'nonce':'nonce'}
    def token(data,signer=key): return jwt.encode(data,signer,algorithm='RS256',headers={'kid':'test'})
    assert social.verify_id_token(token(claims),cfg,{'nonce':'nonce'})==('subject','hello@example.com')
    for override in [{'aud':'wrong'}, {'iss':'https://evil.invalid'}, {'nonce':'wrong'}, {'exp':int(time.time())-1}, {'email_verified':False}, {'azp':'other-client'}]:
        with pytest.raises(Exception):social.verify_id_token(token({**claims,**override}),cfg,{'nonce':'nonce'})
    other=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    with pytest.raises(Exception):social.verify_id_token(token(claims,other),cfg,{'nonce':'nonce'})
    with pytest.raises(Exception):social.verify_id_token(jwt.encode(claims,'attack'*8,algorithm='HS256',headers={'kid':'test'}),cfg,{'nonce':'nonce'})


def test_kakao_and_naver_profile_validation(setup, monkeypatch):
    def fake(url,form=None,bearer=None):
        if form is not None:return {'access_token':'test-access'}
        if 'kakao' in url:return {'id':123,'kakao_account':{'email':'k@example.com','is_email_valid':True,'is_email_verified':True}}
        return {'resultcode':'00','response':{'id':'naver-sub','email':'n@example.com'}}
    monkeypatch.setattr(social,'fetch_json',fake)
    assert social.exchange_identity('kakao','code','state',{},social.config('kakao'))==('123','k@example.com')
    assert social.exchange_identity('naver','code','state',{},social.config('naver'))==('naver-sub','n@example.com')
    monkeypatch.setattr(social,'fetch_json',lambda url,form=None,bearer=None: {'access_token':'test-access'} if form else {'id':123,'kakao_account':{'email':'k@example.com','is_email_valid':True,'is_email_verified':False}})
    with pytest.raises(ValueError):social.exchange_identity('kakao','code','state',{},social.config('kakao'))


def test_callback_success_cancel_replay_and_sanitized_failure(setup, monkeypatch):
    cfg=social.config('google')
    state,browser,_=social.create_state('google','/import-document','ko',cfg)
    monkeypatch.setattr(social,'exchange_identity',lambda *a:('user-123','user@example.com'))
    req=request('/auth/google/callback','tp_oauth_google='+browser)
    result=social.complete('google',req,'code',state)
    assert result.status_code==303 and result.headers['location']=='/import-document'
    assert auth.SESSION_COOKIE+'=' in result.headers.getlist('set-cookie')[0]
    assert social.complete('google',req,'code',state).status_code==400
    state,browser,_=social.create_state('google','/','ko',cfg)
    req=request('/auth/google/callback','tp_oauth_google='+browser)
    result=social.complete('google',req,'code',state,'cancel')
    assert result.status_code==400 and '로그인' in result.body.decode()
    state,browser,_=social.create_state('google','/','en',cfg)
    def bad(*a):raise RuntimeError('DO_NOT_LEAK_SECRET')
    monkeypatch.setattr(social,'exchange_identity',bad)
    result=social.complete('google',request('/auth/google/callback','tp_oauth_google='+browser),'SECRET_CODE',state)
    assert 'DO_NOT_LEAK_SECRET' not in result.body.decode() and 'SECRET_CODE' not in result.body.decode()
    assert result.headers['cache-control']=='no-store'


def test_access_log_redacts_callback_credentials():
    import logging
    record=logging.LogRecord('uvicorn.access',20,'',0,'%s - "%s %s HTTP/%s" %d',('client','GET','/auth/google/callback?code=SECRET&state=SECRET','1.1',303),None)
    social.OAuthAccessLogFilter().filter(record)
    assert 'SECRET' not in record.getMessage()


def test_public_callback_paths_are_exact():
    assert '/auth/apple/callback' in social.PUBLIC_PATHS
    assert '/auth/evil/callback' not in social.PUBLIC_PATHS
    assert '/auth/google/callback/extra' not in social.PUBLIC_PATHS
