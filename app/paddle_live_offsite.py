"""Default-off B2 backup mirroring. No delete, key management or bucket writes."""
import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import ssl
import stat
import tempfile
from urllib.parse import quote, urlencode, urlsplit

from app.paddle_live_backup import verify_backup, _private_file
from app.paddle_subscription_policy import _instant

ENV = 'TRADE_PAPER_PADDLE_LIVE_OFFSITE'
MAX_ARCHIVE = 16 * 1024 * 1024
CAPABILITIES = {'listBuckets', 'readBuckets', 'listFiles', 'readFiles',
                'writeFiles', 'readBucketEncryption'}


@dataclass(frozen=True)
class Settings:
    bucket_id: str
    bucket_name: str
    prefix: str
    key_id: str = field(repr=False)
    application_key: str = field(repr=False)

    @property
    def identity(self):
        return hashlib.sha256(json.dumps([self.bucket_id, self.bucket_name,
            self.prefix, self.key_id, self.application_key]).encode()).hexdigest()


def configuration():
    if os.environ.get(ENV) != '1':
        return None
    values = [os.environ[ENV + '_B2_' + name] for name in
              ('BUCKET_ID', 'BUCKET_NAME', 'PREFIX', 'KEY_ID', 'APPLICATION_KEY')]
    patterns = [r'[a-f0-9]{24}', r'[A-Za-z0-9][A-Za-z0-9-]{5,62}',
                r'(?:[a-z0-9][a-z0-9_-]{0,63}/){1,4}',
                r'[A-Za-z0-9]{20,40}', r'[A-Za-z0-9/+_-]{20,100}']
    if any(not re.fullmatch(pattern, value) for pattern, value in zip(patterns, values)):
        raise ValueError('Invalid offsite configuration')
    return Settings(*values)


def _endpoint(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.fragment
            or parsed.port not in (None, 443)
            or not re.fullmatch(r'[a-z0-9-]+\.(backblazeb2|backblaze)\.com', parsed.hostname or '')):
        raise ValueError('Invalid storage endpoint')
    return parsed


def validate_receipt(value):
    if (not isinstance(value, dict) or value.get('phase') not in ('pending', 'verified')
            or not re.fullmatch(r'[a-f0-9]{64}', value.get('identity', ''))
            or not re.fullmatch(r'[a-f0-9]{64}', value.get('sha256', ''))):
        raise ValueError('Invalid offsite receipt')
    started = _instant(value['started_at'])
    if value['phase'] == 'verified':
        expiry = value['key_expires_at']
        if (_instant(value['verified_at']) < started or type(expiry) not in (int, float)
                or not math.isfinite(expiry) or value.get('attempt') not in ('verified', 'reused')):
            raise ValueError('Invalid offsite verification receipt')


def _request(method, url, headers=None, body=None, *, limit=1024 * 1024):
    parsed = _endpoint(url)
    connection = http.client.HTTPSConnection(parsed.hostname, timeout=20,
                                             context=ssl.create_default_context())
    try:
        # No redirects, proxy environment, response/error logging or retries.
        connection.request(method, parsed.path + ('?' + parsed.query if parsed.query else ''),
                           body=body, headers=headers or {})
        response = connection.getresponse()
        content = response.read(limit + 1)
        if len(content) > limit:
            raise ValueError('Storage response too large')
        return response.status, {k.lower(): v for k, v in response.getheaders()}, content
    finally:
        connection.close()


