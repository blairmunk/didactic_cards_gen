from __future__ import annotations

import json
import os
import sqlite3
import stat
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from didactic_cards.adapters.sqlite_repository import SqliteRepository
from didactic_cards.adapters import sqlite_storage as storage
from didactic_cards.adapters.sqlite_storage import (
    RuntimeStorageLease,
    StorageDestinationExistsError,
    StoragePathError,
    StorageValidationError,
    backup_database,
    ensure_pre_migration_backup,
    inspect_database,
    restore_database,
)
from didactic_cards.domain.entities import Card, CardDeck


def _database_with_deck(tmp_path: Path, name: str = 'data') -> tuple[Path, str]:
    repository = SqliteRepository(tmp_path / name)
    deck = repository.create_deck('Исходная колода')
    repository.save_cards(deck.id, CardDeck([Card(front='Лицевая')]))
    database = repository.database_file
    repository.close()
    return database, deck.id


def _downgrade_to_12(database: Path) -> None:
    with closing(sqlite3.connect(database)) as connection:
        connection.execute('DROP TABLE schema_migrations')
        connection.execute('PRAGMA application_id = 0')
        connection.execute('PRAGMA user_version = 12')
        connection.commit()


def test_inspection_serialization_and_runtime_lease_close_are_idempotent(
    tmp_path,
):
    database, _ = _database_with_deck(tmp_path)

    payload = inspect_database(database).to_dict()
    lease = RuntimeStorageLease(database)
    lease.close()
    lease.close()

    assert payload['path'] == str(database.resolve())
    assert payload['healthy'] is True
    assert payload['sha256_scope'] == 'main-file-only'
    assert payload['counts']['decks'] == 1


def test_lock_target_type_is_rechecked_after_open(tmp_path, monkeypatch):
    target = tmp_path / 'lock-target'
    target.write_bytes(b'lock')
    real_fstat = storage.os.fstat

    monkeypatch.setattr(
        storage.os,
        'fstat',
        lambda descriptor: SimpleNamespace(st_mode=stat.S_IFDIR)
        if descriptor >= 0
        else real_fstat(descriptor),
    )

    with pytest.raises(StoragePathError, match='invalid file type'):
        storage._open_lock_target(target, directory=False)


def test_read_only_inspection_refuses_nonempty_wal_without_shm(tmp_path):
    database, _ = _database_with_deck(tmp_path)
    wal = database.with_name(f'{database.name}-wal')
    wal.write_bytes(b'unpaired WAL data')

    with pytest.raises(StoragePathError, match='non-empty WAL but no SHM'):
        inspect_database(database)

    assert wal.read_bytes() == b'unpaired WAL data'
    assert not database.with_name(f'{database.name}-shm').exists()


def test_semantic_inspection_accepts_valid_template_and_reports_bad_profile(
    tmp_path,
):
    database, deck_id = _database_with_deck(tmp_path)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            '''
            INSERT INTO trusted_templates(
                id, deck_id, version, source_hash, front_source, back_source,
                provenance, status, origin_template_id, created_at, approved_at
            ) VALUES (
                'valid-template', ?, 1,
                'd88632611564d20b87d31cb17164f24e83a9098a1e86ae428e53d0b5e3913214',
                '{{ content }}', '{{ content }}', 'local-author', 'quarantined',
                NULL, '2026-01-01T00:00:00+00:00', NULL
            )
            ''',
            (deck_id,),
        )
        connection.execute(
            '''
            INSERT INTO printer_profiles(
                key, name, duplex_mode, back_rotation_deg,
                front_offset_x_mm, front_offset_y_mm,
                back_offset_x_mm, back_offset_y_mm,
                back_border, registration_marks
            ) VALUES ('INVALID KEY', 'Printer', 'long-edge', 180,
                      0, 0, 0, 0, 0, 0)
            '''
        )
        connection.commit()

    report = inspect_database(database)

    assert 'invalid-trusted-template: valid-template' not in report.issues
    assert 'invalid-printer-profile: INVALID KEY' in report.issues


