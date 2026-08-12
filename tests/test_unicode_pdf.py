from io import BytesIO
import inspect

from fastapi import Request
from pypdf import PdfReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from app import (
    bill_of_lading,
    booking_confirmation,
    certificate_of_origin,
    container_management,
    customs_declaration,
    invoice,
    inspection_certificate,
    insurance_certificate,
    packing,
    proforma,
    quotation,
    shipment,
    shipping_instruction,
    weight_certificate,
)
from app.pdf_fonts import (
    TP_UNICODE,
    TP_UNICODE_BOLD,
    ensure_pdf_fonts,
    fit_pdf_text,
)


KOREAN = "대한무역 주식회사"
JAPANESE = "日本貿易株式会社"
SIMPLIFIED_CHINESE = "中国贸易公司"
TRADITIONAL_CHINESE = "臺灣貿易公司"
MIXED = "한글 日本 中文 English"


def _request(path="/bl/pdf"):
    return Request({
        "type": "http", "method": "POST", "path": path, "headers": [],
        "trade_paper_user": {
            "account_id": "account-a", "company": KOREAN, "email": "a@example.com",
        },
    })


def _text(response):
    return "\n".join(
        page.extract_text() or ""
        for page in PdfReader(BytesIO(response.body)).pages
    )


def _buffer_text(buffer):
    return "\n".join(
        page.extract_text() or ""
        for page in PdfReader(BytesIO(buffer.getvalue())).pages
    )


def _embedded_unicode_fonts(pdf_bytes):
    names = []
    for page in PdfReader(BytesIO(pdf_bytes)).pages:
        for reference in page["/Resources"].get("/Font", {}).values():
            font = reference.get_object()
            descriptor = font.get("/FontDescriptor")
            if descriptor and "/FontFile2" in descriptor.get_object():
                names.append(str(font.get("/BaseFont", "")))
    return names


def _payload(identifier_field, identifier):
    return {
        identifier_field: identifier,
        "invoice_no": "INV-UNICODE",
        "packing_no": "PK-UNICODE",
        "bl_no": "BL-UNICODE",
        "seller": KOREAN,
        "seller_address": "부산광역시 해운대구",
        "seller_email": "seller@example.com",
        "seller_phone": "+82-51-000-0000",
        "buyer": JAPANESE,
        "buyer_address": "東京都千代田区",
        "buyer_email": "buyer@example.jp",
        "shipper": KOREAN,
        "shipper_address": "부산광역시 해운대구",
        "shipper_email": "seller@example.com",
        "shipper_phone": "+82-51-000-0000",
        "consignee": SIMPLIFIED_CHINESE,
        "consignee_address": "北京市朝阳区",
        "consignee_email": "buyer@example.cn",
        "notify_party": TRADITIONAL_CHINESE,
        "vessel": MIXED,
        "items": [{
            "name": "노트북 컴퓨터 日本 中文",
            "hs_code": "847130",
            "quantity": "2",
            "unit_price": "100",
            "carton": "1",
            "net_weight": "10",
            "gross_weight": "12",
        }],
    }


def test_regular_and_bold_fonts_preserve_mixed_unicode_and_fit_width():
    ensure_pdf_fonts()
    output = BytesIO()
    pdf = canvas.Canvas(output)
    pdf.setFont(TP_UNICODE, 12)
    pdf.drawString(30, 800, f"{KOREAN} {JAPANESE} {SIMPLIFIED_CHINESE} {TRADITIONAL_CHINESE}")
    pdf.setFont(TP_UNICODE_BOLD, 12)
    pdf.drawString(30, 780, f"제목 ラベル 标签 標籤 {MIXED}")
    long_text = (KOREAN + JAPANESE + SIMPLIFIED_CHINESE + TRADITIONAL_CHINESE) * 8
    fitted = fit_pdf_text(pdf, long_text, 180, TP_UNICODE, 9)
    assert pdfmetrics.stringWidth(fitted, TP_UNICODE, 9) <= 180
    assert fitted.endswith("...")
    pdf.save()
    reader = PdfReader(BytesIO(output.getvalue()))
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    embedded_fonts = []
    for reference in reader.pages[0]["/Resources"]["/Font"].values():
        font = reference.get_object()
        descriptor = font.get("/FontDescriptor")
        if descriptor and "/FontFile2" in descriptor.get_object():
            embedded_fonts.append(str(font.get("/BaseFont", "")))
    assert any("TradePaperUnicode-Regular" in name for name in embedded_fonts)
    assert any("TradePaperUnicode-Bold" in name for name in embedded_fonts)
    for value in (KOREAN, JAPANESE, SIMPLIFIED_CHINESE, TRADITIONAL_CHINESE, "제목", "ラベル", "标签", "標籤", MIXED):
        assert value in extracted


