from __future__ import annotations

from datetime import datetime, timezone
import html
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import buyer, product, shipment
from app.account_company import load_account_company
from app.routers.company import ACCOUNT_COMPANIES_FILE
from app.storage import data_path, load_json_strict, locked_json_mutation


router = APIRouter()
ONBOARDING_FILE = data_path("onboarding.json")


def _account_id(request):
    return str((request.scope.get("trade_paper_user") or {}).get("account_id", "") or "").strip()


def state_for(account_id, path=None):
    return next((row for row in load_json_strict(path or ONBOARDING_FILE, [], list) if isinstance(row, dict) and row.get("account_id") == account_id), {})


def should_show(account_id):
    state = state_for(account_id)
    return not state.get("dismissed_at") and not state.get("completed_at")


def should_auto_show(account_id, path=None):
    state = state_for(account_id, path)
    return not state.get("started_at") and not state.get("dismissed_at") and not state.get("completed_at")


def mark(account_id, field, path=None):
    now = datetime.now(timezone.utc).isoformat()
    def update(rows):
        state = next((row for row in rows if isinstance(row, dict) and row.get("account_id") == account_id), None)
        if state is None:
            state = {"account_id": account_id}; rows.append(state)
        state[field] = now
    locked_json_mutation(path or ONBOARDING_FILE, [], update, list)


def progress(account_id):
    company = load_account_company(account_id, ACCOUNT_COMPANIES_FILE)
    checks = (bool(company.get("name")), bool(buyer.load_buyers(account_id)), bool(product.load_products(account_id)), bool(shipment.load_shipments(account_id)))
    completed = sum(checks)
    return {"checks": checks, "completed": completed, "percentage": completed * 25}


@router.get("/onboarding", response_class=HTMLResponse)
def onboarding_page(request: Request, replay: int = 0, next: str = ""):
    account_id = _account_id(request)
    if not replay and not should_show(account_id):
        return RedirectResponse("/", status_code=303)
    details = progress(account_id)
    if details["percentage"] == 100 and should_show(account_id):
        mark(account_id, "completed_at")
    steps = (
        ("Company", "/company?next=/onboarding"),
        ("Buyer", "/buyer-form"),
        ("Product", "/product-form"),
        ("Export Wizard", "/export-wizard"),
    )
    cards = "".join(f'<a class="step{" done" if details["checks"][index] else ""}" href="{url}"><span>{"✓" if details["checks"][index] else index + 1}</span><b>{label}</b><small>{"Complete" if details["checks"][index] else "Continue"}</small></a>' for index, (label, url) in enumerate(steps))
    next_target = next if next.startswith("/") and not next.startswith("//") else "/"
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Getting Started</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#f3f4f6;color:#111827;font-family:Arial}}main{{width:min(880px,calc(100% - 32px));margin:50px auto;padding:34px;background:#fff;border-radius:18px}}header{{text-align:center}}h1{{font-size:38px;margin-bottom:8px}}p{{color:#64748b}}.progress-label{{display:flex;justify-content:space-between;margin-top:30px;font-weight:800}}.track{{height:12px;margin:10px 0 26px;overflow:hidden;border-radius:999px;background:#e5e7eb}}.track span{{display:block;height:100%;background:#2563eb}}.steps{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.step{{display:grid;gap:9px;padding:20px;border:1px solid #dbe3ee;border-radius:14px;color:#111827;text-decoration:none}}.step span{{display:grid;width:30px;height:30px;place-items:center;border-radius:50%;background:#e5e7eb}}.step small{{color:#64748b}}.step.done{{background:#f0fdf4;border-color:#bbf7d0}}.step.done span{{background:#dcfce7;color:#166534}}.actions{{display:flex;justify-content:center;gap:12px;margin-top:28px}}button,.actions a{{padding:12px 17px;border:0;border-radius:9px;background:#111827;color:#fff;text-decoration:none;font-weight:800;cursor:pointer}}button{{background:#e5e7eb;color:#111827}}@media(max-width:700px){{.steps{{grid-template-columns:1fr 1fr}}}}@media(max-width:440px){{.steps{{grid-template-columns:1fr}}}}</style></head><body><main><header><span>FIRST LOGIN GUIDE</span><h1>Set up your export workspace</h1><p>Complete the four steps now, or skip and return from the Dashboard later.</p></header><div class="progress-label"><span>Progress</span><strong>{details["percentage"]}%</strong></div><div class="track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{details["percentage"]}"><span style="width:{details["percentage"]}%"></span></div><div class="steps">{cards}</div><div class="actions"><form method="post" action="/onboarding/skip"><input type="hidden" name="next" value="{html.escape(next_target, quote=True)}"><button type="submit">Skip for now</button></form><a href="/">Dashboard</a></div></main></body></html>''')


@router.post("/onboarding/skip")
def skip_onboarding(request: Request, next: str = Form("/")):
    mark(_account_id(request), "dismissed_at")
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    return RedirectResponse(target, status_code=303)
