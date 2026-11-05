import asyncio
import json

import pytest
from fastapi import FastAPI
from starlette.requests import Request

import app.auth as auth
import app.product as product


def request(path, method="GET", query=b""):
    return Request({"type": "http", "method": method, "scheme": "http",
                    "path": path, "raw_path": path.encode(), "query_string": query,
                    "headers": [], "client": ("127.0.0.1", 1), "server": ("testserver", 80)})


@pytest.mark.parametrize("query", ["", "?lang=ko&flow=setup"])
def test_login_from_stale_product_save_returns_to_get_form(tmp_path, monkeypatch, query):
    monkeypatch.setattr(auth, "USERS_FILE", tmp_path / "users.json")
    auth._reset_login_rate_limiter()
    auth.register("Test", "recovery@example.com", "sample-password", "sample-password")
    # This is a returning user whose session expired while editing a product.
    auth.login("recovery@example.com", "sample-password", "/")
    result = auth.login("recovery@example.com", "sample-password", "/save-product" + query)
    assert result.headers["location"] == "/product-form" + query
    assert "HttpOnly" in result.headers["set-cookie"]


@pytest.mark.parametrize("signed_in,method", [(False, "POST"), (False, "GET"), (True, "GET")])
def test_product_save_recovery_never_replays_a_write(tmp_path, monkeypatch, signed_in, method):
    products = tmp_path / "products.json"
    products.write_text(json.dumps([{"account_id": "other", "name": "Existing"}]))
    before = products.read_bytes()
    monkeypatch.setattr(product, "PRODUCT_FILE", products)
    user = {"account_id": "sample", "role": "Owner"} if signed_in else None
    monkeypatch.setattr(auth, "current_user", lambda request: user)
    monkeypatch.setattr(auth, "company_setup_complete", lambda *args: True)
    app = FastAPI()
    app.include_router(product.router)
    app.add_middleware(auth.AuthenticationMiddleware)

    async def call():
        messages = []
        async def receive():
            return {"type": "http.request", "body": b"name=Should-not-save", "more_body": False}
        async def send(message):
            messages.append(message)
        await app(request("/save-product", method, b"flow=setup&lang=ko").scope, receive, send)
        return messages

    messages = asyncio.run(call())
    assert messages[0]["status"] == 303
    location = dict(messages[0]["headers"])[b"location"]
    expected = b"/product-form?flow=setup&lang=ko" if signed_in else b"/login?next=%2Fproduct-form%3Fflow%3Dsetup%26lang%3Dko"
    assert location == expected
    assert products.read_bytes() == before


@pytest.mark.parametrize("target", ["//evil.example/save-product", "https://evil.example/save-product", "/\\evil.example/save-product"])
def test_product_recovery_does_not_allow_external_redirect(target):
    assert auth.safe_next_path(target) == "/"
