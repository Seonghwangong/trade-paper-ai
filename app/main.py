from fastapi import FastAPI, HTTPException, Query, Request, Response as FastAPIResponse
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import html as html_lib
import hashlib
import json
import logging
import os
from pathlib import Path
import re
from urllib.parse import quote, urlencode, urlsplit
from app.storage import (
    DATA_DIR,
    DuplicateIdentifierError,
    PROJECT_ROOT,
    StorageCorruptionError,
    StorageError,
    StorageValidationError,
    data_path,
    load_json_strict,
    locked_json_mutation,
    next_identifier,
)
from app.validation import DataValidationError, require_existing_reference, require_items, require_text
from app.documents import DOCUMENT_DEFINITIONS, get_document_definition
from app.release import (
    APP_NAME, APP_VERSION, EXPECTED_ROUTE_COUNT, LAST_UPDATED, RELEASE_STAGE,
    RELEASE_TYPE, build_release_summary, contact_email, contact_url,
)
from app.ui import ReleaseFooterMiddleware, release_footer
from app.routers.company import router as company_router
from app.product import router as product_router
from app.buyer import router as buyer_router
from app.customer import router as customer_router
from app.export_wizard import router as export_wizard_router
from app.onboarding import router as onboarding_router
from app.team import router as team_router
from app import customer as customer_module
from app.release_pages import router as release_pages_router
from app.founding_beta import router as founding_beta_router
from app.feedback import router as feedback_router
from app.document_email import router as document_email_router
from app.subscription import router as subscription_router
from app.toss_payments import router as toss_payments_router
from app.admin_dashboard import router as admin_dashboard_router
from app.audit_log import router as audit_log_router
from app.backup_restore import router as backup_restore_router
from app.archive import router as archive_router
from app import founding_beta as founding_beta_module
from app import feedback as feedback_module
from app import subscription as subscription_module
from app.auth import AuthenticationMiddleware, router as auth_router
from app import email_delivery
from app import analytics as analytics_module
from app import dashboard_insights as dashboard_insights_module
from app.account_company import load_account_company
from app.routers import company as company_module
from app import buyer as buyer_module
from app import product as product_module
from app import invoice as invoice_module
from app import packing as packing_module
from app import shipping_instruction as shipping_instruction_module
from app import booking_confirmation as booking_module
from app import bill_of_lading as bill_of_lading_module
from app import customs_declaration as customs_module
from app import certificate_of_origin as certificate_of_origin_module
from app import inspection_certificate as inspection_module
from app import insurance_certificate as insurance_module
from app import weight_certificate as weight_module
from app import quotation as quotation_module
from app import proforma as proforma_module

_PRODUCTION_ENVIRONMENTS = frozenset({"production", "prod", "staging", "stage"})
_LOCAL_CORS_ORIGIN_REGEX = r"^https?://(?:localhost|127\.0\.0\.1)(?::\d+)?$"


