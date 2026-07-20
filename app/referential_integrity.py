from __future__ import annotations

import html
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from app.storage import data_path, load_json_strict, locked_json_mutation
from app.documents import get_document_definition


class _ProtectedDelete(Exception):
    def __init__(self, dependencies):
        self.dependencies = dependencies


class _IndexMismatch(Exception):
    pass


_SOURCE_KEYS = {
    "Quotation": "quotation", "Proforma Invoice": "proforma",
    "Commercial Invoice": "invoice", "Packing List": "packing",
    "Shipping Instruction": "shipping_instruction", "Shipment": "shipment",
    "Booking Confirmation": "booking", "Container Management": "container",
    "Bill of Lading": "bill_of_lading", "Certificate of Origin": "certificate_of_origin",
    "Inspection Certificate": "inspection", "Insurance Certificate": "insurance",
    "Weight Certificate": "weight", "Customs Declaration": "customs",
}
_DEPENDENCY_TITLE_OVERRIDES = {"Insurance Certificate": "policy_no"}


def _dependency_source_meta(label):
    definition = get_document_definition(_SOURCE_KEYS[label])
    return (
        definition.storage_filename,
        definition.identifier_field,
        _DEPENDENCY_TITLE_OVERRIDES.get(label, definition.title_field),
        definition.detail_route.replace("{value}", "{id}"),
        definition.edit_route.replace("{value}", "{id}"),
    )


SOURCE_META = {label: _dependency_source_meta(label) for label in _SOURCE_KEYS}


DEPENDENCY_REGISTRY = {
    "Quotation": [("Shipment", "quotation_no")],
    "Proforma Invoice": [("Commercial Invoice", "pi_no"), ("Shipment", "pi_no")],
    "Commercial Invoice": [
        ("Packing List", "invoice_no"), ("Shipping Instruction", "invoice_no"),
        ("Shipment", "invoice_no"), ("Booking Confirmation", "invoice_no"),
        ("Container Management", "invoice_no"), ("Bill of Lading", "invoice_no"),
        ("Certificate of Origin", "invoice_no"), ("Inspection Certificate", "invoice_no"),
        ("Insurance Certificate", "invoice_no"), ("Weight Certificate", "invoice_no"),
        ("Customs Declaration", "invoice_no"),
    ],
    "Packing List": [
        ("Shipping Instruction", "packing_no"), ("Shipment", "packing_no"),
        ("Booking Confirmation", "packing_no"), ("Container Management", "packing_no"),
        ("Bill of Lading", "packing_no"), ("Certificate of Origin", "packing_no"),
        ("Inspection Certificate", "packing_no"), ("Insurance Certificate", "packing_no"),
        ("Weight Certificate", "packing_no"), ("Customs Declaration", "packing_no"),
    ],
    "Shipping Instruction": [("Shipment", "si_no"), ("Booking Confirmation", "si_no")],
    "Shipment": [
        ("Booking Confirmation", "shipment_no"),
        ("Container Management", "shipment_no"),
        ("Customs Declaration", "shipment_no"),
    ],
    "Booking Confirmation": [("Customs Declaration", "booking_record_no")],
    "Container Management": [("Customs Declaration", "container_record_no")],
    "Bill of Lading": [
        ("Shipment", "bl_no"), ("Booking Confirmation", "bl_no"),
        ("Container Management", "bl_no"), ("Certificate of Origin", "bl_no"),
        ("Inspection Certificate", "bl_no"), ("Insurance Certificate", "bl_no"),
        ("Weight Certificate", "bl_no"), ("Customs Declaration", "bl_no"),
    ],
    "Certificate of Origin": [("Shipment", "co_no")],
    "Inspection Certificate": [("Shipment", "inspection_no")],
    "Insurance Certificate": [("Shipment", "insurance_no")],
    "Weight Certificate": [("Shipment", "weight_no")],
    "Customs Declaration": [],
}


SOFT_SNAPSHOT_FIELDS = {
    "Company": [
        ("Quotation", "seller"), ("Proforma Invoice", "seller"),
        ("Commercial Invoice", "seller"), ("Packing List", "seller"),
        ("Shipping Instruction", "shipper"), ("Bill of Lading", "shipper"),
        ("Certificate of Origin", "exporter"), ("Inspection Certificate", "exporter"),
        ("Insurance Certificate", "exporter"), ("Weight Certificate", "exporter"),
        ("Customs Declaration", "exporter"),
    ],
    "Customer": [("Shipment", "customer")],
    "Buyer": [
        ("Quotation", "buyer_name"), ("Proforma Invoice", "buyer"),
        ("Commercial Invoice", "buyer"), ("Packing List", "buyer"),
        ("Shipment", "buyer"), ("Shipping Instruction", "consignee"),
        ("Bill of Lading", "consignee"), ("Certificate of Origin", "consignee"),
        ("Inspection Certificate", "consignee"), ("Insurance Certificate", "consignee"),
        ("Weight Certificate", "consignee"), ("Customs Declaration", "consignee"),
    ],
}


def _normalized(value: Any) -> str:
    return str(value or "").strip()


