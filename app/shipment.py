from pathlib import Path
from datetime import datetime
from io import BytesIO
import html as html_lib
import json
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

router = APIRouter()

from app.storage import atomic_write_json, data_path, load_json_strict, locked_json_mutation, next_identifier
from app.validation import DataValidationError, require_allowed_value, require_consistent_reference, require_existing_reference, require_text
from app.referential_integrity import confirmed_identifier_delete, identifier_delete_confirmation

SHIPMENT_FILE = data_path("shipments.json")

OPERATIONAL_RECORDS = [
    {
        "label": "Booking Confirmation",
        "file": data_path("booking_confirmations.json"),
        "key": "booking_record_no",
        "view": "/booking/{value}",
        "pdf": "/booking-pdf/{value}",
        "edit": "/edit-booking/{value}",
        "create": "/booking-form?shipment_no={shipment_no}",
    },
    {
        "label": "Container Management",
        "file": data_path("containers.json"),
        "key": "container_record_no",
        "view": "/container/{value}",
        "pdf": "/container-pdf/{value}",
        "edit": "/edit-container/{value}",
        "create": "/container-form?shipment_no={shipment_no}",
    },
    {
        "label": "Customs Declaration",
        "file": data_path("customs_declarations.json"),
        "key": "customs_record_no",
        "view": "/customs/{value}",
        "pdf": "/customs-pdf/{value}",
        "edit": "/edit-customs/{value}",
        "create": "/customs-form?shipment_no={shipment_no}",
    },
]

DOCUMENTS = [
    {
        "label": "Quotation",
        "field": "quotation_no",
        "file": data_path("quotations.json"),
        "key": "quotation_no",
        "pdf": "/quotation-pdf/{value}",
        "edit": "/edit-quotation/{value}",
    },
    {
        "label": "Proforma Invoice",
        "field": "pi_no",
        "file": data_path("proformas.json"),
        "key": "pi_no",
        "pdf": "/proforma-pdf/{value}",
        "edit": "/edit-proforma/{value}",
    },
    {
        "label": "Commercial Invoice",
        "field": "invoice_no",
        "file": data_path("invoices.json"),
        "key": "invoice_no",
        "pdf": "/invoice-pdf/{value}",
        "edit": "/edit-invoice/{value}",
    },
    {
        "label": "Packing List",
        "field": "packing_no",
        "file": data_path("packing_lists.json"),
        "key": "packing_no",
        "pdf": "/packing-list-pdf/{value}",
        "edit": "/edit-packing/{value}",
    },
    {
        "label": "Shipping Instruction",
        "field": "si_no",
        "file": data_path("shipping_instructions.json"),
        "key": "si_no",
        "pdf": "/si-pdf/{value}",
        "edit": "/edit-si/{value}",
    },
    {
        "label": "Bill of Lading",
        "field": "bl_no",
        "file": data_path("bills_of_lading.json"),
        "key": "bl_no",
        "pdf": "/bl-pdf/{value}",
        "edit": "/edit-bl/{value}",
    },
    {
        "label": "Certificate of Origin",
        "field": "co_no",
        "file": data_path("certificates_of_origin.json"),
        "key": "co_no",
        "view": "/co/{value}",
        "pdf": "/co-pdf/{value}",
        "edit": "/edit-co/{value}",
    },
    {
        "label": "Inspection Certificate",
        "field": "inspection_no",
        "file": data_path("inspection_certificates.json"),
        "key": "inspection_no",
        "view": "/inspection/{value}",
        "pdf": "/inspection-pdf/{value}",
        "edit": "/edit-inspection/{value}",
    },
    {
        "label": "Insurance Certificate",
        "field": "insurance_no",
        "file": data_path("insurance_certificates.json"),
        "key": "insurance_no",
        "view": "/insurance/{value}",
        "pdf": "/insurance-pdf/{value}",
        "edit": "/edit-insurance/{value}",
    },
    {
        "label": "Weight Certificate",
        "field": "weight_no",
        "file": data_path("weight_certificates.json"),
        "key": "weight_no",
        "view": "/weight/{value}",
        "pdf": "/weight-pdf/{value}",
        "edit": "/edit-weight/{value}",
    },
]

STATUS_OPTIONS = ["Inquiry", "Quoted", "Confirmed", "In Production", "Ready to Ship", "Shipped", "Completed"]


def html_attr(value):
    return html_lib.escape(str(value or ""), quote=True)


def html_text(value):
    return html_lib.escape(str(value or ""))


def load_json(path, default):
    return load_json_strict(path, default, type(default) if isinstance(default, (list, dict)) else None)


def load_shipments():
    return load_json(SHIPMENT_FILE, [])


def save_shipments(records):
    atomic_write_json(SHIPMENT_FILE, records, list)


def next_shipment_no(records):
    return next_identifier(records, "shipment_no", "SHP")
    numbers = [
        int(record.get("shipment_no", "SHP-000").split("-")[1])
        for record in records
        if record.get("shipment_no", "").startswith("SHP-")
    ]
    return f"SHP-{max(numbers, default=0) + 1:03d}"


def blank_shipment():
    record = {
        "shipment_no": "",
        "shipment_date": datetime.now().strftime("%Y-%m-%d"),
        "shipment_name": "",
        "customer": "",
        "buyer": "",
        "status": "Inquiry",
        "remarks": "",
    }
    for doc in DOCUMENTS:
        record[doc["field"]] = ""
    return record


def _cached_records(path, datasets):
    if datasets is None:
        return None
    return datasets.get(Path(path).name)


def load_workflow_datasets():
    """Load each workflow dataset once for one render operation."""
    datasets = {}
    for descriptor in [*DOCUMENTS, *OPERATIONAL_RECORDS]:
        filename = Path(descriptor["file"]).name
        if filename not in datasets:
            datasets[filename] = load_json(descriptor["file"], [])
    return datasets


def document_records(doc, datasets=None):
    cached = _cached_records(doc["file"], datasets)
    return cached if cached is not None else load_json(doc["file"], [])


def document_options(doc):
    records = document_records(doc)
    values = []
    for record in records:
        value = str(record.get(doc["key"], "") or "")
        if value:
            values.append(value)
    return sorted(set(values), reverse=True)


def document_exists(doc, value, datasets=None):
    if not value:
        return False
    return any(record.get(doc["key"]) == value for record in document_records(doc, datasets))


def linked_count(record, datasets=None):
    return sum(
        1
        for doc in DOCUMENTS
        if document_exists(doc, record.get(doc["field"], ""), datasets)
    )


def find_shipment(shipment_no):
    for record in load_shipments():
        if record.get("shipment_no") == shipment_no:
            return record
    return None


def link_direct_document(shipment_no, field, identifier):
    """Link an existing workflow result without adding fields or changing status."""
    allowed = {"invoice_no", "packing_no", "si_no", "bl_no", "co_no", "inspection_no", "insurance_no", "weight_no"}
    shipment_no = str(shipment_no or "").strip()
    identifier = str(identifier or "").strip()
    if field not in allowed or not shipment_no or not identifier:
        return False
    linked = {"value": False}
    def update(records):
        for record in records:
            if str(record.get("shipment_no", "") or "").strip() == shipment_no:
                record[field] = identifier
                linked["value"] = True
                return
    locked_json_mutation(SHIPMENT_FILE, [], update, list)
    return linked["value"]


