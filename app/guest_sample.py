"""Anonymous, numeric-only practice. Never creates account-owned records."""
from base64 import b64encode
from decimal import Decimal
from io import BytesIO
import logging
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen.canvas import Canvas

from app import analytics
from app.invoice import create_invoice_pdf
from app.packing import create_packing_list_pdf

router = APIRouter()
logger = logging.getLogger(__name__)


class PracticeShipment(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    quantity: int = Field(ge=1, le=10000, strict=True)
    unit_price: Decimal = Field(gt=0, le=1000, max_digits=6, decimal_places=2)
    cartons: int = Field(ge=1, le=10000, strict=True)
    net_weight: Decimal = Field(gt=0, le=10000, max_digits=7, decimal_places=2)
    gross_weight: Decimal = Field(gt=0, le=10000, max_digits=7, decimal_places=2)

    @model_validator(mode="after")
    def check_weights(self):
        if self.gross_weight < self.net_weight:
            raise ValueError("Gross weight must be at least net weight.")
        if self.cartons > self.quantity:
            raise ValueError("This sample needs at least one piece per carton.")
        return self


def marked_sample(content, note=""):
    """Watermark only this response, without patching shared PDF renderers."""
    reader, writer = PdfReader(BytesIO(content)), PdfWriter()
    for page in reader.pages:
        overlay = BytesIO()
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        canvas = Canvas(overlay, pagesize=(width, height))
        canvas.setFillColorRGB(.11, .29, .65)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(45, height - 107, "FICTIONAL PRACTICE - NOT FOR COMMERCIAL USE")
        canvas.setFont("Helvetica", 8)
        canvas.drawString(45, 25, "Practice only | www.tradepaper.ai/try-sample")
        if note:
            canvas.drawRightString(width - 45, 25, note)
        canvas.save()
        page.merge_page(PdfReader(overlay).pages[0])
        writer.add_page(page)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def build_practice_pair(shipment):
    # Fixed parties and item names prevent this endpoint becoming a free-form
    # document export or accidentally resolving private master data.
    company = {"name": "Sample Export Co.", "address": "100 Example Road, Sample City",
               "email": "seller@example.com", "phone": ""}
    parties = {"seller": company["name"], "seller_address": company["address"],
               "seller_email": company["email"], "seller_phone": "",
               "buyer": "Sample Import Co.", "buyer_address": "200 Example Street, Demo City",
               "buyer_email": "buyer@example.com", "invoice_no": "PRACTICE-INV-001"}
    item = {"name": "Sample Cotton T-shirt", "hs_code": "", "origin": "Korea",
            "unit": "PCS", "quantity": shipment.quantity}
    invoice = dict(parties, currency="USD", items=[dict(item, unit_price=float(shipment.unit_price))])
    packing = dict(parties, packing_no="PRACTICE-PK-001", items=[dict(
        item, carton=shipment.cartons, net_weight=float(shipment.net_weight),
        gross_weight=float(shipment.gross_weight))])
    return {
        "invoice": marked_sample(create_invoice_pdf(invoice, company).body),
        "packing": marked_sample(create_packing_list_pdf(packing, company, {}).body, "All weights in kg"),
    }


@router.post("/try-sample/documents", include_in_schema=False)
def practice_documents(shipment: PracticeShipment, request: Request):
    documents = build_practice_pair(shipment)
    try:
        source = request.query_params.get("source", "Other")
        if source not in analytics.VISITOR_SOURCES:
            source = "Other"
        analytics.record_visit("Practice Pair Generated", source)
    except Exception:
        logger.exception("Practice generation counter could not be recorded")
    return JSONResponse({name: b64encode(content).decode("ascii") for name, content in documents.items()},
                        headers={"Cache-Control": "no-store"})


COPY = {
    "en": {
        "title": "Try one shipment. No signup.",
        "intro": "Change five numbers, then make a matching Invoice and Packing List. Use fictional numbers only.",
        "badge": "FREE PRACTICE · NO ACCOUNT",
        "shipment": "Your fictional shipment",
        "parties": "Sample Export Co. → Sample Import Co.",
        "item": "100% sample data · Cotton T-shirt · USD · PCS",
        "quantity": "Quantity (pieces)", "price": "Unit price (USD)", "cartons": "Cartons",
        "net": "Total net weight (kg)", "gross": "Total gross weight (kg)",
        "weight_hint": "Enter totals for the whole shipment, not per piece. Gross weight includes packaging.",
        "total": "Invoice total", "button": "Create both sample PDFs", "busy": "Creating your PDFs…",
        "reset": "Reset example", "ready": "Your two sample PDFs are ready.",
        "changed": "Numbers changed. Create both PDFs again to update the downloads.",
        "error": "Could not create the PDFs. Check the numbers and try again.",
        "weight_error": "Gross weight must be at least net weight.",
        "carton_error": "Use no more cartons than pieces in this example.",
        "invoice": "Download sample Invoice", "packing": "Download sample Packing List",
        "review": "Compare the buyer, item and quantity across both PDFs. Check the invoice value, cartons and weights against your entries.",
        "privacy": "Only the numbers are sent to generate these fictional PDFs. They are not saved as documents, and no workspace allowance is used. Anonymous activity counts are recorded, without document contents.",
        "limits": "Practice documents only, not for an actual shipment. No customs filing or compliance certification. In the saved workspace, cartons and weights are entered manually and Invoice edits do not update an existing Packing List.",
        "next": "Would this fit the documents you prepare?",
        "feedback": "Tell us what is missing", "signup": "Create a free workspace",
        "signup_hint": "Workspace: five saved documents per month, Invoice and Packing List counted separately. Paid checkout is off. Practice values are not transferred to signup.",
        "guide": "Step-by-step guide", "js": "Enable JavaScript for this interactive practice, or open the fixed sample PDFs in the guide.",
    },
    "ko": {
        "title": "가입 없이, 가상 출하 한 건 체험",
        "intro": "숫자 다섯 개를 바꾸고 Invoice와 Packing List를 함께 만들어 보세요. 가상 숫자만 사용해 주세요.",
        "badge": "무료 체험 · 가입 불필요", "shipment": "가상 출하 정보",
        "parties": "가상 수출회사 → 가상 수입회사", "item": "가상 품목 · 면 티셔츠 · USD · 개(PCS)",
        "quantity": "수량 (개)", "price": "개당 가격 (USD)", "cartons": "박스 수",
        "net": "포장 제외 순중량 (kg)", "gross": "포장 포함 총중량 (kg)",
        "weight_hint": "개당 중량이 아닌 출하 전체의 중량입니다. 총중량에는 포장 무게가 포함됩니다.",
        "total": "Invoice 합계", "button": "샘플 PDF 두 장 만들기", "busy": "PDF를 만드는 중…",
        "reset": "예시로 초기화", "ready": "샘플 PDF 두 장을 만들었습니다.",
        "changed": "숫자가 바뀌었습니다. PDF 두 장을 다시 만들어 주세요.",
        "error": "PDF를 만들지 못했습니다. 숫자를 확인하고 다시 시도해 주세요.",
        "weight_error": "총중량은 순중량 이상이어야 합니다.",
        "carton_error": "이 예시에서 박스 수는 품목 수량 이하여야 합니다.",
        "invoice": "Invoice 샘플 다운로드", "packing": "Packing List 샘플 다운로드",
        "review": "두 PDF의 바이어·품목·수량을 비교하세요. Invoice 금액과 박스 수·중량이 입력한 값과 맞는지도 확인하세요.",
        "privacy": "가상 PDF 생성에 필요한 숫자만 전송합니다. 문서로 저장하지 않고 계정 한도도 사용하지 않습니다. 문서 내용 없이 익명 이용 횟수만 기록합니다.",
        "limits": "실제 출하에 사용할 수 없는 연습 문서입니다. 통관 신고나 규정 준수 인증을 제공하지 않습니다. 가입 후 작업공간에서도 박스·중량은 직접 입력하며 Invoice를 수정해도 기존 Packing List는 자동 갱신되지 않습니다.",
        "next": "실제로 작성하시는 서류에 맞나요?", "feedback": "빠진 항목이나 불편한 점 알려주기", "signup": "무료 작업공간 만들기",
        "signup_hint": "작업공간은 월 5문서 무료이며 Invoice와 Packing List를 각각 계산합니다. 유료 결제는 꺼져 있습니다. 체험 값은 가입 후 자동 이전되지 않습니다.",
        "guide": "단계별 사용 안내", "js": "직접 체험하려면 JavaScript를 켜 주세요. 사용 안내의 고정 샘플 PDF는 바로 볼 수 있습니다.",
    },
}


@router.get("/try-sample", response_class=HTMLResponse)
def practice_page(lang: str = "en"):
    language = "ko" if lang == "ko" else "en"
    content = (Path(__file__).parent / "templates" / "guest_sample.html").read_text(encoding="utf-8")
    for key, value in {**COPY[language], "lang": language}.items():
        content = content.replace("__" + key + "__", value)
    return HTMLResponse(content, headers={"Cache-Control": "no-store"})
