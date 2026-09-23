import pytest

from app.ui import _help_panel, _workflow_tip


@pytest.mark.parametrize("path", [
    "/contact", "/company", "/company-profile", "/sitemap.xml",
    "/subscription", "/privacy", "/terms", "/refund-policy",
])
def test_unrelated_pages_do_not_inherit_document_workflow_help(path):
    assert _workflow_tip(path) == ""
    assert 'class="tp-workflow-tip"' not in _help_panel(path)


@pytest.mark.parametrize("path,expected", [
    ("/co", "Return to Shipment Hub"),
    ("/co-form", "Return to Shipment Hub"),
    ("/co/CO-001", "Return to Shipment Hub"),
    ("/invoice-list", "Create a Packing List"),
    ("/si-form", "Link the Shipping Instruction"),
    ("/container-list", "Keep the Container linked"),
])
def test_document_routes_keep_their_own_workflow_help(path, expected):
    assert expected in _workflow_tip(path)
    assert 'class="tp-workflow-tip"' in _help_panel(path)
