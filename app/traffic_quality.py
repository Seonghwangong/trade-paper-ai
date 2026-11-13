"""Admin-only traffic/account evidence, without anonymous identity tracking."""
from datetime import datetime, timezone
import html
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import analytics, auth
from app.intake_quality import needs_intake_review
from app.storage import data_path, load_json_strict, locked_json_mutation

router = APIRouter()


def internal_account_ids(users=None):
    users = users if users is not None else load_json_strict(auth.USERS_FILE, [], list)
    marked = load_json_strict(data_path("analytics_internal_accounts.json"), [], list)
    return {str(value) for value in marked if isinstance(value, str)} | {
        str(user.get("account_id")) for user in users
        if isinstance(user, dict) and user.get("account_id") and auth.user_is_admin(user)
    }


def account_evidence(now=None):
    from app.paddle_live_runtime import account_state, LiveBillingUnavailable
    now = now or datetime.now(timezone.utc)
    users = [u for u in load_json_strict(auth.USERS_FILE, [], list) if isinstance(u, dict)]
    internal = internal_account_ids(users)
    event_rows = analytics.events()
    invoices = load_json_strict(data_path("invoices.json"), [], list)
    packing = load_json_strict(data_path("packing_lists.json"), [], list)
    # Workspace = account_id. Team members are not counted as new companies.
    grouped = {}
    for user in users:
        account = str(user.get("account_id", "") or "").strip()
        if account:
            grouped.setdefault(account, []).append(user)
    result = []
    for account, members in grouped.items():
        category = "Internal" if account in internal else (
            "Needs review" if any(needs_intake_review(u.get("company")) for u in members) else "Other accounts")
        inv_ids = {str(row.get("invoice_no")) for row in invoices
                   if isinstance(row, dict) and row.get("account_id") == account and row.get("invoice_no")}
        has_pair = any(isinstance(row, dict) and row.get("account_id") == account
                       and row.get("invoice_no") in inv_ids for row in packing)
        stages = {row["event"] for row in event_rows if row["account_id"] == account}
        try:
            _, decision = account_state(account, now=now)
            paid = bool(decision and decision.starter_access and decision.provider_status == "active")
        except LiveBillingUnavailable:
            paid = None
        result.append({"account_id": account, "category": category,
                       "label": members[0].get("company") or "Unnamed workspace",
                       "emails": ", ".join(str(u.get("email", "")) for u in members),
                       "signup_event": "Signup" in stages,
                       "invoice": bool(inv_ids), "pair": has_pair,
                       "paid": paid})
    return result


@router.post("/admin/growth/internal")
def mark_internal(request: Request, account_id: str = Form(...), internal: str = Form(...)):
    auth.require_admin(request)
    # SameSite cookies plus a strict same-origin form check; never accept a
    # cross-site account-classification submission.
    origin = request.headers.get("origin", "")
    if not origin or urlsplit(origin).netloc != request.url.netloc or urlsplit(origin).scheme != request.url.scheme:
        raise HTTPException(403, "Same-origin form required")
    users = load_json_strict(auth.USERS_FILE, [], list)
    if internal not in {"yes", "no"} or not any(isinstance(u, dict) and u.get("account_id") == account_id for u in users):
        raise HTTPException(422, "Unknown account or classification")
    def update(rows):
        rows[:] = [value for value in rows if value != account_id]
        if internal == "yes":
            rows.append(account_id)
    locked_json_mutation(data_path("analytics_internal_accounts.json"), [], update, list)
    return RedirectResponse("/admin/growth", status_code=303)


def _escape(value):
    return html.escape(str(value), quote=True)