def shipment_context_redirect_url(shipment_no, field, identifier, fallback_url):
    if link_direct_document(shipment_no, field, identifier):
        return f'/shipment/{quote(str(shipment_no).strip(), safe="")}'
    return fallback_url


def resolve_direct_documents(record, datasets=None):
    resolved = []
    for doc in DOCUMENTS:
        value = str(record.get(doc["field"], "") or "")
        resolved.append({"document": doc, "value": value, "exists": document_exists(doc, value, datasets)})
    return resolved


def reverse_records_for(shipment_no, operational, datasets=None):
    matches = []
    records = _cached_records(operational["file"], datasets)
    for record in records if records is not None else load_json(operational["file"], []):
        value = str(record.get(operational["key"], "") or "")
        if value and record.get("shipment_no") == shipment_no:
            matches.append({"value": value, "record": record})
    return matches


def resolve_operational_records(shipment_no, datasets=None):
    return [
        {"operational": operational, "matches": reverse_records_for(shipment_no, operational, datasets)}
        for operational in OPERATIONAL_RECORDS
    ]


def operational_count(shipment_no):
    return sum(len(group["matches"]) for group in resolve_operational_records(shipment_no))


def required_workflow_progress(shipment, resolved_direct=None, resolved_operations=None):
    if resolved_direct is None:
        resolved_direct = resolve_direct_documents(shipment)
    if resolved_operations is None:
        resolved_operations = resolve_operational_records(shipment.get("shipment_no", ""))

    direct_status = {
        resolved["document"]["field"]: resolved["exists"]
        for resolved in resolved_direct
    }
    operational_status = {
        group["operational"]["key"]: bool(group["matches"])
        for group in resolved_operations
    }
    completed = sum([
        bool(direct_status.get("invoice_no")),
        bool(direct_status.get("packing_no")),
        bool(direct_status.get("si_no")),
        bool(operational_status.get("booking_record_no")),
        bool(direct_status.get("bl_no")),
        bool(operational_status.get("customs_record_no")),
    ])
    total = 6
    return {
        "completed": completed,
        "total": total,
        "percentage": round(completed * 100 / total),
    }


def health_score_label(score):
    if score >= 90:
        return "Excellent"
    if score >= 70:
        return "Good"
    if score >= 40:
        return "Attention"
    return "Critical"


def shipment_health_score(
    shipment,
    resolved_direct=None,
    resolved_operations=None,
    workflow_progress=None,
    next_step=None,
):
    if resolved_direct is None:
        resolved_direct = resolve_direct_documents(shipment)
    if resolved_operations is None:
        resolved_operations = resolve_operational_records(shipment.get("shipment_no", ""))
    if workflow_progress is None:
        workflow_progress = required_workflow_progress(shipment, resolved_direct, resolved_operations)
    if next_step is None:
        next_step = next_step_for_shipment(shipment)

    direct_status = {
        resolved["document"]["field"]: resolved["exists"]
        for resolved in resolved_direct
    }
    operational_status = {
        group["operational"]["key"]: bool(group["matches"])
        for group in resolved_operations
    }
    optional_completed = sum([
        bool(direct_status.get("quotation_no")),
        bool(direct_status.get("pi_no")),
        bool(operational_status.get("container_record_no")),
        bool(direct_status.get("co_no")),
        bool(direct_status.get("inspection_no")),
        bool(direct_status.get("insurance_no")),
        bool(direct_status.get("weight_no")),
    ])
    optional_total = 7
    optional_points = round(optional_completed * 10 / optional_total)
    required_completed = workflow_progress["completed"]
    score = max(0, min(100, required_completed * 15 + optional_points))
    return {
        "score": score,
        "label": health_score_label(score),
        "required_completed": required_completed,
        "required_total": 6,
        "optional_completed": optional_completed,
        "optional_total": optional_total,
        "workflow_complete": bool(next_step["is_complete"]),
    }


def document_by_field(field):
    return next((doc for doc in DOCUMENTS if doc["field"] == field), None)


def validate_shipment_values(record):
    record["shipment_name"] = require_text("Shipment name", record.get("shipment_name", ""))
    record["status"] = require_allowed_value("Shipment status", record.get("status", ""), STATUS_OPTIONS)
    linked = {}
    for doc in DOCUMENTS:
        value = record.get(doc["field"], "")
        linked[doc["field"]] = require_existing_reference(
            doc["label"], value, document_records(doc), doc["key"]
        )
    packing = linked.get("packing_no")
    if packing:
        require_consistent_reference("Invoice", record.get("invoice_no", ""), packing.get("invoice_no", ""), "selected Packing List")
    instruction = linked.get("si_no")
    if instruction:
        require_consistent_reference("Packing List", record.get("packing_no", ""), instruction.get("packing_no", ""), "selected Shipping Instruction")
    bill = linked.get("bl_no")
    if bill:
        require_consistent_reference("Packing List", record.get("packing_no", ""), bill.get("packing_no", ""), "selected Bill of Lading")
        require_consistent_reference("Invoice", record.get("invoice_no", ""), bill.get("invoice_no", ""), "selected Bill of Lading")
    for field in ["co_no", "inspection_no", "insurance_no", "weight_no"]:
        certificate = linked.get(field)
        if certificate:
            require_consistent_reference("Bill of Lading", record.get("bl_no", ""), certificate.get("bl_no", ""), "selected certificate")


def operational_by_key(key):
    return next((record for record in OPERATIONAL_RECORDS if record["key"] == key), None)


def workflow_url(path, parameters):
    query = urlencode([(key, value) for key, value in parameters if str(value or "")])
    return f"{path}?{query}" if query else path


def select_operational_match(matches, shipment):
    packing_no = str(shipment.get("packing_no", "") or "")
    bl_no = str(shipment.get("bl_no", "") or "")

    def sort_key(match):
        record = match["record"]
        related = bool(
            (packing_no and record.get("packing_no") == packing_no)
            or (bl_no and record.get("bl_no") == bl_no)
        )
        return related, match["value"]

    return max(matches, key=sort_key) if matches else None


