import app.main as main


def test_dashboard_first_action_follows_required_setup_order():
    assert main.dashboard_first_action(False, [], []) == {
        "label": "Complete Company Setup", "url": "/company",
    }
    assert main.dashboard_first_action(True, [], []) == {
        "label": "Create First Buyer", "url": "/buyer-form",
    }
    assert main.dashboard_first_action(True, [{"name": "Buyer"}], []) == {
        "label": "Create First Product", "url": "/product-form",
    }
    assert main.dashboard_first_action(
        True, [{"name": "Buyer"}], [{"name": "Product"}],
    ) == {"label": "Create First Invoice", "url": "/invoice"}


def test_dashboard_first_action_uses_only_the_current_accounts_state():
    account_a = main.dashboard_first_action(True, [{"name": "Buyer A"}], [])
    account_b = main.dashboard_first_action(True, [], [{"name": "Product B"}])

    assert account_a["url"] == "/product-form"
    assert account_b["url"] == "/buyer-form"
