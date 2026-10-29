from app.package_review import review_headers


def test_missing_headers_do_not_pass():
    assert all(row["status"] == "Not checked" for row in review_headers({}, {}))


def test_header_review_ignores_spacing_but_detects_wrong_reference_and_buyer():
    invoice = {"invoice_no": "INV-1", "seller": "ACME  Ltd", "buyer": "Buyer A"}
    packing = {"invoice_no": "INV-2", "seller": "acme ltd", "buyer": "Buyer B"}
    assert [row["status"] for row in review_headers(invoice, packing)] == ["Review needed", "Match", "Review needed"]
    assert invoice["seller"] == "ACME  Ltd"
