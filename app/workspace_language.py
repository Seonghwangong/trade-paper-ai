"""UI-only language preference. Never translate saved data or PDF contents."""
from http.cookies import SimpleCookie
import re


def language(query, headers):
    explicit = query.get('lang', [''])[0]
    if explicit in ('ko', 'en'):
        return explicit
    cookie = SimpleCookie()
    try:
        cookie.load(headers.get(b'cookie', b'').decode('latin-1'))
        return 'ko' if cookie.get('tp_language') and cookie['tp_language'].value == 'ko' else 'en'
    except Exception:
        return 'en'


def inject(source, selected):
    # Script is same-origin and only replaces known UI labels; no document data
    # is sent to a translation provider.
    source = re.sub(r'<html\b[^>]*>', f'<html lang="{selected}">', source, count=1, flags=re.I)
    before, closing, after = source.rpartition('</body>')
    if not closing:
        return source
    return before + f'<script src="/static/workspace-language.js" data-language="{selected}" defer></script>' + closing + after
