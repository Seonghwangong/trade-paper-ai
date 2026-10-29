from app import landing, release, release_pages


def _body(response):
    return response.body.decode("utf-8")


def test_contact_uses_explicit_configuration_and_operational_fallbacks(monkeypatch):
    for name in (
        "TRADE_PAPER_CONTACT_EMAIL", "TRADE_PAPER_CONTACT_URL",
        "TRADE_PAPER_EMAIL_REPLY_TO", "TRADE_PAPER_EMAIL_FROM_ADDRESS",
        "TRADE_PAPER_PUBLIC_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    unconfigured = _body(release_pages.contact_page())
    assert "Email and website contact channels are not configured" in unconfigured
    assert release.BUSINESS_PHONE in unconfigured
    assert f'href="tel:{release.BUSINESS_PHONE}"' in unconfigured
    assert "hello@tradepaper.ai" not in unconfigured
    assert "www.tradepaper.ai" not in unconfigured
    assert "placeholder" not in unconfigured.casefold()

    monkeypatch.setenv("TRADE_PAPER_CONTACT_EMAIL", "support@operator.test")
    monkeypatch.setenv("TRADE_PAPER_CONTACT_URL", "https://support.operator.test/contact")
    configured = _body(release_pages.contact_page())
    assert 'href="mailto:support@operator.test"' in configured
    assert 'href="https://support.operator.test/contact"' in configured

    monkeypatch.delenv("TRADE_PAPER_CONTACT_EMAIL")
    monkeypatch.delenv("TRADE_PAPER_CONTACT_URL")
    monkeypatch.setenv("TRADE_PAPER_EMAIL_REPLY_TO", "reply@operator.test")
    monkeypatch.setenv("TRADE_PAPER_PUBLIC_BASE_URL", "https://app.operator.test")
    fallback = _body(release_pages.contact_page())
    assert "reply@operator.test" in fallback
    assert "https://app.operator.test" in fallback


def test_release_pages_match_current_public_capabilities():
    about = _body(release_pages.about_page())
    notes = _body(release_pages.release_notes_page())
    history = _body(release_pages.version_history_page())
    combined = " ".join((about, notes, history))
    assert release.APP_VERSION == "3.6.5"
    assert release.RELEASE_STAGE == "Public Release"
    for body in (about, notes, history):
        assert release.APP_VERSION in body
        assert release.RELEASE_STAGE in body
    assert "account isolation" in notes.casefold()
    assert "password recovery" in notes.casefold()
    for item in ("deployment checklist", "https", "backup", "smtp", "contact", "data dir", "session secret"):
        assert item in notes.casefold()
    for obsolete in (
        "business records are not yet isolated by user",
        "data isolation is not included",
        "roles and password recovery are not yet included",
        "authentication foundation",
    ):
        assert obsolete not in combined.casefold()


def test_landing_and_common_footer_expose_consistent_release_links():
    landing_body = _body(landing.landing_page())
    assert f"Version {release.APP_VERSION} · {release.RELEASE_STAGE}" in landing_body
    for path in ("/about", "/release-notes", "/version-history", "/contact", "/privacy", "/terms", "/refund-policy"):
        assert f'href="{path}"' in landing_body


def test_privacy_terms_and_refunds_use_the_same_contact_page():
    for page in (release_pages.privacy_page(), release_pages.terms_page(), release_pages.refund_policy_page()):
        body = _body(page)
        assert 'href="/contact"' in body
        assert "hello@tradepaper.ai" not in body
        assert "www.tradepaper.ai" not in body


def test_quick_start_has_localized_import_return_and_keeps_public_samples():
    from html.parser import HTMLParser
    from urllib.parse import urlsplit, parse_qs
    class Links(HTMLParser):
        def __init__(self):
            super().__init__(); self.hrefs = []; self.ids = []
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'a': self.hrefs.append(attrs.get('href', ''))
            if attrs.get('id'): self.ids.append(attrs['id'])
    for lang in ('en', 'ko'):
        body = _body(release_pages.getting_started_page(lang))
        links = Links(); links.feed(body)
        assert f'/import-document?lang={lang}' in links.hrefs
        registration = next(url for url in links.hrefs if url.startswith('/register?'))
        query = parse_qs(urlsplit(registration).query)
        assert query['next'] == [f'/import-document?lang={lang}']
        assert query['lang'] == [lang]
        assert f'/try-sample?lang={lang}' in links.hrefs
        assert 'sample-documents' in links.ids
        assert '/static/samples/commercial-invoice.pdf' in links.hrefs
        assert '/static/samples/packing-list.pdf' in links.hrefs
        assert f'/starter?lang={lang}' in links.hrefs
        assert body.index('/import-document?') < body.index('/demo?')
