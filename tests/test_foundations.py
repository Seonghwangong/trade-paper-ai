import ast
from pathlib import Path

from app.documents import DOCUMENT_DEFINITIONS
from app.ui import badge, button, empty_state, form_footer, page_shell, status_badge, table, toolbar
from tests.helpers import identifier_snapshot, normalize_html


def test_ui_foundation_is_presentation_only():
    tree = ast.parse(Path("app/ui.py").read_text(encoding="utf-8"))
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not {"app.storage", "app.validation", "app.shipment", "fastapi"}.intersection(imports)
    assert "Dashboard" in page_shell("Title", "Content", navigation=button("Dashboard", "/"))
    assert "tp-badge-success" in status_badge("Linked", "success")
    assert badge("Linked", "success") == status_badge("Linked", "success")
    assert "tp-toolbar-count" in toolbar(button("New", "/new"), count=2)
    assert '<th scope="col">Identifier</th>' in table(["Identifier"], [["DOC-001"]])
    assert 'colspan="1"' in table(["Identifier"], [], empty_message="No records")
    assert "No records" in empty_state("No records")
    assert "Return to Shipment" in form_footer("/list", "Save", shipment_url="/shipment/SHP-001")


def test_document_registry_contains_no_business_logic_imports():
    tree = ast.parse(Path("app/documents.py").read_text(encoding="utf-8"))
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not {"app.storage", "app.validation", "app.shipment", "fastapi"}.intersection(imports)
    assert len(identifier_snapshot(DOCUMENT_DEFINITIONS)) == 18


def test_html_normalization():
    assert normalize_html("<div>\n  Test </div>") == "<div> Test </div>"