class B2Client:
    def __init__(self, settings):
        self.settings = settings

    def exchange(self, payload, name, *, now, before_upload, allow_upload):
        cfg = self.settings

        def api(method, url, headers=None, body=None):
            status, _, raw = _request(method, url, headers, body)
            if status != 200:
                raise ValueError('Storage API failed')
            return json.loads(raw)

        basic = base64.b64encode((cfg.key_id + ':' + cfg.application_key).encode()).decode()
        auth = api('GET', 'https://api.backblazeb2.com/b2api/v4/b2_authorize_account',
                   {'Authorization': 'Basic ' + basic})
        storage = auth['apiInfo']['storageApi']
        allowed = storage['allowed']
        buckets, caps = allowed.get('buckets'), set(allowed['capabilities'])
        expiry = auth.get('applicationKeyExpirationTimestamp')
        if (not isinstance(buckets, list) or len(buckets) != 1
                or buckets[0]['id'] != cfg.bucket_id
                or buckets[0].get('name') != cfg.bucket_name
                or allowed.get('namePrefix') != cfg.prefix
                or not {'readFiles', 'writeFiles'} <= caps or not caps <= CAPABILITIES
                or type(expiry) not in (int, float) or not now.timestamp() < expiry / 1000
                or expiry / 1000 > now.timestamp() + 1000 * 86400):
            raise ValueError('Storage key scope or expiration invalid')
        headers = {'Authorization': auth['authorizationToken']}
        base = storage['apiUrl'].rstrip('/')
        download = storage['downloadUrl'].rstrip('/') + '/file/' + cfg.bucket_name + '/' + quote(name, safe='/')
        _endpoint(base); _endpoint(download)

        def fetch():
            return _request('GET', download, headers, limit=MAX_ARCHIVE)

        status, received_headers, received = fetch()
        uploaded_id = None
        if status == 404:
            missing = json.loads(received)
            # Download-by-name documents not_found, not file_not_present.
            if (not isinstance(missing, dict) or missing.get('code') != 'not_found'
                    or missing.get('status') != 404 or not allow_upload):
                raise ValueError('Remote absence unconfirmed or prior upload uncertain')
            upload = api('GET', base + '/b2api/v4/b2_get_upload_url?' +
                         urlencode({'bucketId': cfg.bucket_id}), headers)
            if upload['bucketId'] != cfg.bucket_id:
                raise ValueError('Storage bucket mismatch')
            _endpoint(upload['uploadUrl'])
            sha1 = hashlib.sha1(payload).hexdigest()
            # Must be durable before POST. A killed/ambiguous upload is only read
            # back next time, never silently submitted for a second version.
            before_upload()
            uploaded = api('POST', upload['uploadUrl'], {
                'Authorization': upload['authorizationToken'],
                'X-Bz-File-Name': quote(name, safe='/'), 'Content-Type': 'application/zip',
                'Content-Length': str(len(payload)), 'X-Bz-Content-Sha1': sha1,
                'X-Bz-Server-Side-Encryption': 'AES256'}, payload)
            if (uploaded['bucketId'] != cfg.bucket_id or uploaded['fileName'] != name
                    or uploaded['contentLength'] != len(payload) or uploaded['contentSha1'] != sha1
                    or uploaded.get('serverSideEncryption') != {'mode': 'SSE-B2', 'algorithm': 'AES256'}):
                raise ValueError('Storage upload receipt invalid')
            uploaded_id = uploaded['fileId']
            status, received_headers, received = fetch()
        if (status != 200 or received != payload
                or received_headers.get('x-bz-server-side-encryption') != 'AES256'
                or not received_headers.get('x-bz-file-id')
                or uploaded_id is not None and received_headers['x-bz-file-id'] != uploaded_id):
            raise ValueError('Storage download verification failed')
        # Check that authenticated success is not coming from a public bucket.
        anonymous, _, _ = _request('GET', download, limit=MAX_ARCHIVE)
        if anonymous not in (401, 403):
            raise ValueError('Storage privacy not confirmed')
        return received, expiry / 1000


def mirror(archive, *, price_id, expected_sha256, settings, prior=None,
           save_pending, now=None, max_age_hours=6, client=None):
    """Called under the existing job lock. Returned receipts contain no secrets."""
    now = datetime.now(timezone.utc) if now is None else now
    if now.tzinfo is None or type(max_age_hours) is not int or not 1 <= max_age_hours <= 24:
        raise ValueError('Invalid offsite clock or interval')
    if not re.fullmatch(r'[a-f0-9]{64}', expected_sha256):
        raise ValueError('Invalid backup checksum')
    name = settings.prefix + expected_sha256 + '.zip'
    receipt = {'identity': settings.identity, 'sha256': expected_sha256,
               'phase': 'pending', 'started_at': now.isoformat()}
    if prior is not None:
        validate_receipt(prior)
    matching = (isinstance(prior, dict) and prior.get('identity') == settings.identity
                and prior.get('sha256') == expected_sha256)
    fd = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or not 0 < info.st_size <= MAX_ARCHIVE):
            raise ValueError('Private bounded archive required')
        payload = stream.read(MAX_ARCHIVE + 1)
    if len(payload) > MAX_ARCHIVE or hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError('Backup changed before transfer')
    with tempfile.TemporaryDirectory(prefix='paddle-offsite-') as directory:
        local = Path(directory) / 'verified.zip'
        with _private_file(local) as stream:
            stream.write(payload)
        verify_backup(local, price_id=price_id, expected_sha256=expected_sha256)
        if matching and prior.get('phase') == 'verified':
            age = (now - _instant(prior['verified_at'])).total_seconds()
            if age < -300:
                raise ValueError('Offsite clock ahead')
            if 0 <= age < max_age_hours * 3600 and prior['key_expires_at'] > now.timestamp():
                return {**prior, 'attempt': 'reused'}
        transport = B2Client(settings) if client is None else client
        received, expires = transport.exchange(payload, name, now=now,
            before_upload=lambda: save_pending(receipt),
            allow_upload=not (matching and prior.get('phase') == 'pending'))
        if hashlib.sha256(received).hexdigest() != expected_sha256:
            raise ValueError('Remote checksum mismatch')
        remote = Path(directory) / 'downloaded.zip'
        with _private_file(remote) as stream:
            stream.write(received)
        # Reinspect the actual remote ZIP, including SQLite integrity/schema.
        verify_backup(remote, price_id=price_id, expected_sha256=expected_sha256)
    return {**receipt, 'phase': 'verified', 'verified_at': now.isoformat(),
            'key_expires_at': expires, 'attempt': 'verified'}
