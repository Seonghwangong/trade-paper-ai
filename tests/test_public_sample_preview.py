"""Public sample links must open previews without changing private exports."""
import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

from app.ui import ReleaseFooterMiddleware


STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def fetch(path, query=b""):
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=STATIC))

    @app.get("/invoice-pdf/INV-001")
    def private_export():
        return Response(b"%PDF-test-export", media_type="application/pdf")

    app.add_middleware(ReleaseFooterMiddleware)
    messages = []

    async def run():
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        await app({"type": "http", "asgi": {"version": "3.0"},
                   "http_version": "1.1", "method": "GET", "scheme": "http",
                   "path": path, "raw_path": path.encode(), "query_string": query,
                   "root_path": "", "server": ("test", 80), "client": ("test", 1),
                   "headers": []}, receive, send)

    asyncio.run(run())
    start = next(m for m in messages if m["type"] == "http.response.start")
    return start["status"], dict(start["headers"]), b"".join(m.get("body", b"") for m in messages)


@pytest.mark.parametrize("name", ["commercial-invoice.pdf", "packing-list.pdf"])
def test_public_sample_opens_inline_with_descriptive_filename_and_unchanged_bytes(name):
    status, headers, body = fetch(f"/static/samples/{name}")
    assert status == 200
    assert headers[b"content-type"] == b"application/pdf"
    assert headers[b"content-disposition"] == f'inline; filename="{name}"'.encode()
    assert body == (STATIC / "samples" / name).read_bytes()


@pytest.mark.parametrize("query,mode", [(b"", b"attachment;"), (b"view=1", b"inline;")])
def test_existing_document_download_and_explicit_preview_modes_are_preserved(query, mode):
    status, headers, body = fetch("/invoice-pdf/INV-001", query)
    assert status == 200
    assert headers[b"content-disposition"].startswith(mode)
    assert body == b"%PDF-test-export"