def next_step_for_shipment(shipment, resolved_direct=None, resolved_operations=None):
    shipment_no = str(shipment.get("shipment_no", "") or "")
    invoice_no = str(shipment.get("invoice_no", "") or "")
    packing_no = str(shipment.get("packing_no", "") or "")
    si_no = str(shipment.get("si_no", "") or "")
    bl_no = str(shipment.get("bl_no", "") or "")

    direct_status = {
        resolved["document"]["field"]: resolved
        for resolved in (resolved_direct or [])
    }
    operation_status = {
        group["operational"]["key"]: group["matches"]
        for group in (resolved_operations or [])
    }
    def direct_exists(field, value):
        resolved = direct_status.get(field)
        return resolved["exists"] if resolved is not None else document_exists(document_by_field(field), value)
    def operation_matches(key):
        if key in operation_status:
            return operation_status[key]
        return reverse_records_for(shipment_no, operational_by_key(key))

    invoice_exists = direct_exists("invoice_no", invoice_no)
    packing_exists = direct_exists("packing_no", packing_no)
    si_exists = direct_exists("si_no", si_no)
    bl_exists = direct_exists("bl_no", bl_no)

    booking_matches = operation_matches("booking_record_no")
    container_matches = operation_matches("container_record_no")
    customs_matches = operation_matches("customs_record_no")

    if not invoice_exists:
        pi_no = str(shipment.get("pi_no", "") or "")
        pi_exists = direct_exists("pi_no", pi_no)
        create_url = workflow_url("/invoice", [("pi_no", pi_no), ("shipment_no", shipment_no)]) if pi_exists else workflow_url("/invoice", [("shipment_no", shipment_no)])
        return {
            "step_label": "Commercial Invoice",
            "reason": "A Commercial Invoice is required before packing and shipping documents can be prepared.",
            "create_url": create_url,
            "is_complete": False,
        }
    if not packing_exists:
        return {
            "step_label": "Packing List",
            "reason": "A Packing List is required to prepare the shipment's cargo documents.",
            "create_url": workflow_url("/packing-page", [("invoice_no", invoice_no), ("shipment_no", shipment_no)]),
            "is_complete": False,
        }
    if not si_exists:
        return {
            "step_label": "Shipping Instruction",
            "reason": "A Shipping Instruction is required before carrier booking.",
            "create_url": workflow_url("/si-form", [("packing_no", packing_no), ("shipment_no", shipment_no)]),
            "is_complete": False,
        }
    if not booking_matches:
        return {
            "step_label": "Booking Confirmation",
            "reason": "A Booking Confirmation is required to schedule this shipment.",
            "create_url": workflow_url("/booking-form", [
                ("shipment_no", shipment_no), ("si_no", si_no), ("packing_no", packing_no),
            ]),
            "is_complete": False,
        }
    if not bl_exists:
        return {
            "step_label": "Bill of Lading",
            "reason": "A Bill of Lading is required before customs clearance.",
            "create_url": workflow_url("/bl-form", [("packing_no", packing_no), ("shipment_no", shipment_no)]),
            "is_complete": False,
        }
    if not customs_matches:
        booking = select_operational_match(booking_matches, shipment)
        container = select_operational_match(container_matches, shipment)
        return {
            "step_label": "Customs Declaration",
            "reason": "A Customs Declaration is the final required workflow record.",
            "create_url": workflow_url("/customs-form", [
                ("shipment_no", shipment_no),
                ("invoice_no", invoice_no),
                ("packing_no", packing_no),
                ("booking_record_no", booking["value"] if booking else ""),
                ("container_record_no", container["value"] if container else ""),
                ("bl_no", bl_no),
            ]),
            "is_complete": False,
        }
    return {
        "step_label": "Workflow Complete",
        "reason": "All required workflow records are linked.",
        "create_url": "",
        "is_complete": True,
    }


def render_workflow_timeline(resolved_direct, resolved_operations, next_step):
    direct_status = {
        resolved["document"]["field"]: resolved["exists"]
        for resolved in resolved_direct
    }
    operational_status = {
        group["operational"]["key"]: bool(group["matches"])
        for group in resolved_operations
    }
    primary_steps = [
        ("Quotation", direct_status.get("quotation_no", False), True),
        ("Proforma Invoice", direct_status.get("pi_no", False), True),
        ("Commercial Invoice", direct_status.get("invoice_no", False), False),
        ("Packing List", direct_status.get("packing_no", False), False),
        ("Shipping Instruction", direct_status.get("si_no", False), False),
        ("Booking Confirmation", operational_status.get("booking_record_no", False), False),
        ("Bill of Lading", direct_status.get("bl_no", False), False),
        ("Customs Declaration", operational_status.get("customs_record_no", False), False),
    ]

    nodes = []
    for label, exists, optional_when_missing in primary_steps:
        if exists:
            state, marker, status = "complete", "✓", "Complete"
        elif optional_when_missing:
            state, marker, status = "optional", "○", "Optional"
        elif not next_step["is_complete"] and next_step["step_label"] == label:
            state, marker, status = "current", "●", "Current"
        else:
            state, marker, status = "pending", "○", "Pending"
        nodes.append(f"""
<div class="timeline-node {state}" data-step="{html_attr(label)}" data-state="{state}">
<span class="timeline-marker">{marker}</span>
<span class="timeline-label">{html_text(label)}</span>
<span class="timeline-state">{status}</span>
</div>""")
    primary_html = '<span class="timeline-connector" aria-hidden="true">→</span>'.join(nodes)

    optional_steps = [
        ("Container Management", operational_status.get("container_record_no", False)),
        ("Certificate of Origin", direct_status.get("co_no", False)),
        ("Inspection Certificate", direct_status.get("inspection_no", False)),
        ("Insurance Certificate", direct_status.get("insurance_no", False)),
        ("Weight Certificate", direct_status.get("weight_no", False)),
    ]
    optional_html = "".join(
        f"""
<div class="optional-node {'linked' if exists else 'optional'}" data-optional-step="{html_attr(label)}" data-state="{'linked' if exists else 'optional'}">
<span class="timeline-marker">{'✓' if exists else '○'}</span>
<span class="timeline-label">{html_text(label)}</span>
<span class="timeline-state">{'Linked' if exists else 'Optional'}</span>
</div>"""
        for label, exists in optional_steps
    )
    return f"""
<section class="workflow-timeline" aria-labelledby="workflow-timeline-title">
<h2 id="workflow-timeline-title">Workflow Timeline</h2>
<div class="timeline-scroll"><div class="timeline-track">{primary_html}</div></div>
<h3>Optional Documents</h3>
<div class="optional-track">{optional_html}</div>
</section>
"""


def render_relationship_node(label, state, records=None, create_url="", root=False):
    records = records or []
    record_html = ""
    for record in records:
        actions = "".join(
            f'<a href="{html_attr(url)}">{html_text(action)}</a>'
            for action, url in record.get("actions", [])
        )
        record_html += f"""
<div class="relationship-record">
<div class="relationship-identifier">{html_text(record.get('identifier', ''))}</div>
<div class="relationship-actions">{actions}</div>
</div>"""
    if not records and create_url:
        record_html = f'<div class="relationship-actions"><a href="{html_attr(create_url)}">Create</a></div>'
    root_class = " root" if root else ""
    return f"""
<div class="relationship-node {state.lower()}{root_class}" data-relationship-node="{html_attr(label)}" data-state="{state.lower()}">
<div class="relationship-node-head"><strong>{html_text(label)}</strong><span class="relationship-badge">{html_text(state)}</span></div>
{record_html}
</div>"""