def _normalized_cors_origin(value):
    candidate = str(value or "").strip()
    if not candidate or candidate == "*" or any(character.isspace() or ord(character) < 32 for character in candidate):
        raise ValueError("CORS origins must be explicit HTTP or HTTPS origins.")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("CORS origin is invalid.") from exc
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.hostname
        or "*" in parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("CORS origin is invalid.")
    scheme = parsed.scheme.casefold()
    host = parsed.hostname.casefold()
    if ":" in host:
        host = f"[{host}]"
    if port is not None and not (
        (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    return f"{scheme}://{host}"


def cors_configuration(environment=None):
    source = os.environ if environment is None else environment
    deployment = str(source.get("TRADE_PAPER_ENV", "") or "").strip().casefold()
    raw_origins = str(source.get("TRADE_PAPER_CORS_ORIGINS", "") or "")
    origins = []
    seen = set()
    for raw_origin in raw_origins.split(","):
        if not raw_origin.strip():
            continue
        try:
            origin = _normalized_cors_origin(raw_origin)
        except ValueError as exc:
            raise RuntimeError("TRADE_PAPER_CORS_ORIGINS contains an invalid origin.") from exc
        if origin not in seen:
            origins.append(origin)
            seen.add(origin)
    return {
        "allow_origins": origins,
        "allow_origin_regex": None if deployment in _PRODUCTION_ENVIRONMENTS else _LOCAL_CORS_ORIGIN_REGEX,
    }


_CORS_CONFIGURATION = cors_configuration()
app = FastAPI()
BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
DATA_FILE = data_path("invoices.json")
PACKING_FILE = data_path("packing_lists.json")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_CONFIGURATION["allow_origins"],
    allow_origin_regex=_CORS_CONFIGURATION["allow_origin_regex"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(ReleaseFooterMiddleware)
app.add_middleware(AuthenticationMiddleware)


class ProductAnalyticsMiddleware:
    """Record successful allow-listed product-flow events without request data."""

    def __init__(self, application):
        self.app = application

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method", "GET") or "GET").upper()
        path = str(scope.get("path", "") or "")
        status = {"code": 500}
        email_success_before = None
        if method == "POST" and path.startswith("/send-email/"):
            email_success_before = sum(
                isinstance(row, dict) and row.get("status") == "Success"
                for row in load_json_strict(data_path("email_history.json"), [], list)
            )

        async def analytics_send(message):
            if message.get("type") == "http.response.start":
                status["code"] = int(message.get("status", 500))
            await send(message)

        await self.app(scope, receive, analytics_send)
        if not 200 <= status["code"] < 400:
            return
        identity = scope.get("trade_paper_user") or {}
        account_id = str(identity.get("account_id", "") or "")
        event_specs = []
        if method == "POST" and path == "/register": event_specs.append(("Signup", "", False))
        if method == "POST" and path == "/login": event_specs.append(("Login", "", False))
        if method == "GET" and path == "/onboarding": event_specs.append(("Onboarding Started", account_id, True))
        if method == "GET" and path == "/export-wizard": event_specs.append(("Export Wizard Started", account_id, False))
        if method == "POST" and path == "/export-wizard":
            event_specs.extend((("Export Wizard Completed", account_id, False), ("Invoice Created", account_id, False), ("Onboarding Completed", account_id, True)))
        if method == "POST" and path == "/invoice": event_specs.append(("Invoice Created", account_id, False))
        if method == "POST" and path == "/team/invite": event_specs.append(("Team Invite", account_id, False))
        if method == "POST" and path == "/feedback": event_specs.append(("Feedback Submitted", account_id, False))
        if email_success_before is not None:
            email_success_after = sum(
                isinstance(row, dict) and row.get("status") == "Success"
                for row in load_json_strict(data_path("email_history.json"), [], list)
            )
            if email_success_after > email_success_before:
                event_specs.append(("Email Sent", account_id, False))
        for event, owner, once in event_specs:
            try:
                analytics_module.record_event(event, owner, once=once)
            except Exception:
                logger.exception("Product analytics event could not be recorded: %s", event)
        if method == "GET" and status["code"] == 200 and not account_id and path in {"/", "/register"}:
            headers = {key.decode("latin-1").casefold(): value.decode("latin-1") for key, value in scope.get("headers", [])}
            page = "Landing" if path == "/" else "Signup"
            source = analytics_module.classify_source(headers.get("referer", ""), bytes(scope.get("query_string", b"")).decode("latin-1"))
            try:
                analytics_module.record_visit(page, source)
            except Exception:
                logger.exception("Visitor analytics could not be recorded: %s", page)


app.add_middleware(ProductAnalyticsMiddleware)


@app.post("/analytics/visit", status_code=204, include_in_schema=False)
def analytics_visit(page: str = Query(""), source: str = Query("Direct")):
    try:
        analytics_module.record_visit(page, source)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid visitor analytics value") from exc
    return FastAPIResponse(status_code=204)

logger = logging.getLogger("trade-paper-ai")


def seo_public_base_url(request=None, environment=None):
    source = os.environ if environment is None else environment
    try:
        return email_delivery.public_base_url(source)
    except email_delivery.EmailConfigurationError:
        deployment = str(source.get("TRADE_PAPER_ENV", "") or "").strip().casefold()
        if deployment in _PRODUCTION_ENVIRONMENTS or request is None:
            raise
        return str(request.base_url).rstrip("/")


@app.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
def robots_txt(request: Request):
    base_url = seo_public_base_url(request)
    template = (BASE_DIR / "static" / "robots.txt").read_text(encoding="utf-8")
    return PlainTextResponse(template.replace("__PUBLIC_BASE_URL__", base_url), media_type="text/plain")


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap_xml(request: Request):
    base_url = seo_public_base_url(request)
    template = (BASE_DIR / "static" / "sitemap.xml").read_text(encoding="utf-8")
    return Response(template.replace("__PUBLIC_BASE_URL__", base_url), media_type="application/xml")


def deployment_readiness(environment=None, data_dir=None):
    source = os.environ if environment is None else environment
    deployment = str(source.get("TRADE_PAPER_ENV", "") or "").strip().casefold()
    backend = email_delivery.validate_email_configuration(source)
    report = {
        "environment": deployment or "development",
        "email_backend": backend,
        "email_configuration": email_delivery.email_readiness(source)["configuration"],
        "warnings": [],
    }
    if backend == "disabled":
        report["warnings"].append(
            "SMTP/email delivery is disabled; password reset email delivery is unavailable."
        )
    if deployment not in _PRODUCTION_ENVIRONMENTS:
        return report

    configured_data_dir = str(source.get("TRADE_PAPER_DATA_DIR", "") or "").strip()
    if not configured_data_dir:
        raise RuntimeError("TRADE_PAPER_DATA_DIR is required in production and staging.")
    resolved_data_dir = Path(data_dir or configured_data_dir).expanduser().resolve()
    if not resolved_data_dir.is_dir():
        raise RuntimeError("TRADE_PAPER_DATA_DIR must exist and be a directory.")
    if not os.access(resolved_data_dir, os.W_OK):
        raise RuntimeError("TRADE_PAPER_DATA_DIR must be writable by the application process.")

    session_secret = str(source.get("TRADE_PAPER_SESSION_SECRET", "") or "")
    if len(session_secret) < 32:
        raise RuntimeError(
            "TRADE_PAPER_SESSION_SECRET is required and must contain at least 32 characters in production and staging."
        )
    try:
        email_delivery.public_base_url(source)
    except email_delivery.EmailConfigurationError as exc:
        raise RuntimeError(
            "TRADE_PAPER_PUBLIC_BASE_URL must be a valid HTTPS origin in production and staging."
        ) from exc

    configured_contact_email = contact_email(source)
    configured_contact_url = contact_url(source)
    if not configured_contact_email and not configured_contact_url:
        raise RuntimeError(
            "Configure TRADE_PAPER_CONTACT_EMAIL or TRADE_PAPER_CONTACT_URL for production and staging."
        )
    if configured_contact_email and (
        "@" not in configured_contact_email
        or "\r" in configured_contact_email
        or "\n" in configured_contact_email
    ):
        raise RuntimeError("The configured customer contact email is invalid.")

    raw_workers = str(source.get("WEB_CONCURRENCY", "1") or "1").strip()
    try:
        workers = int(raw_workers)
    except ValueError as exc:
        raise RuntimeError("WEB_CONCURRENCY must be 1 for JSON storage.") from exc
    if workers != 1:
        raise RuntimeError("WEB_CONCURRENCY must be 1 for JSON storage.")
    return report


def validate_production_configuration(environment=None, data_dir=None):
    deployment_readiness(environment, data_dir)


def _request_expects_json(request):
    path = request.url.path
    accept = request.headers.get("accept", "")
    return (
        ("application/json" in accept and "text/html" not in accept)
        or path in {"/status", "/invoices"}
        or "-data" in path
        or path.endswith("/data")
        or "-source/" in path
    )


def _safe_error_response(request, status_code, title, message):
    if _request_expects_json(request):
        return JSONResponse({"error": title, "message": message}, status_code=status_code)
    safe_title = html_lib.escape(str(title or "Error"))
    safe_message = html_lib.escape(str(message or "The request could not be completed."))
    return HTMLResponse(
        f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{safe_title}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#F3F4F6;color:#111827;font-family:Arial,sans-serif}}
.page{{min-height:100vh;display:grid;place-items:center;padding:30px}}.card{{width:min(560px,100%);background:white;border:1px solid #E5E7EB;border-radius:18px;padding:36px;text-align:center;box-shadow:0 14px 34px rgba(17,24,39,.1)}}
.code{{display:inline-block;background:#E5E7EB;color:#374151;border-radius:999px;padding:7px 11px;font-size:12px;font-weight:bold}}h1{{margin:16px 0 10px;font-size:32px}}p{{margin:0 0 26px;color:#6B7280;line-height:1.6}}a{{display:inline-block;background:#111827;color:white;text-decoration:none;border-radius:10px;padding:12px 18px;font-weight:bold}}a:focus-visible{{outline:3px solid #2563EB;outline-offset:3px}}
</style></head><body><main class="page"><section class="card"><span class="code">{status_code}</span><h1>{safe_title}</h1><p>{safe_message}</p><a href="/">Dashboard</a></section></main></body></html>""",
        status_code=status_code,
    )


@app.exception_handler(404)
async def not_found_error(request: Request, exc):
    return _safe_error_response(request, 404, "Page Not Found", "The requested record or page could not be found.")


@app.exception_handler(409)
async def conflict_error(request: Request, exc):
    detail = getattr(exc, "detail", None)
    message = detail if isinstance(detail, str) and detail else "The request conflicts with existing data."
    return _safe_error_response(request, 409, "Unable to Complete Request", message)


@app.exception_handler(StorageCorruptionError)
async def storage_corruption_error(request: Request, exc):
    logger.error("Storage corruption detected while handling %s", request.url.path)
    return _safe_error_response(
        request,
        503,
        "Storage Temporarily Unavailable",
        "Stored data could not be verified. No changes were made.",
    )


@app.exception_handler(DataValidationError)
async def data_validation_error(request: Request, exc: DataValidationError):
    if _request_expects_json(request):
        return JSONResponse(
            {
                "error": "Validation failed",
                "field": exc.field,
                "reason": exc.reason,
                "correction": exc.correction,
            },
            status_code=409,
        )
    field = html_lib.escape(exc.field)
    reason = html_lib.escape(exc.reason)
    correction = html_lib.escape(exc.correction)
    return HTMLResponse(
        f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Validation Failed</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#F3F4F6;color:#111827;font-family:Arial,sans-serif}}.page{{min-height:100vh;display:grid;place-items:center;padding:30px}}.card{{width:min(620px,100%);background:white;border:1px solid #E5E7EB;border-radius:18px;padding:36px;box-shadow:0 14px 34px rgba(17,24,39,.1)}}h1{{margin:0 0 24px;text-align:center}}.issue{{background:#F9FAFB;border-left:4px solid #92400E;border-radius:10px;padding:16px;margin:12px 0}}.issue b{{display:block;margin-bottom:6px}}.actions{{display:flex;gap:12px;justify-content:center;margin-top:26px;flex-wrap:wrap}}a{{background:#111827;color:white;text-decoration:none;border-radius:10px;padding:12px 18px;font-weight:bold}}a.secondary{{background:#374151}}a:focus-visible{{outline:3px solid #2563EB;outline-offset:3px}}
</style></head><body><main class="page"><section class="card"><h1>Validation Failed</h1><div class="issue"><b>What failed</b>{field}</div><div class="issue"><b>Why it failed</b>{reason}</div><div class="issue"><b>How to correct it</b>{correction}</div><div class="actions"><a class="secondary" href="{html_lib.escape(request.url.path, quote=True)}">Back</a><a href="/">Dashboard</a></div></section></main></body></html>""",
        status_code=409,
    )


@app.exception_handler(DuplicateIdentifierError)
async def duplicate_identifier_error(request: Request, exc: DuplicateIdentifierError):
    return await data_validation_error(
        request,
        DataValidationError(
            "Identifier",
            str(exc) or "The identifier already exists.",
            "Use a unique value or update the existing record.",
        ),
    )


@app.exception_handler(StorageValidationError)
async def storage_validation_error(request: Request, exc):
    return _safe_error_response(
        request,
        409,
        "Unable to Save",
        "Submitted data could not be saved safely. Please review it and try again.",
    )


@app.exception_handler(StorageError)
async def storage_error(request: Request, exc):
    logger.error("Storage operation failed while handling %s", request.url.path)
    return _safe_error_response(
        request,
        503,
        "Storage Temporarily Unavailable",
        "The operation could not be completed safely. No further changes were made.",
    )


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc):
    logger.exception("Unexpected application error while handling %s", request.url.path)
    return _safe_error_response(
        request,
        500,
        "Unexpected Error",
        "The request could not be completed. Please return to the Dashboard and try again.",
    )


KNOWN_ROUTE_CONFLICT_COUNTS = {
    "exact": {("GET", "/invoice-list"): 2},
    "structural": {
        ("GET", "/invoice-list"): 2,
    },
}


def audit_route_registrations(application):
    exact = {}
    structural = {}
    for index, route in enumerate(application.routes):
        route_path = getattr(route, "path", "")
        endpoint = getattr(route, "endpoint", None)
        endpoint_name = getattr(route, "name", "")
        endpoint_module = getattr(endpoint, "__module__", "")
        for method in sorted(getattr(route, "methods", set()) or set()):
            if method in {"HEAD", "OPTIONS"}:
                continue
            entry = {
                "index": index,
                "path": route_path,
                "name": endpoint_name,
                "module": endpoint_module,
            }
            exact.setdefault((method, route_path), []).append(entry)
            normalized = re.sub(r"\{[^/]+\}", "{}", route_path)
            structural.setdefault((method, normalized), []).append(entry)

    exact_conflicts = {key: value for key, value in exact.items() if len(value) > 1}
    structural_conflicts = {key: value for key, value in structural.items() if len(value) > 1}
    return {
        "data_dir": str(DATA_DIR.resolve()),
        "exact_conflicts": exact_conflicts,
        "structural_conflicts": structural_conflicts,
        "route_count": len(application.routes),
    }


@app.on_event("startup")
def startup_stability_audit():
    readiness = deployment_readiness(data_dir=DATA_DIR)
    expected_data_dir = Path(os.environ.get("TRADE_PAPER_DATA_DIR", PROJECT_ROOT / "data")).expanduser().resolve()
    if DATA_DIR.resolve() != expected_data_dir:
        raise RuntimeError("Storage data directory does not resolve from the project root.")

    report = audit_route_registrations(app)
    logger.info("%s", APP_NAME)
    logger.info("Version %s", APP_VERSION)
    logger.info("%s", RELEASE_STAGE)
    logger.info("Startup Complete")
    print(f"{APP_NAME}\nVersion {APP_VERSION}\n{RELEASE_STAGE}\nStartup Complete", flush=True)
    logger.info("JSON data directory resolved to %s", DATA_DIR.resolve())
    logger.warning("JSON storage requires exactly one application worker.")
    for warning in readiness["warnings"]:
        logger.warning("Readiness: %s", warning)
    for category in ["exact", "structural"]:
        conflicts = report[f"{category}_conflicts"]
        known = KNOWN_ROUTE_CONFLICT_COUNTS[category]
        for key, entries in conflicts.items():
            logger.warning(
                "%s route conflict %s %s: %s",
                category.capitalize(),
                key[0],
                key[1],
                ", ".join(f'{entry["module"]}.{entry["name"]}' for entry in entries),
            )
            if key not in known or len(entries) > known[key]:
                raise RuntimeError("A newly introduced route conflict was detected.")
@app.get("/company")
def company_page():
    with open(BASE_DIR / "static" / "company.html", "r") as f:
        return HTMLResponse(f.read())


def _workflow_browser_enhancement(kind):
    common = """
<style>.workflow-message{display:none;margin:18px 0;padding:14px 16px;border-radius:10px;font-family:Arial,sans-serif;font-weight:700}.workflow-error{background:#FEF2F2;border:1px solid #FECACA;color:#991B1B}.workflow-return{background:#F8FAFC;border:1px solid #CBD5E1;color:#111827}.workflow-return a{display:inline-block;margin-top:8px;padding:10px 14px;background:#111827;color:white;text-decoration:none;border-radius:9px}.invoice-next-actions{position:fixed;z-index:10001;right:22px;bottom:22px;width:min(430px,calc(100% - 44px));padding:20px;border:1px solid #CBD5E1;border-radius:16px;background:#fff;color:#111827;box-shadow:0 20px 44px rgba(15,23,42,.24);font-family:Arial,sans-serif}.invoice-next-actions[hidden]{display:none}.invoice-next-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:14px}.invoice-next-heading strong{display:block;color:#166534;font-size:18px}.invoice-next-heading p{margin:7px 0 0;color:#64748B}.invoice-next-close{width:42px;min-width:42px;min-height:42px;border:0;border-radius:9px;background:#F1F5F9;color:#475569;font-size:18px;cursor:pointer}.invoice-next-grid{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:16px}.invoice-next-action{display:inline-flex;min-height:44px;align-items:center;justify-content:center;padding:10px 12px;border-radius:10px;background:#E5E7EB;color:#111827;text-align:center;text-decoration:none;font-weight:bold}.invoice-next-action.primary{background:#111827;color:#fff}.invoice-next-action:focus-visible,.invoice-next-close:focus-visible{outline:3px solid #2563EB;outline-offset:2px}@media(max-width:640px){.invoice-next-actions{right:12px;bottom:12px;width:calc(100% - 24px);padding:16px}.invoice-next-grid{grid-template-columns:1fr}.invoice-next-action,.invoice-next-close{min-height:46px}}</style>
<script>
function workflowMessageArea(){
  let area=document.getElementById("workflow-message");
  if(!area){area=document.createElement("div");area.id="workflow-message";area.className="workflow-message";const firstButton=document.querySelector("button.full");(firstButton?.parentNode||document.body).insertBefore(area,firstButton||null);}
  return area;
}
function clearWorkflowMessage(){const area=workflowMessageArea();area.style.display="none";area.textContent="";area.className="workflow-message";}
function showWorkflowError(message){const area=workflowMessageArea();area.className="workflow-message workflow-error";area.textContent=message||"Unable to save. Review the form and try again.";area.style.display="block";area.scrollIntoView({behavior:"smooth",block:"center"});}
async function workflowErrorMessage(response){
  const type=(response.headers.get("content-type")||"").toLowerCase();
  if(type.includes("application/json")){try{const data=await response.json();return [data.reason||data.message||data.detail||data.error,data.correction].filter(Boolean).join(" ");}catch(error){}}
  if(type.includes("text/html")){try{const text=await response.text();const doc=new DOMParser().parseFromString(text,"text/html");const reason=doc.querySelector(".issue")?.textContent||doc.querySelector("h1")?.textContent;return reason?reason.trim():"The submitted values could not be saved.";}catch(error){}}
  return "The submitted values could not be saved. Review the form and try again.";
}
function showShipmentReturn(shipmentNo,documentLabel,identifier){
  if(window.tpMarkSaved)window.tpMarkSaved();
  const area=workflowMessageArea();area.className="workflow-message workflow-return";area.textContent="✓ "+documentLabel+" saved successfully.";
  const link=document.createElement("a");link.href="/shipment/"+encodeURIComponent(shipmentNo);link.textContent="Return to Shipment";area.appendChild(document.createElement("br"));area.appendChild(link);area.style.display="block";
}
function showInvoiceNextActions(invoiceNo){
  if(window.tpClearInvoiceDraft)window.tpClearInvoiceDraft();if(window.tpMarkSaved)window.tpMarkSaved();if(window.tpRestoreSavingButtons)window.tpRestoreSavingButtons();
  const existing=document.getElementById("invoice-next-actions");if(existing)existing.remove();
  const card=document.createElement("aside");card.id="invoice-next-actions";card.className="invoice-next-actions";card.setAttribute("role","status");card.setAttribute("aria-live","polite");
  const heading=document.createElement("div");heading.className="invoice-next-heading";const copy=document.createElement("div");const saved=document.createElement("strong");saved.textContent="✓ Invoice Saved";const prompt=document.createElement("p");prompt.textContent="What would you like to do next?";copy.appendChild(saved);copy.appendChild(prompt);const close=document.createElement("button");close.type="button";close.className="invoice-next-close";close.textContent="✕";close.setAttribute("aria-label","Close Next Actions");heading.appendChild(copy);heading.appendChild(close);card.appendChild(heading);
  const actions=document.createElement("div");actions.className="invoice-next-grid";const links=[
    ["Create Packing List","/packing-page?invoice_no="+encodeURIComponent(invoiceNo),"primary",false],
    ["Create Another Invoice","/invoice","",false],
    ["Download PDF","/invoice-pdf/"+encodeURIComponent(invoiceNo),"",true],
    ["Back to Invoice List","/invoice-list","",false]
  ];links.forEach(function(item){const link=document.createElement("a");link.className="invoice-next-action"+(item[2]?" "+item[2]:"");link.href=item[1];link.textContent=item[0];if(item[3])link.setAttribute("download",invoiceNo+".pdf");actions.appendChild(link);});card.appendChild(actions);document.body.appendChild(card);
  let timer=window.setTimeout(function(){card.remove();},15000);close.addEventListener("click",function(){window.clearTimeout(timer);card.remove();});
}
function showPackingNextActions(packingNo){
  if(window.tpMarkSaved)window.tpMarkSaved();if(window.tpRestoreSavingButtons)window.tpRestoreSavingButtons();
  const existing=document.getElementById("packing-next-actions");if(existing)existing.remove();
  const card=document.createElement("aside");card.id="packing-next-actions";card.className="invoice-next-actions";card.setAttribute("role","status");card.setAttribute("aria-live","polite");
  const heading=document.createElement("div");heading.className="invoice-next-heading";const copy=document.createElement("div");const saved=document.createElement("strong");saved.textContent="✓ Packing List Saved";const number=document.createElement("p");number.textContent="Packing "+packingNo;copy.appendChild(saved);copy.appendChild(number);const close=document.createElement("button");close.type="button";close.className="invoice-next-close";close.textContent="✕";close.setAttribute("aria-label","Close Next Actions");heading.appendChild(copy);heading.appendChild(close);card.appendChild(heading);
  const actions=document.createElement("div");actions.className="invoice-next-grid";const links=[
    ["Create Shipping Instruction","/si-form?packing_no="+encodeURIComponent(packingNo),"primary",false],
    ["Create Another Packing List","/packing-page","",false],
    ["Download PDF","/packing-list-pdf/"+encodeURIComponent(packingNo),"",true],
    ["Back to Packing List","/packing-list","",false]
  ];links.forEach(function(item){const link=document.createElement("a");link.className="invoice-next-action"+(item[2]?" "+item[2]:"");link.href=item[1];link.textContent=item[0];if(item[3])link.setAttribute("download",packingNo+".pdf");actions.appendChild(link);});card.appendChild(actions);document.body.appendChild(card);
  let timer=window.setTimeout(function(){card.remove();},15000);close.addEventListener("click",function(){window.clearTimeout(timer);card.remove();});
}
</script>
"""
    if kind == "invoice":
        behavior = """
<script>
window.saveInvoice=async function(){
  clearWorkflowMessage();
  const params=new URLSearchParams(window.location.search);const piNo=(params.get("pi_no")||"").trim();const shipmentNo=(params.get("shipment_no")||"").trim();
  const data={seller:document.getElementById("seller").value,seller_address:document.getElementById("seller_address").value,seller_email:document.getElementById("seller_email").value,seller_phone:document.getElementById("seller_phone").value,currency:document.getElementById("currency").value,buyer:document.getElementById("buyer").value,buyer_address:document.getElementById("buyer_address").value,buyer_email:document.getElementById("buyer_email").value,items:getItems()};
  if(piNo)data.pi_no=piNo;
  if(shipmentNo)data.shipment_no=shipmentNo;
  let response;try{response=await fetch("/invoice",{method:"POST",headers:{"Content-Type":"application/json","Accept":"application/json"},body:JSON.stringify(data)});}catch(error){showWorkflowError("The Invoice could not be saved because the server could not be reached.");return;}
  if(!response.ok){showWorkflowError(await workflowErrorMessage(response));return;}
  let result;try{result=await response.json();}catch(error){showWorkflowError("The Invoice was not confirmed by the server.");return;}
  if(!result||!result.invoice_no){showWorkflowError("The Invoice was not confirmed by the server.");return;}
  if(shipmentNo)showShipmentReturn(shipmentNo,"Invoice",result.invoice_no);
  showInvoiceNextActions(result.invoice_no);
};
</script>
"""
    else:
        behavior = """
<script>
window.savePacking=async function(){
  clearWorkflowMessage();
  const data=getPackingData();if(!data.invoice_no){showWorkflowError("Please select Invoice No.");return;}
  const shipmentNo=(new URLSearchParams(window.location.search).get("shipment_no")||"").trim();if(shipmentNo)data.shipment_no=shipmentNo;
  let response;try{response=await fetch("/packing-list",{method:"POST",headers:{"Content-Type":"application/json","Accept":"application/json"},body:JSON.stringify(data)});}catch(error){showWorkflowError("The Packing List could not be saved because the server could not be reached.");return;}
  if(!response.ok){showWorkflowError(await workflowErrorMessage(response));return;}
  const type=(response.headers.get("content-type")||"").toLowerCase();let result;
  try{result=type.includes("application/json")?await response.json():null;}catch(error){showWorkflowError("The Packing List was not confirmed by the server.");return;}
  if(!result||!result.packing_no){showWorkflowError("The Packing List was not confirmed by the server.");return;}
  if(shipmentNo){await window.tpSavedThenRedirect("/shipment/"+encodeURIComponent(shipmentNo));return;}
  showPackingNextActions(result.packing_no);
};
</script>
"""
    return common + behavior


def _enhance_workflow_page(source, kind):
    return source.replace("</body>", _workflow_browser_enhancement(kind) + "</body>")

@app.get("/invoice")
def invoice_page():
    with open(BASE_DIR / "static" / "invoice.html", "r", encoding="utf-8") as f:
        return HTMLResponse(_enhance_workflow_page(f.read(), "invoice"))

@app.get("/invoice-page")
def invoice_page():
    with open(BASE_DIR / "static" / "invoice.html", "r") as f:
        return HTMLResponse(_enhance_workflow_page(f.read(), "invoice"))


@app.get("/packing-page")
def packing_page():
    with open(BASE_DIR / "static" / "packing.html", "r") as f:
        return HTMLResponse(_enhance_workflow_page(f.read(), "packing"))
@app.get("/status")
def status():
    return {
        "service": "trade-paper-backend",
        "version": APP_VERSION,
        "status": "ok",
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": APP_VERSION,
        "release": RELEASE_STAGE,
    }
from app.invoice import router as invoice_router
from app.packing import router as packing_router
from app.quotation import router as quotation_router
from app.proforma import router as proforma_router
from app.bill_of_lading import router as bill_of_lading_router
from app.certificate_of_origin import router as certificate_of_origin_router
from app.inspection_certificate import router as inspection_certificate_router
from app.insurance_certificate import router as insurance_certificate_router
from app.weight_certificate import router as weight_certificate_router
from app.shipping_instruction import router as shipping_instruction_router
from app.shipment import (
    router as shipment_router,
    next_step_for_shipment,
    required_workflow_progress,
    resolve_direct_documents,
    resolve_operational_records,
    health_score_label,
    shipment_health_score,
    shipment_pdf,
    shipment_detail,
)
from app import shipment as shipment_module
from app.container_management import router as container_router
from app import container_management as container_module
from app.booking_confirmation import router as booking_router
from app.customs_declaration import router as customs_router
app.include_router(invoice_router)
app.include_router(packing_router)
app.include_router(quotation_router)
app.include_router(proforma_router)
app.include_router(bill_of_lading_router)
app.include_router(certificate_of_origin_router)
app.include_router(inspection_certificate_router)
app.include_router(insurance_certificate_router)
app.include_router(weight_certificate_router)
app.include_router(shipping_instruction_router)
app.include_router(shipment_router)
app.include_router(container_router)
app.include_router(booking_router)
app.include_router(customs_router)
app.include_router(company_router)
app.include_router(product_router)
app.include_router(buyer_router)
app.include_router(customer_router)
app.include_router(export_wizard_router)
app.include_router(onboarding_router)
app.include_router(team_router)
app.include_router(release_pages_router)
app.include_router(founding_beta_router)
app.include_router(feedback_router)
app.include_router(document_email_router)
app.include_router(subscription_router)
app.include_router(toss_payments_router)
app.include_router(admin_dashboard_router)
app.include_router(audit_log_router)
app.include_router(backup_restore_router)
app.include_router(archive_router)
app.include_router(auth_router)
def load_packing_lists():
    return load_json_strict(PACKING_FILE, [], list)
def load_invoices():
    return load_json_strict(DATA_FILE, [], list)


def save_invoice(invoice_data, account_id):
    record = dict(invoice_data)
    record.pop("account_id", None)
    account_id = str(account_id or "").strip()
    record["account_id"] = account_id
    record["seller"] = require_text("Seller", record.get("seller", ""))
    record["buyer"] = require_text("Buyer", record.get("buyer", ""))
    require_items(record.get("items", []))
    require_existing_reference(
        "Proforma Invoice", record.get("pi_no", ""),
        proforma_module.load_proformas(account_id), "pi_no",
    )
    require_existing_reference(
        "Shipment", record.get("shipment_no", ""),
        shipment_module.load_shipments(account_id), "shipment_no",
    )
    def add_invoice(invoices):
        record["invoice_no"] = next_identifier(invoices, "invoice_no", "INV")
        invoices.append(record)
    locked_json_mutation(DATA_FILE, [], add_invoice, list)
    return record
@app.post("/save-invoice")
def create_invoice(request: Request, invoice: dict):
    user = request.scope.get("trade_paper_user") or {}
    saved = save_invoice(invoice, user.get("account_id", ""))
    from app.audit_log import record_request_audit
    record_request_audit(request, "Create", "Commercial Invoice", saved["invoice_no"], path=DATA_FILE.with_name("audit_log.json"))
    return {"message": "✓ Invoice saved successfully."}
@app.get("/invoices")
def get_invoices(request: Request):
    user = request.scope.get("trade_paper_user") or {}
    return invoice_module.load_invoices(user.get("account_id", ""))
@app.get("/invoice/pdf/{index}")
def invoice_pdf(index: int, request: Request):
    invoices = invoice_module.load_invoice_records()

    user = request.scope.get("trade_paper_user") or {}
    account_id = str(user.get("account_id", "") or "").strip()
    if (
        index < 0
        or index >= len(invoices)
        or not isinstance(invoices[index], dict)
        or str(invoices[index].get("account_id", "") or "").strip() != account_id
    ):
        raise HTTPException(status_code=404, detail="Invoice not found")

    company = load_account_company(account_id, company_module.ACCOUNT_COMPANIES_FILE)
    return invoice_module.create_invoice_pdf(invoice_module.public_invoice(invoices[index]), company)

def load_dashboard_json(filename, default):
    path = data_path(filename)
    return load_json_strict(path, default, type(default) if isinstance(default, (list, dict)) else None)


def dashboard_list(filename):
    value = load_dashboard_json(filename, [])
    return value if isinstance(value, list) else []


def dashboard_text(value):
    return html_lib.escape(str(value or ""))


def operations_dashboard_summary(applications, feedback_records, limit=5):
    applications = [record for record in applications if isinstance(record, dict)]
    feedback_records = [record for record in feedback_records if isinstance(record, dict)]
    beta_counts = {status: 0 for status in ("New", "Contacted", "Demo Scheduled", "Beta Customer")}
    for record in applications:
        status = str(record.get("status", "") or "").strip() or "New"
        if status in beta_counts:
            beta_counts[status] += 1
    return {
        "beta_counts": beta_counts,
        "feedback_counts": {
            "Total": len(feedback_records),
            "Bug": sum(record.get("category") == "Bug" for record in feedback_records),
            "Feature": sum(record.get("category") == "Feature Request" for record in feedback_records),
            "UI/UX": sum(record.get("category") == "UI/UX" for record in feedback_records),
        },
        "recent_applications": list(reversed(applications))[:limit],
        "recent_feedback": list(reversed(feedback_records))[:limit],
    }


def dashboard_card(label, count, create_route, list_route):
    return f"""
<article class="document-card">
<h3>{dashboard_text(label)}</h3>
<div class="document-count">{count}</div>
<div class="document-actions">
<a class="document-button primary" href="{create_route}">Create</a>
<a class="document-button secondary" href="{list_route}">List</a>
</div>
</article>
"""


def _search_source(definition):
    source = {
        "module": definition.label,
        "file": definition.storage_filename,
        "identifier": definition.identifier_field,
        "title": definition.title_field,
        "fields": list(definition.searchable_fields),
    }
    if definition.dashboard_category == "Master Data":
        source["url"] = definition.detail_route
    elif definition.detail_route:
        source["detail"] = definition.detail_route
    else:
        source["list"] = definition.list_route
    source["edit"] = definition.edit_route
    if definition.key == "company":
        source["single"] = True
    return source


SEARCH_SOURCES = [_search_source(definition) for definition in DOCUMENT_DEFINITIONS]


def search_result_url(source, identifier, record_index=None):
    if source["module"] == "Buyers" and record_index is not None:
        return f"/buyer/{record_index}"
    if source["module"] == "Products" and record_index is not None:
        return f"/edit-product/{record_index}"
    if source.get("url"):
        return source["url"]
    if source.get("detail"):
        return source["detail"].format(value=quote(identifier, safe=""))
    if source.get("edit"):
        return source["edit"].format(value=quote(identifier, safe=""))
    query = urlencode({"search": identifier}) if identifier else ""
    return f'{source["list"]}?{query}' if query else source["list"]


ACTIVITY_MODULES = [
    "Shipment",
    "Quotation",
    "Proforma Invoice",
    "Commercial Invoice",
    "Packing List",
    "Bill of Lading",
]

ACTIVITY_DATE_FIELDS = (
    "shipment_date",
    "quotation_date",
    "pi_date",
    "invoice_date",
    "packing_date",
    "bl_date",
    "issue_date",
    "date",
)


def recent_activity_entries(records_by_module, limit=10):
    sources = {source["module"]: source for source in SEARCH_SOURCES}
    candidates = []
    for module_order, module in enumerate(ACTIVITY_MODULES):
        source = sources[module]
        records = records_by_module.get(module, [])
        if not isinstance(records, list):
            continue
        for record_order, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            identifier = str(record.get(source["identifier"], "") or "").strip()
            if not identifier:
                continue
            title = str(record.get(source["title"], "") or "").strip() or identifier
            document_date = next(
                (
                    str(record.get(field, "") or "").strip()
                    for field in ACTIVITY_DATE_FIELDS
                    if str(record.get(field, "") or "").strip()
                ),
                "",
            )
            candidates.append({
                "module": module,
                "identifier": identifier,
                "title": title,
                "url": search_result_url(source, identifier),
                "document_date": document_date,
                "record_order": record_order,
                "module_order": module_order,
            })

    candidates.sort(key=lambda entry: entry["identifier"], reverse=True)
    candidates.sort(key=lambda entry: entry["module_order"])
    candidates.sort(key=lambda entry: entry["record_order"], reverse=True)
    candidates.sort(key=lambda entry: entry["document_date"], reverse=True)

    selected = []
    selected_ids = set()
    by_module = {
        module: [entry for entry in candidates if entry["module"] == module]
        for module in ACTIVITY_MODULES
    }
    for selection_round in range(2):
        for module in ACTIVITY_MODULES:
            module_entries = by_module[module]
            if selection_round < len(module_entries) and len(selected) < max(0, limit):
                entry = module_entries[selection_round]
                selected.append(entry)
                selected_ids.add(id(entry))
    for entry in candidates:
        if len(selected) >= max(0, limit):
            break
        if id(entry) not in selected_ids:
            selected.append(entry)
            selected_ids.add(id(entry))

    return [
        {key: entry[key] for key in ["module", "identifier", "title", "url"]}
        for entry in selected
    ]


def recent_invoice_entries(invoices, limit=5):
    """Normalize recent Invoice presentation data without rereading storage."""
    source = next(source for source in SEARCH_SOURCES if source["module"] == "Commercial Invoice")
    entries = []
    for record_order, record in enumerate(invoices if isinstance(invoices, list) else []):
        if not isinstance(record, dict):
            continue
        invoice_no = str(record.get("invoice_no", "") or "").strip()
        if not invoice_no:
            continue
        invoice_date = next(
            (
                str(record.get(field, "") or "").strip()
                for field in ACTIVITY_DATE_FIELDS
                if str(record.get(field, "") or "").strip()
            ),
            "",
        )
        entries.append({
            "invoice_no": invoice_no,
            "buyer": str(record.get("buyer", "") or record.get("buyer_name", "") or "").strip(),
            "date": invoice_date,
            "url": search_result_url(source, invoice_no),
            "record_order": record_order,
        })
    entries.sort(key=lambda entry: entry["invoice_no"], reverse=True)
    entries.sort(key=lambda entry: entry["record_order"], reverse=True)
    entries.sort(key=lambda entry: entry["date"], reverse=True)
    return entries[:max(0, limit)]


def dashboard_workflow_guidance(buyers, products, invoices, packing_lists):
    """Build presentation-only setup guidance from already-loaded datasets."""
    definitions = [
        ("Buyer", bool(buyers), "/buyer-form"),
        ("Product", bool(products), "/product-form"),
        ("Commercial Invoice", bool(invoices), "/invoice"),
        ("Packing List", bool(packing_lists), "/packing-page"),
    ]
    current_index = next((index for index, (_, complete, _) in enumerate(definitions) if not complete), None)
    steps = [
        {
            "label": label,
            "state": "complete" if complete else "current" if index == current_index else "pending",
        }
        for index, (label, complete, _) in enumerate(definitions)
    ]
    completed = sum(1 for _, complete, _ in definitions if complete)
    messages = [
        "No Buyers yet. Register a Buyer first.",
        "Add a Product before creating trade documents.",
        "Buyer and Product are ready. Create a Commercial Invoice.",
        "Invoice created. Now create a Packing List.",
    ]
    if current_index is None:
        return {
            "steps": steps,
            "completed": completed,
            "total": len(definitions),
            "percentage": 100,
            "message": "All setup documents are ready.",
            "next_label": "",
            "next_url": "",
            "is_complete": True,
        }
    label, _, url = definitions[current_index]
    return {
        "steps": steps,
        "completed": completed,
        "total": len(definitions),
        "percentage": round(completed * 100 / len(definitions)),
        "message": messages[current_index],
        "next_label": f"Create {label}",
        "next_url": url,
        "is_complete": False,
    }


def dashboard_first_action(company_ready, buyers, products):
    """Return the first actionable setup step using account-owned dashboard data."""
    if not company_ready:
        return {"label": "Complete Company Setup", "url": "/company"}
    if not buyers:
        return {"label": "Create First Buyer", "url": "/buyer-form"}
    if not products:
        return {"label": "Create First Product", "url": "/product-form"}
    return {"label": "Create First Invoice", "url": "/invoice"}


def dashboard_notifications(shipment_summaries, shipments_by_recency, limit=5):
    recency_order = {id(shipment): index for index, shipment in enumerate(shipments_by_recency)}
    candidates = []
    for summary in shipment_summaries:
        shipment = summary["shipment"]
        shipment_no = str(shipment.get("shipment_no", "") or "")
        url = f'/shipment/{quote(shipment_no, safe="")}'
        sort_values = (recency_order.get(id(shipment), len(recency_order)), shipment_no)

        for resolved in summary["resolved_direct"]:
            if resolved["value"] and not resolved["exists"]:
                candidates.append({
                    "priority": 0,
                    "kind": "stale",
                    "message": f'{shipment_no} · Stale {resolved["document"]["label"]} reference',
                    "url": url,
                    "sort_values": sort_values,
                })

        score = summary["health_score"]["score"]
        if score < 70:
            candidates.append({
                "priority": 1,
                "kind": "health",
                "message": f"{shipment_no} · Health Score {score} / 100",
                "url": url,
                "sort_values": sort_values,
            })

        next_step = summary["next_step"]
        if next_step["is_complete"]:
            candidates.append({
                "priority": 3,
                "kind": "complete",
                "message": f"{shipment_no} · Workflow Complete",
                "url": url,
                "sort_values": sort_values,
            })
        else:
            candidates.append({
                "priority": 2,
                "kind": "next",
                "message": f'{shipment_no} · Next: {next_step["step_label"]}',
                "url": url,
                "sort_values": sort_values,
            })

    candidates.sort(key=lambda item: (item["priority"], *item["sort_values"]))
    return [
        {key: item[key] for key in ["kind", "message", "url"]}
        for item in candidates[:max(0, limit)]
    ]


def search_match_rank(query, identifier, title, other_values):
    identifier_value = identifier.casefold()
    title_value = title.casefold()
    if identifier_value == query:
        return 0
    if identifier_value.startswith(query):
        return 1
    if query in identifier_value:
        return 2
    if title_value == query:
        return 3
    if title_value.startswith(query):
        return 4
    if any(query in value.casefold() for value in other_values):
        return 5
    return None


def global_search_results(query, company=None, customers=None, buyers=None, products=None, invoices=None, packing_lists=None, shipping_instructions=None, bookings=None, shipments=None, containers=None, bills_of_lading=None, customs=None, certificates_of_origin=None, inspections=None, insurances=None, weights=None, quotations=None, proformas=None):
    normalized = str(query or "").strip().casefold()

    results = []
    for module_order, source in enumerate(SEARCH_SOURCES):
        loaded = (
            company
            if source.get("single") and source["module"] == "Company"
            else customers
            if source["module"] == "Customers" and customers is not None
            else quotations
            if source["module"] == "Quotation" and quotations is not None
            else proformas
            if source["module"] == "Proforma Invoice" and proformas is not None
            else buyers
            if source["module"] == "Buyers" and buyers is not None
            else products
            if source["module"] == "Products" and products is not None
            else invoices
            if source["module"] == "Commercial Invoice" and invoices is not None
            else packing_lists
            if source["module"] == "Packing List" and packing_lists is not None
            else shipping_instructions
            if source["module"] == "Shipping Instruction" and shipping_instructions is not None
            else bookings
            if source["module"] == "Booking Confirmation" and bookings is not None
            else shipments
            if source["module"] == "Shipment" and shipments is not None
            else containers
            if source["module"] == "Container Management" and containers is not None
            else bills_of_lading
            if source["module"] == "Bill of Lading" and bills_of_lading is not None
            else customs
            if source["module"] == "Customs Declaration" and customs is not None
            else certificates_of_origin
            if source["module"] == "Certificate of Origin" and certificates_of_origin is not None
            else inspections
            if source["module"] == "Inspection Certificate" and inspections is not None
            else insurances
            if source["module"] == "Insurance Certificate" and insurances is not None
            else weights
            if source["module"] == "Weight Certificate" and weights is not None
            else None
        )
        records = [loaded] if source.get("single") and isinstance(loaded, dict) else loaded
        if not isinstance(records, list):
            continue
        for record_index, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            if record.get("archived_at"):
                continue
            identifier = str(record.get(source["identifier"], "") or "")
            title = str(record.get(source["title"], "") or "")
            if not identifier and not source.get("url"):
                continue
            other_values = [str(record.get(field, "") or "") for field in source["fields"]]
            other_values.extend(
                str(item.get("name", "") or "")
                for item in record.get("items", [])
                if isinstance(item, dict)
            )
            rank = 6 if not normalized else search_match_rank(normalized, identifier, title, other_values)
            if rank is None:
                continue
            subtitle_values = []
            for field in ["buyer", "buyer_name", "customer", "seller", "exporter", "consignee", "status", "country", "origin", "hs_code"]:
                value = str(record.get(field, "") or "")
                if value and value not in subtitle_values and value not in [identifier, title]:
                    subtitle_values.append(value)
            results.append({
                "module": source["module"],
                "identifier": identifier,
                "title": title or identifier,
                "subtitle": " · ".join(subtitle_values[:3]),
                "search_text": " ".join([identifier, title, *other_values]),
                "url": search_result_url(source, identifier, record_index),
                "match_rank": rank,
                "module_order": module_order,
            })

    results.sort(key=lambda result: result["identifier"], reverse=True)
    results.sort(key=lambda result: (result["match_rank"], result["module_order"]))
    return results


def _account_search_records(account_id):
    return (
        load_account_company(account_id, company_module.ACCOUNT_COMPANIES_FILE),
        customer_module.load_customers(account_id), buyer_module.load_buyers(account_id),
        product_module.load_products(account_id), invoice_module.load_invoices(account_id),
        packing_module.load_packing_lists(account_id), shipping_instruction_module.load_shipping_instructions(account_id),
        booking_module.load_bookings(account_id), shipment_module.load_shipments(account_id),
        container_module.load_containers(account_id), bill_of_lading_module.load_bills_of_lading(account_id),
        customs_module.load_customs(account_id), certificate_of_origin_module.load_certificates(account_id),
        inspection_module.load_inspections(account_id), insurance_module.load_insurances(account_id),
        weight_module.load_weights(account_id), quotation_module.load_quotations(account_id),
        proforma_module.load_proformas(account_id),
    )


@app.get("/search-suggestions", response_class=JSONResponse)
def global_search_suggestions(request: Request, q: str = ""):
    query = str(q or "").strip()
    if not query:
        return []
    user = request.scope.get("trade_paper_user") or {}
    results = global_search_results(query, *_account_search_records(user.get("account_id", "")))[:10]
    return [{"value": result["identifier"] or result["title"], "label": f'{result["module"]} · {result["title"]}'} for result in results]


@app.get("/search", response_class=HTMLResponse)
def global_search(request: Request, q: str = "", include_archived: bool = False):
    query = str(q or "").strip()
    user = request.scope.get("trade_paper_user") or {}
    recent_search_key = "trade-paper-recent-searches-" + hashlib.sha256(str(user.get("account_id", "")).encode()).hexdigest()[:16]
    company, customers, buyers, products, invoices, packing_lists, shipping_instructions, bookings, shipments, containers, bills_of_lading, customs, certificates_of_origin, inspections, insurances, weights, quotations, proformas = _account_search_records(user.get("account_id", ""))
    all_results = global_search_results("", company, customers, buyers, products, invoices, packing_lists, shipping_instructions, bookings, shipments, containers, bills_of_lading, customs, certificates_of_origin, inspections, insurances, weights, quotations, proformas)
    matched_results = global_search_results(query, company, customers, buyers, products, invoices, packing_lists, shipping_instructions, bookings, shipments, containers, bills_of_lading, customs, certificates_of_origin, inspections, insurances, weights, quotations, proformas) if query else all_results
    if include_archived:
        from app.archive import archived_records
        additions = [{"module": f'{item["label"]} (Archived)', "identifier": item["identifier"], "title": item["identifier"], "subtitle": "Archived", "search_text": item["identifier"], "url": "/archive", "match_rank": 0, "module_order": 99} for item in archived_records(user.get("account_id", ""), query)]
        all_results.extend(additions)
        matched_results.extend(additions)
    matched_keys = {(result["module"], result["identifier"], result["url"]) for result in matched_results}
    if not all_results:
        content = '<div id="search-empty" class="empty">No documents yet. Create your first document to start searching.</div>'
    else:
        cards = ""
        for result in all_results:
            identifier = result["identifier"] or "—"
            subtitle = f'<p>{dashboard_text(result["subtitle"])}</p>' if result["subtitle"] else ""
            search_value = " ".join([result["module"], result["search_text"]]).casefold()
            hidden = "" if (result["module"], result["identifier"], result["url"]) in matched_keys else " hidden"
            cards += f"""
<article class="result-card" data-search="{dashboard_text(search_value)}"{hidden}>
<div class="result-copy"><span class="module-badge">{dashboard_text(result['module'])}</span>
<div class="identifier">{dashboard_text(identifier)}</div><h2>{dashboard_text(result['title'])}</h2>{subtitle}</div>
<a class="open-button" href="{dashboard_text(result['url'])}">Open</a>
</article>"""
        empty_hidden = " hidden" if matched_results else ""
        content = f'<div id="search-count" class="count" role="status" aria-live="polite">Total Results: {len(matched_results)}</div><div id="search-results" class="results">{cards}</div><div id="search-empty" class="empty"{empty_hidden}>No matching documents found. Try a document number, Buyer, Seller, or Company name.</div>'

    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0"><title>Global Search</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#F3F4F6;color:#111827;font-family:Arial,sans-serif}}
.page{{width:94%;max-width:1080px;margin:auto;padding:46px 0 60px}}h1{{font-size:46px;text-align:center;margin:0 0 10px}}
.sub{{text-align:center;color:#6B7280;margin:0 0 28px}}.toolbar{{display:flex;gap:12px;max-width:900px;margin:0 auto 34px}}.search-field{{position:relative;display:flex;flex:1;min-width:0}}
.toolbar input{{width:100%;min-width:0;padding:14px 48px 14px 16px;border:1px solid #D1D5DB;border-radius:11px;font-size:16px}}.clear-button{{position:absolute;right:6px;top:50%;width:38px;height:38px;transform:translateY(-50%);border:0;border-radius:9px;background:transparent;color:#64748B;font-size:20px;cursor:pointer}}.clear-button:hover{{background:#E5E7EB;color:#111827}}
.button,.open-button{{display:inline-block;padding:14px 18px;border:0;border-radius:11px;background:#111827;color:white;text-decoration:none;font-weight:bold;cursor:pointer}}
.dashboard-button{{background:#374151}}.recent-searches{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;max-width:900px;margin:-20px auto 30px;color:#64748B;font-size:13px}}.recent-searches a{{padding:6px 9px;border-radius:999px;background:#E5E7EB;color:#374151;text-decoration:none;font-weight:bold}}.count{{font-weight:bold;margin-bottom:14px;color:#374151}}.results{{display:grid;gap:14px}}
.result-card{{display:flex;align-items:center;justify-content:space-between;gap:20px;background:white;border:1px solid #E5E7EB;border-radius:16px;padding:22px;box-shadow:0 8px 22px rgba(17,24,39,.06)}}
.module-badge{{display:inline-block;background:#E5E7EB;color:#374151;padding:6px 9px;border-radius:999px;font-size:12px;font-weight:bold}}
.identifier{{font-weight:bold;color:#6B7280;margin:11px 0 4px}}.result-card h2{{font-size:21px;margin:0}}.result-card p{{color:#6B7280;margin:7px 0 0}}
.empty{{background:white;border:1px solid #E5E7EB;border-radius:16px;padding:34px;text-align:center;color:#6B7280}}
@media(max-width:640px){{.page{{padding-top:28px}}h1{{font-size:36px}}.toolbar,.result-card{{align-items:stretch;flex-direction:column}}.search-field{{width:100%}}.button,.open-button{{min-height:46px;text-align:center}}}}
</style></head><body><main class="page"><h1>Global Search</h1><p class="sub">Search across the complete Trade Paper AI workflow</p>
<form class="toolbar" method="get" action="/search"><div class="search-field"><input id="global-search-input" name="q" value="{dashboard_text(query)}" placeholder="Search Buyer, Seller, Company, Invoice No, Packing No..." autocomplete="off"><button id="search-clear" class="clear-button" type="button" aria-label="Clear search">✕</button></div>
<label><input type="checkbox" name="include_archived" value="true"{' checked' if include_archived else ''}> Include Archived</label><button class="button" type="submit">Search</button><a class="button dashboard-button" href="/">Dashboard</a></form><nav id="recent-searches" class="recent-searches" aria-label="Recent searches"></nav>{content}<script>
(function(){{
  const input=document.getElementById('global-search-input');const clear=document.getElementById('search-clear');const cards=Array.from(document.querySelectorAll('.result-card'));const count=document.getElementById('search-count');const empty=document.getElementById('search-empty');
  const recent=document.getElementById('recent-searches');const recentKey={json.dumps(recent_search_key)};const submitted=(input.value||'').trim();
  function loadRecent(){{try{{const value=JSON.parse(localStorage.getItem(recentKey)||'[]');return Array.isArray(value)?value.filter(item=>typeof item==='string').slice(0,5):[]}}catch(error){{return []}}}}
  function renderRecent(){{const values=loadRecent();recent.replaceChildren();if(!values.length)return;const label=document.createElement('strong');label.textContent='Recent searches';recent.append(label,...values.map(function(value){{const link=document.createElement('a');link.href='/search?q='+encodeURIComponent(value);link.textContent=value;return link}}))}}
  if(submitted){{const values=[submitted,...loadRecent().filter(value=>value.toLocaleLowerCase()!==submitted.toLocaleLowerCase())].slice(0,5);localStorage.setItem(recentKey,JSON.stringify(values))}}renderRecent();
  function filterResults(){{const query=(input.value||'').trim().toLocaleLowerCase();let visible=0;cards.forEach(function(card){{const matches=!query||(card.dataset.search||'').toLocaleLowerCase().includes(query);card.hidden=!matches;if(matches)visible++;}});if(count)count.textContent='Total Results: '+visible;if(empty)empty.hidden=visible!==0;}}
  input.addEventListener('input',filterResults);clear.addEventListener('click',function(){{input.value='';filterResults();input.focus();if(window.history&&window.history.replaceState)window.history.replaceState(null,'','/search');}});filterResults();
}})();
</script></main></body></html>"""
    return HTMLResponse(html)


@app.get("/")
def home(request: Request):
    user = request.scope.get("trade_paper_user") or {}
    subscription_summary = subscription_module.usage_summary(user.get("account_id", ""))
    subscription_limit = "Unlimited" if subscription_summary["limit"] is None else str(subscription_summary["limit"])
    subscription_html = f'''<section class="subscription-card"><div><span>Current Plan</span><h2>{dashboard_text(subscription_summary["plan"])}</h2><p>{dashboard_text(subscription_summary["status"])} · {subscription_summary["used"]} / {subscription_limit} documents this month</p></div><div><a href="/pricing">Upgrade</a><a href="/subscription">Manage Subscription</a></div></section>'''
    company = load_account_company(user.get("account_id", ""), company_module.ACCOUNT_COMPANIES_FILE)
    company_count = 1 if isinstance(company, dict) and str(company.get("name", "")).strip() else 0
    customers = customer_module.load_customers(user.get("account_id", ""))
    buyers = buyer_module.load_buyers(user.get("account_id", ""))
    products = product_module.load_products(user.get("account_id", ""))
    operations_summary = operations_dashboard_summary(
        load_json_strict(founding_beta_module.BETA_APPLICATION_FILE, [], list),
        load_json_strict(feedback_module.FEEDBACK_FILE, [], list),
    )
    beta_cards = "".join(
        f'<a class="operations-stat-card" href="/admin/founding-beta"><span>{dashboard_text(label)}</span><strong>{count}</strong></a>'
        for label, count in operations_summary["beta_counts"].items()
    )
    feedback_cards = "".join(
        f'<a class="operations-stat-card" href="/admin/feedback"><span>{dashboard_text(label)}</span><strong>{count}</strong></a>'
        for label, count in operations_summary["feedback_counts"].items()
    )
    recent_beta_rows = "".join(
        '<article class="operations-row"><div><strong>'
        f'{dashboard_text(record.get("company_name", "") or "—")}</strong><span>'
        f'{dashboard_text(record.get("contact_name", "") or "—")} · {dashboard_text(record.get("email", "") or "—")}</span></div>'
        f'<span class="status-pill">{dashboard_text(record.get("status", "") or "New")}</span></article>'
        for record in operations_summary["recent_applications"]
    ) or '<div class="activity-empty">아직 Founding Beta 신청이 없습니다.</div>'
    recent_feedback_rows = "".join(
        '<article class="operations-row"><div><strong>'
        f'{dashboard_text(record.get("category", "") or "Other")}</strong><span>{dashboard_text(record.get("feedback", ""))}</span></div>'
        f'<span class="operations-meta">{dashboard_text(record.get("rating", "") or "—")} / 5</span></article>'
        for record in operations_summary["recent_feedback"]
    ) or '<div class="activity-empty">아직 Feedback이 없습니다.</div>'

    quotations = quotation_module.load_quotations(user.get("account_id", ""))
    proformas = proforma_module.load_proformas(user.get("account_id", ""))
    invoices = invoice_module.load_invoices(user.get("account_id", ""))
    packing_lists = packing_module.load_packing_lists(user.get("account_id", ""))

    shipments = shipment_module.load_shipments(user.get("account_id", ""))
    shipping_instructions = shipping_instruction_module.load_shipping_instructions(user.get("account_id", ""))
    bookings = booking_module.load_bookings(user.get("account_id", ""))
    containers = container_module.load_containers(user.get("account_id", ""))
    bills_of_lading = bill_of_lading_module.load_bills_of_lading(user.get("account_id", ""))

    certificates_of_origin = certificate_of_origin_module.load_certificates(user.get("account_id", ""))
    inspections = inspection_module.load_inspections(user.get("account_id", ""))
    insurances = insurance_module.load_insurances(user.get("account_id", ""))
    weights = weight_module.load_weights(user.get("account_id", ""))
    customs = customs_module.load_customs(user.get("account_id", ""))
    personal_insights_html = dashboard_insights_module.render_dashboard_insights(
        dashboard_insights_module.dashboard_insights(user.get("account_id", ""))
    )

    workflow_datasets = {
        "quotations.json": quotations,
        "proformas.json": proformas,
        "invoices.json": invoices,
        "packing_lists.json": packing_lists,
        "shipping_instructions.json": shipping_instructions,
        "booking_confirmations.json": bookings,
        "containers.json": containers,
        "bills_of_lading.json": bills_of_lading,
        "certificates_of_origin.json": certificates_of_origin,
        "inspection_certificates.json": inspections,
        "insurance_certificates.json": insurances,
        "weight_certificates.json": weights,
        "customs_declarations.json": customs,
    }

    status_counts = {status: 0 for status in ["inquiry", "confirmed", "ready to ship", "shipped", "completed"]}
    for shipment in shipments:
        status = str(shipment.get("status", "") or "").strip().lower()
        if status in status_counts:
            status_counts[status] += 1

    shipments_by_recency = sorted(
        shipments,
        key=lambda record: (
            str(record.get("shipment_date", "") or ""),
            str(record.get("shipment_no", "") or ""),
        ),
        reverse=True,
    )
    shipment_summaries = []
    shipment_summary_cache = {}
    for shipment in shipments:
        shipment_no = str(shipment.get("shipment_no", "") or "")
        resolved_direct = resolve_direct_documents(shipment, workflow_datasets)
        resolved_operations = resolve_operational_records(shipment_no, workflow_datasets)
        workflow_progress = required_workflow_progress(
            shipment,
            resolved_direct,
            resolved_operations,
        )
        next_step = next_step_for_shipment(shipment, resolved_direct, resolved_operations)
        health_score = shipment_health_score(
            shipment,
            resolved_direct,
            resolved_operations,
            workflow_progress,
            next_step,
        )
        summary = {
            "shipment": shipment,
            "resolved_direct": resolved_direct,
            "resolved_operations": resolved_operations,
            "workflow_progress": workflow_progress,
            "next_step": next_step,
            "health_score": health_score,
        }
        shipment_summaries.append(summary)
        shipment_summary_cache[id(shipment)] = summary

    shipment_total = len(shipment_summaries)
    workflow_complete = sum(
        1 for summary in shipment_summaries if summary["next_step"]["is_complete"]
    )
    shipments_in_progress = shipment_total - workflow_complete
    critical_shipments = sum(
        1 for summary in shipment_summaries if summary["health_score"]["score"] < 40
    )
    average_health_score = round(
        sum(summary["health_score"]["score"] for summary in shipment_summaries) / shipment_total
    ) if shipment_total else 0
    average_workflow_progress = round(
        sum(summary["workflow_progress"]["percentage"] for summary in shipment_summaries) / shipment_total
    ) if shipment_total else 0
    average_health_label = health_score_label(average_health_score) if shipment_total else "No data"

    notifications = dashboard_notifications(shipment_summaries, shipments_by_recency)
    notification_rows = "".join(
        f"""
<a class="notification-row {dashboard_text(notification['kind'])}" href="{dashboard_text(notification['url'])}">
<span class="notification-dot" aria-hidden="true"></span><span>{dashboard_text(notification['message'])}</span>
</a>"""
        for notification in notifications
    )
    if not notification_rows:
        notification_rows = '<div class="notification-empty">No workflow notifications.</div>'

    recent_activity = recent_activity_entries({
        "Shipment": shipments,
        "Quotation": quotations,
        "Proforma Invoice": proformas,
        "Commercial Invoice": invoices,
        "Packing List": packing_lists,
        "Bill of Lading": bills_of_lading,
    })
    activity_rows = "".join(
        f"""
<article class="activity-row">
<div class="activity-copy"><span class="activity-module">{dashboard_text(activity['module'])}</span>
<div><strong>{dashboard_text(activity['identifier'])}</strong><span class="activity-title">{dashboard_text(activity['title'])}</span></div></div>
<a class="activity-open" href="{dashboard_text(activity['url'])}">Open</a>
</article>"""
        for activity in recent_activity
    )
    if not activity_rows:
        activity_rows = '<div class="activity-empty">No recent activity yet.</div>'

    recent_invoices = recent_invoice_entries(invoices)
    recent_invoice_rows = "".join(
        f"""
<article class="recent-invoice-row">
<div><span>Invoice No</span><strong>{dashboard_text(invoice['invoice_no'])}</strong></div>
<div><span>Buyer</span><strong>{dashboard_text(invoice['buyer'] or '—')}</strong></div>
<div><span>Date</span><strong>{dashboard_text(invoice['date'] or '—')}</strong></div>
<a class="activity-open" href="{dashboard_text(invoice['url'])}">Open</a>
</article>"""
        for invoice in recent_invoices
    )
    if not recent_invoice_rows:
        recent_invoice_rows = '<div class="activity-empty">No invoices yet. Create your first Invoice.</div>'

    workflow_guide = dashboard_workflow_guidance(buyers, products, invoices, packing_lists)
    workflow_guide_steps = "".join(
        f'<div class="guide-step {dashboard_text(step["state"])}">'
        f'<span class="guide-icon" aria-hidden="true">{"✓" if step["state"] == "complete" else "●" if step["state"] == "current" else "○"}</span>'
        f'<strong>{dashboard_text(step["label"])}</strong><small>{dashboard_text(step["state"].title())}</small></div>'
        for step in workflow_guide["steps"]
    )
    workflow_guide_action = (
        f'<a class="guide-next-button" href="{dashboard_text(workflow_guide["next_url"])}">{dashboard_text(workflow_guide["next_label"])} →</a>'
        if workflow_guide["next_url"] else '<span class="guide-complete-badge">✓ Ready</span>'
    )

    setup_steps = [
        ("Company Information", bool(company_count), "/company"),
        ("Buyer", bool(buyers), "/buyer-form"),
        ("Product", bool(products), "/product-form"),
        ("Export Wizard", bool(shipments), "/export-wizard"),
    ]
    setup_completed = sum(1 for _, complete, _ in setup_steps if complete)
    setup_percentage = round(setup_completed * 100 / len(setup_steps))
    setup_steps_html = "".join(
        f'<a class="setup-step{" complete" if complete else ""}" href="{url}">'
        f'<span aria-hidden="true">{"✓" if complete else index}</span><b>{dashboard_text(label)}</b>'
        f'<small>{"Complete" if complete else "Start"}</small></a>'
        for index, (label, complete, url) in enumerate(setup_steps, 1)
    )
    first_action = dashboard_first_action(bool(company_count), buyers, products)
    welcome_html = "" if invoices else (
        '<section class="section" id="welcome-banner"><div class="welcome-card"><div>'
        '<h2>Welcome to Trade Paper AI.</h2><p>Let\'s create your first export document.</p>'
        '<div id="tp-continue-work" class="continue-work" hidden></div></div>'
        f'<div class="quick-start"><small>QUICK START</small><a href="{dashboard_text(first_action["url"])}">{dashboard_text(first_action["label"])}</a>'
        '<a class="secondary" href="/demo">Try Demo Data</a></div></div></section>'
    )
    completion_html = "" if setup_percentage < 100 else (
        '<section class="section"><div class="workflow-celebration"><span aria-hidden="true">🎉</span>'
        '<div><h2>Congratulations!</h2><p>You have successfully completed your first export workflow using Trade Paper AI.</p></div>'
        '<div class="celebration-actions"><a href="/invoice">➕ Create New Invoice</a>'
        '<a href="/packing-page">➕ Create New Packing List</a><a href="/buyers">👥 Manage Buyers</a>'
        '<a href="/products">📦 Manage Products</a></div></div></section>'
    )

    recent_shipments = shipments_by_recency[:5]
    recent_rows = ""
    for shipment in recent_shipments:
        shipment_no = str(shipment.get("shipment_no", "") or "")
        operational_count = sum(
            1 for records in [bookings, containers, customs]
            for record in records
            if shipment_no and record.get("shipment_no") == shipment_no
        )
        cached_summary = shipment_summary_cache[id(shipment)]
        next_step = cached_summary["next_step"]
        workflow_progress = cached_summary["workflow_progress"]
        next_step_label = "Complete" if next_step["is_complete"] else next_step["step_label"]
        next_step_class = "complete" if next_step["is_complete"] else "pending"
        recent_rows += f"""
<tr>
<td>{dashboard_text(shipment_no)}</td>
<td>{dashboard_text(shipment.get("shipment_name", ""))}</td>
<td>{dashboard_text(shipment.get("buyer", "") or shipment.get("customer", ""))}</td>
<td><span class="status-pill">{dashboard_text(shipment.get("status", ""))}</span></td>
<td><span class="workflow-progress-text">{workflow_progress['completed']} / {workflow_progress['total']} · {workflow_progress['percentage']}%</span></td>
<td>{operational_count}</td>
<td><span class="next-step-pill {next_step_class}">{dashboard_text(next_step_label)}</span></td>
<td><a class="table-link" href="/shipment/{dashboard_text(shipment_no)}">View</a></td>
</tr>
"""
    if not recent_rows:
        recent_rows = '<tr><td class="empty" colspan="8">No shipments yet.</td></tr>'

    next_action_items = []
    for shipment in shipments_by_recency:
        cached_summary = shipment_summary_cache[id(shipment)]
        next_step = cached_summary["next_step"]
        if next_step["is_complete"] or not next_step["create_url"]:
            continue
        shipment_no = str(shipment.get("shipment_no", "") or "")
        next_action_items.append(
            f'<a class="next-action-card" href="{dashboard_text(next_step["create_url"])}">'
            f'<span>{dashboard_text(shipment_no)}</span><b>Create {dashboard_text(next_step["step_label"])}</b>'
            '<small>Open the prefilled next workflow form →</small></a>'
        )
        if len(next_action_items) == 3:
            break
    next_actions_html = "".join(next_action_items) or '<div class="next-actions-empty">All caught up. No required workflow actions are pending.</div>'

    def registered_card(key, count, label=None):
        definition = get_document_definition(key)
        return dashboard_card(label or definition.label, count, definition.form_route, definition.list_route)

    commercial_cards = "".join([
        registered_card("quotation", len(quotations)),
        registered_card("proforma", len(proformas)),
        registered_card("invoice", len(invoices)),
        registered_card("packing", len(packing_lists)),
    ])
    shipping_cards = "".join([
        registered_card("shipment", len(shipments), "Shipment Hub"),
        registered_card("shipping_instruction", len(shipping_instructions)),
        registered_card("booking", len(bookings)),
        registered_card("container", len(containers)),
        registered_card("bill_of_lading", len(bills_of_lading)),
    ])
    compliance_cards = "".join([
        registered_card("certificate_of_origin", len(certificates_of_origin)),
        registered_card("inspection", len(inspections)),
        registered_card("insurance", len(insurances)),
        registered_card("weight", len(weights)),
        registered_card("customs", len(customs)),
    ])

    route_report = audit_route_registrations(app)
    system_health = [
        ("Storage", DATA_DIR.is_dir()),
        ("Route", route_report["route_count"] == EXPECTED_ROUTE_COUNT and not route_report["exact_conflicts"]),
        ("Workflow", required_workflow_progress({}, [], [])["total"] == 6),
        ("Validation", callable(require_text) and callable(require_items)),
        ("PDF", callable(shipment_pdf)),
    ]
    health_cards = "".join(
        f'<div class="health-card"><span class="health-check" aria-hidden="true">✓</span><b>{dashboard_text(label)}</b><span>OK</span></div>'
        for label, passed in system_health
        if passed
    )
    registered_get_paths = {
        route.path
        for route in app.routes
        if "GET" in (getattr(route, "methods", set()) or set())
    }
    navigation_paths = {"/", "/search", "/shipment-list", "/demo", "/about", "/release-notes", "/version-history", "/contact", "/privacy", "/terms"} | {
        definition.list_route for definition in DOCUMENT_DEFINITIONS if definition.list_route
    }
    release_checklist = [
        ("Route", route_report["route_count"] == EXPECTED_ROUTE_COUNT and not route_report["exact_conflicts"]),
        ("Workflow", required_workflow_progress({}, [], [])["total"] == 6),
        ("Validation", callable(require_text) and callable(require_items)),
        ("Search", callable(global_search)),
        ("Dashboard", "/" in registered_get_paths),
        ("Shipment", callable(shipment_detail) and "/shipment/{shipment_no}" in registered_get_paths),
        ("PDF", callable(shipment_pdf)),
        ("Version", APP_VERSION == "3.5.0"),
        ("Health Card", len(system_health) == 5 and all(passed for _, passed in system_health)),
        ("Footer", callable(release_footer)),
        ("Navigation", navigation_paths.issubset(registered_get_paths)),
    ]
    release_summary = build_release_summary(release_checklist)
    release_checks_html = "".join(
        f'<div class="release-check"><span aria-hidden="true">✓</span>{dashboard_text(label)}</div>'
        for label, passed in release_checklist
        if passed
    )
    release_notes_html = "".join(
        f'<li>{dashboard_text(note)}</li>' for note in release_summary["notes"]
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trade Paper AI</title>
<style>
*{{box-sizing:border-box;}}
body{{margin:0;background:#F3F4F6;color:#111827;font-family:Arial,sans-serif;overflow-x:hidden;}}
.page{{width:94%;max-width:1280px;min-width:0;margin:auto;padding:46px 0 60px;}}
.hero{{text-align:center;margin-bottom:34px;}}
.hero h1{{font-size:56px;margin:0 0 10px;}}
.hero p{{font-size:18px;color:#6B7280;letter-spacing:.5px;margin:0;}}
.release-badge{{display:inline-flex;align-items:center;gap:8px;margin-top:14px;padding:8px 12px;border:1px solid #BFDBFE;border-radius:999px;background:#EFF6FF;color:#1D4ED8;font-size:13px;font-weight:bold}}
.global-search{{display:flex;gap:10px;max-width:660px;margin:24px auto 0;}}
.global-search input{{flex:1;min-width:0;padding:13px 15px;border:1px solid #D1D5DB;border-radius:10px;font-size:15px;}}
.global-search button{{padding:13px 18px;background:#111827;color:white;border:0;border-radius:10px;font-weight:bold;cursor:pointer;}}
.section{{min-width:0;margin:0 0 40px;}}
.section-title{{font-size:30px;text-align:center;margin:0 0 22px;}}
.overview-grid,.status-grid,.document-grid{{display:grid;gap:18px;min-width:0;}}
.overview-grid{{grid-template-columns:repeat(5,minmax(0,1fr));}}
.dashboard-stat-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:18px}}.dashboard-stat-card{{display:flex;min-height:142px;flex-direction:column;justify-content:center;background:white;color:#111827;border:1px solid #E5E7EB;border-radius:16px;padding:22px;text-decoration:none;box-shadow:0 9px 22px rgba(17,24,39,.07);transition:transform .15s ease,box-shadow .15s ease}}.dashboard-stat-card:hover{{transform:translateY(-3px);box-shadow:0 12px 26px rgba(17,24,39,.11)}}.dashboard-stat-card:focus-visible{{outline:3px solid #2563EB;outline-offset:3px}}.dashboard-stat-card span{{color:#64748B;font-weight:bold}}.dashboard-stat-card strong{{margin-top:10px;font-size:44px;color:#111827}}
.insight-card-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}}.insight-card,.insight-columns article{{background:#fff;border:1px solid #E5E7EB;border-radius:16px;padding:20px}}.insight-card span{{display:block;color:#64748B;font-weight:bold}}.insight-card strong{{display:block;margin-top:8px;font-size:34px}}.insight-columns{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-top:14px}}.insight-columns h3{{margin-top:0}}.insight-columns ol{{list-style:none;margin:0;padding:0}}.insight-columns li{{display:flex;justify-content:space-between;gap:12px;padding:9px 0;border-bottom:1px solid #E5E7EB}}.insight-columns a{{color:#1D4ED8;font-weight:bold}}.personal-insights details{{margin-top:14px;background:#fff;border:1px solid #E5E7EB;border-radius:16px;padding:16px}}.personal-insights summary{{cursor:pointer;font-weight:bold}}
.operations-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}}.operations-panel{{background:white;border:1px solid #E5E7EB;border-radius:18px;padding:22px;box-shadow:0 9px 22px rgba(17,24,39,.07)}}.operations-panel h2{{margin:0 0 16px;font-size:24px}}.operations-stat-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}}.operations-stat-card{{display:flex;min-height:92px;flex-direction:column;justify-content:center;padding:14px;border:1px solid #E5E7EB;border-radius:12px;background:#F8FAFC;color:#111827;text-decoration:none}}.operations-stat-card span{{color:#64748B;font-size:12px;font-weight:bold}}.operations-stat-card strong{{margin-top:7px;font-size:28px}}.operations-recent-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px;margin-top:18px}}.operations-list{{overflow:hidden;border:1px solid #E5E7EB;border-radius:14px;background:white}}.operations-list h3{{margin:0;padding:15px 17px;border-bottom:1px solid #E5E7EB;font-size:18px}}.operations-row{{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px 17px;border-bottom:1px solid #E5E7EB}}.operations-row:last-child{{border-bottom:0}}.operations-row div{{display:grid;gap:5px;min-width:0}}.operations-row div span{{overflow:hidden;color:#64748B;font-size:13px;text-overflow:ellipsis;white-space:nowrap}}.operations-meta{{color:#64748B;font-size:13px;font-weight:bold;white-space:nowrap}}
.workflow-guide{{background:white;border:1px solid #E5E7EB;border-radius:18px;padding:24px;box-shadow:0 10px 24px rgba(17,24,39,.07)}}.guide-progress-row{{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-bottom:18px}}.guide-progress-copy{{display:grid;gap:4px}}.guide-progress-copy span{{color:#64748B;font-size:13px;font-weight:bold}}.guide-progress-copy strong{{font-size:24px}}.guide-progress-track{{flex:1;max-width:460px;height:8px;overflow:hidden;border-radius:999px;background:#E5E7EB}}.guide-progress-fill{{display:block;height:100%;border-radius:inherit;background:#2563EB}}.guide-progress-fill.complete{{background:#166534}}.guide-steps{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.guide-step{{position:relative;display:grid;gap:7px;min-height:112px;align-content:center;border:1px solid #D1D5DB;border-radius:13px;padding:16px;background:#F8FAFC;color:#475569}}.guide-step.complete{{border-color:#BBF7D0;background:#F0FDF4;color:#166534}}.guide-step.current{{border:2px solid #2563EB;background:#111827;color:white;box-shadow:0 8px 20px rgba(37,99,235,.18)}}.guide-step small{{font-weight:bold;opacity:.72}}.guide-icon{{font-size:18px;font-weight:bold}}.guide-context{{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-top:18px;padding:16px;border-radius:12px;background:#F8FAFC;color:#334155}}.guide-context p{{margin:0;line-height:1.5}}.guide-next-button{{display:inline-flex;min-height:44px;align-items:center;justify-content:center;padding:11px 16px;border-radius:10px;background:#1D4ED8;color:white;text-decoration:none;font-weight:bold;white-space:nowrap}}.guide-next-button:focus-visible{{outline:3px solid #2563EB;outline-offset:3px}}.guide-complete-badge{{display:inline-flex;padding:9px 12px;border-radius:999px;background:#DCFCE7;color:#166534;font-weight:bold;white-space:nowrap}}
.status-grid{{grid-template-columns:repeat(6,minmax(0,1fr));}}
.document-grid{{grid-template-columns:repeat(auto-fit,minmax(210px,1fr));}}
.summary-card,.status-card,.document-card{{background:#111827;color:white;border-radius:16px;padding:24px;box-shadow:0 10px 24px rgba(17,24,39,.12);}}
.summary-card{{text-decoration:none;transition:transform .15s ease;}}
.summary-card:hover{{transform:translateY(-3px);}}
.welcome-card{{display:grid;grid-template-columns:1.4fr 1fr;gap:24px;align-items:center;background:#111827;color:white;border-radius:18px;padding:28px;box-shadow:0 12px 28px rgba(17,24,39,.14)}}.welcome-card h2{{margin:0 0 9px;font-size:30px}}.welcome-card p{{margin:0;color:#CBD5E1;line-height:1.6}}.quick-start{{display:grid;gap:10px}}.quick-start a{{display:block;padding:12px 15px;border-radius:11px;background:white;color:#111827;text-decoration:none;font-weight:bold}}.quick-start a.secondary{{background:#374151;color:white}}.quick-start small{{color:#CBD5E1}}
.getting-started{{background:#fff;border:1px solid #E5E7EB;border-radius:18px;padding:24px;box-shadow:0 10px 24px rgba(17,24,39,.07)}}.setup-heading{{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:16px}}.setup-heading h2{{margin:0;font-size:26px}}.setup-heading strong{{color:#1D4ED8}}.setup-track{{height:8px;overflow:hidden;border-radius:999px;background:#E5E7EB;margin-bottom:18px}}.setup-track span{{display:block;height:100%;border-radius:inherit;background:#2563EB;transition:width .25s ease}}.setup-steps{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px}}.setup-step{{display:grid;grid-template-columns:auto 1fr;gap:5px 9px;align-items:center;min-height:78px;padding:13px;border:1px solid #D1D5DB;border-radius:12px;background:#F8FAFC;color:#334155;text-decoration:none}}.setup-step>span{{grid-row:1/3;display:grid;place-items:center;width:28px;height:28px;border-radius:999px;background:#E5E7EB;font-weight:bold}}.setup-step small{{color:#64748B}}.setup-step.complete{{border-color:#BBF7D0;background:#F0FDF4;color:#166534}}.setup-step.complete>span{{background:#DCFCE7;color:#166534}}
.workflow-celebration{{display:grid;grid-template-columns:auto 1fr;gap:14px 18px;align-items:center;padding:24px;border:1px solid #BBF7D0;border-radius:18px;background:#F0FDF4;color:#166534;box-shadow:0 10px 24px rgba(22,101,52,.08)}}.workflow-celebration>span{{font-size:34px}}.workflow-celebration h2{{margin:0 0 5px}}.workflow-celebration p{{margin:0;color:#47725A}}.celebration-actions{{grid-column:1/-1;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-top:5px}}.celebration-actions a{{display:flex;min-height:46px;align-items:center;justify-content:center;padding:10px;border-radius:10px;background:#fff;color:#166534;text-decoration:none;font-weight:bold;text-align:center}}
.continue-work{{margin-top:16px}}.continue-work a{{display:inline-flex;align-items:center;gap:8px;padding:10px 13px;border:1px solid #475569;border-radius:10px;background:#1F2937;color:white;text-decoration:none;font-weight:bold}}.continue-work small{{display:block;margin-top:6px;color:#94A3B8}}
.summary-card h3,.document-card h3{{font-size:20px;margin:0 0 15px;}}
.summary-count,.document-count{{font-size:40px;font-weight:bold;}}
.status-card{{display:flex;min-height:112px;flex-direction:column;align-items:center;justify-content:center;background:white;color:#111827;border:1px solid #E5E7EB;text-align:center;}}
.status-card b{{display:block;font-size:32px;margin-top:10px;}}
.summary-health-label{{display:block;margin-top:5px;color:#6B7280;font-size:13px;font-weight:bold;}}
.table-wrap{{background:white;border:1px solid #E5E7EB;border-radius:16px;overflow-x:auto;box-shadow:0 10px 24px rgba(17,24,39,.07);}}
table{{width:100%;min-width:900px;border-collapse:collapse;}}
th{{background:#111827;color:white;text-align:left;padding:14px;font-size:14px;}}
td{{padding:14px;border-bottom:1px solid #E5E7EB;font-size:14px;}}
.empty{{text-align:center;color:#6B7280;padding:28px;}}
.status-pill{{display:inline-block;background:#E5E7EB;padding:7px 10px;border-radius:999px;font-weight:bold;}}
.next-step-pill{{display:inline-block;padding:7px 10px;border-radius:999px;font-size:13px;font-weight:bold;white-space:nowrap;}}
.next-step-pill.complete{{background:#DCFCE7;color:#166534;}}
.next-step-pill.pending{{background:#E5E7EB;color:#374151;}}
.workflow-progress-text{{font-weight:bold;color:#374151;white-space:nowrap;}}
.table-link,.master-link{{display:inline-block;min-height:36px;background:#111827;color:white;text-decoration:none;padding:9px 12px;border-radius:9px;font-weight:bold;}}
.document-actions{{display:flex;gap:10px;margin-top:18px;}}
.document-button{{flex:1;text-align:center;padding:11px 9px;border-radius:9px;text-decoration:none;font-weight:bold;}}
.document-button.primary{{background:white;color:#111827;}}
.document-button.secondary{{background:#374151;color:white;}}
.action-row{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;}}
.action-link{{display:flex;align-items:center;gap:12px;min-height:72px;background:white;color:#111827;border:1px solid #E5E7EB;border-radius:14px;padding:16px 18px;text-decoration:none;font-weight:bold;box-shadow:0 7px 18px rgba(17,24,39,.06);transition:border-color .15s ease,box-shadow .15s ease,transform .15s ease;}}
.action-link:hover{{border-color:#9CA3AF;box-shadow:0 10px 22px rgba(17,24,39,.1);transform:translateY(-2px);}}
.action-link:focus-visible{{outline:3px solid #2563EB;outline-offset:3px;}}
.action-icon{{display:grid;place-items:center;width:32px;height:32px;flex:0 0 32px;background:#F3F4F6;border-radius:9px;color:#111827;font-size:20px;}}
.action-copy{{display:grid;gap:4px}}.action-copy b{{font-size:14px}}.action-copy small{{color:#64748B;font-size:12px;font-weight:normal}}
.notification-list{{display:grid;gap:10px;}}
.notification-row{{display:flex;align-items:center;gap:12px;min-height:50px;background:white;color:#111827;border:1px solid #E5E7EB;border-left:4px solid #111827;border-radius:12px;padding:12px 15px;text-decoration:none;font-weight:bold;box-shadow:0 6px 16px rgba(17,24,39,.05);}}
.notification-row:hover{{box-shadow:0 9px 20px rgba(17,24,39,.09);}}
.notification-row:focus-visible{{outline:3px solid #2563EB;outline-offset:3px;}}
.notification-dot{{width:8px;height:8px;flex:0 0 8px;border-radius:999px;background:#111827;}}
.notification-row.stale{{border-left-color:#991B1B;}}.notification-row.stale .notification-dot{{background:#991B1B;}}
.notification-row.health{{border-left-color:#92400E;}}.notification-row.health .notification-dot{{background:#92400E;}}
.notification-row.next{{border-left-color:#1D4ED8;}}.notification-row.next .notification-dot{{background:#1D4ED8;}}
.notification-row.complete{{border-left-color:#166534;}}.notification-row.complete .notification-dot{{background:#166534;}}
.notification-empty{{background:white;border:1px solid #E5E7EB;border-radius:12px;padding:24px;text-align:center;color:#6B7280;}}
.activity-list{{background:white;border:1px solid #E5E7EB;border-radius:16px;overflow:hidden;box-shadow:0 10px 24px rgba(17,24,39,.07);}}
.activity-row{{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:15px 18px;border-bottom:1px solid #E5E7EB;}}
.activity-row:last-child{{border-bottom:0;}}
.activity-copy{{display:flex;align-items:center;gap:14px;min-width:0;}}
.activity-module{{display:inline-block;min-width:142px;background:#E5E7EB;color:#374151;padding:7px 10px;border-radius:999px;font-size:12px;font-weight:bold;text-align:center;}}
.activity-title{{margin-left:10px;color:#6B7280;}}
.activity-open{{display:inline-block;background:#111827;color:white;text-decoration:none;padding:9px 13px;border-radius:9px;font-weight:bold;}}
.activity-open:focus-visible{{outline:3px solid #2563EB;outline-offset:3px;}}
.activity-empty{{padding:28px;text-align:center;color:#6B7280;}}
.activity-subtitle{{margin:0 0 12px;color:#475569;font-size:17px}}.activity-subtitle.all{{margin-top:24px}}.recent-invoice-list{{display:grid;background:white;border:1px solid #E5E7EB;border-radius:16px;overflow:hidden;box-shadow:0 10px 24px rgba(17,24,39,.07)}}.recent-invoice-row{{display:grid;grid-template-columns:1fr 1.4fr 1fr auto;gap:18px;align-items:center;padding:15px 18px;border-bottom:1px solid #E5E7EB}}.recent-invoice-row:last-child{{border-bottom:0}}.recent-invoice-row div{{display:grid;gap:4px;min-width:0}}.recent-invoice-row span{{color:#6B7280;font-size:12px;font-weight:bold}}.recent-invoice-row strong{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.next-actions-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}}.next-action-card{{display:flex;min-height:118px;flex-direction:column;justify-content:center;background:white;color:#111827;border:1px solid #E5E7EB;border-radius:14px;padding:18px;text-decoration:none;box-shadow:0 7px 18px rgba(17,24,39,.06)}}.next-action-card span{{color:#6B7280;font-size:12px;font-weight:bold}}.next-action-card b{{margin:8px 0;font-size:17px}}.next-action-card small{{color:#1D4ED8}}.next-actions-empty{{grid-column:1/-1;background:white;border:1px solid #E5E7EB;border-radius:14px;padding:24px;text-align:center;color:#6B7280}}
.master-row{{display:flex;gap:12px;justify-content:center;flex-wrap:wrap;}}
.master-link{{padding:14px 18px;}}
.master-link{{background:white;color:#111827;border:1px solid #D1D5DB;}}
.system-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;}}
.system-card,.health-card{{min-height:96px;background:white;border:1px solid #E5E7EB;border-radius:14px;padding:18px;display:flex;flex-direction:column;justify-content:center;box-shadow:0 7px 18px rgba(17,24,39,.05);}}
.system-card span,.health-card span{{margin-top:7px;color:#6B7280;font-size:13px;}}.system-card b{{font-size:20px;}}
.health-grid{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:14px;}}.health-card{{align-items:center;text-align:center;}}
.health-card .health-check{{display:grid;place-items:center;width:24px;height:24px;margin:0 0 7px;border-radius:999px;background:#DCFCE7;color:#166534;font-weight:bold;}}
.release-card{{background:#111827;color:white;border-radius:18px;padding:26px;box-shadow:0 10px 24px rgba(17,24,39,.12)}}.release-heading{{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;flex-wrap:wrap}}.release-heading h3{{margin:0 0 7px;font-size:26px}}.release-heading p{{margin:0;color:#CBD5E1}}.release-status{{display:inline-block;padding:8px 12px;border-radius:999px;background:#DCFCE7;color:#166534;font-weight:bold}}.release-checks{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:9px;margin:22px 0}}.release-check{{display:flex;align-items:center;gap:8px;background:#1F2937;border-radius:10px;padding:10px 12px;font-size:13px;font-weight:bold}}.release-check span{{color:#86EFAC}}.release-notes{{margin:0;padding-left:20px;color:#E5E7EB;line-height:1.7}}
.subscription-card{{display:flex;align-items:center;justify-content:space-between;gap:20px;margin:0 0 24px;padding:22px 26px;border-radius:16px;background:#EFF6FF;border:1px solid #BFDBFE}}.subscription-card span{{color:#1D4ED8;font-size:12px;font-weight:800;text-transform:uppercase}}.subscription-card h2{{margin:5px 0}}.subscription-card p{{margin:0;color:#475569}}.subscription-card a{{padding:11px 16px;border-radius:9px;background:#111827;color:#fff;text-decoration:none;font-weight:800}}
.tp-release-footer{{width:100%;margin:34px auto 0;padding:24px 0 0;border-top:1px solid #D1D5DB;color:#6B7280;text-align:center;font-size:13px;line-height:1.7}}.tp-release-footer strong{{display:block;color:#374151}}.tp-release-version{{font-size:12px}}
@media(max-width:1000px){{.overview-grid{{grid-template-columns:repeat(3,1fr);}}.dashboard-stat-grid,.guide-steps{{grid-template-columns:repeat(2,minmax(0,1fr))}}.operations-grid,.operations-recent-grid,.insight-columns{{grid-template-columns:1fr}}.setup-steps,.celebration-actions{{grid-template-columns:repeat(2,minmax(0,1fr))}}.status-grid{{grid-template-columns:repeat(3,1fr);}}.action-row{{grid-template-columns:repeat(2,minmax(0,1fr));}}.next-actions-grid{{grid-template-columns:1fr 1fr}}}}
@media(max-width:1000px){{.system-grid,.health-grid{{grid-template-columns:repeat(2,minmax(0,1fr));}}}}
@media(max-width:640px){{.page{{padding-top:28px;}}.hero h1{{font-size:38px;}}.welcome-card{{grid-template-columns:1fr;padding:22px}}.dashboard-stat-grid,.guide-steps,.setup-steps,.celebration-actions,.insight-card-grid{{grid-template-columns:1fr}}.operations-stat-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}.workflow-celebration{{grid-template-columns:1fr;text-align:center}}.setup-heading{{align-items:flex-start;flex-direction:column}}.guide-progress-row,.guide-context{{align-items:stretch;flex-direction:column}}.guide-progress-track{{width:100%;max-width:none}}.guide-next-button{{width:100%}}.overview-grid,.status-grid,.system-grid,.health-grid{{grid-template-columns:1fr 1fr;}}.next-actions-grid{{grid-template-columns:1fr}}.action-row{{grid-template-columns:1fr;}}.global-search{{flex-direction:column;}}.activity-row,.activity-copy{{align-items:stretch;flex-direction:column;}}.recent-invoice-row{{grid-template-columns:1fr;gap:12px}}.recent-invoice-row strong{{white-space:normal}}.activity-module{{min-width:0;width:max-content;}}.activity-title{{display:block;margin:5px 0 0;}}.activity-open{{min-height:46px;text-align:center;}}}}
@media(max-width:420px){{.overview-grid,.status-grid{{grid-template-columns:1fr;}}}}
</style>
</head>
<body>
<main class="page">
<header class="hero"><h1>{dashboard_text(APP_NAME)}</h1><p>Version {dashboard_text(APP_VERSION)}</p><span class="release-badge">🚀 {dashboard_text(RELEASE_TYPE)}</span>
<form class="global-search" method="get" action="/search"><input name="q" placeholder="Search documents, shipments, companies..."><button type="submit">Search</button></form>
</header>

{welcome_html}
{subscription_html}
{personal_insights_html}

<section class="section"><div class="getting-started"><div class="setup-heading"><h2>Getting Started</h2><strong>{setup_percentage}% Complete</strong></div><div class="setup-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{setup_percentage}"><span style="width:{setup_percentage}%"></span></div><div class="setup-steps">{setup_steps_html}</div><p><a href="/onboarding?replay=1">View setup guide again</a></p></div></section>
{completion_html}

<section class="section"><h2 class="section-title">Dashboard Statistics</h2><div class="dashboard-stat-grid">
<a class="dashboard-stat-card" href="/buyers"><span>Total Buyers</span><strong>{len(buyers)}</strong></a>
<a class="dashboard-stat-card" href="/products"><span>Total Products</span><strong>{len(products)}</strong></a>
<a class="dashboard-stat-card" href="/invoice-list"><span>Total Invoices</span><strong>{len(invoices)}</strong></a>
<a class="dashboard-stat-card" href="/packing-list"><span>Total Packing Lists</span><strong>{len(packing_lists)}</strong></a>
</div></section>

<section class="section"><div class="operations-grid">
<div class="operations-panel"><h2>Founding Beta</h2><div class="operations-stat-grid">{beta_cards}</div></div>
<div class="operations-panel"><h2>Feedback</h2><div class="operations-stat-grid">{feedback_cards}</div></div>
</div><div class="operations-recent-grid">
<div class="operations-list"><h3>최근 신청 5건</h3>{recent_beta_rows}</div>
<div class="operations-list"><h3>최근 Feedback 5건</h3>{recent_feedback_rows}</div>
</div></section>

<section class="section"><h2 class="section-title">Workflow Guide</h2><div class="workflow-guide">
<div class="guide-progress-row"><div class="guide-progress-copy"><span>Workflow Progress</span><strong>{workflow_guide['percentage']}%</strong></div><div class="guide-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{workflow_guide['percentage']}"><span class="guide-progress-fill{' complete' if workflow_guide['is_complete'] else ''}" style="width:{workflow_guide['percentage']}%"></span></div></div>
<div class="guide-steps">{workflow_guide_steps}</div>
<div class="guide-context"><p>{dashboard_text(workflow_guide['message'])}</p>{workflow_guide_action}</div>
</div></section>

<section class="section"><h2 class="section-title">Overview</h2><div class="overview-grid">
<a class="summary-card" href="/company"><h3>Company</h3><div class="summary-count">{company_count}</div></a>
<a class="summary-card" href="/customer"><h3>Customers</h3><div class="summary-count">{len(customers)}</div></a>
<a class="summary-card" href="/buyers"><h3>Buyers</h3><div class="summary-count">{len(buyers)}</div></a>
<a class="summary-card" href="/products"><h3>Products</h3><div class="summary-count">{len(products)}</div></a>
<a class="summary-card" href="/shipment-list"><h3>Shipments</h3><div class="summary-count">{len(shipments)}</div></a>
</div></section>

<section class="section"><h2 class="section-title">Shipment Overview</h2><div class="status-grid">
<div class="status-card">Total Shipments<b>{len(shipments)}</b></div>
<div class="status-card">Inquiry<b>{status_counts['inquiry']}</b></div>
<div class="status-card">Confirmed<b>{status_counts['confirmed']}</b></div>
<div class="status-card">Ready to Ship<b>{status_counts['ready to ship']}</b></div>
<div class="status-card">Shipped<b>{status_counts['shipped']}</b></div>
<div class="status-card">Completed<b>{status_counts['completed']}</b></div>
</div></section>

<section class="section"><h2 class="section-title">Shipment Summary</h2><div class="status-grid">
<div class="status-card">Total Shipments<b>{shipment_total}</b></div>
<div class="status-card">Workflow Complete<b>{workflow_complete}</b></div>
<div class="status-card">In Progress<b>{shipments_in_progress}</b></div>
<div class="status-card">Critical<b>{critical_shipments}</b></div>
<div class="status-card">Average Health Score<b>{average_health_score} / 100</b><span class="summary-health-label">{dashboard_text(average_health_label)}</span></div>
<div class="status-card">Average Workflow Progress<b>{average_workflow_progress}%</b></div>
</div></section>

<section class="section"><h2 class="section-title">Notification Center</h2><div class="notification-list">{notification_rows}</div></section>

<section class="section"><h2 class="section-title">Quick Actions</h2><div class="action-row">
<a class="action-link" href="/export-wizard"><span class="action-icon" aria-hidden="true">⚡</span><span class="action-copy"><b>Export Wizard</b><small>Create the core document chain.</small></span></a>
<a class="action-link" href="/invoice"><span class="action-icon" aria-hidden="true">＋</span><span class="action-copy"><b>New Invoice</b><small>Create an export invoice.</small></span></a>
<a class="action-link" href="/packing-page"><span class="action-icon" aria-hidden="true">＋</span><span class="action-copy"><b>New Packing List</b><small>Generate packing documents.</small></span></a>
<a class="action-link" href="/buyer-form"><span class="action-icon" aria-hidden="true">＋</span><span class="action-copy"><b>New Buyer</b><small>Manage your customers.</small></span></a>
<a class="action-link" href="/product-form"><span class="action-icon" aria-hidden="true">＋</span><span class="action-copy"><b>New Product</b><small>Manage your products.</small></span></a>
<a class="action-link" href="/team"><span class="action-icon" aria-hidden="true">👥</span><span class="action-copy"><b>Team</b><small>Invite users and manage roles.</small></span></a>
</div></section>

<section class="section"><h2 class="section-title">Today's Next Actions</h2><div class="next-actions-grid">{next_actions_html}</div></section>

<section class="section"><h2 class="section-title">Recent Activity</h2><h3 class="activity-subtitle">Recent Invoices</h3><div class="recent-invoice-list">{recent_invoice_rows}</div><h3 class="activity-subtitle all">All Activity</h3><div class="activity-list">{activity_rows}</div></section>

<section class="section"><h2 class="section-title">Recent Shipments</h2><div class="table-wrap"><table>
<thead><tr><th>Shipment No</th><th>Shipment Name</th><th>Buyer / Customer</th><th>Status</th><th>Workflow Progress</th><th>Operational Records</th><th>Next Step</th><th>View</th></tr></thead>
<tbody>{recent_rows}</tbody></table></div></section>

<section class="section"><h2 class="section-title">Commercial Documents</h2><div class="document-grid">{commercial_cards}</div></section>
<section class="section"><h2 class="section-title">Shipping Operations</h2><div class="document-grid">{shipping_cards}</div></section>
<section class="section"><h2 class="section-title">Certificates and Compliance</h2><div class="document-grid">{compliance_cards}</div></section>

<section class="section"><h2 class="section-title">Master Data</h2><div class="master-row">
<a class="master-link" href="/company">Company</a><a class="master-link" href="/customer">Customers</a>
<a class="master-link" href="/buyers">Buyers</a><a class="master-link" href="/products">Products</a>
</div></section>
<section class="section"><h2 class="section-title">System Information</h2><div class="system-grid">
<div class="system-card"><span>Version</span><b>v{dashboard_text(APP_VERSION)}</b></div>
<div class="system-card"><span>Total Routes</span><b>{len(app.routes)}</b></div>
<div class="system-card"><span>Total Documents</span><b>{len(DOCUMENT_DEFINITIONS)}</b></div>
<div class="system-card"><span>Last Updated</span><b>{dashboard_text(LAST_UPDATED)}</b></div>
</div></section>
<section class="section"><h2 class="section-title">System Health</h2><div class="health-grid">{health_cards}</div></section>
<section class="section"><h2 class="section-title">Release Summary</h2><div class="release-card">
<div class="release-heading"><div><h3>{dashboard_text(release_summary['product'])}</h3><p>Version: {dashboard_text(release_summary['version'])} · Release Stage: {dashboard_text(release_summary['release_stage'])}</p></div><span class="release-status">{dashboard_text(release_summary['status'])}</span></div>
<div class="release-checks">{release_checks_html}</div><ul class="release-notes">{release_notes_html}</ul>
</div></section>
{release_footer()}
</main>
</body>
</html>"""
    return HTMLResponse(html)
