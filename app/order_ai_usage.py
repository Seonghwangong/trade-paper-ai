"""Public beta allowance, separate from provider request/cost protection.

Only hashes, time, quota bucket and outcome are retained. No order or AI result.
Failed calls consume the global request budget but not the customer allowance.
"""
from datetime import datetime, timezone
import hashlib
import os
import re
import sqlite3

from fastapi import HTTPException


def entitlement(account, now=None):
    from app import paddle_live_runtime
    now = now or datetime.now(timezone.utc)
    try:
        _, decision = paddle_live_runtime.account_state(account, now=now)
    except HTTPException:
        raise HTTPException(503, 'ai_unavailable') from None
    if (os.environ.get('TRADE_PAPER_PADDLE_LIVE_ACCESS') == '1'
            and decision is not None and decision.starter_access
            and decision.period_start is not None):
        return {'kind': 'starter', 'bucket': decision.period_start.isoformat(),
                'limit': 20, 'resets_at': decision.access_until.isoformat()}
    # No entitlement from a browser plan name or stale users.json.
    return {'kind': 'free', 'bucket': 'lifetime', 'limit': 1, 'resets_at': None}


def _owner(account):
    return hashlib.sha256(account.encode()).hexdigest()


def _initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS requests (day TEXT NOT NULL, owner TEXT NOT NULL)')
    db.execute('''CREATE TABLE IF NOT EXISTS public_ai_requests (
        id TEXT PRIMARY KEY, owner TEXT NOT NULL, bucket TEXT NOT NULL,
        started REAL NOT NULL, state TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS public_ai_owner ON public_ai_requests(owner,bucket)')


def _used(db, owner, bucket):
    return db.execute("SELECT count(*) FROM public_ai_requests WHERE owner=? AND bucket=? "
                      "AND state IN ('pending','succeeded','uncertain')", (owner, bucket)).fetchone()[0]


def summary(path, account):
    access = entitlement(account)
    used = 0
    if path.exists():
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='public_ai_requests'").fetchone():
                used = _used(db, _owner(account), access['bucket'])
    return {k: access[k] for k in ('kind','limit','resets_at')} | {
        'used': used, 'remaining': max(0, access['limit'] - used)}


def reserve(path, account, request_id):
    if not isinstance(request_id, str) or not re.fullmatch(r'[a-f0-9-]{36}', request_id):
        raise HTTPException(422, 'ai_request_id')
    now = datetime.now(timezone.utc)
    access = entitlement(account, now)
    owner = _owner(account)
    # Account scoping prevents another tenant's supplied UUID from colliding.
    identity = hashlib.sha256((owner + request_id).encode()).hexdigest()
    stamp = now.strftime('%Y-%m-%d')
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=5) as db:
        os.chmod(path, 0o600)
        _initialize(db)
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM public_ai_requests WHERE id=?', (identity,)).fetchone():
            raise HTTPException(409, 'ai_duplicate')
        # A crashed/uncertain request may have reached the provider; do not
        # automatically refund it or send it again. Release only its busy lock.
        db.execute("UPDATE public_ai_requests SET state='uncertain' WHERE state='pending' AND started<?",
                   (now.timestamp() - 120,))
        if db.execute("SELECT 1 FROM public_ai_requests WHERE owner=? AND state='pending'", (owner,)).fetchone():
            raise HTTPException(409, 'ai_busy')
        if _used(db, owner, access['bucket']) >= access['limit']:
            raise HTTPException(429, 'ai_allowance')
        monthly = db.execute('SELECT count(*) FROM requests WHERE day LIKE ?', (stamp[:7]+'%',)).fetchone()[0]
        daily = db.execute('SELECT count(*) FROM requests WHERE day=?', (stamp,)).fetchone()[0]
        own = db.execute('SELECT count(*) FROM requests WHERE day=? AND owner=?', (stamp, owner)).fetchone()[0]
        if monthly >= 20 or daily >= 5 or own >= 3:
            raise HTTPException(429, 'ai_limit')
        db.execute('INSERT INTO requests VALUES (?,?)', (stamp, owner))
        db.execute('INSERT INTO public_ai_requests VALUES (?,?,?,?,?)',
                   (identity, owner, access['bucket'], now.timestamp(), 'pending'))
        db.execute('DELETE FROM requests WHERE day < ?', (stamp[:7]+'-01',))
    return identity


def finish(path, identity, *, success):
    try:
        with sqlite3.connect(path, timeout=5) as db:
            db.execute("UPDATE public_ai_requests SET state=? WHERE id=? AND state='pending'",
                       ('succeeded' if success else 'failed', identity))
    except sqlite3.Error:
        # Leave reservation counted; never silently retry or grant extra use.
        raise HTTPException(503, 'ai_unavailable') from None