def test_invoice_packing_and_bl_pdf_extract_unicode(monkeypatch):
    invoice_pdf = invoice.create_invoice_pdf(_payload("invoice_no", "INV-UNICODE"))
    packing_pdf = packing.create_packing_list_pdf(_payload("packing_no", "PK-UNICODE"))

    monkeypatch.setattr(bill_of_lading, "validate_bl_links", lambda *args, **kwargs: None)
    monkeypatch.setattr(bill_of_lading, "resolve_party_snapshot", lambda payload, account_id: payload)
    bl_pdf = bill_of_lading.create_bl_pdf(_request(), _payload("bl_no", "BL-UNICODE"))

    invoice_text = _text(invoice_pdf)
    packing_text = _text(packing_pdf)
    bl_text = _text(bl_pdf)
    for text in (invoice_text, packing_text):
        assert KOREAN in text and JAPANESE in text and "노트북 컴퓨터" in text
    for value in (KOREAN, SIMPLIFIED_CHINESE, TRADITIONAL_CHINESE, "노트북 컴퓨터", MIXED):
        assert value in bl_text
    for response in (invoice_pdf, packing_pdf, bl_pdf):
        assert response.body.startswith(b"%PDF")
        assert b"account_id" not in response.body


def test_first_phase_pdf_modules_do_not_use_direct_helvetica():
    for module in (invoice, packing, bill_of_lading):
        source = inspect.getsource(module)
        assert '"Helvetica"' not in source
        assert '"Helvetica-Bold"' not in source


def _workflow_payload():
    long_address = "부산광역시 해운대구 日本東京都 北京市朝阳区 臺北市信義區 " * 10
    long_item = "노트북 컴퓨터 日本語商品 简体中文商品 繁體中文商品 Trade Paper AI " * 6
    return {
        "shipment_no": "SHP-UNICODE", "shipment_name": MIXED,
        "si_no": "SI-UNICODE", "booking_record_no": "BKG-UNICODE",
        "booking_no": "BOOK-UNICODE", "container_record_no": "CTR-UNICODE",
        "container_no": "CONT-UNICODE", "packing_no": "PK-UNICODE",
        "invoice_no": "INV-UNICODE", "bl_no": "BL-UNICODE",
        "shipper": KOREAN, "shipper_address": long_address,
        "exporter": KOREAN, "exporter_name": KOREAN,
        "exporter_address": long_address, "exporter_email": "seller@example.com",
        "exporter_phone": "+82-51-000-0000",
        "consignee": JAPANESE, "consignee_name": JAPANESE,
        "consignee_address": "東京都千代田区 中国北京市 臺灣臺北市",
        "consignee_email": "buyer@example.jp", "notify_party": SIMPLIFIED_CHINESE,
        "customer": TRADITIONAL_CHINESE, "buyer": SIMPLIFIED_CHINESE,
        "carrier": MIXED, "vessel": "한글船舶 日本号 中国轮船 English",
        "remarks": MIXED, "shipping_marks": TRADITIONAL_CHINESE,
        "freight_terms": JAPANESE, "special_instructions": SIMPLIFIED_CHINESE,
        "items": [{
            "name": long_item, "hs_code": "847130", "quantity": "2",
            "carton": "1", "net_weight": "10", "gross_weight": "12",
        }],
        "total_carton": "1", "total_net_weight": "10", "total_gross_weight": "12",
    }