def render_document_relationship_graph(shipment, resolved_direct, resolved_operations, workflow_progress, next_step):
    direct = {
        resolved["document"]["field"]: resolved
        for resolved in resolved_direct
    }
    operational = {
        group["operational"]["key"]: group
        for group in resolved_operations
    }

    def valid_direct_value(field):
        resolved = direct[field]
        return resolved["value"] if resolved["exists"] else ""

    def direct_node(field, required, create_url):
        resolved = direct[field]
        doc = resolved["document"]
        if resolved["exists"]:
            value = resolved["value"]
            actions = []
            if doc.get("view"):
                actions.append(("View", doc["view"].format(value=value)))
            actions.extend([
                ("PDF", doc["pdf"].format(value=value)),
                ("Edit", doc["edit"].format(value=value)),
            ])
            return render_relationship_node(doc["label"], "Linked", [{"identifier": value, "actions": actions}])
        return render_relationship_node(doc["label"], "Missing" if required else "Optional", create_url=create_url)

    def operational_node(key, required, create_url):
        group = operational[key]
        descriptor = group["operational"]
        records = []
        for match in group["matches"]:
            value = match["value"]
            records.append({
                "identifier": value,
                "actions": [
                    ("View", descriptor["view"].format(value=value)),
                    ("PDF", descriptor["pdf"].format(value=value)),
                    ("Edit", descriptor["edit"].format(value=value)),
                ],
            })
        state = "Linked" if records else ("Missing" if required else "Optional")
        return render_relationship_node(descriptor["label"], state, records, create_url)

    shipment_no = str(shipment.get("shipment_no", "") or "")
    quotation_no = valid_direct_value("quotation_no")
    pi_no = valid_direct_value("pi_no")
    invoice_no = valid_direct_value("invoice_no")
    packing_no = valid_direct_value("packing_no")
    si_no = valid_direct_value("si_no")
    bl_no = valid_direct_value("bl_no")

    booking_matches = operational["booking_record_no"]["matches"]
    container_matches = operational["container_record_no"]["matches"]
    booking = select_operational_match(booking_matches, shipment)
    container = select_operational_match(container_matches, shipment)

    quotation = direct_node("quotation_no", False, "/quotation-form")
    proforma = direct_node("pi_no", False, workflow_url("/proforma-form", [("quotation_no", quotation_no)]))
    invoice = direct_node("invoice_no", True, workflow_url("/invoice", [("pi_no", pi_no)]))
    packing = direct_node("packing_no", True, workflow_url("/packing-page", [("invoice_no", invoice_no)]))
    shipping_instruction = direct_node("si_no", True, workflow_url("/si-form", [("packing_no", packing_no)]))
    booking_node = operational_node("booking_record_no", True, workflow_url("/booking-form", [
        ("shipment_no", shipment_no), ("si_no", si_no), ("packing_no", packing_no),
    ]))
    container_node = operational_node("container_record_no", False, workflow_url("/container-form", [
        ("shipment_no", shipment_no), ("packing_no", packing_no), ("bl_no", bl_no),
    ]))
    bill_of_lading = direct_node("bl_no", True, workflow_url("/bl-form", [("packing_no", packing_no), ("shipment_no", shipment_no)]))
    certificate = direct_node("co_no", False, workflow_url("/co-form", [("bl_no", bl_no), ("shipment_no", shipment_no)]))
    inspection = direct_node("inspection_no", False, workflow_url("/inspection-form", [("bl_no", bl_no), ("shipment_no", shipment_no)]))
    insurance = direct_node("insurance_no", False, workflow_url("/insurance-form", [("bl_no", bl_no), ("shipment_no", shipment_no)]))
    weight = direct_node("weight_no", False, workflow_url("/weight-form", [("bl_no", bl_no), ("shipment_no", shipment_no)]))
    customs_node = operational_node("customs_record_no", True, workflow_url("/customs-form", [
        ("shipment_no", shipment_no),
        ("invoice_no", invoice_no),
        ("packing_no", packing_no),
        ("booking_record_no", booking["value"] if booking else ""),
        ("container_record_no", container["value"] if container else ""),
        ("bl_no", bl_no),
    ]))
    shipment_root = render_relationship_node("Shipment", "Linked", [{"identifier": shipment_no, "actions": []}], root=True)

    return f"""
<section class="document-relationship" aria-labelledby="document-relationship-title" data-required-completed="{workflow_progress['completed']}" data-workflow-complete="{str(next_step['is_complete']).lower()}">
<div class="relationship-heading"><h2 id="document-relationship-title">Document Relationship</h2><span>{workflow_progress['completed']} / {workflow_progress['total']} required linked</span></div>
<div class="relationship-scroll">
<ul class="relationship-tree"><li>{shipment_root}<ul>
<li>{quotation}</li>
<li>{proforma}</li>
<li>{invoice}</li>
<li>{packing}<ul>
<li>{shipping_instruction}</li>
<li>{booking_node}</li>
<li>{container_node}</li>
<li>{bill_of_lading}<ul>
<li>{certificate}</li>
<li>{inspection}</li>
<li>{insurance}</li>
<li>{weight}</li>
<li>{customs_node}</li>
</ul></li>
</ul></li>
</ul></li></ul>
</div>
</section>
"""


def build_record(
    shipment_no, shipment_date, shipment_name, customer, buyer, status, remarks,
    quotation_no, pi_no, invoice_no, packing_no, si_no, bl_no, co_no,
    inspection_no, insurance_no, weight_no,
):
    return {
        "shipment_no": shipment_no,
        "shipment_date": shipment_date,
        "shipment_name": shipment_name,
        "customer": customer,
        "buyer": buyer,
        "status": status,
        "remarks": remarks,
        "quotation_no": quotation_no,
        "pi_no": pi_no,
        "invoice_no": invoice_no,
        "packing_no": packing_no,
        "si_no": si_no,
        "bl_no": bl_no,
        "co_no": co_no,
        "inspection_no": inspection_no,
        "insurance_no": insurance_no,
        "weight_no": weight_no,
    }


def select_html(name, selected, options, placeholder):
    html = [f'<select name="{html_attr(name)}">']
    html.append(f'<option value="">{html_text(placeholder)}</option>')
    for value in options:
        checked = " selected" if value == selected else ""
        html.append(f'<option value="{html_attr(value)}"{checked}>{html_text(value)}</option>')
    html.append("</select>")
    return "".join(html)


def render_form(record, action, title, button_text, show_shipment_no=False):
    shipment_no_input = ""
    if show_shipment_no:
        shipment_no_input = f'<input type="text" name="shipment_no" value="{html_attr(record.get("shipment_no", ""))}" placeholder="Shipment No" readonly>'

    status_options = "".join(
        f'<option value="{html_attr(option)}"{" selected" if record.get("status") == option else ""}>{html_text(option)}</option>'
        for option in STATUS_OPTIONS
    )

    document_fields = ""
    for doc in DOCUMENTS:
        value = record.get(doc["field"], "")
        document_fields += f"""
<div>
<label>{html_text(doc["label"])}</label>
{select_html(doc["field"], value, document_options(doc), f"Select {doc['label']}")}
</div>
"""

    html = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Shipment Management</title>
