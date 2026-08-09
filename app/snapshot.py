"""Shared helpers for immutable document snapshot fallback."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence
from uuid import uuid4


@dataclass
class SnapshotContext:
    """Resolved standard trade-document sources for a single account."""

    resolved: Dict[str, Any]
    preserve_empty: bool
    shipment: Mapping[str, Any]
    bill: Mapping[str, Any]
    packing: Mapping[str, Any]
    invoice: Mapping[str, Any]
    company: Mapping[str, Any]
    buyer: Mapping[str, Any]
    shipment_no: str
    bl_no: str
    packing_no: str
    invoice_no: str


def find_by_identifier(
    records: Iterable[Mapping[str, Any]],
    field: str,
    value: Any,
    *,
    normalize: bool = False,
) -> Optional[Mapping[str, Any]]:
    """Find one record in an already account-scoped collection."""
    if not value:
        return None
    if normalize:
        target = str(value or "").strip()
        return next(
            (record for record in records
             if str(record.get(field, "") or "").strip() == target),
            None,
        )
    return next((record for record in records if record.get(field) == value), None)


def resolve_source_chain(
    record: Mapping[str, Any],
    account_id: str,
    *,
    document_id_field: str,
    load_shipments: Callable[[str], Iterable[Mapping[str, Any]]],
    load_bills: Callable[[str], Iterable[Mapping[str, Any]]],
    load_packings: Callable[[str], Iterable[Mapping[str, Any]]],
    load_invoices: Callable[[str], Iterable[Mapping[str, Any]]],
    load_company: Callable[[str], Mapping[str, Any]],
    load_buyers: Callable[[str], Iterable[Mapping[str, Any]]],
    shipment: Optional[Mapping[str, Any]] = None,
    bill: Optional[Mapping[str, Any]] = None,
    packing: Optional[Mapping[str, Any]] = None,
    invoice: Optional[Mapping[str, Any]] = None,
    load_shipment_without_reference: bool = False,
) -> SnapshotContext:
    """Resolve the shared Shipment → B/L → Packing → Invoice → Master chain."""

    resolved = deepcopy(record or {})
    preserve_empty = bool(resolved.get(document_id_field))
    shipment_no = str(resolved.get("shipment_no", "") or "").strip()
    if shipment is None and (shipment_no or load_shipment_without_reference):
        shipment = find_by_identifier(
            load_shipments(account_id), "shipment_no", shipment_no, normalize=True,
        )
    shipment = shipment or {}

    bl_no = str(snapshot_value(
        resolved, "bl_no", (shipment.get("bl_no"),), preserve_empty=preserve_empty,
    ) or "").strip()
    if bill is None:
        bill = find_by_identifier(load_bills(account_id), "bl_no", bl_no, normalize=True)
    bill = bill or {}

    packing_no = str(snapshot_value(
        resolved, "packing_no", (shipment.get("packing_no"), bill.get("packing_no")),
        preserve_empty=preserve_empty,
    ) or "").strip()
    if packing is None:
        packing = find_by_identifier(
            load_packings(account_id), "packing_no", packing_no, normalize=True,
        )
    packing = packing or {}

    invoice_no = str(snapshot_value(
        resolved, "invoice_no",
        (shipment.get("invoice_no"), bill.get("invoice_no"), packing.get("invoice_no")),
        preserve_empty=preserve_empty,
    ) or "").strip()
    if invoice is None:
        invoice = find_by_identifier(
            load_invoices(account_id), "invoice_no", invoice_no, normalize=True,
        )
    invoice = invoice or {}

    company = load_company(account_id) or {}
    consignee_name = (
        resolved.get("consignee_name") or resolved.get("consignee")
        or shipment.get("consignee") or bill.get("consignee")
        or packing.get("buyer") or invoice.get("buyer") or ""
    )
    buyer = next(
        (row for row in load_buyers(account_id)
         if str(row.get("name", "") or "").strip().casefold()
         == str(consignee_name).strip().casefold()),
        {},
    )
    return SnapshotContext(
        resolved=resolved, preserve_empty=preserve_empty,
        shipment=shipment, bill=bill, packing=packing, invoice=invoice,
        company=company, buyer=buyer, shipment_no=shipment_no, bl_no=bl_no,
        packing_no=packing_no, invoice_no=invoice_no,
    )


def snapshot_value(
    snapshot: Mapping[str, Any],
    field: str,
    candidates: Iterable[Any] = (),
    *,
    preserve_empty: bool = True,
) -> Any:
    """Prefer a stored key, including an explicit empty value, over legacy fallbacks."""
    if field in snapshot and (preserve_empty or snapshot[field]):
        return snapshot[field]
    return next((value for value in candidates if value), "")


def fill_missing_snapshot_fields(
    snapshot: MutableMapping[str, Any],
    fallbacks: Mapping[str, Iterable[Any]],
    *,
    preserve_empty: bool = True,
) -> None:
    """Fill absent legacy keys without replacing authoritative stored empty values."""
    for field, candidates in fallbacks.items():
        if field not in snapshot or (not preserve_empty and not snapshot[field]):
            snapshot[field] = next((value for value in candidates if value), "")


def set_submitted_snapshot_fields(snapshot: MutableMapping[str, Any], values: Mapping[str, Any]) -> None:
    """Store submitted values, including empty strings, while leaving omitted fields absent."""
    for field, value in values.items():
        if value is None:
            snapshot.pop(field, None)
        else:
            snapshot[field] = value


def new_item_id() -> str:
    """Return an opaque stable identifier for a newly-created cargo snapshot item."""
    return f"ITEM-{uuid4().hex}"


def assign_item_ids(
    items: Sequence[MutableMapping[str, Any]],
    submitted_ids: Optional[Sequence[str]] = None,
    current_items: Sequence[Mapping[str, Any]] = (),
) -> List[MutableMapping[str, Any]]:
    """Attach submitted IDs, with index fallback only for legacy/ID-less submissions."""
    if not isinstance(submitted_ids, (list, tuple)):
        submitted_ids = ()
    if not isinstance(current_items, (list, tuple)):
        current_items = ()
    seen: set[str] = set()
    for index, item in enumerate(items):
        submitted = str(submitted_ids[index] or "").strip() if index < len(submitted_ids) else ""
        prior = current_items[index] if index < len(current_items) else {}
        inherited = str(prior.get("item_id", "") or "").strip()
        item_id = submitted or inherited or new_item_id()
        if item_id in seen:
            item_id = new_item_id()
        item["item_id"] = item_id
        seen.add(item_id)
    return list(items)


def preserve_omitted_item_fields(
    items: Sequence[MutableMapping[str, Any]],
    current_items: Sequence[Mapping[str, Any]],
    omitted_fields: Iterable[str],
) -> None:
    """Preserve fields by stable ID, falling back to index for legacy items/submissions."""
    prior_by_id = {
        str(item.get("item_id", "") or "").strip(): item
        for item in current_items
        if isinstance(item, Mapping) and str(item.get("item_id", "") or "").strip()
    }
    for index, item in enumerate(items):
        item_id = str(item.get("item_id", "") or "").strip()
        prior = prior_by_id.get(item_id)
        if prior is None and not prior_by_id and index < len(current_items):
            candidate = current_items[index]
            if isinstance(candidate, Mapping):
                prior = candidate
        prior = prior or {}
        for field in omitted_fields:
            item[field] = prior.get(field, "")
