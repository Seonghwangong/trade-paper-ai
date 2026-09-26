from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import stat
import threading
import zipfile

import pytest

from app import paddle_live_backup as backup
from app.paddle_live_store import PaddleLiveStore
from tests.test_paddle_live_store import store, bound, send, event, PRICE, SUB, TXN, NOW
from tests.test_paddle_live_adjustments import adjustment
from tests.test_paddle_live_reconcile import Provider, review


def contents(store):
    with store.connect() as db:
        return {name: db.execute('SELECT * FROM ' + name + ' ORDER BY 1').fetchall()
                for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


@pytest.fixture
def populated(store):
    bound(store)
    send(store, event(2))
    send(store, adjustment(action='chargeback'))
    review((store, Provider()), expected=review((store, Provider()))['digest'])
    # Unknown checkout and cancellation results are intentional durable guards.
    with store.connect() as db:
        db.execute("INSERT INTO live_operations VALUES ('account-B', 'checkout', 1000, NULL, NULL)")
        db.execute("INSERT INTO live_operations VALUES ('account-A', 'cancel', 1000, ?, NULL)", (SUB,))
    return store


def make(store, path=None):
    return backup.create_backup(store.path, path or store.path.parent / 'live.zip', price_id=PRICE)


def restore(result, destination):
    return backup.stage_restore(result['archive'], destination, price_id=PRICE,
                                expected_sha256=result['archive_sha256'])


def restored(directory):
    return PaddleLiveStore(directory / 'paddle_live.sqlite3', price_id=PRICE, environment='live', read_only=True)


def test_complete_roundtrip_preserves_access_holds_dedupe_audit_and_uncertain_operations(populated):
    store = populated
    before, raw = contents(store), store.path.read_bytes()
    result = make(store)
    assert store.path.read_bytes() == raw and contents(store) == before
    target = store.path.parent / 'restore'
    receipt = restore(result, target)
    copy = restored(target)
    assert contents(copy) == before
    assert copy.access_for_account('account-A', now=NOW).starter_access
    assert copy.account_state('account-B', now=NOW) == (True, None)
    assert not copy.access_for_account('account-A', now=datetime(2026, 10, 1, tzinfo=timezone.utc)).starter_access
    assert receipt['activated'] is False and receipt['requires_reconciliation'] is True
    assert json.loads((target / 'RESTORE_NOT_ACTIVATED.json').read_text())['activated'] is False
    assert stat.S_IMODE(Path(result['archive']).stat().st_mode) == 0o600
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in target.iterdir())
    writer = PaddleLiveStore(copy.path, price_id=PRICE, environment='live')
    assert send(writer, adjustment(action='chargeback')) == 'duplicate'
    send(writer, adjustment(11, action='chargeback', status='reversed', day=26))
    assert not copy.access_for_account('account-A', now=NOW).starter_access
    assert store.access_for_account('account-A', now=NOW).starter_access


def test_wal_committed_pages_included_and_uncommitted_transaction_excluded(populated):
    with closing(sqlite3.connect(populated.path)) as keeper:
        assert keeper.execute('PRAGMA journal_mode=WAL').fetchone() == ('wal',)
        keeper.execute('PRAGMA wal_autocheckpoint=0')
        keeper.execute('BEGIN')
        keeper.execute('SELECT COUNT(*) FROM events').fetchone()
        send(populated, adjustment(11, action='chargeback', day=26))
        assert Path(str(populated.path) + '-wal').stat().st_size > 0
        keeper.rollback()
        keeper.execute('BEGIN IMMEDIATE')
        keeper.execute("INSERT INTO live_operations VALUES ('not-committed', 'checkout', 1, NULL, NULL)")
        result = make(populated)
        target = populated.path.parent / 'wal-restore'
        restore(result, target)
        copy = restored(target)
        assert not copy.access_for_account('account-A', now=NOW).starter_access
        assert copy.account_state('not-committed', now=NOW) == (False, None)
        assert not Path(str(copy.path) + '-wal').exists()
        keeper.rollback()


def test_backup_remains_consistent_while_signed_events_commit(populated):
    with closing(sqlite3.connect(populated.path)) as keeper:
        keeper.execute('PRAGMA journal_mode=WAL')
        start, wrote = threading.Event(), threading.Event()
        def writer():
            start.wait(5)
            for n in range(100, 160):
                send(populated, adjustment(n, action='chargeback', day=26))
                wrote.set()
        with ThreadPoolExecutor(max_workers=1) as pool:
            job = pool.submit(writer)
            start.set()
            assert wrote.wait(5)
            result = make(populated)
            job.result(timeout=10)
        target = populated.path.parent / 'concurrent-restore'
        restore(result, target)
        copy = restored(target)
        state = contents(copy)
        assert len(state['live_adjustment_events']) >= 2
        assert len(state['events']) == len(state['live_adjustment_events']) + 2
        assert not copy.access_for_account('account-A', now=NOW).starter_access


def rewrite_archive(path, transform):
    with zipfile.ZipFile(path) as z:
        entries = {name: z.read(name) for name in z.namelist()}
    transform(entries)
    with zipfile.ZipFile(path, 'w') as z:
        for name, value in entries.items():
            z.writestr(name, value)


