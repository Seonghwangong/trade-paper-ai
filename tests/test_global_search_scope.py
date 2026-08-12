import app.main as main


def test_global_search_only_uses_explicit_scoped_datasets(monkeypatch):
    def raw_storage_must_not_be_read(*args, **kwargs):
        raise AssertionError("Global Search attempted a raw storage fallback")

    monkeypatch.setattr(main, "load_dashboard_json", raw_storage_must_not_be_read)

    results = main.global_search_results(
        "",
        company={"name": "Scoped Company"},
        customers=[{"company": "Scoped Customer"}],
        invoices=[{"invoice_no": "INV-A", "buyer": "Scoped Buyer", "items": []}],
        packing_lists=[{"packing_no": "PK-A", "invoice_no": "INV-A", "buyer": "Scoped Buyer"}],
        shipments=[{"shipment_no": "SHP-A", "shipment_name": "Scoped Shipment"}],
        certificates_of_origin=[{"co_no": "CO-A", "consignee": "Scoped Consignee"}],
        inspections=[{"inspection_no": "IC-A", "consignee": "Scoped Consignee"}],
        insurances=[{"insurance_no": "INS-A", "consignee": "Scoped Consignee"}],
        weights=[{"weight_no": "WT-A", "consignee": "Scoped Consignee"}],
    )

    assert {(result["module"], result["identifier"]) for result in results} == {
        ("Company", "Scoped Company"),
        ("Customers", "Scoped Customer"),
        ("Commercial Invoice", "INV-A"),
        ("Packing List", "PK-A"),
        ("Shipment", "SHP-A"),
        ("Certificate of Origin", "CO-A"),
        ("Inspection Certificate", "IC-A"),
        ("Insurance Certificate", "INS-A"),
        ("Weight Certificate", "WT-A"),
    }


def test_global_search_uninjected_and_unknown_sources_fail_closed(monkeypatch):
    unknown = {
        "module": "Future Document",
        "file": "future_documents.json",
        "identifier": "future_no",
        "title": "title",
        "fields": ["title"],
        "list": "/future-list",
    }
    monkeypatch.setattr(main, "SEARCH_SOURCES", [*main.SEARCH_SOURCES, unknown])
    monkeypatch.setattr(
        main,
        "load_dashboard_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Global Search attempted a raw storage fallback")
        ),
    )

    assert main.global_search_results("Future") == []
    account_a = main.global_search_results(
        "Shared", customers=[{"company": "Shared Customer A"}]
    )
    account_b = main.global_search_results(
        "Shared", customers=[{"company": "Shared Customer B"}]
    )
    assert [result["title"] for result in account_a] == ["Shared Customer A"]
    assert [result["title"] for result in account_b] == ["Shared Customer B"]
