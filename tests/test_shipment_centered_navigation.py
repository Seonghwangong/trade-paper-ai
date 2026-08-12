from __future__ import annotations

import inspect

from app import bill_of_lading, booking_confirmation, certificate_of_origin
from app import container_management, customs_declaration, inspection_certificate
from app import insurance_certificate, main, packing, shipment, shipping_instruction
from app import weight_certificate


def test_shipment_navigation_helpers_are_owned_and_unambiguous(monkeypatch):
    records = {
        "A": [
            {"account_id": "A", "shipment_no": "SHP-1", "packing_no": "PK-1", "bl_no": "BL-1"},
            {"account_id": "A", "shipment_no": "SHP-2", "packing_no": "PK-SHARED"},
            {"account_id": "A", "shipment_no": "SHP-3", "packing_no": "PK-SHARED"},
        ],
        "B": [{"account_id": "B", "shipment_no": "SHP-B", "packing_no": "PK-B"}],
    }
    monkeypatch.setattr(shipment, "owned_shipment_records", lambda account: records.get(account, []))

    assert shipment.shipment_detail_redirect_url("SHP-1", "A", "/fallback") == "/shipment/SHP-1"
    assert shipment.shipment_detail_redirect_url("SHP-B", "A", "/fallback") == "/fallback"
    assert shipment.shipment_detail_redirect_url("", "A", "/fallback") == "/fallback"
    assert shipment.direct_document_shipment_no("packing_no", "PK-1", "A") == "SHP-1"
    assert shipment.direct_document_shipment_no("bl_no", "BL-1", "A") == "SHP-1"
    assert shipment.direct_document_shipment_no("packing_no", "PK-SHARED", "A") == ""
    assert shipment.direct_document_shipment_no("packing_no", "PK-B", "A") == ""


def test_all_scoped_document_create_and_edit_paths_use_shipment_navigation():
    create_with_direct_link = (
        bill_of_lading.save_bl, certificate_of_origin.save_co,
        inspection_certificate.save_inspection, insurance_certificate.save_inspection,
        weight_certificate.save_weight,
    )
    create_with_owned_shipment = (
        booking_confirmation.save_booking, customs_declaration.save_customs_record,
        container_management.save_container,
    )
    updates_with_owned_shipment = (
        shipping_instruction.update_si, booking_confirmation.update_booking,
        customs_declaration.update_customs, container_management.update_container,
        certificate_of_origin.update_co, inspection_certificate.update_inspection,
        insurance_certificate.update_inspection, weight_certificate.update_weight,
    )
    for handler in create_with_direct_link:
        assert "shipment_context_redirect_url" in inspect.getsource(handler)
    for handler in (*create_with_owned_shipment, *updates_with_owned_shipment):
        assert "shipment_detail_redirect_url" in inspect.getsource(handler)
    assert "shipment_context_redirect_url" in inspect.getsource(shipping_instruction.save_si)
    assert "direct_document_shipment_no" in inspect.getsource(packing.update_packing)
    assert "direct_document_shipment_no" in inspect.getsource(bill_of_lading.update_bl)


def test_packing_browser_save_preserves_shipment_return_and_offers_contextless_next_step():
    source = inspect.getsource(main._workflow_browser_enhancement)
    assert 'tpSavedThenRedirect("/shipment/"+encodeURIComponent(shipmentNo))' in source
    assert "showPackingNextActions(result.packing_no)" in source
    assert '"/si-form?packing_no="+encodeURIComponent(packingNo)' in source
