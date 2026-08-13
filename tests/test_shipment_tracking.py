import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import shipment
from app.validation import DataValidationError


def _request(account_id):
    return Request({"type": "http", "method": "GET", "path": "/shipment/SHP-001",
                    "headers": [], "trade_paper_user": {"account_id": account_id}})


def test_tracking_status_fields_detail_edit_and_account_isolation(tmp_path, monkeypatch):
    shipment_file = tmp_path / "shipments.json"
    shipment_file.write_text(json.dumps([
        {"account_id": "A", "shipment_no": "SHP-001", "shipment_name": "Owned", "status": "Draft"},
        {"account_id": "B", "shipment_no": "SHP-002", "shipment_name": "Foreign", "status": "Booked"},
    ]))
    monkeypatch.setattr(shipment, "SHIPMENT_FILE", shipment_file)
    monkeypatch.setattr(shipment, "load_workflow_datasets", lambda account: {
        **{descriptor["file"].name: [] for descriptor in [*shipment.DOCUMENTS, *shipment.OPERATIONAL_RECORDS]},
    })

    response = shipment.update_shipment_tracking(
        "SHP-001", _request("A"), status="In Transit", container_no="CONT-001",
        seal_no="SEAL-001", container_type="40HC", etd="2026-08-20", eta="2026-09-02",
        actual_departure="2026-08-21", actual_arrival="", tracking_memo="Vessel departed.",
    )
    assert response.status_code == 303 and response.headers["location"] == "/shipment/SHP-001"
    stored = json.loads(shipment_file.read_text())[0]
    assert stored["status"] == "In Transit"
    assert stored["container_no"] == "CONT-001" and stored["seal_no"] == "SEAL-001"
    assert stored["etd"] == "2026-08-20" and stored["tracking_memo"] == "Vessel departed."

    form = shipment.edit_shipment_tracking("SHP-001", _request("A")).body.decode()
    assert '<option value="In Transit" selected>' in form
    assert 'name="container_no" value="CONT-001"' in form
    detail = shipment.shipment_detail("SHP-001", _request("A")).body.decode()
    assert all(value in detail for value in ("In Transit", "CONT-001", "SEAL-001", "40HC", "Vessel departed.", "Edit Tracking"))

    for action in (
        lambda: shipment.edit_shipment_tracking("SHP-001", _request("B")),
        lambda: shipment.update_shipment_tracking("SHP-001", _request("B"), status="Delivered"),
    ):
        with pytest.raises(HTTPException) as denied:
            action()
        assert denied.value.status_code == 404


def test_tracking_rejects_unknown_status_and_only_suggests_empty_values(monkeypatch):
    record = {"shipment_no": "SHP-001", "status": "Booked", "container_no": "USER-CONT", "etd": ""}
    monkeypatch.setattr(shipment, "find_shipment", lambda number, account: record if account == "A" else None)
    monkeypatch.setattr(shipment, "tracking_suggestions", lambda current, account: {"container_no": "SUGGESTED", "etd": "2026-08-20"})
    html = shipment.edit_shipment_tracking("SHP-001", _request("A")).body.decode()
    assert 'name="container_no" value="USER-CONT"' in html
    assert 'name="etd" value="2026-08-20"' in html
    with pytest.raises(DataValidationError):
        shipment.update_shipment_tracking("SHP-001", _request("A"), status="Auto Shipped")
