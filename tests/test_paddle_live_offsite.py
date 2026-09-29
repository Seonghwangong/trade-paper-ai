from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from app import paddle_live_backup as backup, paddle_live_offsite as remote
from tests.test_paddle_live_store import store, PRICE
from tests.test_paddle_live_jobs import setup, run as run_job, state as job_state, check as check_job

NOW = datetime(2026, 9, 29, 7, tzinfo=timezone.utc)


@pytest.fixture
def settings():
    return remote.Settings('a' * 24, 'synthetic-private-backups', 'ledger/',
                           '0' * 25, 'syntheticSecretNotToBeLogged000')


@pytest.fixture
def archive(store, tmp_path):
    result = backup.create_backup(store.path, tmp_path / 'source.zip', price_id=PRICE)
    return tmp_path / 'source.zip', result['archive_sha256']


class Provider:
    def __init__(self, settings):
        self.settings = settings
        self.calls = []
        self.content = None
        self.public = False
        self.bad_encryption = False
        self.bad_bytes = False
        self.wrong_version = False
        self.timeout = None
        self.caps = sorted(remote.CAPABILITIES)
        self.buckets = [{'id': settings.bucket_id, 'name': settings.bucket_name}]
        self.prefix = settings.prefix
        self.expiry = (NOW + timedelta(days=30)).timestamp() * 1000
        self.api_url = 'https://api005.backblazeb2.com'
        self.download_url = 'https://f005.backblazeb2.com'

    def request(self, method, url, headers=None, body=None, **kwargs):
        self.calls.append((method, url))
        if url.endswith('/b2_authorize_account'):
            data = {'authorizationToken': 'synthetic-account-token',
                    'applicationKeyExpirationTimestamp': self.expiry,
                    'apiInfo': {'storageApi': {'apiUrl': self.api_url,
                        'downloadUrl': self.download_url,
                        'allowed': {'buckets': self.buckets, 'capabilities': self.caps,
                                    'namePrefix': self.prefix}}}}
        elif 'b2_get_upload_url?' in url:
            data = {'bucketId': self.settings.bucket_id,
                    'uploadUrl': 'https://pod-005-000.backblaze.com/b2api/v4/b2_upload_file',
                    'authorizationToken': 'synthetic-upload-token'}
        elif method == 'POST':
            assert headers['Content-Length'] == str(len(body))
            assert headers['X-Bz-Server-Side-Encryption'] == 'AES256'
            if self.timeout != 'undelivered':
                self.content = body
            if self.timeout:
                raise TimeoutError('private provider error must not escape')
            data = {'bucketId': self.settings.bucket_id,
                    'fileName': headers['X-Bz-File-Name'], 'contentLength': len(body),
                    'contentSha1': hashlib.sha1(body).hexdigest(), 'fileId': 'synthetic-version-1',
                    'serverSideEncryption': {'mode': 'SSE-B2', 'algorithm': 'AES256'}}
        elif '/file/' in url:
            if not headers and not self.public:
                return 401, {}, b'{}'
            if self.content is None:
                return 404, {}, b'{"code":"file_not_present"}'
            return 200, {'x-bz-server-side-encryption': 'none' if self.bad_encryption else 'AES256',
                         'x-bz-file-id': 'wrong' if self.wrong_version else 'synthetic-version-1'}, (
                             b'corrupted' if self.bad_bytes else self.content)
        else:
            pytest.fail('Unexpected operation')
        return 200, {}, json.dumps(data).encode()

    @property
    def uploads(self):
        return sum(method == 'POST' for method, _ in self.calls)


@pytest.fixture
def provider(settings, monkeypatch):
    result = Provider(settings)
    monkeypatch.setattr(remote, '_request', result.request)
    return result


def mirror(archive, settings, **kwargs):
    options = dict(price_id=PRICE, expected_sha256=archive[1], settings=settings,
                   save_pending=lambda value: None, now=NOW)
    options.update(kwargs)
    return remote.mirror(archive[0], **options)


def test_disabled_does_not_read_credentials(monkeypatch):
    monkeypatch.delenv(remote.ENV, raising=False)
    monkeypatch.setenv(remote.ENV + '_B2_APPLICATION_KEY', 'invalid')
    assert remote.configuration() is None


def test_configuration_and_repr_do_not_expose_secret(settings, monkeypatch):
    monkeypatch.setenv(remote.ENV, '1')
    for suffix, value in zip(('BUCKET_ID', 'BUCKET_NAME', 'PREFIX', 'KEY_ID', 'APPLICATION_KEY'),
                            (settings.bucket_id, settings.bucket_name, settings.prefix, settings.key_id, settings.application_key)):
        monkeypatch.setenv(remote.ENV + '_B2_' + suffix, value)
    assert remote.configuration() == settings
    assert settings.application_key not in repr(settings) and settings.key_id not in repr(settings)
    monkeypatch.setenv(remote.ENV + '_B2_PREFIX', '../')
    with pytest.raises(ValueError):
        remote.configuration()


