from dataclasses import FrozenInstanceError

import pytest

import app.main as main
from app.documents import DOCUMENT_DEFINITIONS, document_url, get_document_definition


def test_all_document_definitions_are_unique_and_immutable():
    assert len(DOCUMENT_DEFINITIONS) == 18
    assert len({definition.key for definition in DOCUMENT_DEFINITIONS}) == 18
    assert len({definition.label for definition in DOCUMENT_DEFINITIONS}) == 18
    with pytest.raises(FrozenInstanceError):
        get_document_definition("invoice").label = "Changed"


def test_search_sources_are_registry_backed():
    assert [source["module"] for source in main.SEARCH_SOURCES] == [definition.label for definition in DOCUMENT_DEFINITIONS]
    for source, definition in zip(main.SEARCH_SOURCES, DOCUMENT_DEFINITIONS):
        assert source["file"] == definition.storage_filename
        assert source["identifier"] == definition.identifier_field
        assert source["title"] == definition.title_field
        assert source["fields"] == list(definition.searchable_fields)


def test_document_urls_encode_identifiers():
    assert document_url("shipment", "detail", "SHP A/1") == "/shipment/SHP%20A%2F1"
    assert document_url("quotation", "detail", "QT-001") == ""


def test_registered_metadata_routes_exist():
    registered = {route.path for route in main.app.routes}
    for definition in DOCUMENT_DEFINITIONS:
        for action in ("list", "form", "detail", "edit", "pdf", "delete"):
            route = getattr(definition, f"{action}_route")
            if not route:
                continue
            normalized = route.replace("{value}", "")
            assert any(candidate.replace("{shipment_no}", "").replace("{quotation_no}", "").replace("{pi_no}", "").replace("{invoice_no}", "").replace("{packing_no}", "").replace("{si_no}", "").replace("{booking_record_no}", "").replace("{container_record_no}", "").replace("{bl_no}", "").replace("{co_no}", "").replace("{inspection_no}", "").replace("{insurance_no}", "").replace("{weight_no}", "").replace("{customs_record_no}", "").replace("{index}", "") == normalized for candidate in registered), (definition.key, action, route)