def _result(module: str, record: dict[str, Any]) -> dict[str, str] | None:
    filename, id_field, title_field, view_pattern, edit_pattern = SOURCE_META[module]
    identifier = _normalized(record.get(id_field))
    if not identifier:
        return None
    encoded = quote(identifier, safe="")
    return {
        "module": module,
        "identifier": identifier,
        "title": _normalized(record.get(title_field)) or identifier,
        "view_url": view_pattern.format(id=encoded) if view_pattern else "",
        "edit_url": edit_pattern.format(id=encoded) if edit_pattern else "",
    }


def find_dependencies(module: str, identifier: Any) -> list[dict[str, str]]:
    target = _normalized(identifier)
    if not target:
        return []
    dependencies = []
    for dependent_module, reference_field in DEPENDENCY_REGISTRY.get(module, []):
        filename = SOURCE_META[dependent_module][0]
        for record in load_json_strict(data_path(filename), [], list):
            if not isinstance(record, dict):
                continue
            if _normalized(record.get(reference_field)) != target:
                continue
            normalized = _result(dependent_module, record)
            if normalized:
                dependencies.append(normalized)
    return sorted(
        dependencies,
        key=lambda item: (item["module"].casefold(), item["identifier"].casefold(), item["title"].casefold()),
    )


def find_soft_warnings(module: str, name: Any) -> list[dict[str, str]]:
    target = _normalized(name).casefold()
    if not target:
        return []
    warnings = []
    if module == "Product":
        fields = [(source, "items") for source in SOURCE_META]
    else:
        fields = SOFT_SNAPSHOT_FIELDS.get(module, [])
    for source_module, field in fields:
        filename = SOURCE_META[source_module][0]
        for record in load_json_strict(data_path(filename), [], list):
            if not isinstance(record, dict):
                continue
            matched = False
            if module == "Product":
                items = record.get(field, [])
                if isinstance(items, list):
                    matched = any(
                        isinstance(item, dict) and _normalized(item.get("name")).casefold() == target
                        for item in items
                    )
            else:
                matched = _normalized(record.get(field)).casefold() == target
            if matched:
                normalized = _result(source_module, record)
                if normalized:
                    warnings.append(normalized)
    unique = {(item["module"], item["identifier"]): item for item in warnings}
    return sorted(unique.values(), key=lambda item: (item["module"].casefold(), item["identifier"].casefold()))


def _record_rows(records: list[dict[str, str]]) -> str:
    rows = []
    for record in records:
        links = []
        if record["view_url"]:
            links.append(f'<a href="{html.escape(record["view_url"], quote=True)}">View</a>')
        if record["edit_url"]:
            links.append(f'<a href="{html.escape(record["edit_url"], quote=True)}">Edit</a>')
        rows.append(
            "<li><div><b>{}</b> <span>{}</span><small>{}</small></div><div class=\"links\">{}</div></li>".format(
                html.escape(record["module"]), html.escape(record["identifier"]),
                html.escape(record["title"]), "".join(links),
            )
        )
    return "".join(rows)