@pytest.mark.parametrize('change', ['db-byte', 'manifest-price', 'manifest-count', 'unknown-format',
                                  'extra-member', 'missing-member', 'bad-json', 'corrupt-zip'])
def test_invalid_archive_rejected_without_publishing_restore(populated, change):
    result = make(populated)
    archive = Path(result['archive'])
    def alter(entries):
        if change == 'db-byte':
            entries['ledger.sqlite3'] += b'tampered'
        elif change == 'extra-member':
            entries['../../unsafe'] = b'no'
        elif change == 'missing-member':
            del entries['ledger.sqlite3']
        elif change == 'bad-json':
            entries['manifest.json'] = b'not-json'
        else:
            data = json.loads(entries['manifest.json'])
            if change == 'manifest-price':
                data['metadata']['price_id'] = 'pri_' + 'z' * 26
            elif change == 'manifest-count':
                data['tables']['events'] += 1
            elif change == 'unknown-format':
                data['format'] = 'unknown'
            entries['manifest.json'] = json.dumps(data).encode()
    if change == 'corrupt-zip':
        archive.write_bytes(b'not-a-zip')
    else:
        rewrite_archive(archive, alter)
    target = populated.path.parent / 'restore'
    with pytest.raises((ValueError, zipfile.BadZipFile)):
        backup.stage_restore(archive, target, price_id=PRICE, expected_sha256=backup._sha(archive))
    assert not target.exists()
    assert not (populated.path.parent / 'unsafe').exists()


def test_independently_recorded_checksum_detects_replaced_archive(populated):
    result = make(populated)
    archive = Path(result['archive'])
    rewrite_archive(archive, lambda entries: entries.update({'unexpected': b'value'}))
    with pytest.raises(backup.BackupError, match='checksum'):
        restore(result, populated.path.parent / 'restore')


def test_checksum_required_for_restore_and_price_cannot_cross_environment(populated):
    result = make(populated)
    with pytest.raises(backup.BackupError, match='checksum required'):
        backup.stage_restore(result['archive'], populated.path.parent / 'restore', price_id=PRICE, expected_sha256=None)
    with pytest.raises(backup.BackupError, match='price mismatch'):
        backup.verify_backup(result['archive'], price_id='pri_' + 'z' * 26)


@pytest.mark.parametrize('mutation', ['sandbox', 'orphan-binding', 'orphan-snapshot', 'missing-receipt',
    'orphan-coverage', 'bad-audit', 'bad-operation', 'unknown-table', 'trigger', 'missing-table', 'missing-review-table'])
def test_logically_invalid_ledger_is_not_backed_up(populated, mutation):
    with populated.connect() as db:
        if mutation == 'sandbox':
            db.execute("UPDATE paddle_live_meta SET value='sandbox' WHERE key='environment'")
        elif mutation == 'orphan-binding':
            db.execute('DELETE FROM checkouts')
        elif mutation == 'orphan-snapshot':
            db.execute('DELETE FROM bindings')
        elif mutation == 'missing-receipt':
            db.execute("DELETE FROM events WHERE result='adjustment_review'")
        elif mutation == 'orphan-coverage':
            db.execute('DELETE FROM live_review_releases')
        elif mutation == 'bad-audit':
            db.execute("UPDATE live_review_releases SET evidence='{}'")
        elif mutation == 'bad-operation':
            db.execute("UPDATE live_operations SET result='ready' WHERE kind='checkout'")
        elif mutation == 'unknown-table':
            db.execute('CREATE TABLE unsupported (x TEXT)')
        elif mutation == 'trigger':
            db.execute('CREATE TRIGGER unsupported AFTER INSERT ON events BEGIN SELECT 1; END')
        elif mutation == 'missing-table':
            db.execute('DROP TABLE events')
        elif mutation == 'missing-review-table':
            db.execute('DROP TABLE live_review_releases')
    with pytest.raises((backup.BackupError, ValueError, KeyError)):
        make(populated)
    assert not (populated.path.parent / 'live.zip').exists()
    assert not list(populated.path.parent.glob('.paddle-backup-*'))


def test_legacy_schema_roundtrip_is_read_only_and_does_not_migrate(store):
    bound(store)
    send(store, event(2))
    with store.connect() as db:
        for table in ('live_review_coverage', 'live_review_releases', 'live_adjustment_events', 'live_operations'):
            db.execute('DROP TABLE ' + table)
    raw = store.path.read_bytes()
    result = make(store)
    assert store.path.read_bytes() == raw
    target = store.path.parent / 'restore'
    restore(result, target)
    assert contents(restored(target)) == contents(store)
    assert restored(target).access_for_account('account-A', now=NOW).starter_access


def test_missing_ownership_constraints_are_rejected_even_without_duplicate_rows(populated):
    with populated.connect() as db:
        db.execute('ALTER TABLE checkouts RENAME TO old_checkouts')
        db.execute('CREATE TABLE checkouts (transaction_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, price_id TEXT NOT NULL)')
        db.execute('INSERT INTO checkouts SELECT * FROM old_checkouts')
        db.execute('DROP TABLE old_checkouts')
    with pytest.raises(backup.BackupError, match='uniqueness'):
        make(populated)


