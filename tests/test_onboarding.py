import json

from starlette.requests import Request

from app import buyer, onboarding, product, shipment


def request(account="A"):
    return Request({"type": "http", "method": "GET", "path": "/onboarding", "headers": [], "trade_paper_user": {"account_id": account, "email": f"{account}@test"}})


def test_first_login_progress_skip_complete_and_isolation(tmp_path, monkeypatch):
    files = {name: tmp_path / name for name in ("onboarding.json", "account_companies.json", "buyers.json", "products.json", "shipments.json", "users.json")}
    for path in files.values(): path.write_text("[]", encoding="utf-8")
    files["users.json"].write_text(json.dumps([{"account_id": "A"}, {"account_id": "B"}]))
    monkeypatch.setattr(onboarding, "ONBOARDING_FILE", files["onboarding.json"])
    monkeypatch.setattr(onboarding, "ACCOUNT_COMPANIES_FILE", files["account_companies.json"])
    monkeypatch.setattr(buyer, "BUYER_FILE", files["buyers.json"]); monkeypatch.setattr(buyer, "USERS_FILE", files["users.json"])
    monkeypatch.setattr(product, "PRODUCT_FILE", files["products.json"]); monkeypatch.setattr(product, "USERS_FILE", files["users.json"])
    monkeypatch.setattr(shipment, "SHIPMENT_FILE", files["shipments.json"]); monkeypatch.setattr(shipment, "USERS_FILE", files["users.json"], raising=False)
    assert onboarding.should_show("A") and onboarding.should_auto_show("A") and onboarding.progress("A")["percentage"] == 0
    assert 'aria-valuenow="0"' in onboarding.onboarding_page(request()).body.decode()
    onboarding.skip_onboarding(request(), "/")
    assert not onboarding.should_show("A") and onboarding.should_show("B")
    onboarding.mark("B", "completed_at")
    assert not onboarding.should_show("B")
