import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import audit_log, auth, subscription, team


def request(account="A", role="Owner", email="owner@example.com"):
    return Request({"type": "http", "method": "POST", "path": "/team", "headers": [], "trade_paper_user": {"account_id": account, "company": "Alpha", "email": email, "role": role}})


def setup_files(tmp_path, monkeypatch, plan="Professional"):
    users = tmp_path / "users.json"
    users.write_text(json.dumps([
        {"account_id": "A", "company": "Alpha", "email": "owner@example.com", "password": auth._password_hash("ownerpass"), "role": "Owner", "plan": plan, "subscription_status": "Active"},
        {"account_id": "B", "company": "Beta", "email": "other@example.com", "password": auth._password_hash("otherpass"), "role": "Owner", "plan": "Professional", "subscription_status": "Active"},
    ]), encoding="utf-8")
    monkeypatch.setattr(auth, "USERS_FILE", users); monkeypatch.setattr(subscription, "USERS_FILE", users)
    monkeypatch.setattr(audit_log, "AUDIT_FILE", tmp_path / "audit_log.json")
    return users


def test_professional_invite_role_audit_and_account_isolation(tmp_path, monkeypatch):
    users = setup_files(tmp_path, monkeypatch)
    team.invite_user(request(), "staff@example.com", "temporary1", "Staff")
    invited = next(row for row in json.loads(users.read_text()) if row["email"] == "staff@example.com")
    assert invited["account_id"] == "A" and invited["role"] == "Staff" and invited["password"] != "temporary1"
    team.update_role(request(), "staff@example.com", "Viewer")
    assert next(row for row in json.loads(users.read_text()) if row["email"] == "staff@example.com")["role"] == "Viewer"
    with pytest.raises(HTTPException) as denied:
        team.update_role(request(), "other@example.com", "Staff")
    assert denied.value.status_code == 404
    actions = [row["action"] for row in json.loads((tmp_path / "audit_log.json").read_text())]
    assert actions == ["Invite", "Role Change"]


def test_team_management_requires_professional_and_owner_or_admin(tmp_path, monkeypatch):
    setup_files(tmp_path, monkeypatch, "Starter")
    with pytest.raises(HTTPException) as plan_denied:
        team.invite_user(request(), "new@example.com", "temporary1", "Staff")
    assert plan_denied.value.status_code == 403
    with pytest.raises(HTTPException) as role_denied:
        team.team_page(request(role="Manager"))
    assert role_denied.value.status_code == 403
    assert team.role_for_identity({"role": "Viewer"}) == "Viewer"
