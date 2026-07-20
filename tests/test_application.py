from collections import Counter

import app.main as main
from tests.helpers import normalize_html, route_snapshot_digest


EXPECTED_ROUTE_DIGEST = "d6b86061066eb27dcd8a8b70a83ec2e7e29d78b7945c6829306340d0eea2ead7"


def test_application_import_and_route_order():
    assert main.app is not None
    assert len(main.app.routes) == 190
    assert route_snapshot_digest(main.app) == EXPECTED_ROUTE_DIGEST


def test_no_route_duplicates():
    routes = [
        (method, route.path)
        for route in main.app.routes
        for method in sorted(route.methods or [])
        if method not in {"HEAD", "OPTIONS"}
    ]
    duplicates = {key: count for key, count in Counter(routes).items() if count > 1}
    assert duplicates == {}


def test_dashboard_and_search_render():
    dashboard = normalize_html(main.home())
    search = normalize_html(main.global_search(""))
    assert "Trade Paper AI" in dashboard
    assert dashboard.count("Shipment Summary") == 1
    assert "Global Search" in search