def test_existing_review_hold_is_preserved_without_a_release(store):
    bound(store)
    send(store, event(2))
    send(store, adjustment())
    result = make(store)
    target = store.path.parent / 'restore'
    restore(result, target)
    assert not restored(target).access_for_account('account-A', now=NOW).starter_access


def test_corrupt_database_never_produces_a_backup(tmp_path):
    source = tmp_path / 'corrupt.sqlite3'
    source.write_bytes(b'not-a-database')
    with pytest.raises(sqlite3.DatabaseError):
        backup.create_backup(source, tmp_path / 'backup.zip', price_id=PRICE)
    assert not (tmp_path / 'backup.zip').exists()


def test_existing_destinations_and_symlinks_are_never_overwritten(populated):
    result = make(populated)
    old = Path(result['archive']).read_bytes()
    with pytest.raises(backup.BackupError, match='already exists'):
        make(populated)
    assert Path(result['archive']).read_bytes() == old
    destination = populated.path.parent / 'restore'
    destination.mkdir()
    (destination / 'keep').write_text('unchanged')
    with pytest.raises(backup.BackupError, match='must not exist'):
        restore(result, destination)
    assert (destination / 'keep').read_text() == 'unchanged'
    symlink = populated.path.parent / 'source-link'
    symlink.symlink_to(populated.path)
    with pytest.raises(backup.BackupError, match='symlinks'):
        backup.create_backup(symlink, populated.path.parent / 'other.zip', price_id=PRICE)
    dangling = populated.path.parent / 'dangling.zip'
    dangling.symlink_to(populated.path.parent / 'absent')
    with pytest.raises(backup.BackupError):
        make(populated, dangling)


def test_missing_source_does_not_create_a_database(tmp_path):
    source = tmp_path / 'missing.sqlite3'
    with pytest.raises(FileNotFoundError):
        backup.create_backup(source, tmp_path / 'backup.zip', price_id=PRICE)
    assert not source.exists() and not list(tmp_path.iterdir())


def test_publication_failure_does_not_leave_partial_restore(populated, monkeypatch):
    result = make(populated)
    link, calls = backup.os.link, []
    def fail_on_marker(src, dst):
        calls.append(str(dst))
        if str(dst).endswith('RESTORE_NOT_ACTIVATED.json'):
            raise OSError('disk failure')
        return link(src, dst)
    monkeypatch.setattr(backup.os, 'link', fail_on_marker)
    target = populated.path.parent / 'restore'
    with pytest.raises(OSError):
        restore(result, target)
    assert len(calls) == 2 and not target.exists()
    assert not list(populated.path.parent.glob('.paddle-restore-*'))


def test_racing_backup_publish_does_not_replace_winner(populated):
    def run(_):
        try:
            return make(populated)['archive_sha256']
        except (FileExistsError, backup.BackupError):
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert sum(r is not None for r in results) == 1
    assert backup.verify_backup(populated.path.parent / 'live.zip', price_id=PRICE)['archive_sha256'] in results


def test_size_limit_and_duplicate_zip_members_are_rejected(populated, monkeypatch):
    result = make(populated)
    monkeypatch.setattr(backup, 'MAX_DB', 1024)
    with pytest.raises(backup.BackupError):
        backup.verify_backup(result['archive'], price_id=PRICE)
    monkeypatch.undo()
    with zipfile.ZipFile(result['archive'], 'a') as z:
        with pytest.warns(UserWarning):
            z.writestr('manifest.json', '{}')
    with pytest.raises(backup.BackupError, match='members'):
        backup.verify_backup(result['archive'], price_id=PRICE)


def test_backup_timeout_is_bounded_and_does_not_publish(populated, monkeypatch):
    ticks = iter([0, 61])
    monkeypatch.setattr(backup.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(backup.BackupError, match='time or size'):
        make(populated)
    assert not (populated.path.parent / 'live.zip').exists()


def test_cli_roundtrip_and_redacted_failure(populated, capsys):
    archive = populated.path.parent / 'cli.zip'
    prefix = ['--price-id', PRICE]
    backup.main(prefix + ['backup', '--source', str(populated.path), '--output', str(archive)])
    result = json.loads(capsys.readouterr().out)
    backup.main(prefix + ['verify', '--archive', str(archive), '--sha256', result['archive_sha256']])
    assert json.loads(capsys.readouterr().out)['database_sha256'] == result['database_sha256']
    target = populated.path.parent / 'restore'
    backup.main(prefix + ['restore', '--archive', str(archive), '--sha256', result['archive_sha256'], '--output-dir', str(target)])
    assert json.loads(capsys.readouterr().out)['activated'] is False
    with pytest.raises(SystemExit) as caught:
        backup.main(prefix + ['backup', '--source', 'PRIVATE-MISSING-PATH', '--output', str(archive)])
    output = capsys.readouterr()
    assert caught.value.code == 2 and 'PRIVATE-MISSING-PATH' not in output.err