def test_workflow_pdf_buffers_extract_unicode_and_embed_fonts():
    payload = _workflow_payload()
    buffers = (
        shipping_instruction.create_shipping_instruction_pdf(payload),
        booking_confirmation.create_booking_pdf_buffer(payload),
        container_management.create_container_pdf_buffer(payload),
    )
    for buffer in buffers:
        raw = buffer.getvalue()
        text = _buffer_text(buffer)
        assert raw.startswith(b"%PDF")
        assert KOREAN in text and JAPANESE in text
        assert "노트북 컴퓨터" in text and "..." in text
        fonts = _embedded_unicode_fonts(raw)
        assert any("TradePaperUnicode-Regular" in name for name in fonts)
        assert any("TradePaperUnicode-Bold" in name for name in fonts)
        assert b"account_id" not in raw


def test_shipment_pdf_extracts_unicode_and_preserves_public_projection(monkeypatch):
    payload = _workflow_payload()
    stored = {**payload, "account_id": "account-a", "status": "booked"}
    monkeypatch.setattr(shipment, "find_shipment", lambda shipment_no, account_id: stored)
    monkeypatch.setattr(shipment, "resolve_shipment_snapshot", lambda record, account_id: record)
    monkeypatch.setattr(shipment, "load_workflow_datasets", lambda account_id: {})
    monkeypatch.setattr(shipment, "resolve_direct_documents", lambda record, datasets: [])
    monkeypatch.setattr(shipment, "resolve_operational_records", lambda shipment_no, datasets: [])
    monkeypatch.setattr(shipment, "set_pdf_export_record", lambda request, record: None)
    response = shipment.shipment_pdf("SHP-UNICODE", _request("/shipment-pdf/SHP-UNICODE"))
    text = _text(response)
    assert KOREAN in text and JAPANESE in text and SIMPLIFIED_CHINESE in text
    assert "노트북 컴퓨터" in text and "..." in text
    fonts = _embedded_unicode_fonts(response.body)
    assert any("TradePaperUnicode-Regular" in name for name in fonts)
    assert any("TradePaperUnicode-Bold" in name for name in fonts)
    assert b"account_id" not in response.body


def test_second_phase_pdf_modules_do_not_use_direct_helvetica():
    for module in (shipment, shipping_instruction, booking_confirmation, container_management):
        source = inspect.getsource(module)
        assert '"Helvetica"' not in source
        assert '"Helvetica-Bold"' not in source


def _certificate_payload():
    payload = _workflow_payload()
    long_number = "CERT-한글-日本-简体-繁體-" * 20
    long_identifier = "CERT-" + "1234567890" * 30
    payload.update({
        "customs_record_no": long_identifier,
        "declaration_no": long_number,
        "co_no": long_identifier,
        "inspection_no": long_identifier,
        "insurance_no": long_identifier,
        "weight_no": long_identifier,
        "exporter": KOREAN,
        "consignee": JAPANESE,
        "country_of_origin": SIMPLIFIED_CHINESE,
        "destination_country": TRADITIONAL_CHINESE,
        "transport_details": MIXED * 12,
        "port_of_loading": "부산港 日本港 中国港",
        "port_of_discharge": "臺灣港 English Port",
        "inspection_company": SIMPLIFIED_CHINESE,
        "inspection_location": MIXED * 10,
        "inspection_result": "합격 合格 合格 PASS",
        "insurance_company": TRADITIONAL_CHINESE,
        "policy_no": long_number,
        "coverage_type": MIXED * 8,
        "insured_amount": "100000",
        "currency": "USD",
        "total_quantity": "2",
        "total_amount": "200",
    })
    return payload


