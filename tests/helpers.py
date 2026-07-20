from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


def route_snapshot(application):
    return [
        {
            "methods": sorted(route.methods or []),
            "path": route.path,
            "module": getattr(route.endpoint, "__module__", ""),
            "endpoint": getattr(route.endpoint, "__name__", ""),
        }
        for route in application.routes
    ]


def route_snapshot_digest(application):
    raw = json.dumps(route_snapshot(application), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def record_schema(records):
    if isinstance(records, dict):
        return tuple(sorted(records))
    if not isinstance(records, list):
        return ()
    return tuple(sorted({key for record in records if isinstance(record, dict) for key in record}))


def identifier_snapshot(definitions):
    return tuple(
        (definition.key, definition.storage_filename, definition.identifier_field, definition.identifier_prefix)
        for definition in definitions
    )


def normalize_html(value):
    text = value.body.decode() if hasattr(value, "body") else str(value)
    text = re.sub(r"\s+", " ", text)
    return re.sub(r">\s+<", "><", text).strip()


def assert_pdf_response(response, filename):
    assert response.media_type == "application/pdf"
    disposition = response.headers.get("content-disposition", "")
    assert filename in disposition
    assert len(response.body) > 100
    assert response.body.startswith(b"%PDF")


def file_hashes(directory: Path):
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in directory.glob("*.json")
    }