<style>
body{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;color:#111827;}
.container{max-width:1080px;margin:auto;background:white;padding:35px;border-radius:16px;box-shadow:0 12px 35px rgba(15,23,42,.08);}
h1{text-align:center;font-size:46px;margin:8px 0 10px;}
.sub{text-align:center;color:#6B7280;margin-bottom:35px;}
.nav-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:25px;}
.card{border:1px solid #E5E7EB;border-radius:16px;padding:25px;margin-bottom:25px;background:#fff;}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;}
label{display:block;font-weight:bold;margin-bottom:7px;color:#374151;}
input,select,textarea{width:100%;padding:14px;border:1px solid #D1D5DB;border-radius:10px;font-size:16px;box-sizing:border-box;background:white;}
textarea{min-height:100px;resize:vertical;}
button{padding:15px 18px;background:#111827;color:white;border:none;border-radius:12px;font-size:16px;cursor:pointer;}
.small{min-width:170px;}
.full{width:100%;margin-top:10px;font-size:18px;}
@media(max-width:820px){body{padding:18px}.grid{grid-template-columns:1fr}h1{font-size:34px}}
</style>
</head>
<body>
<div class="container">
<div class="nav-row">
<a href="/"><button class="small" type="button">Dashboard</button></a>
<a href="/shipment-list"><button class="small" type="button">Shipment List</button></a>
</div>
<h1>__TITLE__</h1>
<p class="sub">Group trade documents under one shipment project without duplicating document data</p>

<form action="__ACTION__" method="post">
<div class="card">
<h2>Shipment Information</h2>
<div class="grid">
__SHIPMENT_NO_INPUT__
<div><label>Shipment Date</label><input type="date" name="shipment_date" value="__SHIPMENT_DATE__"></div>
<div><label>Shipment Name</label><input type="text" name="shipment_name" value="__SHIPMENT_NAME__" placeholder="Shipment Name"></div>
<div><label>Customer</label><input type="text" name="customer" value="__CUSTOMER__" placeholder="Customer"></div>
<div><label>Buyer</label><input type="text" name="buyer" value="__BUYER__" placeholder="Buyer"></div>
<div><label>Status</label><select name="status">__STATUS_OPTIONS__</select></div>
</div>
<br>
<label>Remarks</label>
<textarea name="remarks" placeholder="Remarks">__REMARKS__</textarea>
</div>

<div class="card">
<h2>Document References</h2>
<div class="grid">
__DOCUMENT_FIELDS__
</div>
</div>

<button class="full" type="submit">__BUTTON_TEXT__</button>
</form>
</div>
</body>
</html>
"""
    replacements = {
        "__TITLE__": html_text(title),
        "__ACTION__": html_attr(action),
        "__SHIPMENT_NO_INPUT__": shipment_no_input,
        "__SHIPMENT_DATE__": html_attr(record.get("shipment_date", "")),
        "__SHIPMENT_NAME__": html_attr(record.get("shipment_name", "")),
        "__CUSTOMER__": html_attr(record.get("customer", "")),
        "__BUYER__": html_attr(record.get("buyer", "")),
        "__STATUS_OPTIONS__": status_options,
        "__REMARKS__": html_text(record.get("remarks", "")),
        "__DOCUMENT_FIELDS__": document_fields,
        "__BUTTON_TEXT__": html_text(button_text),
    }
    for key, value in replacements.items():
        html = html.replace(key, value)
    return HTMLResponse(html)


@router.get("/shipment-list", response_class=HTMLResponse)
def shipment_list(search: str = ""):
    shipments = sorted(load_shipments(), key=lambda record: record.get("shipment_no", ""), reverse=True)
    if search:
        term = search.lower()
        shipments = [
            record for record in shipments
            if term in str(record.get("shipment_no", "")).lower()
            or term in str(record.get("shipment_name", "")).lower()
            or term in str(record.get("customer", "")).lower()
            or term in str(record.get("buyer", "")).lower()
            or term in str(record.get("status", "")).lower()
        ]

    rows = ""
    for record in shipments:
        shipment_no = record.get("shipment_no", "")
        progress = f"{linked_count(record)} / {len(DOCUMENTS)}"
        rows += f"""
<tr>
<td>{html_text(shipment_no)}</td>
<td>{html_text(record.get('shipment_name', ''))}</td>
<td>{html_text(record.get('buyer', '') or record.get('customer', ''))}</td>
<td><span class="pill">{html_text(record.get('status', ''))}</span></td>
<td>{html_text(progress)}</td>
<td><a class="link" href="/shipment/{html_attr(shipment_no)}">View</a></td>
<td><a class="link" href="/edit-shipment/{html_attr(shipment_no)}">Edit</a></td>
<td><a class="danger" href="/delete-shipment/{html_attr(shipment_no)}">Delete</a></td>
</tr>
"""

    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Shipments</title>
<style>
body{{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;color:#111827;}}
.container{{width:94%;margin:auto;}}
h1{{text-align:center;font-size:46px;margin:8px 0 10px;}}
.sub{{text-align:center;color:#6B7280;margin-bottom:28px;}}
.toolbar{{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:20px;}}
.nav{{display:flex;gap:12px;flex-wrap:wrap;}}
button,.btn{{display:inline-block;padding:13px 18px;background:#111827;color:white;border:none;border-radius:12px;text-decoration:none;font-size:15px;cursor:pointer;}}
.reset{{background:#6B7280;}}
.search{{display:flex;gap:10px;flex-wrap:wrap;}}
input{{padding:13px;border:1px solid #D1D5DB;border-radius:10px;font-size:15px;min-width:260px;}}
.count{{font-weight:bold;color:#374151;margin-bottom:14px;}}
.table-wrap{{background:white;border:1px solid #E5E7EB;border-radius:16px;overflow:hidden;box-shadow:0 12px 35px rgba(15,23,42,.08);}}
table{{width:100%;border-collapse:collapse;}}
th{{background:#111827;color:white;text-align:left;padding:14px;font-size:14px;}}
td{{padding:14px;border-bottom:1px solid #E5E7EB;font-size:14px;}}
.link{{background:#111827;color:white;padding:8px 11px;border-radius:9px;text-decoration:none;}}
.danger{{background:#991B1B;color:white;padding:8px 11px;border-radius:9px;text-decoration:none;}}
.pill{{background:#E5E7EB;color:#111827;padding:7px 10px;border-radius:999px;font-weight:bold;font-size:13px;}}
</style>
</head>
<body>
<div class="container">
<h1>Shipment Management</h1>
<p class="sub">Track each shipment project and its linked trade documents</p>
<div class="toolbar">
<div class="nav">
<a class="btn" href="/">Dashboard</a>
<a class="btn" href="/shipment-form">+ New Shipment</a>
</div>
<form class="search" action="/shipment-list" method="get">
<input type="text" name="search" value="{html_attr(search)}" placeholder="Search shipment, customer, buyer, status">
<button type="submit">Search</button>
<a class="btn reset" href="/shipment-list">Reset</a>
</form>
</div>
<div class="count">Total Shipments: {len(shipments)}</div>
<div class="table-wrap">
<table>
<thead>
<tr>
<th>Shipment No</th><th>Shipment Name</th><th>Buyer / Customer</th><th>Status</th><th>Linked Direct Documents</th><th>View</th><th>Edit</th><th>Delete</th>
</tr>
</thead>
<tbody>{rows}</tbody>
</table>
</div>
</div>
</body>
</html>
"""
    return HTMLResponse(html)


@router.get("/shipment-form", response_class=HTMLResponse)
def shipment_form():
    record = blank_shipment()
    record["shipment_no"] = next_shipment_no(load_shipments())
    return render_form(record, "/shipment", "New Shipment", "Save Shipment", show_shipment_no=True)


@router.post("/shipment")
def save_shipment(
    shipment_date: str = Form(""),
    shipment_name: str = Form(""),
    customer: str = Form(""),
    buyer: str = Form(""),
    status: str = Form("Inquiry"),
    remarks: str = Form(""),
    quotation_no: str = Form(""),
    pi_no: str = Form(""),
    invoice_no: str = Form(""),
    packing_no: str = Form(""),
    si_no: str = Form(""),
    bl_no: str = Form(""),
    co_no: str = Form(""),
    inspection_no: str = Form(""),
    insurance_no: str = Form(""),
    weight_no: str = Form(""),
):
    def add_shipment(shipments):
        record = build_record(
        next_identifier(shipments, "shipment_no", "SHP"), shipment_date, shipment_name, customer, buyer,
        status, remarks, quotation_no, pi_no, invoice_no, packing_no, si_no,
        bl_no, co_no, inspection_no, insurance_no, weight_no,
        )
        validate_shipment_values(record)
        shipments.append(record)
    locked_json_mutation(SHIPMENT_FILE, [], add_shipment, list)
    return RedirectResponse("/shipment-list", status_code=303)


@router.get("/shipment/{shipment_no}", response_class=HTMLResponse)
def shipment_detail(shipment_no: str):
    shipment = find_shipment(shipment_no)
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")

    workflow_datasets = load_workflow_datasets()
    cards = ""
    resolved_direct = resolve_direct_documents(shipment, workflow_datasets)
    for resolved in resolved_direct:
        doc = resolved["document"]
        value = resolved["value"]
        exists = resolved["exists"]
        status = "Linked" if exists else "Missing"
        badge_class = "linked" if exists else "missing"
        actions = ""
        if exists:
            view_action = ""
            if doc.get("view"):
                view_action = f'<a href="{html_attr(doc["view"].format(value=value))}">View</a>'
            actions = f"""
<div class="actions">
{view_action}
<a href="{html_attr(doc['pdf'].format(value=value))}">PDF</a>
<a href="{html_attr(doc['edit'].format(value=value))}">Edit</a>
</div>
"""
        cards += f"""
<div class="doc-card">
<div class="doc-title">{html_text(doc["label"])}</div>
<div class="doc-no">{html_text(value if exists else "-")}</div>
<span class="badge {badge_class}">{status}</span>
{actions}
</div>
"""

    operational_cards = ""
    resolved_operations = resolve_operational_records(shipment_no, workflow_datasets)
    for group in resolved_operations:
        operational = group["operational"]
        matches = group["matches"]
        if matches:
            record_rows = ""
            for match in matches:
                value = match["value"]
                record_rows += f"""
<div class="operational-record">
<div class="doc-no">{html_text(value)}</div>
<span class="badge linked">Linked</span>
<div class="actions">
<a href="{html_attr(operational['view'].format(value=value))}">View</a>
<a href="{html_attr(operational['pdf'].format(value=value))}">PDF</a>
<a href="{html_attr(operational['edit'].format(value=value))}">Edit</a>
</div>
</div>
"""
        else:
            record_rows = f"""
<div class="doc-no">-</div>
<span class="badge missing">Missing</span>
<div class="actions">
<a href="{html_attr(operational['create'].format(shipment_no=shipment_no))}">Create</a>
</div>
"""
        operational_cards += f"""
<div class="doc-card operational-card">
<div class="doc-title">{html_text(operational['label'])}</div>
{record_rows}
</div>
"""

    linked_operations = sum(len(group["matches"]) for group in resolved_operations)
    workflow_progress = required_workflow_progress(shipment, resolved_direct, resolved_operations)
    next_step = next_step_for_shipment(shipment, resolved_direct, resolved_operations)
    health_score = shipment_health_score(
        shipment, resolved_direct, resolved_operations, workflow_progress, next_step
    )
    health_colors = {
        "Excellent": "#166534",
        "Good": "#1D4ED8",
        "Attention": "#92400E",
        "Critical": "#991B1B",
    }
    health_color = health_colors[health_score["label"]]
    workflow_timeline = render_workflow_timeline(resolved_direct, resolved_operations, next_step)
    relationship_graph = render_document_relationship_graph(
        shipment, resolved_direct, resolved_operations, workflow_progress, next_step
    )
    if next_step["is_complete"]:
        next_step_card = f"""
<section class="next-step complete">
<div class="next-kicker">Workflow Status</div>
<h2><span class="complete-check" aria-hidden="true">✓</span>{html_text(next_step['step_label'])}</h2>
<p>{html_text(next_step['reason'])}</p>
</section>
"""
    else:
        next_step_card = f"""
<section class="next-step">
<div>
<div class="next-kicker">Next Step</div>
<h2>{html_text(next_step['step_label'])}</h2>
<p>{html_text(next_step['reason'])}</p>
</div>
<a class="next-action" href="{html_attr(next_step['create_url'])}">Create</a>
</section>
"""

    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html_text(shipment_no)}</title>
<style>
body{{font-family:Arial,sans-serif;background:#f3f4f6;padding:40px;color:#111827;}}
.container{{width:94%;max-width:1180px;margin:auto;}}
.nav-row{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:25px;}}
button,.btn{{display:inline-block;padding:13px 18px;background:#111827;color:white;border:none;border-radius:12px;text-decoration:none;font-size:15px;cursor:pointer;}}
.header{{background:#111827;color:white;border-radius:16px;padding:30px;box-shadow:0 12px 35px rgba(15,23,42,.12);}}
.header h1{{font-size:42px;margin:0 0 8px 0;}}
.meta{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-top:22px;}}
.meta div{{background:#1F2937;border-radius:12px;padding:14px;}}
.label{{color:#CBD5E1;font-size:13px;margin-bottom:5px;}}
.value{{font-weight:bold;}}
.workflow-progress-value{{display:flex;align-items:center;justify-content:space-between;gap:10px;font-weight:bold;}}
.progress-track{{display:block;height:6px;margin-top:9px;background:#374151;border-radius:999px;overflow:hidden;}}
.progress-fill{{display:block;height:100%;border-radius:999px;background:#3B82F6;}}
.progress-fill.complete{{background:#22C55E;}}
.health-score-line{{display:flex;align-items:baseline;justify-content:space-between;gap:10px;font-weight:bold;}}
.health-score-number{{font-size:19px;}}
.health-score-label{{font-size:13px;}}
.health-score-detail{{display:flex;gap:10px;flex-wrap:wrap;margin-top:7px;color:#CBD5E1;font-size:12px;}}
.health-track{{display:block;height:5px;margin-top:9px;background:#374151;border-radius:999px;overflow:hidden;}}
.health-fill{{display:block;height:100%;border-radius:999px;}}
.remarks{{margin-top:14px;background:#1F2937;border-radius:12px;padding:14px;}}
.next-step{{display:flex;align-items:center;justify-content:space-between;gap:22px;margin-top:24px;padding:24px 26px;background:#111827;color:white;border-radius:16px;box-shadow:0 12px 30px rgba(15,23,42,.14);}}
.next-step h2{{font-size:28px;margin:5px 0 8px;}}
.next-step p{{color:#D1D5DB;margin:0;line-height:1.5;}}
.next-kicker{{color:#93C5FD;text-transform:uppercase;letter-spacing:.1em;font-size:12px;font-weight:bold;}}
.next-action{{display:inline-block;min-width:120px;text-align:center;background:white;color:#111827;text-decoration:none;padding:13px 18px;border-radius:10px;font-weight:bold;}}
.next-step.complete{{display:block;background:#111827;border:1px solid #374151;}}
.next-step.complete .next-kicker{{color:#9CA3AF;}}
.next-step.complete h2{{display:flex;align-items:center;gap:10px;}}
.next-step.complete p{{color:#D1D5DB;}}
.complete-check{{display:inline-flex;align-items:center;justify-content:center;width:25px;height:25px;border-radius:999px;background:#DCFCE7;color:#166534;font-size:16px;line-height:1;}}
.workflow-timeline{{margin-top:24px;background:white;border:1px solid #E5E7EB;border-radius:16px;padding:24px;box-shadow:0 10px 25px rgba(15,23,42,.07);}}
.workflow-timeline h2{{font-size:26px;margin:0 0 20px;}}
.workflow-timeline h3{{font-size:17px;margin:24px 0 13px;color:#374151;}}
.timeline-scroll{{max-width:100%;overflow-x:auto;padding:2px 2px 10px;}}
.timeline-track{{display:flex;align-items:center;min-width:max-content;}}
.timeline-node{{display:grid;grid-template-columns:auto 1fr;column-gap:9px;row-gap:4px;align-items:center;width:172px;min-height:92px;padding:15px;border:1px solid #D1D5DB;border-radius:13px;background:#F9FAFB;}}
.timeline-marker{{grid-row:1/3;font-size:18px;font-weight:bold;}}
.timeline-label{{font-size:14px;font-weight:bold;line-height:1.25;}}
.timeline-state{{font-size:12px;color:#6B7280;}}
.timeline-connector{{padding:0 9px;color:#9CA3AF;font-size:19px;}}
.timeline-node.complete{{background:#F8FAFC;border-color:#BBF7D0;}}
.timeline-node.complete .timeline-marker{{color:#166534;}}
.timeline-node.current{{background:#111827;border-color:#2563EB;color:white;box-shadow:0 0 0 3px #DBEAFE;}}
.timeline-node.current .timeline-marker{{color:#60A5FA;}}
.timeline-node.current .timeline-state{{color:#BFDBFE;}}
.timeline-node.pending,.timeline-node.optional{{color:#6B7280;background:#F3F4F6;}}
.optional-track{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:11px;}}
.optional-node{{display:grid;grid-template-columns:auto 1fr;column-gap:8px;row-gap:3px;align-items:center;min-width:0;padding:13px;border:1px solid #D1D5DB;border-radius:12px;background:#F9FAFB;}}
.optional-node .timeline-marker{{grid-row:1/3;}}
.optional-node.linked{{border-color:#BBF7D0;}}
.optional-node.linked .timeline-marker{{color:#166534;}}
.optional-node.optional{{color:#6B7280;background:#F3F4F6;}}
.document-relationship{{margin-top:24px;background:white;border:1px solid #E5E7EB;border-radius:16px;padding:24px;box-shadow:0 10px 25px rgba(15,23,42,.07);}}
.relationship-heading{{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:20px;}}
.relationship-heading h2{{font-size:26px;margin:0;}}
.relationship-heading span{{color:#6B7280;font-size:13px;font-weight:bold;}}
.relationship-scroll{{max-width:100%;overflow-x:auto;padding:2px 4px 14px;}}
.relationship-tree,.relationship-tree ul{{list-style:none;margin:0;padding-left:28px;position:relative;}}
.relationship-tree{{padding-left:0;min-width:1080px;}}
.relationship-tree ul::before{{content:"";position:absolute;left:10px;top:0;bottom:22px;border-left:1px solid #CBD5E1;}}
.relationship-tree li{{position:relative;padding:9px 0 0 28px;}}
.relationship-tree>li{{padding-left:0;}}
.relationship-tree li::before{{content:"";position:absolute;left:10px;top:34px;width:18px;border-top:1px solid #CBD5E1;}}
.relationship-tree>li::before{{display:none;}}
.relationship-node{{width:250px;background:white;border:1px solid #D1D5DB;border-left:4px solid #DC2626;border-radius:12px;padding:13px;}}
.relationship-node.linked{{border-left-color:#16A34A;}}
.relationship-node.optional{{border-left-color:#9CA3AF;background:#F9FAFB;}}
.relationship-node.root{{background:#111827;color:white;border-color:#111827;width:280px;}}
.relationship-node-head{{display:flex;align-items:flex-start;justify-content:space-between;gap:9px;}}
.relationship-badge{{flex:none;padding:5px 8px;border-radius:999px;background:#FEE2E2;color:#991B1B;font-size:11px;font-weight:bold;}}
.relationship-node.linked .relationship-badge{{background:#DCFCE7;color:#166534;}}
.relationship-node.optional .relationship-badge{{background:#E5E7EB;color:#4B5563;}}
.relationship-node.root .relationship-badge{{background:#374151;color:white;}}
.relationship-record{{border-top:1px solid #E5E7EB;margin-top:10px;padding-top:9px;}}
.relationship-node.root .relationship-record{{border-top-color:#374151;}}
.relationship-identifier{{font-size:13px;font-weight:bold;word-break:break-word;}}
.relationship-actions{{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;}}
.relationship-actions a{{background:#111827;color:white;text-decoration:none;padding:6px 8px;border-radius:7px;font-size:11px;font-weight:bold;}}
.relationship-node.root .relationship-actions a{{background:white;color:#111827;}}
.docs{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px;margin-top:24px;}}
.doc-card{{background:white;border:1px solid #E5E7EB;border-radius:16px;padding:22px;box-shadow:0 10px 25px rgba(15,23,42,.07);}}
.doc-title{{font-size:17px;font-weight:bold;margin-bottom:14px;}}
.doc-no{{font-size:24px;font-weight:bold;margin-bottom:12px;color:#111827;}}
.badge{{display:inline-block;padding:7px 10px;border-radius:999px;font-size:13px;font-weight:bold;}}
.linked{{background:#DCFCE7;color:#166534;}}
.missing{{background:#FEE2E2;color:#991B1B;}}
.actions{{display:flex;gap:10px;margin-top:16px;}}
.actions a{{flex:1;text-align:center;background:#111827;color:white;text-decoration:none;padding:10px;border-radius:10px;font-weight:bold;}}
.section-title{{margin:34px 0 0;font-size:26px;}}
.operational-record+.operational-record{{border-top:1px solid #E5E7EB;margin-top:18px;padding-top:18px;}}
@media(max-width:780px){{body{{padding:18px}}.meta{{grid-template-columns:1fr}}.header h1{{font-size:32px}}.next-step{{align-items:flex-start;flex-direction:column}}.optional-track{{grid-template-columns:1fr 1fr}}}}
@media(max-width:480px){{.optional-track{{grid-template-columns:1fr}}}}
@media(max-width:780px){{.relationship-heading{{align-items:flex-start;flex-direction:column}}.relationship-tree{{min-width:0}}.relationship-tree,.relationship-tree ul{{padding-left:20px}}.relationship-tree ul::before{{left:6px}}.relationship-tree li{{padding-left:20px}}.relationship-tree li::before{{left:6px;width:14px}}.relationship-node,.relationship-node.root{{width:100%;max-width:100%}}}}
</style>
</head>
<body>
<div class="container">
<div class="nav-row">
<a class="btn" href="/">Dashboard</a>
<a class="btn" href="/shipment-list">Shipment List</a>
<a class="btn" href="/edit-shipment/{html_attr(shipment_no)}">Edit Shipment</a>
<a class="btn" href="/shipment-pdf/{html_attr(shipment_no)}">PDF</a>
</div>
<div class="header">
<h1>{html_text(shipment.get("shipment_no", ""))}</h1>
<div>{html_text(shipment.get("shipment_name", ""))}</div>
<div class="meta">
<div><div class="label">Date</div><div class="value">{html_text(shipment.get("shipment_date", ""))}</div></div>
<div><div class="label">Customer</div><div class="value">{html_text(shipment.get("customer", ""))}</div></div>
<div><div class="label">Buyer</div><div class="value">{html_text(shipment.get("buyer", ""))}</div></div>
<div><div class="label">Status</div><div class="value">{html_text(shipment.get("status", ""))}</div></div>
<div><div class="label">Linked Direct Documents</div><div class="value">{linked_count(shipment, workflow_datasets)} / {len(DOCUMENTS)}</div></div>
<div><div class="label">Operational Records</div><div class="value">{linked_operations} linked</div></div>
<div><div class="label">Workflow Progress</div><span class="workflow-progress-value"><span>{workflow_progress['completed']} / {workflow_progress['total']}</span><span>{workflow_progress['percentage']}%</span></span><span class="progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{workflow_progress['percentage']}"><span class="progress-fill{' complete' if workflow_progress['percentage'] == 100 else ''}" style="width:{workflow_progress['percentage']}%"></span></span></div>
<div><div class="label">Health Score</div><span class="health-score-line"><span class="health-score-number">{health_score['score']} / 100</span><span class="health-score-label" style="color:{health_color}">{health_score['label']}</span></span><span class="health-score-detail"><span>Required: {health_score['required_completed']} / {health_score['required_total']}</span><span>Optional: {health_score['optional_completed']} / {health_score['optional_total']}</span></span><span class="health-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{health_score['score']}"><span class="health-fill" style="width:{health_score['score']}%;background:{health_color}"></span></span></div>
</div>
<div class="remarks"><div class="label">Remarks</div><div>{html_text(shipment.get("remarks", ""))}</div></div>
</div>
{next_step_card}
{workflow_timeline}
{relationship_graph}
<div class="docs">{cards}</div>
<h2 class="section-title">Operational Records</h2>
<div class="docs operational-docs">{operational_cards}</div>
</div>
</body>
</html>
"""
    return HTMLResponse(html)


def draw_pdf_text(pdf, text, x, y, max_width=88):
    value = str(text or "")
    lines = []
    while len(value) > max_width:
        split_at = value.rfind(" ", 0, max_width + 1)
        if split_at <= 0:
            split_at = max_width
        lines.append(value[:split_at])
        value = value[split_at:].lstrip()
    lines.append(value)
    for line in lines:
        pdf.drawString(x, y, line)
        y -= 14
    return y


@router.get("/shipment-pdf/{shipment_no}")
def shipment_pdf(shipment_no: str):
    shipment = find_shipment(shipment_no)
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    navy = colors.HexColor("#111827")
    muted = colors.HexColor("#6B7280")

    def header():
        pdf.setFillColor(navy)
        pdf.rect(0, height - 82, width, 82, fill=1, stroke=0)
        pdf.setFillColor(colors.white)
        pdf.setFont("Helvetica-Bold", 22)
        pdf.drawString(42, height - 50, "SHIPMENT SUMMARY")
        return height - 112

    def ensure_space(y, needed=48):
        if y < needed:
            pdf.showPage()
            return header()
        return y

    y = header()
    pdf.setFillColor(navy)
    for label, value in [
        ("Shipment No", shipment.get("shipment_no", "")),
        ("Shipment Date", shipment.get("shipment_date", "")),
        ("Shipment Name", shipment.get("shipment_name", "")),
        ("Customer", shipment.get("customer", "")),
        ("Buyer", shipment.get("buyer", "")),
        ("Status", shipment.get("status", "")),
        ("Remarks", shipment.get("remarks", "")),
    ]:
        y = ensure_space(y)
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(42, y, f"{label}:")
        pdf.setFont("Helvetica", 10)
        y = draw_pdf_text(pdf, value, 145, y)
        y -= 3

    y -= 10
    y = ensure_space(y, 80)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(42, y, "Direct Document Status")
    y -= 24
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(42, y, "Document")
    pdf.drawString(245, y, "Record No")
    pdf.drawString(430, y, "Status")
    y -= 15
    for resolved in resolve_direct_documents(shipment):
        y = ensure_space(y)
        pdf.setFont("Helvetica", 9)
        pdf.drawString(42, y, resolved["document"]["label"])
        pdf.drawString(245, y, resolved["value"] or "-")
        pdf.drawString(430, y, "Linked" if resolved["exists"] else "Missing")
        y -= 16

    y -= 12
    y = ensure_space(y, 80)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(42, y, "Operational Records")
    y -= 24
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(42, y, "Record Type")
    pdf.drawString(245, y, "Record No")
    pdf.drawString(430, y, "Status")
    y -= 15
    for group in resolve_operational_records(shipment_no):
        matches = group["matches"] or [{"value": "-"}]
        for match in matches:
            y = ensure_space(y)
            pdf.setFont("Helvetica", 9)
            pdf.drawString(42, y, group["operational"]["label"])
            pdf.drawString(245, y, match["value"])
            pdf.drawString(430, y, "Linked" if group["matches"] else "Missing")
            y -= 16

    pdf.setFillColor(muted)
    pdf.setFont("Helvetica", 8)
    pdf.drawCentredString(width / 2, 24, "Generated by Trade Paper AI")
    pdf.save()
    buffer.seek(0)
    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{shipment_no}.pdf"'},
    )


@router.get("/edit-shipment/{shipment_no}", response_class=HTMLResponse)
def edit_shipment(shipment_no: str):
    for record in load_shipments():
        if record.get("shipment_no") == shipment_no:
            return render_form(
                record,
                f"/update-shipment/{html_attr(shipment_no)}",
                "Edit Shipment",
                "Update Shipment",
                show_shipment_no=True,
            )
    raise HTTPException(status_code=404, detail="Shipment not found")


@router.post("/update-shipment/{shipment_no}")
def update_shipment(
    shipment_no: str,
    shipment_date: str = Form(""),
    shipment_name: str = Form(""),
    customer: str = Form(""),
    buyer: str = Form(""),
    status: str = Form("Inquiry"),
    remarks: str = Form(""),
    quotation_no: str = Form(""),
    pi_no: str = Form(""),
    invoice_no: str = Form(""),
    packing_no: str = Form(""),
    si_no: str = Form(""),
    bl_no: str = Form(""),
    co_no: str = Form(""),
    inspection_no: str = Form(""),
    insurance_no: str = Form(""),
    weight_no: str = Form(""),
):
    def replace_shipment(shipments):
        for index, record in enumerate(shipments):
            if record.get("shipment_no") != shipment_no:
                continue
            updated = build_record(
                shipment_no, shipment_date, shipment_name, customer, buyer,
                status, remarks, quotation_no, pi_no, invoice_no, packing_no,
                si_no, bl_no, co_no, inspection_no, insurance_no, weight_no,
            )
            validate_shipment_values(updated)
            shipments[index] = updated
            return
        raise HTTPException(status_code=404, detail="Shipment not found")
    locked_json_mutation(SHIPMENT_FILE, [], replace_shipment, list)
    return RedirectResponse("/shipment-list", status_code=303)


@router.get("/delete-shipment/{shipment_no}")
def delete_shipment(shipment_no: str):
    return identifier_delete_confirmation("Shipment", "Shipment", shipment_no, SHIPMENT_FILE, "shipment_no", f"/delete-shipment/{shipment_no}", "/shipment-list")

@router.post("/delete-shipment/{shipment_no}")
def confirm_delete_shipment(shipment_no: str):
    return confirmed_identifier_delete("Shipment", "Shipment", shipment_no, SHIPMENT_FILE, "shipment_no", f"/delete-shipment/{shipment_no}", "/shipment-list", "/shipment-list")


@router.get("/shipment-data/{shipment_no}")
def shipment_data(shipment_no: str):
    for record in load_shipments():
        if record.get("shipment_no") == shipment_no:
            return record
    raise HTTPException(status_code=404, detail="Shipment not found")