def render_delete_page(
    document: str,
    identifier: str,
    action_url: str,
    cancel_url: str,
    dependencies: list[dict[str, str]] | None = None,
    warnings: list[dict[str, str]] | None = None,
    *,
    status_code: int = 200,
    expected_name: str = "",
    blocked_message: str = "",
) -> HTMLResponse:
    dependencies = dependencies or []
    warnings = warnings or []
    protected = bool(dependencies) or bool(blocked_message)
    status_class = "blocked" if protected else ("advisory" if warnings else "safe")
    heading = blocked_message or ("This record is currently referenced." if protected else "Confirm deletion")
    explanation = (
        "Deletion is blocked to protect linked records."
        if protected else "This operation permanently removes one record and does not cascade."
    )
    dependent_section = ""
    if dependencies:
        modules = ", ".join(sorted({item["module"] for item in dependencies}))
        dependent_section = f'<section><h2>Dependent records</h2><p><b>Affected modules:</b> {html.escape(modules)}</p><ul>{_record_rows(dependencies)}</ul></section>'
    warning_section = ""
    if warnings:
        warning_section = f'<section class="warning"><h2>Advisory warning</h2><p>Historical documents contain copied values from this record. Those snapshots will remain unchanged.</p><ul>{_record_rows(warnings)}</ul></section>'
    delete_form = ""
    if not protected:
        hidden = f'<input type="hidden" name="expected_name" value="{html.escape(expected_name, quote=True)}">' if expected_name else ""
        delete_form = f'<form action="{html.escape(action_url, quote=True)}" method="post">{hidden}<button class="delete" type="submit">Delete</button></form>'
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Delete {html.escape(document)}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#F3F4F6;color:#111827;font-family:Arial,sans-serif}}main{{width:min(760px,calc(100% - 32px));margin:56px auto}}.card{{background:#fff;border:1px solid #E5E7EB;border-radius:18px;padding:32px;box-shadow:0 14px 35px rgba(15,23,42,.08)}}.eyebrow{{font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.08em;color:#64748B}}h1{{margin:10px 0 8px;font-size:28px}}.identity{{background:#F8FAFC;border-radius:12px;padding:14px 16px;margin:22px 0}}.identity b,.identity span{{display:block}}.identity span{{color:#475569;margin-top:4px}}.status{{border-left:4px solid #1E3A5F;padding:12px 14px;background:#F8FAFC;border-radius:8px}}.status.blocked{{border-color:#991B1B}}.status.advisory{{border-color:#92400E}}section{{margin-top:24px}}h2{{font-size:16px}}ul{{list-style:none;padding:0;margin:12px 0}}li{{display:flex;justify-content:space-between;gap:16px;padding:12px 0;border-top:1px solid #E5E7EB}}li span{{margin-left:8px;color:#475569}}li small{{display:block;color:#64748B;margin-top:3px}}.links{{display:flex;gap:8px;align-items:center}}.links a{{color:#1D4ED8;text-decoration:none;font-weight:700}}.warning{{background:#FFFBEB;padding:16px;border-radius:12px}}.actions{{display:flex;justify-content:flex-end;gap:10px;margin-top:28px;flex-wrap:wrap}}.actions a,.actions button{{border:0;border-radius:10px;padding:12px 18px;font-weight:800;font-size:14px;text-decoration:none;cursor:pointer}}.cancel{{background:#E5E7EB;color:#111827}}.delete{{background:#111827;color:#fff}}a:focus-visible,button:focus-visible{{outline:3px solid #2563EB;outline-offset:3px}}@media(max-width:560px){{main{{margin:24px auto}}.card{{padding:22px}}li{{display:block}}.links{{margin-top:8px}}}}
</style></head><body><main><div class="card"><div class="eyebrow">Delete protection</div><h1>{html.escape(heading)}</h1><p>{html.escape(explanation)}</p><div class="identity"><b>Document</b><span>{html.escape(document)}</span><b style="margin-top:10px">Identifier</b><span>{html.escape(identifier)}</span></div><div class="status {status_class}">{html.escape(heading)}</div>{dependent_section}{warning_section}<div class="actions"><a class="cancel" href="{html.escape(cancel_url, quote=True)}">Cancel</a>{delete_form}</div></div></main></body></html>"""
    return HTMLResponse(body, status_code=status_code)


def identifier_delete_confirmation(
    module: str, document: str, identifier: Any, storage_path: str | Path,
    id_field: str, action_url: str, cancel_url: str,
) -> HTMLResponse:
    normalized = _normalized(identifier)
    records = load_json_strict(storage_path, [], list)
    if not any(isinstance(record, dict) and _normalized(record.get(id_field)) == normalized for record in records):
        raise HTTPException(status_code=404, detail=f"{document} not found")
    return render_delete_page(document, normalized, action_url, cancel_url, find_dependencies(module, normalized))


def confirmed_identifier_delete(
    module: str, document: str, identifier: Any, storage_path: str | Path,
    id_field: str, action_url: str, cancel_url: str, redirect_url: str,
):
    normalized = _normalized(identifier)
    def remove(records):
        index = next((i for i, record in enumerate(records) if isinstance(record, dict) and _normalized(record.get(id_field)) == normalized), None)
        if index is None:
            raise HTTPException(status_code=404, detail=f"{document} not found")
        dependencies = find_dependencies(module, normalized)
        if dependencies:
            raise _ProtectedDelete(dependencies)
        records.pop(index)

    try:
        locked_json_mutation(storage_path, [], remove, list)
    except _ProtectedDelete as exc:
        return render_delete_page(document, normalized, action_url, cancel_url, exc.dependencies, status_code=409)
    return RedirectResponse(redirect_url, status_code=303)


def indexed_delete_confirmation(
    module: str, document: str, index: int, storage_path: str | Path,
    name_field: str, action_url: str, cancel_url: str,
) -> HTMLResponse:
    records = load_json_strict(storage_path, [], list)
    if index < 0 or index >= len(records) or not isinstance(records[index], dict):
        raise HTTPException(status_code=404, detail=f"{document} not found")
    name = _normalized(records[index].get(name_field))
    if not name:
        raise HTTPException(status_code=404, detail=f"{document} not found")
    warnings = find_soft_warnings(module, name)
    return render_delete_page(document, name, action_url, cancel_url, warnings=warnings, expected_name=name)


def confirmed_indexed_delete(
    module: str, document: str, index: int, expected_name: Any, storage_path: str | Path,
    name_field: str, action_url: str, cancel_url: str, redirect_url: str,
):
    expected = _normalized(expected_name)
    def remove(records):
        if index < 0 or index >= len(records) or not isinstance(records[index], dict):
            raise HTTPException(status_code=404, detail=f"{document} not found")
        current = _normalized(records[index].get(name_field))
        if not expected or current.casefold() != expected.casefold():
            raise _IndexMismatch()
        records.pop(index)

    try:
        locked_json_mutation(storage_path, [], remove, list)
    except _IndexMismatch:
        return render_delete_page(
            document, expected or str(index), action_url, cancel_url,
            warnings=find_soft_warnings(module, expected), status_code=409, expected_name=expected,
            blocked_message="The record changed before deletion. Nothing was deleted.",
        )
    return RedirectResponse(redirect_url, status_code=303)
