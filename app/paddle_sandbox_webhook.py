"""Opt-in sandbox-only webhook. Never changes application subscription access."""
import json
import os
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Request
from app.paddle_sandbox_core import MAX_BODY, SandboxStore, verify
from app.storage import data_path
import time

router = APIRouter()
WEBHOOK_PATH = "/webhooks/paddle-sandbox"


@lru_cache(maxsize=1)
def sandbox_store():
    return SandboxStore(data_path("paddle_sandbox.sqlite3"))


@router.post(WEBHOOK_PATH)
async def paddle_sandbox_webhook(request: Request):
    if os.environ.get("TRADE_PAPER_PADDLE_SANDBOX_ENABLED") != "1":
        raise HTTPException(404, "Not found")
    secret = os.environ.get("TRADE_PAPER_PADDLE_SANDBOX_WEBHOOK_SECRET", "")
    price = os.environ.get("TRADE_PAPER_PADDLE_SANDBOX_PRICE_ID", "")
    if not secret or not price.startswith("pri_"):
        raise HTTPException(503, "Sandbox webhook not configured")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_BODY:
            raise HTTPException(413, "Event too large")
    body = bytes(raw)
    verify(body, request.headers.get("paddle-signature", ""), secret, time.time())
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Invalid JSON") from None
    result = sandbox_store().apply(payload, body, price)
    return {"environment": "sandbox", "result": result}
