from app.snapshot import assign_item_ids, preserve_omitted_item_fields


def test_item_ids_survive_reorder_delete_add_and_legacy_fallback():
    current = [
        {"item_id": "ITEM-A", "name": "Alpha", "carton": "1", "net_weight": "10"},
        {"item_id": "ITEM-B", "name": "Beta", "carton": "2", "net_weight": "20"},
    ]

    reordered = [{"name": "Beta"}, {"name": "Alpha"}, {"name": "Gamma"}]
    assign_item_ids(reordered, ["ITEM-B", "ITEM-A", ""], current)
    preserve_omitted_item_fields(reordered, current, ("carton", "net_weight"))
    assert [(item["name"], item["carton"], item["net_weight"]) for item in reordered] == [
        ("Beta", "2", "20"), ("Alpha", "1", "10"), ("Gamma", "", ""),
    ]
    assert reordered[0]["item_id"] == "ITEM-B"
    assert reordered[1]["item_id"] == "ITEM-A"
    assert reordered[2]["item_id"].startswith("ITEM-")

    after_delete = [{"name": "Gamma"}, {"name": "Beta"}]
    assign_item_ids(after_delete, [reordered[2]["item_id"], "ITEM-B"], reordered)
    preserve_omitted_item_fields(after_delete, reordered, ("carton", "net_weight"))
    assert [item["item_id"] for item in after_delete] == [reordered[2]["item_id"], "ITEM-B"]
    assert after_delete[1]["carton"] == "2"

    legacy = [{"name": "Legacy A", "carton": "7"}, {"name": "Legacy B", "carton": "8"}]
    legacy_update = [{"name": "Legacy A"}, {"name": "Legacy B"}]
    assign_item_ids(legacy_update, [], legacy)
    preserve_omitted_item_fields(legacy_update, legacy, ("carton",))
    assert [item["carton"] for item in legacy_update] == ["7", "8"]
    assert all(item["item_id"].startswith("ITEM-") for item in legacy_update)