def test_customs_and_certificate_pdf_extract_unicode_and_embed_fonts(monkeypatch):
    payload = _certificate_payload()
    monkeypatch.setattr(certificate_of_origin, "validate_co_links", lambda *args, **kwargs: None)
    monkeypatch.setattr(certificate_of_origin, "resolve_co_snapshot", lambda record, account_id: record)
    monkeypatch.setattr(inspection_certificate, "validate_inspection_links", lambda *args, **kwargs: None)
    monkeypatch.setattr(inspection_certificate, "resolve_inspection_snapshot", lambda record, account_id: record)
    monkeypatch.setattr(insurance_certificate, "validate_insurance_links", lambda *args, **kwargs: None)
    monkeypatch.setattr(insurance_certificate, "resolve_insurance_snapshot", lambda record, account_id: record)

    buffers = (
        customs_declaration.create_customs_pdf_buffer(payload),
        weight_certificate.create_weight_certificate_pdf(payload),
    )
    responses = (
        certificate_of_origin.create_co_pdf(_request("/co/pdf"), payload),
        inspection_certificate.create_inspection_pdf(_request("/inspection/pdf"), payload),
        insurance_certificate.create_inspection_pdf(_request("/insurance/pdf"), payload),
    )
    documents = [(buffer.getvalue(), _buffer_text(buffer)) for buffer in buffers]
    documents += [(response.body, _text(response)) for response in responses]
    for raw, text in documents:
        assert raw.startswith(b"%PDF")
        assert KOREAN in text and JAPANESE in text
        assert "노트북 컴퓨터" in text and "..." in text
        fonts = _embedded_unicode_fonts(raw)
        assert any("TradePaperUnicode-Regular" in name for name in fonts)
        assert any("TradePaperUnicode-Bold" in name for name in fonts)
        assert b"account_id" not in raw


def test_third_phase_pdf_modules_do_not_use_direct_helvetica():
    modules = (
        customs_declaration,
        certificate_of_origin,
        inspection_certificate,
        insurance_certificate,
        weight_certificate,
    )
    for module in modules:
        source = inspect.getsource(module)
        assert '"Helvetica"' not in source
        assert '"Helvetica-Bold"' not in source


def _sales_offer_payload(identifier_field, identifier):
    long_address = "부산광역시 해운대구 日本東京都 北京市朝阳区 臺北市信義區 " * 10
    long_item = "노트북 컴퓨터 日本語商品 简体中文商品 繁體中文商品 Trade Paper AI " * 6
    return {
        identifier_field: identifier,
        "seller": KOREAN,
        "seller_address": long_address,
        "seller_email": "seller@example.com",
        "buyer": JAPANESE,
        "buyer_name": JAPANESE,
        "buyer_address": "東京都千代田区 中国北京市 臺灣臺北市",
        "buyer_email": "buyer@example.jp",
        "valid_until": "2027-12-31",
        "pi_date": "2026-08-10",
        "currency": "원円人民币USD",
        "total_amount": "246.90",
        "items": [{
            "name": long_item,
            "hs_code": "847130",
            "qty": "2",
            "unit_price": "123.45",
            "amount": "246.90",
        }],
    }


def test_quotation_and_proforma_pdf_extract_unicode_and_embed_fonts(monkeypatch):
    monkeypatch.setattr(quotation, "load_company", lambda account_id: {})
    monkeypatch.setattr(proforma, "load_company", lambda account_id: {})
    documents = (
        quotation.create_quotation_pdf(
            _request("/quotation/pdf"),
            _sales_offer_payload("quotation_no", "QT-UNICODE"),
            validate_sources=False,
        ),
        proforma.create_proforma_pdf(
            _request("/proforma/pdf"),
            _sales_offer_payload("pi_no", "PI-UNICODE"),
            validate_sources=False,
        ),
    )
    for response in documents:
        text = _text(response)
        assert response.body.startswith(b"%PDF")
        assert KOREAN in text and JAPANESE in text
        assert "노트북 컴퓨터" in text and "..." in text
        assert "원円人民币USD" in text and "246.90" in text
        fonts = _embedded_unicode_fonts(response.body)
        assert any("TradePaperUnicode-Regular" in name for name in fonts)
        assert any("TradePaperUnicode-Bold" in name for name in fonts)
        assert b"account_id" not in response.body


def test_fourth_phase_pdf_modules_do_not_use_direct_helvetica():
    for module in (quotation, proforma):
        source = inspect.getsource(module)
        assert '"Helvetica"' not in source
        assert '"Helvetica-Bold"' not in source
