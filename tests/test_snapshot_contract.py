from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest


APP_DIR = Path(__file__).resolve().parents[1] / "app"


@dataclass(frozen=True)
class SnapshotContract:
    document: str
    module: str
    resolver: str
    edit: str
    api: str
    pdf: str
    public_view: str


CONTRACTS = (
    SnapshotContract("Invoice", "invoice", "public_invoice", "edit_invoice", "invoice_data", "invoice_pdf", "public_invoice"),
    SnapshotContract("Packing", "packing", "public_packing", "edit_packing", "packing_data", "packing_list_pdf", "public_packing"),
    SnapshotContract("Shipment", "shipment", "resolve_shipment_snapshot", "edit_shipment", "shipment_data", "shipment_pdf", "public_shipment"),
    SnapshotContract("Shipping Instruction", "shipping_instruction", "resolve_si_snapshot", "edit_si", "si_data", "si_pdf", "public_shipping_instruction"),
    SnapshotContract("Booking", "booking_confirmation", "resolve_booking_snapshot", "edit_booking", "booking_data", "booking_pdf", "public_booking"),
    SnapshotContract("Bill of Lading", "bill_of_lading", "resolve_party_snapshot", "edit_bl", "bl_data", "bl_pdf", "public_bill_of_lading"),
    SnapshotContract("Customs", "customs_declaration", "resolve_customs_snapshot", "edit_customs", "customs_data", "customs_pdf", "public_customs"),
    SnapshotContract("Certificate of Origin", "certificate_of_origin", "resolve_co_snapshot", "edit_co", "co_data", "co_pdf", "public_certificate_of_origin"),
    SnapshotContract("Inspection", "inspection_certificate", "resolve_inspection_snapshot", "edit_inspection", "inspection_data", "inspection_pdf", "public_inspection"),
    SnapshotContract("Insurance", "insurance_certificate", "resolve_insurance_snapshot", "edit_inspection", "inspection_data", "inspection_pdf", "public_insurance"),
    SnapshotContract("Weight", "weight_certificate", "resolve_weight_snapshot", "edit_weight", "weight_data", "weight_pdf", "public_weight"),
    SnapshotContract("Container", "container_management", "resolve_container_snapshot", "edit_container", "container_data", "container_pdf", "public_container"),
)


def _functions(module: str) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse((APP_DIR / f"{module}.py").read_text(encoding="utf-8"))
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _reachable(functions, start: str, target: str, seen=None) -> bool:
    if start == target:
        return True
    if start not in functions:
        return False
    seen = set() if seen is None else seen
    if start in seen:
        return False
    seen.add(start)
    calls = {
        node.func.id
        for node in ast.walk(functions[start])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    return target in calls or any(
        _reachable(functions, called, target, seen)
        for called in calls
        if called in functions
    )


@pytest.mark.parametrize("contract", CONTRACTS, ids=lambda item: item.document)
@pytest.mark.parametrize("channel", ("edit", "api", "pdf"))
def test_snapshot_channels_share_one_resolution_path(contract, channel):
    """The shared path makes current, legacy, and explicit-empty snapshots resolve identically."""
    if contract.document == "Packing" and channel == "api":
        pytest.xfail("Known contract gap: Packing has no same-record JSON API")
    functions = _functions(contract.module)
    endpoint = getattr(contract, channel)
    assert endpoint in functions, (
        f"{contract.document} {channel.upper()} contract mismatch: "
        f"endpoint function {endpoint} is missing"
    )
    assert _reachable(functions, endpoint, contract.resolver), (
        f"{contract.document} {channel.upper()} contract mismatch: "
        f"{endpoint} does not use {contract.resolver}"
    )


@pytest.mark.parametrize("contract", CONTRACTS, ids=lambda item: item.document)
def test_snapshot_api_uses_public_account_safe_projection(contract):
    if contract.document == "Packing":
        pytest.xfail("Known contract gap: Packing has no same-record JSON API")
    functions = _functions(contract.module)
    assert contract.api in functions, (
        f"{contract.document} API account-isolation contract mismatch: "
        f"endpoint function {contract.api} is missing"
    )
    assert _reachable(functions, contract.api, contract.public_view), (
        f"{contract.document} API account_id exposure risk: "
        f"{contract.api} does not use {contract.public_view}"
    )
