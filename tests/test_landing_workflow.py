from app.landing import landing_page
from app.release import APP_VERSION, RELEASE_STAGE


def test_landing_matches_required_first_document_workflow_and_conversion_ctas():
    html = landing_page().body.decode()
    workflow = ("Company", "Buyer", "Product", "Invoice", "Packing List", "PDF")
    positions = [html.index(f'<div class="step">{step}</div>') for step in workflow]

    assert positions == sorted(positions)
    assert 'aria-label="Company, Buyer, Product, Invoice, Packing List, PDF"' in html
    assert "Complete the required setup first" in html
    assert '<a class="secondary" href="/register">Start Free</a>' in html
    assert '<a class="primary" href="/founding-beta">Apply for Founding Beta</a>' in html
    assert html.count('href="/demo">View Demo</a>') == 2
    assert '<a class="primary" href="/register">Create Your First Invoice</a>' in html
    assert '<a class="nav-link" href="/login">Sign in</a>' in html


def test_landing_hero_communicates_comfort_first_and_current_trust_values():
    html = landing_page().body.decode()
    assert "Comfort First trade documentation" in html
    assert "Enter once.<br>Reuse across documents." in html
    assert "Reuse Company, Buyer, and Product data." in html
    assert "Preserve stable document snapshots." in html
    assert "Create Unicode PDFs." in html
    assert "Follow a shipment-guided workflow." in html
    assert "Enter your information once. Reuse it across your export documents." in html
    assert 'aria-label="Product trust highlights"' in html
    for trust_value in (
        "✓ Unicode PDF",
        "✓ Stable Snapshots",
        "✓ Account Isolation",
        "✓ Guided Workflow",
        "✓ Founding Beta",
    ):
        assert f"<span>{trust_value}</span>" in html


def test_landing_describes_only_implemented_conversion_values():
    html = landing_page().body.decode()
    for text in (
        "Reusable Master Data",
        "Guided Next Steps",
        "Stable Snapshots",
        "Shipment Hub",
        "Unicode PDF",
        "Search",
        "Founding Beta",
        "Frequently Asked Questions",
        "Nothing is saved until you choose Save.",
    ):
        assert text in html
    for question in (
        "What documents are supported?",
        "Are previous PDFs affected?",
        "Does Trade Paper AI support Unicode?",
        "Is my company data isolated?",
        "What happens in Demo Mode?",
        "Can I delete my data?",
    ):
        assert f"<summary>{question}</summary>" in html


def test_landing_footer_exposes_beta_and_support_navigation():
    html = landing_page().body.decode()
    assert f"Version {APP_VERSION} · {RELEASE_STAGE} · Built for Exporters." in html
    for label, path in (
        ("Apply for Founding Beta", "/founding-beta"),
        ("Send Feedback", "/feedback"),
        ("Demo", "/demo"),
        ("About", "/about"),
        ("Release Notes", "/release-notes"),
        ("Version History", "/version-history"),
        ("Contact", "/contact"),
        ("Privacy", "/privacy"),
        ("Terms", "/terms"),
        ("Sign In", "/login"),
    ):
        assert f'<a href="{path}">{label}</a>' in html
