from datetime import datetime

def generate_packing_list(data: dict) -> dict:
    """
    data 예시:
    {
      "shipper": {"name": "..."},
      "consignee": {"name": "..."},
      "packing_no": "PL-2025-0001",
      "packages": [
        {"package_no": 1, "weight_kg": 20, "cbm": 0.03, "items": ["Steel Pipe"]}
      ]
    }
    """
    packages = data.get("packages", [])
    total_weight = 0
    total_cbm = 0

    normalized_packages = []
    for p in packages:
        weight = float(p.get("weight_kg", 0))
        cbm = float(p.get("cbm", 0))
        total_weight += weight
        total_cbm += cbm

        normalized_packages.append({
            "package_no": p.get("package_no"),
            "weight_kg": weight,
            "cbm": cbm,
            "items": p.get("items", []),
        })

    result = {
        "type": "packing_list",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "packing_no": data.get("packing_no", ""),
        "shipper": data.get("shipper", {}),
        "consignee": data.get("consignee", {}),
        "packages": normalized_packages,
        "total_packages": len(normalized_packages),
        "total_weight_kg": total_weight,
        "total_cbm": total_cbm,
    }
    return result
