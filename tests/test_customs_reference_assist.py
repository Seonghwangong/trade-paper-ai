from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import customs_declaration as customs


def _request(account_id):
    return Request({
        "type": "http", "method": "GET", "path": "/customs-source/shipment/SHP-1",
        "headers": [], "trade_paper_user": {"account_id": account_id},
    })


def _sources(monkeypatch):
    shipments = {
        "A": [
            {"shipment_no": "SHP-1", "invoice_no": "INV-1", "packing_no": "PK-1", "bl_no": "BL-1"},
            {"shipment_no": "SHP-LEGACY", "bl_no": "BL-1"},
            {"shipment_no": "SHP-BAD", "invoice_no": "INV-1", "packing_no": "PK-BAD", "bl_no": "BL-1"},
        ],
        "B": [{"shipment_no": "SHP-B", "invoice_no": "INV-B"}],
    }
    invoices = {
        "A": [{"invoice_no": "INV-1"}, {"invoice_no": "INV-2"}],
        "B": [{"invoice_no": "INV-B"}],
    }
    packings = {
        "A": [
            {"packing_no": "PK-1", "invoice_no": "INV-1"},
            {"packing_no": "PK-BAD", "invoice_no": "INV-2"},
        ],
        "B": [],
    }
    bills = {
        "A": [{"bl_no": "BL-1", "packing_no": "PK-1", "invoice_no": "INV-1"}],
        "B": [],
    }
    bookings = {"A": [{"booking_record_no": "BK-1", "shipment_no": "SHP-1"}], "B": []}
    containers = {"A": [{"container_record_no": "CON-1", "shipment_no": "SHP-1"}], "B": []}
    monkeypatch.setattr(customs, "load_shipments", lambda account: shipments.get(account, []))
    monkeypatch.setattr(customs, "load_invoices", lambda account: invoices.get(account, []))
    monkeypatch.setattr(customs, "load_packing_lists", lambda account: packings.get(account, []))
    monkeypatch.setattr(customs, "load_bills_of_lading", lambda account: bills.get(account, []))
    monkeypatch.setattr(customs, "load_bookings", lambda account: bookings.get(account, []))
    monkeypatch.setattr(customs, "load_containers", lambda account: containers.get(account, []))
    return bookings, containers


def test_shipment_assist_auto_selects_safe_direct_and_single_reverse_references(monkeypatch):
    _sources(monkeypatch)
    result = customs.customs_source_shipment("SHP-1", _request("A"))
    assert result == {
        "shipment_no": "SHP-1",
        "auto_select": {
            "invoice_no": "INV-1", "packing_no": "PK-1", "bl_no": "BL-1",
            "booking_record_no": "BK-1", "container_record_no": "CON-1",
        },
        "suggestions": {},
    }
    assert "account_id" not in str(result)


def test_shipment_assist_proposes_multiple_reverse_candidates_without_selecting(monkeypatch):
    bookings, containers = _sources(monkeypatch)
    bookings["A"].append({"booking_record_no": "BK-2", "shipment_no": "SHP-1"})
    containers["A"].append({"container_record_no": "CON-2", "shipment_no": "SHP-1"})
    result = customs.shipment_reference_assist("SHP-1", "A")
    assert "booking_record_no" not in result["auto_select"]
    assert "container_record_no" not in result["auto_select"]
    assert {item["identifier"] for item in result["suggestions"]["booking_record_no"]} == {"BK-1", "BK-2"}
    assert {item["identifier"] for item in result["suggestions"]["container_record_no"]} == {"CON-1", "CON-2"}
    assert all(item["relationship"] == "Related" for items in result["suggestions"].values() for item in items)


def test_shipment_assist_keeps_legacy_inference_as_suggestions_and_rejects_bad_chain(monkeypatch):
    _sources(monkeypatch)
    legacy = customs.shipment_reference_assist("SHP-LEGACY", "A")
    assert legacy["auto_select"] == {"bl_no": "BL-1"}
    assert legacy["suggestions"]["packing_no"] == [{
        "identifier": "PK-1", "source": "Bill of Lading", "relationship": "Related",
    }]
    assert legacy["suggestions"]["invoice_no"] == [{
        "identifier": "INV-1", "source": "Packing List", "relationship": "Related",
    }]

    invalid = customs.shipment_reference_assist("SHP-BAD", "A")
    assert not any(field in invalid["auto_select"] for field in ("invoice_no", "packing_no", "bl_no"))
    assert invalid["suggestions"]["packing_no"][0]["relationship"] == "Review relationship"


def test_shipment_assist_is_account_isolated_and_form_preserves_existing_values(monkeypatch):
    _sources(monkeypatch)
    with pytest.raises(HTTPException) as denied:
        customs.customs_source_shipment("SHP-1", _request("B"))
    assert denied.value.status_code == 404

    monkeypatch.setattr(customs, "load_products", lambda account: [])
    html = customs.render_form({
        "shipment_no": "SHP-1", "invoice_no": "INV-2", "packing_no": "PK-BAD",
        "booking_record_no": "BK-1", "container_record_no": "CON-1", "bl_no": "BL-1",
        "items": [],
    }, "/update-customs/CD-1", "Edit Customs Declaration", "Update Customs Declaration", account_id="A").body.decode()
    for name, value in (
        ("invoice_no", "INV-2"), ("packing_no", "PK-BAD"),
        ("booking_record_no", "BK-1"), ("container_record_no", "CON-1"), ("bl_no", "BL-1"),
    ):
        assert f'<option value="{value}" selected>' in html
    assert "if(!select || select.value) return false;" in html
    assert "loadShipmentReferenceAssist(event.target.value)" in html
    assert "loadShipmentReferenceAssist(document" not in html
    assert 'id="reference_assist"' in html
