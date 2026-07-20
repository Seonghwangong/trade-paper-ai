import json

import app.shipment as shipment


def test_complete_workflow_fixture(workflow_fixture):
    direct = shipment.resolve_direct_documents(workflow_fixture)
    operations = shipment.resolve_operational_records(workflow_fixture["shipment_no"])
    progress = shipment.required_workflow_progress(workflow_fixture, direct, operations)
    next_step = shipment.next_step_for_shipment(workflow_fixture, direct, operations)
    assert progress == {"completed": 6, "total": 6, "percentage": 100}
    assert next_step == {
        "step_label": "Workflow Complete",
        "reason": "All required workflow records are linked.",
        "create_url": "",
        "is_complete": True,
    }
    workflow_fixture["co_no"] = ""
    direct = shipment.resolve_direct_documents(workflow_fixture)
    operations = shipment.resolve_operational_records(workflow_fixture["shipment_no"])
    assert shipment.required_workflow_progress(workflow_fixture, direct, operations)["completed"] == 6


def test_customs_is_required_and_certificate_is_optional(workflow_fixture):
    workflow_fixture["co_no"] = ""
    direct = shipment.resolve_direct_documents(workflow_fixture)
    operations = shipment.resolve_operational_records(workflow_fixture["shipment_no"])
    assert shipment.next_step_for_shipment(workflow_fixture, direct, operations)["is_complete"] is True

    customs = operations[-1]["matches"]
    operations[-1]["matches"] = []
    try:
        assert shipment.next_step_for_shipment(workflow_fixture, direct, operations)["step_label"] == "Customs Declaration"
        assert shipment.required_workflow_progress(workflow_fixture, direct, operations)["completed"] == 5
    finally:
        operations[-1]["matches"] = customs


def test_first_missing_step_url(workflow_fixture):
    workflow_fixture["invoice_no"] = ""
    direct = shipment.resolve_direct_documents(workflow_fixture)
    operations = shipment.resolve_operational_records(workflow_fixture["shipment_no"])
    assert shipment.next_step_for_shipment(workflow_fixture, direct, operations)["create_url"] == "/invoice?pi_no=PI-001&shipment_no=SHP-001"


def test_required_workflow_zero_through_six(workflow_fixture):
    booking_file = next(item["file"] for item in shipment.OPERATIONAL_RECORDS if item["key"] == "booking_record_no")
    customs_file = next(item["file"] for item in shipment.OPERATIONAL_RECORDS if item["key"] == "customs_record_no")
    percentages = [0, 17, 33, 50, 67, 83, 100]
    labels = [
        "Commercial Invoice", "Packing List", "Shipping Instruction",
        "Booking Confirmation", "Bill of Lading", "Customs Declaration",
        "Workflow Complete",
    ]
    for completed in range(7):
        record = dict(workflow_fixture)
        record["invoice_no"] = "INV-001" if completed >= 1 else ""
        record["packing_no"] = "PK-001" if completed >= 2 else ""
        record["si_no"] = "SI-001" if completed >= 3 else ""
        record["bl_no"] = "BL-001" if completed >= 5 else ""
        booking_file.write_text(json.dumps([{"booking_record_no": "BK-001", "shipment_no": "SHP-001"}] if completed >= 4 else []))
        customs_file.write_text(json.dumps([{"customs_record_no": "CD-001", "shipment_no": "SHP-001"}] if completed >= 6 else []))
        direct = shipment.resolve_direct_documents(record)
        operations = shipment.resolve_operational_records(record["shipment_no"])
        assert shipment.required_workflow_progress(record, direct, operations) == {
            "completed": completed, "total": 6, "percentage": percentages[completed],
        }
        assert shipment.next_step_for_shipment(record, direct, operations)["step_label"] == labels[completed]