@pytest.mark.parametrize(
    ('target', 'mutation', 'expected_issue'),
    [
        (
            'decks',
            "UPDATE decks SET created_at = '2026-01-01T00:00:00'",
            'invalid-deck-metadata:',
        ),
        (
            'cards',
            'UPDATE cards SET version = 0',
            'invalid-card-metadata:',
        ),
    ],
)
def test_semantic_inspection_reports_invalid_entity_metadata(
    tmp_path, target, mutation, expected_issue
):
    database, _ = _database_with_deck(tmp_path)
    with closing(sqlite3.connect(database)) as connection:
        if target == 'cards':
            connection.execute('PRAGMA ignore_check_constraints = ON')
        connection.execute(mutation)
        connection.commit()

    report = inspect_database(database)

    assert any(issue.startswith(expected_issue) for issue in report.issues)


@pytest.mark.parametrize(
    'mutation',
    [
        'DELETE FROM schema_migrations',
        "UPDATE schema_migrations SET name = ''",
        "UPDATE schema_migrations SET applied_at = 'not-a-time'",
        'UPDATE schema_migrations SET version = 99',
    ],
)
def test_semantic_inspection_reports_invalid_migration_history(
    tmp_path, mutation
):
    database, _ = _database_with_deck(tmp_path)
    with closing(sqlite3.connect(database)) as connection:
        if 'version = 99' in mutation:
            connection.execute('PRAGMA ignore_check_constraints = ON')
        connection.execute(mutation)
        connection.commit()

    report = inspect_database(database)

    assert any(
        issue.startswith('invalid-migration-history:')
        for issue in report.issues
    )


def test_connection_validation_surfaces_integrity_result_and_quick_mode(
    monkeypatch,
):
    class FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def __iter__(self):
            return iter(self._rows)

    class FakeConnection:
        def __init__(self):
            self.statements = []

        def execute(self, statement):
            self.statements.append(statement)
            if statement == 'PRAGMA quick_check(1)':
                return FakeResult([('page 7 is damaged',)])
            if statement == 'PRAGMA foreign_key_check':
                return FakeResult([])
            raise AssertionError(statement)

    connection = FakeConnection()
    monkeypatch.setattr(storage, 'schema_structure_issues', lambda *a, **k: [])
    monkeypatch.setattr(
        storage,
        '_semantic_issues',
        lambda connection, **_kwargs: [],
    )

    issues = storage.connection_validation_issues(connection, quick=True)

    assert issues == ['integrity: page 7 is damaged']
    assert connection.statements[0] == 'PRAGMA quick_check(1)'


def test_backup_rejects_invalid_staged_snapshot_and_cleans_temporaries(
    tmp_path, monkeypatch
):
    database, _ = _database_with_deck(tmp_path)
    output = tmp_path / 'exports' / 'snapshot.sqlite3'

    def write_invalid_snapshot(source, destination):
        destination.write_bytes(b'not sqlite')

    monkeypatch.setattr(storage, '_copy_sqlite_snapshot', write_invalid_snapshot)

    with pytest.raises(StorageValidationError, match='sqlite-error'):
        backup_database(database, output)

    assert not output.exists()
    assert not output.with_suffix('.sqlite3.manifest.json').exists()
    assert not list(output.parent.glob('.*.tmp'))


@pytest.mark.parametrize('failure', [FileExistsError, OSError])
def test_backup_publication_failure_removes_both_hardlinks_and_temporaries(
    tmp_path, monkeypatch, failure
):
    database, _ = _database_with_deck(tmp_path)
    output = tmp_path / 'snapshot.sqlite3'
    manifest = output.with_suffix('.sqlite3.manifest.json')

    def fail_fsync(directory):
        raise failure('injected directory fsync failure')

    monkeypatch.setattr(storage, '_fsync_directory', fail_fsync)

    expected = (
        StorageDestinationExistsError if failure is FileExistsError else OSError
    )
    with pytest.raises(expected, match='injected|already exists'):
        backup_database(database, output)

    assert not output.exists()
    assert not manifest.exists()
    assert not list(tmp_path.glob(f'.{output.name}.*'))


def test_optional_manifest_can_be_absent_and_symlink_manifest_is_rejected(
    tmp_path
):
    database, _ = _database_with_deck(tmp_path)
    report = inspect_database(database)

    assert storage._verify_manifest(database, report) is None

    manifest = database.with_suffix('.sqlite3.manifest.json')
    target = tmp_path / 'manifest-target'
    target.write_text('{}', encoding='utf-8')
    manifest.symlink_to(target)
    with pytest.raises(StorageValidationError, match='must not be a symlink'):
        storage._verify_manifest(database, report)


