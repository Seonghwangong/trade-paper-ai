from datetime import datetime

def generate_invoice(data: dict) -> dict:
    """
    data 예시:
    {
      "seller": {"name": "...", "address": "..."},
      "buyer": {"name": "...", "address": "..."},
      "invoice_no": "INV-2025-0001",
      "items": [
        {"description": "Steel Pipe", "qty": 10, "unit_price": 100}
      ],
      "currency": "USD"
    }
    """
    items = data.get("items", [])
    currency = data.get("currency", "USD")

    subtotal = 0
    normalized_items = []
    for it in items:
        qty = float(it.get("qty", 0))
        unit_price = float(it.get("unit_price", 0))
        amount = qty * unit_price
        subtotal += amount
        normalized_items.append({
            "description": it.get("description", ""),
            "qty": qty,
            "unit_price": unit_price,
            "amount": amount,
        })

    result = {
        "type": "invoice",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "invoice_no": data.get("invoice_no", ""),
        "seller": data.get("seller", {}),
        "buyer": data.get("buyer", {}),
        "currency": currency,
        "items": normalized_items,
        "subtotal": subtotal,
    }
    return result