@router.get("/admin/growth", response_class=HTMLResponse)
def growth_dashboard(request: Request):
    auth.require_admin(request)
    evidence = account_evidence()
    traffic_rows = []
    for category in analytics.AUDIENCES:
        metric = analytics.visitor_metrics(audience=category)
        traffic_rows.append("<tr>" + "".join(f"<td>{_escape(value)}</td>" for value in (
            category, metric["visits"], metric["pages"]["Practice Page"],
            metric["practice"]["Practice Started"], metric["practice"]["Practice Pair Generated"],
            metric["practice"]["Practice Invoice Download Click"], metric["practice"]["Practice Packing Download Click"])) + "</tr>")
    cohort_rows = []
    for category in ("Other accounts", "Internal", "Needs review"):
        rows = [row for row in evidence if row["category"] == category]
        cohort_rows.append("<tr>" + "".join(f"<td>{_escape(value)}</td>" for value in (
            category, len(rows), sum(r["signup_event"] for r in rows), sum(r["invoice"] for r in rows),
            sum(r["pair"] for r in rows), sum(r["paid"] is True for r in rows),
            sum(r["paid"] is None for r in rows))) + "</tr>")
    account_rows = []
    for row in evidence:
        value = "no" if row["category"] == "Internal" else "yes"
        label = "Remove manual internal mark" if value == "no" else "Mark as internal test"
        account_rows.append(f'<tr><td>{_escape(row["label"])}</td><td>{_escape(row["emails"])}</td><td>{_escape(row["category"])}</td><td><form method="post" action="/admin/growth/internal"><input type="hidden" name="account_id" value="{_escape(row["account_id"])}"><input type="hidden" name="internal" value="{value}"><button>{label}</button></form></td></tr>')
    return HTMLResponse('''<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width, initial-scale=1"><title>Traffic quality · Trade Paper AI</title><style>body{font:16px system-ui;margin:32px auto;padding:0 20px;max-width:1200px;color:#172033;background:#f8fafc}section{margin:28px 0;background:white;padding:20px;border:1px solid #dbe1ea;border-radius:12px}table{border-collapse:collapse;width:100%}td,th{padding:12px;text-align:left;border-bottom:1px solid #dbe1ea}th{background:#eef2ff}.scroll{overflow:auto}p{line-height:1.6}button,a{color:#1e40af}button{padding:9px;cursor:pointer}h1{font-size:28px}</style></head><body>
<a href="/admin/dashboard">← Admin Dashboard</a><h1>Traffic quality / 유입과 실제 사용 구분</h1>
<p>전체 누적 기록입니다. 내부 테스트와 자동화 징후를 따로 표시하며, 과거 익명 기록은 소급 추정하지 않습니다.</p>
<section><h2>Traffic and practice / 유입·체험 횟수</h2><p>Unclassified는 외부 고객 또는 사람임이 확인된 수치가 아닙니다. Automation hint는 알려진 User-Agent 문자열에 따른 참고 분류이며 봇 확정값이 아닙니다. Legacy unknown은 분류 도입 전 기록입니다. 생성·다운로드는 횟수이며 고유 사용자나 파일 저장 완료를 뜻하지 않습니다.</p>
<p>운영자 QA에는 <a href="/try-sample?lang=ko&amp;traffic=internal">한국어 테스트 링크</a> 또는 <a href="/try-sample?lang=en&amp;traffic=internal">English test link</a>를 사용하세요. 테스트 표시가 체험 요청까지 이어집니다. 일반 고객에게는 traffic=internal 없는 링크를 보냅니다.</p>
<div class="scroll"><table><thead><tr><th>구분</th><th>Page views</th><th>Practice page</th><th>Started</th><th>Pairs generated</th><th>Invoice clicks</th><th>Packing clicks</th></tr></thead><tbody>''' + "".join(traffic_rows) + '''</tbody></table></div></section>
<section><h2>Account evidence / 계정별 가입·사용·결제 근거</h2><p>동일 계정 ID의 작업공간을 한 번만 셉니다. Other accounts는 내부·검토 대상 외 계정이며 고객 확인을 의미하지 않습니다. 가입 이벤트 연결은 개선 적용 이후부터 가능하고, 기존 가입 이벤트는 소급 연결하지 않습니다. Pair는 같은 계정의 저장된 송장과 연결된 패킹리스트이며 익명 체험과 별개입니다. 익명 체험에서 가입까지 사람 단위 추적이나 전환율은 제공하지 않습니다.</p>
<p>Active paid는 현재 서명 검증된 Paddle 원장에 유효한 결제 기간이 확인되는 활성 구독입니다. 누적 매출·첫 고객·갱신 횟수가 아닙니다. 결제 원장 확인 실패는 Unknown에 표시합니다. 내부 계정은 별도 행에 남기며 원본 계정·문서·결제 기록을 변경하지 않습니다.</p>
<div class="scroll"><table><thead><tr><th>구분</th><th>Workspaces</th><th>Linked signup</th><th>Has invoice</th><th>Has saved pair</th><th>Active paid</th><th>Billing unknown</th></tr></thead><tbody>''' + "".join(cohort_rows) + '''</tbody></table></div></section>
<section><h2>Internal account classification / 내부 계정 지정</h2><p>직접 확인한 운영·테스트 계정만 지정하세요. 회사명만으로 고객·스팸 여부를 판단하지 않습니다. 서버에 설정된 관리자 계정은 자동으로 Internal이며 수동 표시 제거로 해제되지 않습니다. 이 분류는 통계에만 영향을 주며 권한·구독을 변경하지 않습니다.</p><div class="scroll"><table><thead><tr><th>Workspace</th><th>Account emails</th><th>구분</th><th>작업</th></tr></thead><tbody>''' + "".join(account_rows) + '</tbody></table></div></section></body></html>', headers={"Cache-Control": "no-store"})
