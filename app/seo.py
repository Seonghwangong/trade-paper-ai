"""Search metadata for public pages, without exposing workspace routes."""
import html
import os
import re
from urllib.parse import parse_qs

from app import email_delivery


PUBLIC_PAGES = (
    "/", "/getting-started", "/founding-beta", "/starter", "/terms",
    "/privacy", "/refund-policy", "/contact", "/try-sample",
    "/about", "/release-notes", "/version-history",
)
LOCALIZED_PAGES = {"/", "/getting-started", "/founding-beta", "/starter", "/try-sample"}
NOINDEX_PAGES = {
    "/login", "/register", "/forgot-password", "/reset-password",
    "/analytics/visit", "/founding-beta/thank-you", "/feedback/thank-you",
}

# Public descriptions summarize visible functionality, never private account data.
PAGE_COPY = {
    '/getting-started': {
        'en': ('Invoice & Packing List Guide | Trade Paper AI', 'Start export documents from a file, a fictional sample or saved company, buyer and item details. Review the Invoice, packing quantities and weights before downloading PDFs.'),
        'ko': ('인보이스·패킹리스트 작성 안내 | Trade Paper AI', '파일 가져오기, 가입 없는 가상 예시, 직접 입력 중 편한 방법으로 수출 서류 작성을 시작하세요. 송장과 포장 수량·중량을 확인하고 PDF로 내려받으세요.'),
    },
    '/try-sample': {
        'en': ('Try Invoice & Packing List PDFs Free | Trade Paper AI', 'Try fictional invoice and packing details without signup. Change quantities and packing values, then preview separate Commercial Invoice and Packing List PDFs.'),
        'ko': ('가입 없이 인보이스·패킹리스트 PDF 체험 | Trade Paper AI', '가상 수출 자료로 인보이스와 패킹리스트 작성을 체험하세요. 가입 없이 수량·포장 정보를 바꾸고 각각의 PDF를 확인할 수 있습니다.'),
    },
    '/starter': {
        'en': ('Starter Monthly Plan for Export Documents | Trade Paper AI', 'Review the Starter monthly plan for recurring export document preparation, unlimited documents and first-use guidance. Check subscription, renewal and cancellation terms.'),
        'ko': ('수출 서류 작성 Starter 월 구독 | Trade Paper AI', '반복적인 수출 서류 작성을 위한 Starter 월 구독을 확인하세요. 문서 무제한과 첫 사용 안내를 제공하며, 결제 전 요금·갱신·해지 조건을 확인할 수 있습니다.'),
    },
    '/founding-beta': {
        'en': ('First-use Help for Export Documents | Trade Paper AI', 'Request first-use help with a fictional Commercial Invoice and Packing List workflow. You can also explore the guide and free sample before creating an account.'),
        'ko': ('인보이스·패킹리스트 첫 사용 도움 | Trade Paper AI', '가상 송장·포장명세서 작성 흐름으로 첫 사용 도움을 받아보세요. 계정 생성 전에도 작성 안내와 무료 예시를 살펴볼 수 있습니다.'),
    },
}


def public_head(path, lang="en"):
    if path not in PUBLIC_PAGES:
        return ""
    try:
        base = email_delivery.public_base_url(os.environ)
    except email_delivery.EmailConfigurationError:
        return ""
    english = base + path
    korean = english + "?lang=ko"
    canonical = korean if lang == "ko" and path in LOCALIZED_PAGES else english
    tags = f'<link rel="canonical" href="{html.escape(canonical, quote=True)}">'
    if path in LOCALIZED_PAGES:
        for code, url in (("en", english), ("ko", korean), ("x-default", english)):
            tags += f'<link rel="alternate" hreflang="{code}" href="{html.escape(url, quote=True)}">'
    if path in PAGE_COPY:
        title, description = PAGE_COPY[path]['ko' if lang == 'ko' else 'en']
        for attribute, name, value in (
            ('name', 'description', description),
            ('property', 'og:type', 'website'), ('property', 'og:title', title),
            ('property', 'og:description', description), ('property', 'og:url', canonical),
            ('property', 'og:image', base + '/static/product-hunt/hero.png'),
            ('name', 'twitter:card', 'summary_large_image'), ('name', 'twitter:title', title),
            ('name', 'twitter:description', description),
            ('name', 'twitter:image', base + '/static/product-hunt/hero.png'),
        ):
            tags += f'<meta {attribute}="{name}" content="{html.escape(value, quote=True)}">'
    return tags


class SearchMetadataMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        query = parse_qs(scope.get("query_string", b"").decode("utf-8", "ignore"))
        lang = "ko" if query.get("lang", [""])[0] == "ko" else "en"
        start = None
        chunks = []

        async def send_metadata(message):
            nonlocal start
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                private_home = path == "/" and bool(scope.get("trade_paper_user"))
                if path in NOINDEX_PAGES or private_home:
                    headers.append((b"x-robots-tag", b"noindex, follow"))
                message = {**message, "headers": headers}
                content_type = dict(headers).get(b"content-type", b"")
                if (path in PUBLIC_PAGES and not private_home
                        and message["status"] == 200 and b"text/html" in content_type
                        and b"content-encoding" not in dict(headers)):
                    start = message
                    return
            if start is not None and message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                if message.get("more_body", False):
                    return
                body = b"".join(chunks).decode("utf-8")
                # The home renderer owns its canonical and structured-data URLs.
                if path != "/":
                    if path in PAGE_COPY:
                        title = html.escape(PAGE_COPY[path][lang][0])
                        body = re.sub(r'<title>.*?</title>', lambda _: '<title>' + title + '</title>', body, count=1, flags=re.I | re.S)
                    body = re.sub(r"</head\s*>", lambda _: public_head(path, lang) + "</head>", body, count=1, flags=re.I)
                encoded = body.encode("utf-8")
                headers = [(k, v) for k, v in start["headers"] if k.lower() != b"content-length"]
                start["headers"] = headers + [(b"content-length", str(len(encoded)).encode())]
                await send(start)
                await send({"type": "http.response.body", "body": encoded, "more_body": False})
                return
            await send(message)

        await self.app(scope, receive, send_metadata)
