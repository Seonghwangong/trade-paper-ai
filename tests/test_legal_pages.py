from app import auth, landing, release_pages


def _body(response):
    return response.body.decode("utf-8")


def test_privacy_policy_matches_current_beta_data_handling():
    body = _body(release_pages.privacy_page())
    for text in (
        "Information We Collect",
        "Account information",
        "Business information",
        "Export document information",
        "Passwords, Sessions, and Email",
        "stored by Trade Paper AI only as hashes",
        "expire after 30 minutes",
        "Export Documents and Snapshots",
        "Storage and Retention",
        "Account deletion is handled by request",
        "Security",
        'href="/contact"',
    ):
        assert text in body
    assert "Legal placeholder" not in body
    assert "approved production privacy policy" not in body


def test_terms_describe_only_current_beta_service_and_user_responsibility():
    body = _body(release_pages.terms_page())
    for text in (
        "Agreement and Beta Notice",
        "Service Description",
        "Accounts",
        "Your Data and Documents",
        "Snapshots are designed to preserve saved document values",
        "Your Responsibilities",
        "Prohibited Use",
        "Availability and Changes",
        "Disclaimers and Limitation of Liability",
        "does not submit documents to customs authorities",
        'href="/contact"',
    ):
        assert text in body
    assert "Legal placeholder" not in body
    assert "approved production terms" not in body


def test_legal_pages_do_not_claim_unimplemented_services():
    combined = (_body(release_pages.privacy_page()) + _body(release_pages.terms_page())).casefold()
    for claim in (
        "team collaboration",
        "payment automation",
        "ai agent",
        "api integration",
        "electronic signature",
        "e-signature",
        "customs transmission",
    ):
        assert claim not in combined


def test_legal_pages_are_available_before_registration_and_linked_from_landing():
    assert {"/privacy", "/terms"}.issubset(auth.PUBLIC_PATHS)
    body = _body(landing.landing_page())
    assert '<a href="/privacy">Privacy</a>' in body
    assert '<a href="/terms">Terms</a>' in body
