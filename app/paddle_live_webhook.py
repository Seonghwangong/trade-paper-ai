"""Default-off Live webhook; signature authentication replaces browser sessions."""
import json
import os
import sqlite3
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app import paddle_live_runtime as runtime
from app.paddle_live_store import BillingConflict
from app.paddle_sandbox_core import MAX_BODY, verify

router = APIRouter()
WEBHOOK_PATH = '/webhooks/paddle-live'


def reply(code, **body):
    return JSONResponse(body, status_code=code, headers={'Cache-Control': 'no-store'})


def process(raw, signature, secret):
    now = time.time()
    # Reject unsigned traffic before even opening/creating the Live database.
    verify(raw, signature, secret, now)
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError('Invalid event envelope')
    # Refund/cancellation evidence must still work if the sales catalog is
    # unavailable or changed; only new checkout binding requires its contract.
    offer = runtime.offer() if payload.get('event_type') == 'transaction.completed' else None
    ledger = runtime.store()
    return ledger.apply_signed_event(raw, signature, secret=secret, offer=offer, now=now)


@router.post(WEBHOOK_PATH)
async def live_webhook(request: Request):
    if os.environ.get('TRADE_PAPER_PADDLE_LIVE_WEBHOOK') != '1':
        return reply(404, detail='Not found')
    secret = os.environ.get('TRADE_PAPER_PADDLE_LIVE_WEBHOOK_SECRET', '')
    if not secret or secret == os.environ.get('TRADE_PAPER_PADDLE_SANDBOX_WEBHOOK_SECRET'):
        return reply(503, detail='Live webhook is not configured')
    try:
        runtime.price_id()
        length = request.headers.get('content-length')
        if length is not None:
            if not length.isdecimal():
                return reply(400, detail='Invalid content length')
            if int(length) > MAX_BODY:
                return reply(413, detail='Event too large')
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY:
                return reply(413, detail='Event too large')
        result = await run_in_threadpool(process, bytes(body), request.headers.get('paddle-signature', ''), secret)
        if result == 'unregistered':
            return reply(409, detail='Checkout registration required; retry after reconciliation')
        return reply(200, environment='live', result=result)
    except runtime.LiveBillingUnavailable:
        return reply(503, detail='Live billing storage is unavailable')
    except HTTPException as exc:
        return reply(exc.status_code, detail='Invalid webhook signature')
    except BillingConflict:
        return reply(409, detail='Billing state requires retry or reconciliation')
    except ValueError:
        return reply(400, detail='Invalid webhook event')
    except (OSError, sqlite3.Error):
        return reply(503, detail='Live billing storage is unavailable')