def test_existing_pre_migration_backup_must_be_healthy(tmp_path):
    database, _ = _database_with_deck(tmp_path)
    _downgrade_to_12(database)
    destination = (
        database.parent / 'backups' / 'pre-migration-v12-stable.sqlite3'
    )
    destination.parent.mkdir()
    destination.write_bytes(b'broken backup')

    with pytest.raises(
        StorageValidationError, match='pre-migration backup is invalid'
    ):
        ensure_pre_migration_backup(database, 12)


def test_existing_pre_migration_backup_must_match_live_logical_state(tmp_path):
    database, _ = _database_with_deck(tmp_path)
    _downgrade_to_12(database)
    ensure_pre_migration_backup(database, 12)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE decks SET name = 'Изменено после backup'")
        connection.commit()

    with pytest.raises(StorageValidationError, match='does not match'):
        ensure_pre_migration_backup(database, 12)


def test_restore_source_rejects_wal_and_unhealthy_migratable_database(tmp_path):
    database, _ = _database_with_deck(tmp_path)
    backup = backup_database(database, tmp_path / 'source.sqlite3')
    wal = backup.path.with_name(f'{backup.path.name}-wal')
    wal.write_bytes(b'pending pages')
    with pytest.raises(StorageValidationError, match='without a WAL'):
        storage._restore_source_report(backup.path)
    wal.unlink()

    _downgrade_to_12(backup.path)
    with closing(sqlite3.connect(backup.path)) as connection:
        connection.execute('DROP TABLE printer_profiles')
        connection.commit()
    with pytest.raises(StorageValidationError, match='missing-table'):
        storage._restore_source_report(backup.path)


def test_staged_migration_validation_failure_rolls_back(tmp_path, monkeypatch):
    database, _ = _database_with_deck(tmp_path)
    _downgrade_to_12(database)

    def leave_incomplete_schema(connection, version):
        connection.execute('CREATE TABLE partial_migration(value TEXT)')
        connection.execute('PRAGMA user_version = 13')

    monkeypatch.setattr(storage, 'migrate_to_current', leave_incomplete_schema)

    with pytest.raises(StorageValidationError, match='staged migration failed'):
        storage._migrate_staged_database(database, 12)

    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 12
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type = 'table' AND name = 'partial_migration'"
        ).fetchone()[0] == 0


def test_publish_moves_and_removes_live_sidecars(tmp_path):
    live = tmp_path / 'cards.sqlite3'
    staged = tmp_path / '.restore.sqlite3'
    live.write_bytes(b'old main')
    staged.write_bytes(b'new main')
    for suffix in ('-wal', '-shm'):
        live.with_name(f'{live.name}{suffix}').write_bytes(suffix.encode())

    storage._publish_staged_database(staged, live)

    assert live.read_bytes() == b'new main'
    assert not staged.exists()
    assert not live.with_name(f'{live.name}-wal').exists()
    assert not live.with_name(f'{live.name}-shm').exists()
    assert not list(tmp_path.glob('*.rollback-*'))


def test_publish_failure_restores_live_sidecars(tmp_path, monkeypatch):
    live = tmp_path / 'cards.sqlite3'
    staged = tmp_path / '.restore.sqlite3'
    live.write_bytes(b'old main')
    staged.write_bytes(b'new main')
    sidecar_payloads = {'-wal': b'wal', '-shm': b'shm'}
    for suffix, payload in sidecar_payloads.items():
        live.with_name(f'{live.name}{suffix}').write_bytes(payload)
    real_replace = storage.os.replace

    def fail_staged_replace(source, destination):
        if Path(source) == staged and Path(destination) == live:
            raise OSError('injected swap failure')
        return real_replace(source, destination)

    monkeypatch.setattr(storage.os, 'replace', fail_staged_replace)

    with pytest.raises(OSError, match='injected swap failure'):
        storage._publish_staged_database(staged, live)

    assert live.read_bytes() == b'old main'
    for suffix, payload in sidecar_payloads.items():
        assert live.with_name(f'{live.name}{suffix}').read_bytes() == payload


