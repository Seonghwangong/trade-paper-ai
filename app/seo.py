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
                    body = re.sub(r"</head\s*>", lambda _: public_head(path, lang) + "</head>", body, count=1, flags=re.I)
                encoded = body.encode("utf-8")
                headers = [(k, v) for k, v in start["headers"] if k.lower() != b"content-length"]
                start["headers"] = headers + [(b"content-length", str(len(encoded)).encode())]
                await send(start)
                await send({"type": "http.response.body", "body": encoded, "more_body": False})
                return
            await send(message)

        await self.app(scope, receive, send_metadata)
