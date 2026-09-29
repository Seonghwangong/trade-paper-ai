"""Opt-in Healthchecks signal for a completed scheduler cycle; no customer data."""
import argparse
from dataclasses import dataclass, field
import http.client
import json
import os
import re
import ssl

PREFIX = 'TRADE_PAPER_PADDLE_LIVE_'
STATUSES = {'ok', 'warning', 'critical'}


@dataclass(frozen=True)
class Settings:
    path: str = field(repr=False)


def configuration():
    if os.environ.get(PREFIX + 'WATCHDOG') != '1':
        return None
    url = os.environ.get(PREFIX + 'WATCHDOG_URL', '')
    match = re.fullmatch(r'https://hc-ping\.com/([0-9a-f]{8}-[0-9a-f]{4}-'
                         r'[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', url)
    if match is None:
        raise ValueError('Invalid watchdog destination')
    return Settings('/' + match[1])


def notify(status):
    settings = configuration()
    if settings is None:
        return 'disabled'
    if status not in STATUSES:
        raise ValueError('Invalid watchdog outcome')
    path = settings.path + ('' if status == 'ok' else '/fail')
    connection = http.client.HTTPSConnection('hc-ping.com', timeout=5,
                                             context=ssl.create_default_context())
    try:
        # No body, redirect, proxy environment, retry, or provider response logging.
        connection.request('POST', path, body=b'', headers={'Content-Length': '0'})
        response = connection.getresponse()
        content = response.read(129)
        # Unknown/rate-limited checks can also return HTTP 200; require exact OK.
        if response.status != 200 or content.strip() != b'OK':
            raise ValueError('Watchdog signal not acknowledged')
    finally:
        connection.close()
    return 'sent'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', required=True, choices=sorted(STATUSES))
    args = parser.parse_args(argv)
    try:
        result = notify(args.status)
    except Exception:
        result = 'failed'
    print(json.dumps({'watchdog': result}))
    return 2 if result == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