def test_publish_preserves_original_error_when_sidecar_rollback_also_fails(
    tmp_path, monkeypatch
):
    live = tmp_path / 'cards.sqlite3'
    staged = tmp_path / '.restore.sqlite3'
    live.write_bytes(b'old main')
    staged.write_bytes(b'new main')
    live.with_name(f'{live.name}-wal').write_bytes(b'wal')
    real_replace = storage.os.replace

    def fail_swap_and_rollback(source, destination):
        source = Path(source)
        destination = Path(destination)
        if source == staged and destination == live:
            raise OSError('original publication error')
        if '.rollback-' in source.name and destination.name.endswith('-wal'):
            raise OSError('secondary rollback error')
        return real_replace(source, destination)

    monkeypatch.setattr(storage.os, 'replace', fail_swap_and_rollback)

    with pytest.raises(OSError, match='original publication error'):
        storage._publish_staged_database(staged, live)


@pytest.mark.parametrize(
    ('checkpoint', 'journal_mode', 'message'),
    [
        ((1, 0, 0), 'delete', 'WAL is busy'),
        ((0, 0, 0), 'wal', 'could not detach'),
    ],
)
def test_checkpoint_refuses_busy_or_attached_wal(
    tmp_path, monkeypatch, checkpoint, journal_mode, message
):
    class Result:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def execute(self, statement):
            if statement == 'PRAGMA wal_checkpoint(TRUNCATE)':
                return Result(checkpoint)
            if statement == 'PRAGMA journal_mode = DELETE':
                return Result((journal_mode,))
            raise AssertionError(statement)

        def close(self):
            pass

    monkeypatch.setattr(storage.sqlite3, 'connect', lambda *a, **k: Connection())

    with pytest.raises(storage.StorageBusyError, match=message):
        storage._checkpoint_live_database(tmp_path / 'cards.sqlite3')


def test_copy_regular_file_cleans_partial_destination_and_closes_descriptor(
    tmp_path
):
    source = tmp_path / 'source-directory'
    source.mkdir()
    destination = tmp_path / 'partial-copy'

    with pytest.raises(IsADirectoryError):
        storage._copy_regular_file(source, destination)

    assert not destination.exists()


def test_quarantine_captures_existing_sidecars_and_existing_backup_directory(
    tmp_path
):
    live = tmp_path / 'cards.sqlite3'
    live.write_bytes(b'main')
    for suffix in ('-wal', '-shm', '-journal'):
        live.with_name(f'{live.name}{suffix}').write_bytes(suffix.encode())
    backups = tmp_path / 'backups'
    backups.mkdir(mode=0o750)

    bundle = storage._quarantine_database_family(live)

    payload = json.loads(
        (bundle / 'forensic-manifest.json').read_text(encoding='utf-8')
    )
    assert set(payload['members']) == {
        'cards.sqlite3',
        'cards.sqlite3-wal',
        'cards.sqlite3-shm',
        'cards.sqlite3-journal',
    }
    assert backups.stat().st_mode & 0o777 == 0o750


