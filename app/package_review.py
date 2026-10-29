"""Read-only comparison of saved invoice and packing-list header values."""


def review_headers(invoice, packing):
    """Missing values are unverified, never interpreted as a successful check."""
    checks = []
    for field, label in (("invoice_no", "Invoice reference"), ("seller", "Seller"), ("buyer", "Buyer")):
        left = str(invoice.get(field, "") or "").strip()
        right = str(packing.get(field, "") or "").strip()
        if not left or not right:
            status = "Not checked"
        elif " ".join(left.split()).casefold() == " ".join(right.split()).casefold():
            status = "Match"
        else:
            status = "Review needed"
        checks.append({"label": label, "invoice": left, "packing": right, "status": status})
    return checks
