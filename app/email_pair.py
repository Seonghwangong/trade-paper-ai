"""Private, short-lived PDF pairs reviewed before a single delivery attempt."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
import sqlite3
import time
from typing import Annotated
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app import document_email as email, email_delivery
from app.packing_workflow import comparable, fingerprint
from app.storage import data_path, locked_json_mutation
from app.ui import page_shell
from app.validation import DataValidationError

router = APIRouter()
REVIEW_DB = data_path("email_pair_reviews.sqlite3")
TTL_SECONDS = 3600
MAX_PAIR_BYTES = 20 * 1024 * 1024
PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "Referrer-Policy": "same-origin"}
esc = email.html_attr


def _lang(request):
    return "ko" if request.query_params.get("lang", request.cookies.get("tp_language", "en")) == "ko" else "en"


def _text(request, en, ko):
    return ko if _lang(request) == "ko" else en


def _url(path, request):
    return f"{path}?lang={_lang(request)}"


def _post_guard(request):
    # SameSite cookies are a second guard; explicitly reject cross-site form posts.
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin != str(request.base_url).rstrip("/")):
        raise HTTPException(403, "Cross-site request rejected")


@contextmanager
def _database():
    REVIEW_DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(REVIEW_DB, timeout=10)
    try:
        os.chmod(REVIEW_DB, 0o600)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA secure_delete=ON")
        db.execute("""CREATE TABLE IF NOT EXISTS reviews (
            id TEXT PRIMARY KEY, account_id TEXT NOT NULL, expires REAL NOT NULL,
            state TEXT NOT NULL, metadata TEXT NOT NULL, invoice BLOB, packing BLOB)""")
        # Remove expired content on access, including failed/unconfirmed attempts.
        db.execute("DELETE FROM reviews WHERE expires < ?", (time.time(),))
        db.commit()
        yield db
    finally:
        db.close()


def _context(packing_no, account):
    from app import invoice, packing
    _, _, packed = email._document("packing", packing_no, account)
    _, _, source = email._document("invoice", str(packed.get("invoice_no", "")), account)
    company = invoice.load_account_company(account, invoice.ACCOUNT_COMPANIES_FILE)
    buyer = packing._buyer_master(account, packed.get("buyer", ""))
    return deepcopy({"invoice": source, "packing": packed, "company": company, "buyer": buyer})


def _load(token, account):
    with _database() as db:
        row = db.execute("SELECT * FROM reviews WHERE id=? AND account_id=?", (token, account)).fetchone()
    if row is None:
        raise HTTPException(404, "Review unavailable or expired. Prepare the two PDFs again.")
    row = dict(row)
    row["metadata"] = json.loads(row["metadata"])
    # Revalidate current ownership and linkage even for existing frozen files.
    context = _context(row["metadata"]["packing_no"], account)
    if str(context["invoice"]["invoice_no"]) != row["metadata"]["invoice_no"]:
        raise HTTPException(409, "Document link changed. Prepare the two PDFs again.")
    return row, context


STYLES = """*{box-sizing:border-box}body{margin:0;background:#f3f5f7;color:#172c43;font:16px/1.6 Arial,sans-serif}
.pair-review{max-width:1200px;margin:32px auto;padding:0 24px}h1{font-size:30px;line-height:1.25}h2{font-size:21px}
.pair-review section,.pair-review form{background:#fff;border:1px solid #dbe2e9;border-radius:16px;padding:24px;margin:20px 0}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}.pair section{margin:0;min-width:0}
iframe{width:100%;height:470px;border:1px solid #dbe2e9;border-radius:8px;background:#f3f5f7}
a{color:#244d71}button,.action{display:inline-block;background:#183b58;color:white;padding:12px 20px;border:0;border-radius:9px;text-decoration:none;font:inherit;cursor:pointer}
label{display:block;margin:14px 0 5px}input:not([type=checkbox]),textarea{width:100%;padding:12px;border:1px solid #aebdcc;border-radius:8px;font:inherit}textarea{min-height:130px}
.notice{border-left:4px solid #b4873e;padding:12px 18px;background:#fff7e9}.muted{color:#536679}button:disabled{opacity:.6}p{overflow-wrap:anywhere}
.tp-release-footer{padding:24px;font-size:12px}.tp-release-footer span,.tp-release-footer a{margin-right:10px}
@media(max-width:760px){.pair-review{padding:0 16px;margin:20px auto}.pair{grid-template-columns:1fr}.pair-review section,.pair-review form{padding:16px}iframe{height:360px}}
"""


def _page(request, content, status=200):
    title = _text(request, "Review and send both PDFs", "두 PDF 확인 후 함께 보내기")
    return HTMLResponse(page_shell(title, content, styles=STYLES, lang=_lang(request), main_class="pair-review"), status_code=status, headers=PRIVATE_HEADERS)


@router.get("/email-pair/prepare/{packing_no}")
def prepare_form(packing_no: str, request: Request):
    context = _context(packing_no, email._account_id(request))
    source, packed = context["invoice"], context["packing"]
    target = _url(f"/email-pair/prepare/{quote(packing_no, safe='')}", request)
    content = f'<p translate="no">Invoice {esc(source["invoice_no"])} + Packing List {esc(packing_no)}</p>'
    if comparable(source) != comparable(packed):
        content += f'<p class="notice">{_text(request, "Shared fields differ. Compare and correct the documents before preparing the pair.", "거래처·품목·수량 등 공통 항목이 다릅니다. 비교 후 수정해 주세요.")}</p>'
        content += f'<a class="action" href="/packing-sync/{quote(packing_no, safe="")}">{_text(request, "Compare with Invoice", "Invoice와 비교")}</a>'
    else:
        content += f'<p>{_text(request, "Prepare two private PDF copies to review together. They are available for one hour. This step does not send an email or create new documents.", "두 PDF 사본을 만들어 함께 확인합니다. 사본은 1시간 동안 이용할 수 있습니다. 이 단계에서는 메일을 보내거나 새 문서를 생성하지 않습니다.")}</p>'
        content += f'<form method="post" action="{esc(target)}" data-native-submit="true"><button type="submit">{_text(request, "Prepare both PDFs", "두 PDF 준비하기")}</button></form>'
    content += f'<p><a href="/send-email/packing/{quote(packing_no, safe="")}">{_text(request, "Back to document email", "문서 이메일로 돌아가기")}</a></p>'
    return _page(request, content)


@router.post("/email-pair/prepare/{packing_no}")
def prepare_pair(packing_no: str, request: Request):
    _post_guard(request)
    account = email._account_id(request)
    context = _context(packing_no, account)
    source, packed = context["invoice"], context["packing"]
    if comparable(source) != comparable(packed):
        return prepare_form(packing_no, request)
    from app import invoice, packing
    try:
        responses = (invoice.create_invoice_pdf(invoice.public_invoice(source), context["company"]),
                     packing.create_packing_list_pdf(packing.public_packing(packed), context["company"], context["buyer"]))
        if any(r.status_code != 200 or r.media_type != "application/pdf" or not r.body.startswith(b"%PDF") for r in responses):
            raise DataValidationError("PDF", "PDF generation failed.", "Review both documents.")
    except DataValidationError as error:
        back = _url('/email-pair/prepare/' + quote(packing_no, safe=''), request)
        return _page(request, f'<p role="alert">{esc(error.reason)} {esc(error.correction)}</p><a href="{esc(back)}">Back / 돌아가기</a>', 400)
    if sum(len(r.body) for r in responses) > MAX_PAIR_BYTES:
        raise HTTPException(413, "The two PDFs exceed the 20 MB attachment limit.")
    token, now = uuid4().hex, time.time()
    invoice_no = str(source["invoice_no"])
    metadata = {"invoice_no": invoice_no, "packing_no": packing_no, "fingerprint": fingerprint(context),
                "prepared_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                "recipient": email._recipient(source, "invoice", account),
                "shipment_no": email._linked_shipment_no(packed, "packing", account),
                "buyer": str(source.get("buyer", "")),
                "filenames": [f"Invoice-{invoice_no}.pdf", f"PackingList-{packing_no}.pdf"]}
    with _database() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT COUNT(*) FROM reviews WHERE account_id=?", (account,)).fetchone()[0] >= 10:
            raise HTTPException(429, "Ten PDF reviews are already prepared. Use an existing review or wait for expiry.")
        db.execute("INSERT INTO reviews VALUES (?,?,?,?,?,?,?)",
                   (token, account, now + TTL_SECONDS, "ready", json.dumps(metadata), responses[0].body, responses[1].body))
        db.commit()
    return RedirectResponse(_url(f"/email-pair/review/{token}", request), 303)


def _review_page(token, row, context, request, *, values=None, error="", status=200):
    meta = row["metadata"]
    stale = fingerprint(context) != meta["fingerprint"]
    content = f'<p translate="no">Invoice {esc(meta["invoice_no"])} + Packing List {esc(meta["packing_no"])}</p>'
    content += f'<p translate="no">{esc(meta["buyer"])}</p>'
    content += f'<p class="muted">{_text(request, "Copies prepared at (UTC)", "사본 생성 시각 (UTC)")}: <span translate="no">{esc(meta["prepared_at"])}</span></p>'
    content += f'<p>{_text(request, "The two PDFs below are the exact files that will be attached. Available for one hour; previous downloads stay unchanged. Check actual cartons and weights.", "아래 두 PDF가 그대로 첨부됩니다. 사본은 1시간 동안 이용할 수 있으며 이전 다운로드는 바뀌지 않습니다. 실제 박스·중량도 확인해 주세요.")}</p>'
    if error:
        content += f'<p class="notice" role="alert">{esc(error)}</p>'
    content += '<div class="pair">'
    for index, name in enumerate(meta["filenames"]):
        url = f"/email-pair/pdf/{token}/{index}"
        content += f'<section><h2>{"Commercial Invoice" if index == 0 else "Packing List"}</h2><p translate="no">{esc(name)}</p><iframe title="{esc(name)}" src="{url}#navpanes=0&amp;view=FitH"></iframe><p><a href="{url}" target="_blank" rel="noopener">{_text(request, "Open this PDF", "PDF 크게 보기")}</a></p></section>'
    content += '</div>'
    if stale:
        content += f'<p class="notice">{_text(request, "Source details changed after preparation. Prepare and review new copies before sending.", "PDF 준비 후 원본 정보가 바뀌었습니다. 새 사본을 준비하고 다시 확인해 주세요.")}</p>'
    elif row["state"] == "ready":
        recipient, subject, body = values or (meta["recipient"], f'Invoice {meta["invoice_no"]} and Packing List {meta["packing_no"]}', 'Dear Partner,\n\nPlease find the Commercial Invoice and Packing List attached.\n\nBest regards')
        action = _url(f"/email-pair/send/{token}", request)
        content += f'''<form method="post" action="{esc(action)}" data-native-submit="true">
<label for="recipient">{_text(request, "Recipient", "받는 사람")}</label><input id="recipient" name="recipient" type="email" required value="{esc(recipient)}">
<label for="subject">{_text(request, "Subject", "제목")}</label><input id="subject" name="subject" maxlength="200" required value="{esc(subject)}">
<label for="body">{_text(request, "Message", "본문")}</label><textarea id="body" name="body" maxlength="10000" required>{esc(body)}</textarea>
<label><input type="checkbox" name="confirmed" value="yes" required> {_text(request, "I checked both PDFs, recipient, quantities, cartons and weights.", "두 PDF와 수신자·수량·박스·중량을 확인했습니다.")}</label>
<button type="submit">{_text(request, "Send these two PDFs", "확인한 두 PDF 보내기")}</button></form>'''
    else:
        content += f'<p class="notice">{_text(request, "A send attempt is already recorded. Do not retry until you have checked delivery history and the recipient inbox; delivery may be unconfirmed.", "이미 발송을 시도한 사본입니다. 전송 결과가 불확실할 수 있으므로 발송 이력과 수신함을 확인하기 전에는 다시 보내지 마세요.")}</p>'
    back = _url('/email-pair/prepare/' + quote(meta['packing_no'], safe=''), request)
    content += f'<p><a href="{esc(back)}">{_text(request, "Prepare a new review", "새 사본 준비")}</a> · <a href="/packing-list">{_text(request, "Back to documents", "문서 목록")}</a></p>'
    return _page(request, content, status)


@router.get("/email-pair/review/{token}")
def review_pair(token: str, request: Request):
    row, context = _load(token, email._account_id(request))
    if row["state"] == "success":
        return RedirectResponse(f"/email-result/{token}", 303)
    return _review_page(token, row, context, request)


@router.get("/email-pair/pdf/{token}/{index}")
def review_pdf(token: str, index: int, request: Request):
    row, _ = _load(token, email._account_id(request))
    if index not in (0, 1):
        raise HTTPException(404, "PDF not found")
    filename = row["metadata"]["filenames"][index]
    return Response(row["invoice" if index == 0 else "packing"], media_type="application/pdf",
                    headers={**PRIVATE_HEADERS, "X-Content-Type-Options": "nosniff",
                             "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename, safe='')}"})


@router.post("/email-pair/send/{token}")
def send_pair(token: str, request: Request, recipient: Annotated[str, Form()], subject: Annotated[str, Form()],
              body: Annotated[str, Form()], confirmed: Annotated[str, Form()] = ""):
    _post_guard(request)
    account = email._account_id(request)
    row, context = _load(token, account)
    if row["state"] == "success":
        return RedirectResponse(f"/email-result/{token}", 303)
    values = recipient.strip(), subject.strip(), body.strip()
    recipient, subject, body = values
    if (confirmed != "yes" or not email._valid_recipient(recipient) or not subject or len(subject) > 200
            or "\r" in subject or "\n" in subject or not body or len(body) > 10000):
        return _review_page(token, row, context, request, values=values,
                            error=_text(request, "Check both PDFs and enter a valid recipient, subject and message.", "두 PDF를 확인하고 올바른 수신자·제목·본문을 입력해 주세요."), status=400)
    if row["state"] != "ready" or fingerprint(context) != row["metadata"]["fingerprint"]:
        return _review_page(token, row, context, request, values=values, status=409)
    # Commit before calling the provider: double clicks, races and process restarts
    # cannot send this review twice. An ambiguous failure is never auto-retried.
    with _database() as db:
        claimed = db.execute("UPDATE reviews SET state='sending' WHERE id=? AND account_id=? AND state='ready' AND expires>=?",
                             (token, account, time.time())).rowcount
        db.commit()
    if not claimed:
        raise HTTPException(409, "This review was already submitted or expired. Check delivery history.")
    meta = row["metadata"]
    attachments = tuple(email_delivery.EmailAttachment(name, row[key]) for name, key in zip(meta["filenames"], ("invoice", "packing")))
    message = email_delivery.DeliveryMessage(recipient, subject, body, f'<p>{esc(body).replace(chr(10), "<br>")}</p>', "document_delivery", attachments, retry_transient=False)
    success = email_delivery.deliver_email(message)
    entry = {"id": token, "account_id": account, "sent_at": datetime.now(timezone.utc).isoformat(),
             "document_type": "packing", "document_no": meta["packing_no"], "shipment_no": meta["shipment_no"],
             "recipient": recipient, "subject": subject, "status": "Success" if success else "Failed",
             "attachment_prepared_at": meta["prepared_at"], "attachments": [
                 {"filename": a.filename, "sha256": hashlib.sha256(a.content).hexdigest()} for a in attachments]}
    locked_json_mutation(email.HISTORY_FILE, [], lambda rows: rows.append(entry), list)
    with _database() as db:
        db.execute("UPDATE reviews SET state=? WHERE id=? AND account_id=?", ("success" if success else "uncertain", token, account))
        db.commit()
    from app.audit_log import record_request_audit
    record_request_audit(request, "Send Email", "Invoice + Packing List", meta["packing_no"], path=email.HISTORY_FILE.with_name("audit_log.json"))
    if success:
        return RedirectResponse(f"/email-result/{token}", 303)
    row["state"] = "uncertain"
    return _review_page(token, row, context, request, values=values,
                        error=_text(request, "Sending failed or could not be confirmed. No automatic retry was made.", "발송에 실패했거나 결과를 확인할 수 없습니다. 자동 재발송하지 않았습니다."), status=502)