def test_real_archive_roundtrip_private_encryption_and_cache(archive, settings, provider):
    pending = []
    receipt = mirror(archive, settings, save_pending=pending.append)
    assert receipt['phase'] == 'verified' and len(pending) == 1 and pending[0]['phase'] == 'pending'
    assert provider.uploads == 1 and provider.content == archive[0].read_bytes()
    assert all(method == 'GET' or url.endswith('/b2_upload_file') for method, url in provider.calls)
    calls = len(provider.calls)
    cached = mirror(archive, settings, prior=receipt, now=NOW + timedelta(minutes=15))
    assert cached['attempt'] == 'reused' and cached['verified_at'] == receipt['verified_at']
    assert len(provider.calls) == calls
    renewed = mirror(archive, settings, prior=cached, now=NOW + timedelta(hours=6))
    assert renewed['attempt'] == 'verified' and provider.uploads == 1
    assert settings.application_key not in json.dumps(receipt)


@pytest.mark.parametrize('kind', ['deleteFiles', 'writeBuckets', 'writeBucketEncryption',
    'listAllBucketNames', 'writeKeys', 'missing_read', 'bucket', 'all_buckets', 'prefix', 'expiry', 'never'])
def test_overbroad_or_wrong_credentials_fail_before_transfer(archive, settings, provider, kind):
    if kind in ('deleteFiles', 'writeBuckets', 'writeBucketEncryption', 'listAllBucketNames', 'writeKeys'):
        provider.caps.append(kind)
    elif kind == 'missing_read':
        provider.caps.remove('readFiles')
    elif kind == 'bucket':
        provider.buckets[0]['id'] = 'b' * 24
    elif kind == 'all_buckets':
        provider.buckets = None
    elif kind == 'prefix':
        provider.prefix = ''
    else:
        provider.expiry = None if kind == 'never' else NOW.timestamp() * 1000
    with pytest.raises(ValueError):
        mirror(archive, settings)
    assert len(provider.calls) == 1 and provider.uploads == 0


@pytest.mark.parametrize('kind', ['public', 'bad_encryption', 'bad_bytes', 'wrong_version'])
def test_remote_failure_never_produces_verified_receipt(archive, settings, provider, kind):
    setattr(provider, kind, True)
    pending = []
    with pytest.raises(ValueError):
        mirror(archive, settings, save_pending=pending.append)
    assert len(pending) == 1 and pending[0]['phase'] == 'pending'


@pytest.mark.parametrize('delivered', [True, False])
def test_uncertain_upload_is_read_back_without_resubmission(archive, settings, provider, delivered):
    provider.timeout = 'delivered' if delivered else 'undelivered'
    pending = []
    with pytest.raises(TimeoutError):
        mirror(archive, settings, save_pending=pending.append)
    provider.timeout = None
    if delivered:
        assert mirror(archive, settings, prior=pending[0])['phase'] == 'verified'
    else:
        with pytest.raises(ValueError, match='prior upload uncertain'):
            mirror(archive, settings, prior=pending[0])
    assert provider.uploads == 1


@pytest.mark.parametrize('kind', ['checksum', 'corrupt', 'symlink', 'public_file', 'oversized'])
def test_untrusted_local_archive_never_reaches_network(archive, settings, provider, monkeypatch, kind):
    path, sha = archive
    if kind == 'checksum':
        sha = '0' * 64
    elif kind == 'corrupt':
        path.write_bytes(b'not a backup')
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
    elif kind == 'symlink':
        link = path.with_name('linked.zip'); link.symlink_to(path); path = link
    elif kind == 'public_file':
        path.chmod(0o644)
    else:
        monkeypatch.setattr(remote, 'MAX_ARCHIVE', 1)
    with pytest.raises((OSError, ValueError, backup.zipfile.BadZipFile)):
        mirror((path, sha), settings)
    assert provider.calls == []


@pytest.mark.parametrize('url', ['http://api005.backblazeb2.com', 'https://backblazeb2.com.evil.test',
    'https://user@api005.backblazeb2.com', 'https://api005.backblazeb2.com:444', 'https://127.0.0.1'])
def test_untrusted_endpoints_are_rejected(url):
    with pytest.raises(ValueError):
        remote._endpoint(url)


