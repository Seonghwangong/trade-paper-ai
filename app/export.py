"""Read-only export presentation helpers; PDF generation remains in document modules."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from urllib.parse import unquote

from app.documents import DOCUMENT_DEFINITIONS
from app.storage import StorageError, data_path, load_json_strict


_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename_part(value: object) -> str:
    """Return a compact, filesystem-safe filename component."""
    normalized = _UNSAFE_FILENAME.sub("_", str(value or "").strip()).strip("._-")
    return normalized[:80]


def export_identifier(value: object) -> str:
    """Add the current year to simple PREFIX-NNN export names without changing stored IDs."""
    identifier = safe_filename_part(value)
    match = re.fullmatch(r"([A-Za-z]+)-(\d+)", identifier)
    if not match:
        return identifier
    return f"{match.group(1)}-{date.today().year}-{match.group(2)}"


def _pdf_identifier(definition, request_path: str) -> str:
    route = definition.pdf_route
    if not route or "{value}" not in route:
        return ""
    prefix, suffix = route.split("{value}", 1)
    if not request_path.startswith(prefix) or (suffix and not request_path.endswith(suffix)):
        return ""
    end = len(request_path) - len(suffix) if suffix else len(request_path)
    return unquote(request_path[len(prefix):end]).strip()


def pdf_export_filename(request_path: str, fallback: str = "document.pdf") -> str:
    """Build a safe filename from an existing document identifier and party metadata."""
    for definition in DOCUMENT_DEFINITIONS:
        identifier = _pdf_identifier(definition, request_path)
        if not identifier:
            continue
        try:
            records = load_json_strict(data_path(definition.storage_filename), [], list)
        except StorageError:
            records = []
        record = next(
            (
                item for item in records
                if isinstance(item, dict)
                and str(item.get(definition.identifier_field, "") or "").strip() == identifier
            ),
            {},
        )
        buyer = next(
            (record.get(field) for field in ("buyer", "buyer_name", "consignee") if record.get(field)),
            "",
        )
        seller = next(
            (record.get(field) for field in ("seller", "shipper", "exporter") if record.get(field)),
            "",
        )
        parts = [export_identifier(identifier)]
        for party in (buyer, seller):
            safe_party = safe_filename_part(party)
            if safe_party and safe_party not in parts:
                parts.append(safe_party)
        return "_".join(filter(None, parts)) + ".pdf"

    fallback_name = Path(str(fallback or "document.pdf")).name
    fallback_stem = safe_filename_part(Path(fallback_name).stem) or "document"
    return fallback_stem + ".pdf"
