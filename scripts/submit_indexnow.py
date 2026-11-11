"""Notify IndexNow of explicitly selected public canonical changes. Dry-run default.

Run from repository root: python -m scripts.submit_indexnow URL [URL ...]
Add --submit --output NEW_RECEIPT.json to send once. Never runs on app startup.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.error import HTTPError
from urllib.request import Request, HTTPRedirectHandler, build_opener
from xml.etree import ElementTree

from app.search_submission import INDEXNOW_KEY, INDEXNOW_PATH
from app.seo import PUBLIC_PAGES, LOCALIZED_PAGES

ORIGIN = 'https://www.tradepaper.ai'
ENDPOINT = 'https://api.indexnow.org/indexnow'


def allowed_urls():
    template = Path(__file__).resolve().parents[1] / 'app' / 'static' / 'sitemap.xml'
    tree = ElementTree.fromstring(template.read_text().replace('__PUBLIC_BASE_URL__', ORIGIN))
    return {node.text for node in tree.iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')}


def payload_for(urls):
    selected = list(dict.fromkeys(urls))
    allowed = allowed_urls()
    def valid(url):
        parts = urlsplit(url)
        return (url in allowed and url.startswith(ORIGIN + '/') and parts.path in PUBLIC_PAGES
                and not parts.fragment and parts.query in ('', 'lang=ko')
                and (not parts.query or parts.path in LOCALIZED_PAGES))
    if not selected or any(not valid(url) for url in selected):
        raise ValueError('Only exact public canonical URLs in the Trade Paper AI sitemap are allowed.')
    return {'host': 'www.tradepaper.ai', 'key': INDEXNOW_KEY,
            'keyLocation': ORIGIN + INDEXNOW_PATH, 'urlList': selected}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Redirect refused; check the canonical host and endpoint.')


def submit(payload, output):
    # Revalidate even when called directly. Never forward arbitrary payload fields.
    payload = payload_for(payload['urlList'])
    report = {'time_utc': datetime.now(timezone.utc).isoformat(), 'endpoint': ENDPOINT,
              'urls': payload['urlList'], 'indexed': 'not verified', 'state': 'not_sent'}
    opener = build_opener(NoRedirect())
    # Exclusive receipt prevents repeating this run accidentally. No auto-retries.
    with Path(output).open('x', encoding='utf-8') as receipt:
        try:
            with opener.open(payload['keyLocation'], timeout=25) as response:
                if response.status != 200 or response.read(1024).decode().strip() != INDEXNOW_KEY:
                    raise ValueError('Deployed ownership file does not match; nothing submitted.')
            report['key_file_http'] = 200
            report['state'] = 'submission_outcome_unknown'
            request = Request(ENDPOINT, data=json.dumps(payload).encode(), method='POST',
                              headers={'Content-Type': 'application/json; charset=utf-8'})
            try:
                with opener.open(request, timeout=30) as response:
                    report['http'] = response.status
                    report['response'] = response.read(4096).decode('utf-8', 'replace')
            except HTTPError as error:
                report['http'] = error.code
                report['response'] = error.read(4096).decode('utf-8', 'replace')
            report['state'] = {200: 'received_not_indexed', 202: 'key_validation_pending'}.get(report['http'], 'rejected')
        except Exception as error:
            report['error'] = str(error)
        finally:
            json.dump(report, receipt, ensure_ascii=False, indent=2)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('urls', nargs='+')
    parser.add_argument('--submit', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    payload = payload_for(args.urls)
    if not args.submit:
        print(json.dumps({'dry_run': True, 'payload': payload}, indent=2))
        return
    if args.output is None:
        parser.error('--submit requires a new --output receipt path')
    report = submit(payload, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report.get('http') not in (200, 202):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