def test_storage_reply_cannot_redirect_tokens(archive, settings, provider):
    provider.download_url = 'https://evil.test'
    with pytest.raises(ValueError):
        mirror(archive, settings)
    assert len(provider.calls) == 1


def test_durable_pending_failure_prevents_post(archive, settings, provider):
    def fail(receipt):
        raise OSError('disk full')
    with pytest.raises(OSError):
        mirror(archive, settings, save_pending=fail)
    assert provider.uploads == 0


def test_rotated_key_forces_revalidation_and_clock_rollback_is_not_fresh(archive, settings, provider):
    receipt = mirror(archive, settings)
    with pytest.raises(ValueError, match='clock ahead'):
        mirror(archive, settings, prior=receipt, now=NOW - timedelta(minutes=6))
    assert mirror(archive, replace(settings, application_key='new-synthetic-key'), prior=receipt)['attempt'] == 'verified'


@pytest.mark.parametrize('mutation', [{'phase': 'invalid'}, {'sha256': 'invalid'},
    {'key_expires_at': float('inf')}, {'verified_at': 'invalid'}])
def test_corrupt_receipt_cannot_be_reused(archive, settings, provider, mutation):
    receipt = mirror(archive, settings)
    calls = len(provider.calls)
    with pytest.raises(ValueError):
        mirror(archive, settings, prior={**receipt, **mutation})
    assert len(provider.calls) == calls


def test_enabled_jobs_save_remote_verification_and_reuse(setup, settings, provider, monkeypatch):
    monkeypatch.setattr(remote, 'configuration', lambda: settings)
    result = run_job(setup)
    assert result['status'] == 'ok' and result['offsite_backup'] == 'verified'
    assert job_state(setup)['offsite']['phase'] == 'verified'
    assert check_job(setup) == result
    calls = len(provider.calls)
    assert run_job(setup)['offsite_backup'] == 'reused'
    assert len(provider.calls) == calls
    assert settings.application_key not in json.dumps(job_state(setup))


def test_provider_failure_is_critical_and_private_details_do_not_escape(setup, settings, provider, monkeypatch):
    monkeypatch.setattr(remote, 'configuration', lambda: settings)
    provider.timeout = 'undelivered'
    result = run_job(setup)
    assert result['status'] == 'critical' and result['offsite_backup'] == 'failed'
    assert any(issue['code'] == 'offsite_backup_failed' for issue in result['issues'])
    assert job_state(setup)['offsite']['phase'] == 'pending'
    assert check_job(setup) == result
    assert 'private provider' not in json.dumps(result)
    assert run_job(setup)['status'] == 'critical' and provider.uploads == 1
    assert len(list(setup[1].glob('ledger-*.zip'))) == 1


def test_process_killed_after_post_resumes_with_readback(setup, settings, provider, monkeypatch):
    monkeypatch.setattr(remote, 'configuration', lambda: settings)
    original = provider.request
    def crash(method, *args, **kwargs):
        reply = original(method, *args, **kwargs)
        if method == 'POST':
            raise SystemExit('simulated process death')
        return reply
    monkeypatch.setattr(remote, '_request', crash)
    with pytest.raises(SystemExit):
        run_job(setup)
    assert job_state(setup)['phase'] == 'running'
    assert job_state(setup)['offsite']['phase'] == 'pending'
    monkeypatch.setattr(remote, '_request', original)
    result = run_job(setup)
    assert result['offsite_backup'] == 'verified' and provider.uploads == 1
    assert any(item['code'] == 'previous_job_incomplete' for item in result['issues'])


def test_bad_opt_in_does_not_hide_valid_local_backup(setup, monkeypatch):
    monkeypatch.setenv(remote.ENV, '1')
    monkeypatch.delenv(remote.ENV + '_B2_KEY_ID', raising=False)
    result = run_job(setup)
    assert result['status'] == 'critical' and result['offsite_backup'] == 'failed'
    assert result['backup_attempt'] == 'created' and job_state(setup)['latest']


def test_http_transport_keeps_tls_verification_and_never_follows_redirect(monkeypatch):
    calls = []
    class Response:
        status = 302
        def read(self, limit):
            return b''
        def getheaders(self):
            return [('Location', 'https://evil.test')]
    class Connection:
        def __init__(self, hostname, *, timeout, context):
            assert context.check_hostname and context.verify_mode == remote.ssl.CERT_REQUIRED
            calls.append(hostname)
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            return Response()
        def close(self):
            calls.append('closed')
    monkeypatch.setattr(remote.http.client, 'HTTPSConnection', Connection)
    assert remote._request('GET', 'https://api005.backblazeb2.com/check')[0] == 302
    assert calls == ['api005.backblazeb2.com', 'closed']