def test_quarantine_copy_failure_keeps_completed_evidence(tmp_path, monkeypatch):
    live = tmp_path / 'cards.sqlite3'
    live.write_bytes(b'main')
    live.with_name(f'{live.name}-wal').write_bytes(b'wal')
    real_copy = storage._copy_regular_file
    calls = 0

    def fail_second_copy(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('forensic disk full')
        return real_copy(source, destination)

    monkeypatch.setattr(storage, '_copy_regular_file', fail_second_copy)

    with pytest.raises(OSError, match='forensic disk full'):
        storage._quarantine_database_family(live)

    bundles = list((tmp_path / 'backups').glob('forensic-*'))
    assert len(bundles) == 1
    assert (bundles[0] / live.name).read_bytes() == b'main'
    assert live.read_bytes() == b'main'


def test_restore_forensic_bundle_reinstates_main_and_all_sidecars(tmp_path):
    live = tmp_path / 'cards.sqlite3'
    live.write_bytes(b'broken replacement')
    bundle = tmp_path / 'forensic'
    bundle.mkdir()
    (bundle / live.name).write_bytes(b'original main')
    for suffix in ('-wal', '-shm', '-journal'):
        (bundle / f'{live.name}{suffix}').write_bytes(suffix.encode())

    storage._restore_forensic_bundle(bundle, live)

    assert live.read_bytes() == b'original main'
    for suffix in ('-wal', '-shm', '-journal'):
        assert live.with_name(f'{live.name}{suffix}').read_bytes() == suffix.encode()
    assert not list(tmp_path.glob('.forensic-rollback-*'))


def test_restore_rejects_hardlink_to_live_database(tmp_path):
    database, _ = _database_with_deck(tmp_path)
    hardlink = tmp_path / 'same-inode.sqlite3'
    os.link(database, hardlink)

    with pytest.raises(StoragePathError, match='must differ'):
        restore_database(database, hardlink)


def test_restore_detects_source_change_while_staging(tmp_path, monkeypatch):
    live, _ = _database_with_deck(tmp_path, 'live')
    source_db, _ = _database_with_deck(tmp_path, 'source')
    backup = backup_database(source_db, tmp_path / 'snapshot.sqlite3')
    real_report = storage._restore_source_report
    calls = 0

    def changed_second_report(candidate):
        nonlocal calls
        calls += 1
        report = real_report(candidate)
        if calls == 2:
            return replace(report, schema_version=12)
        return report

    monkeypatch.setattr(storage, '_restore_source_report', changed_second_report)

    with pytest.raises(StorageValidationError, match='changed while restore'):
        restore_database(live, backup.path)


def test_restore_rejects_structurally_damaged_staged_copy(
    tmp_path, monkeypatch
):
    live, _ = _database_with_deck(tmp_path, 'live')
    source, _ = _database_with_deck(tmp_path, 'source')
    backup = backup_database(source, tmp_path / 'snapshot.sqlite3')
    real_copy = storage._copy_sqlite_snapshot

    def damage_restore_stage(candidate, destination):
        real_copy(candidate, destination)
        if destination.name.startswith('.restore-'):
            with closing(sqlite3.connect(destination)) as connection:
                connection.execute('DROP TABLE printer_profiles')
                connection.commit()

    monkeypatch.setattr(storage, '_copy_sqlite_snapshot', damage_restore_stage)

    with pytest.raises(StorageValidationError, match='missing-table'):
        restore_database(live, backup.path)

    assert inspect_database(live).healthy


def test_restore_rejects_unmigrated_staged_schema_12_copy(
    tmp_path, monkeypatch
):
    source, _ = _database_with_deck(tmp_path, 'source')
    _downgrade_to_12(source)
    backup = backup_database(
        source, tmp_path / 'schema-12.sqlite3', expected_schema_version=12
    )
    live, _ = _database_with_deck(tmp_path, 'live')
    monkeypatch.setattr(storage, '_migrate_staged_database', lambda *a: None)

    with pytest.raises(StorageValidationError, match='missing-table'):
        restore_database(live, backup.path)


def test_failed_forensic_restore_reinstates_unhealthy_live_database(
    tmp_path, monkeypatch
):
    live, deck_id = _database_with_deck(tmp_path, 'live')
    with closing(sqlite3.connect(live)) as connection:
        connection.execute("UPDATE decks SET name = 'Unhealthy original'")
        connection.execute('DELETE FROM deck_render_settings')
        connection.commit()
    source, _ = _database_with_deck(tmp_path, 'source')
    backup = backup_database(source, tmp_path / 'snapshot.sqlite3')
    real_publish = storage._publish_staged_database
    publish_calls = 0

    def corrupt_first_publication(staged, target):
        nonlocal publish_calls
        publish_calls += 1
        real_publish(staged, target)
        if publish_calls == 1:
            with closing(sqlite3.connect(target)) as connection:
                connection.execute('DELETE FROM deck_render_settings')
                connection.commit()

    monkeypatch.setattr(
        storage, '_publish_staged_database', corrupt_first_publication
    )

    with pytest.raises(StorageValidationError, match='post-restore validation'):
        restore_database(live, backup.path, allow_unhealthy_live=True)

    assert publish_calls == 2
    report = inspect_database(live)
    assert report.healthy is False
    with closing(sqlite3.connect(live)) as connection:
        assert connection.execute(
            'SELECT name FROM decks WHERE id = ?', (deck_id,)
        ).fetchone()[0] == 'Unhealthy original'
