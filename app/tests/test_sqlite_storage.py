from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

from didactic_cards.adapters import sqlite_repository as sqlite_repository_module
from didactic_cards.adapters.sqlite_repository import (
    SQLITE_SCHEMA_VERSION,
    SqliteRepository,
    UnsupportedSqliteSchemaError,
)
from didactic_cards.adapters.sqlite_schema import (
    MINIMUM_MIGRATABLE_SCHEMA_VERSION,
    initialize_current_schema,
    migrate_to_current,
)
from didactic_cards.adapters import sqlite_storage as sqlite_storage_module
from didactic_cards.adapters.sqlite_storage import (
    StorageBusyError,
    StorageDestinationExistsError,
    StoragePathError,
    StorageValidationError,
    backup_database,
    inspect_database,
    restore_database,
)
from didactic_cards.domain.entities import Card, CardDeck
from didactic_cards.domain.printing import PrinterProfile
from didactic_cards.domain.rendering import DeckRenderSettings


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_state(path: Path) -> tuple[int, int, int, str]:
    stat = path.stat()
    return (stat.st_mode, stat.st_size, stat.st_mtime_ns, _sha256(path))


def _sqlite_family_state(database: Path) -> dict[str, tuple[int, int, int, str]]:
    return {
        candidate.name: _file_state(candidate)
        for candidate in (
            database,
            database.with_name(f'{database.name}-wal'),
            database.with_name(f'{database.name}-shm'),
        )
        if candidate.exists()
    }


def _downgrade_current_database_to_schema_12(database: Path) -> None:
    with closing(sqlite3.connect(database)) as connection:
        connection.execute('DROP TABLE schema_migrations')
        connection.execute('PRAGMA application_id = 0')
        connection.execute('PRAGMA user_version = 12')
        connection.commit()


def _downgrade_current_database_to_schema_13(database: Path) -> None:
    with closing(sqlite3.connect(database)) as connection:
        connection.execute('DELETE FROM schema_migrations')
        connection.execute(
            'INSERT INTO schema_migrations(version, name, applied_at) '
            "VALUES (13, 'initial-current-schema', "
            "'2026-08-24T00:00:00+00:00')"
        )
        connection.execute('PRAGMA user_version = 13')
        connection.commit()


def test_inspect_missing_database_is_strictly_read_only(tmp_path):
    database = tmp_path / 'missing' / 'cards.sqlite3'

    with pytest.raises(StoragePathError, match='does not exist'):
        inspect_database(database)

    assert not database.parent.exists()


@pytest.mark.parametrize('payload', [
    b'not a sqlite database',
    b'SQLite format 3\x00' + b'\x00' * 48,
])
def test_inspect_malformed_or_truncated_database_reports_issue_without_mutation(
    tmp_path, payload
):
    database = tmp_path / 'damaged.sqlite3'
    database.write_bytes(payload)
    before = _file_state(database)

    report = inspect_database(database)

    assert report.healthy is False
    assert any(issue.startswith('sqlite-error:') for issue in report.issues)
    assert _file_state(database) == before


def test_inspect_rejects_directory_and_symbolic_link_paths(tmp_path):
    directory = tmp_path / 'directory.sqlite3'
    directory.mkdir()
    database = tmp_path / 'cards.sqlite3'
    database.write_bytes(b'keep')
    link = tmp_path / 'link.sqlite3'
    link.symlink_to(database)

    with pytest.raises(StoragePathError, match='regular file'):
        inspect_database(directory)
    with pytest.raises(StoragePathError, match='symlink'):
        inspect_database(link)

    assert database.read_bytes() == b'keep'


def test_inspect_reports_actual_schema_counts_and_does_not_modify_file(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    deck = repository.create_deck('Проверка')
    repository.save_cards(deck.id, CardDeck([Card(front='Вопрос')]))
    repository.close()
    database = tmp_path / 'data' / 'cards.sqlite3'
    before_hash = _sha256(database)
    before_mtime = database.stat().st_mtime_ns
    wal = database.with_name(f'{database.name}-wal')
    shm = database.with_name(f'{database.name}-shm')
    assert not wal.exists()
    assert not shm.exists()

    report = inspect_database(database)

    assert report.healthy is True
    assert report.schema_version == SQLITE_SCHEMA_VERSION
    assert report.counts['decks'] == 1
    assert report.counts['cards'] == 1
    assert report.issues == ()
    assert _sha256(database) == before_hash
    assert database.stat().st_mtime_ns == before_mtime
    assert not wal.exists()
    assert not shm.exists()


def test_schema_initializer_refuses_nonempty_version_zero_database():
    with closing(sqlite3.connect(':memory:')) as connection:
        connection.execute('CREATE TABLE foreign_table(value TEXT)')

        with pytest.raises(ValueError, match='not an empty database'):
            initialize_current_schema(connection)

        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall() == [('foreign_table',)]


def test_migration_registry_fails_closed_when_path_is_missing():
    with closing(sqlite3.connect(':memory:')) as connection:
        with pytest.raises(ValueError, match='no migration path'):
            migrate_to_current(
                connection, MINIMUM_MIGRATABLE_SCHEMA_VERSION - 1
            )
        with pytest.raises(ValueError, match='future SQLite schema'):
            migrate_to_current(connection, SQLITE_SCHEMA_VERSION + 1)


def test_inspect_reports_structural_schema_drift(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    repository.close()
    with closing(sqlite3.connect(repository.database_file)) as connection:
        connection.execute('DROP TABLE printer_profiles')
        connection.execute('ALTER TABLE decks DROP COLUMN description')
        connection.execute('ALTER TABLE decks ADD COLUMN alien TEXT')
        connection.execute('CREATE TABLE foreign_table(value TEXT)')
        connection.execute('DROP INDEX idx_trusted_templates_one_approved')
        connection.execute('CREATE INDEX idx_alien_deck_name ON decks(name)')
        connection.commit()

    report = inspect_database(repository.database_file)

    assert report.healthy is False
    assert 'missing-table: printer_profiles' in report.issues
    assert 'unexpected-table: foreign_table' in report.issues
    assert 'missing-column: decks.description' in report.issues
    assert 'unexpected-column: decks.alien' in report.issues
    assert 'missing-index: idx_trusted_templates_one_approved' in report.issues
    assert 'unexpected-index: idx_alien_deck_name' in report.issues
    assert report.counts == {}


def test_same_named_index_with_wrong_definition_is_rejected_by_inspect_and_startup(
    tmp_path
):
    repository = SqliteRepository(tmp_path / 'data')
    repository.close()
    with closing(sqlite3.connect(repository.database_file)) as connection:
        connection.execute('DROP INDEX idx_deck_cards_position')
        connection.execute(
            'CREATE INDEX idx_deck_cards_position ON deck_cards(card_id)'
        )
        connection.commit()

    report = inspect_database(repository.database_file)

    assert report.healthy is False
    assert 'index-definition: idx_deck_cards_position' in report.issues
    with pytest.raises(
        UnsupportedSqliteSchemaError,
        match='index-definition: idx_deck_cards_position',
    ):
        SqliteRepository(repository.data_dir)


def test_inspect_reports_unsupported_expected_schema_version(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    repository.close()

    report = inspect_database(
        repository.database_file,
        expected_schema_version=SQLITE_SCHEMA_VERSION + 100,
    )

    assert report.healthy is False
    assert report.issues == (
        f'unsupported-schema-version: {SQLITE_SCHEMA_VERSION + 100}',
    )


def test_existing_unknown_version_zero_database_is_never_adopted(tmp_path):
    data_dir = tmp_path / 'unknown'
    data_dir.mkdir()
    database = data_dir / 'cards.sqlite3'
    with closing(sqlite3.connect(database)) as connection:
        connection.execute('CREATE TABLE foreign_application(value TEXT)')
        connection.execute("INSERT INTO foreign_application VALUES ('keep')")
        connection.commit()

    with pytest.raises(UnsupportedSqliteSchemaError, match='unrecognized schema 0'):
        SqliteRepository(data_dir)

    with closing(sqlite3.connect(database)) as connection:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert tables == {'foreign_application'}
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 0


def test_current_database_with_missing_table_is_rejected_not_repaired(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    repository.close()
    with closing(sqlite3.connect(repository.database_file)) as connection:
        connection.execute('DROP TABLE printer_profiles')
        connection.commit()

    with pytest.raises(UnsupportedSqliteSchemaError, match='schema validation'):
        SqliteRepository(repository.data_dir)

    with closing(sqlite3.connect(repository.database_file)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type = 'table' AND name = 'printer_profiles'"
        ).fetchone()[0] == 0


def test_read_transaction_keeps_one_wal_snapshot(tmp_path):
    first = SqliteRepository(tmp_path / 'data')
    second = SqliteRepository(tmp_path / 'data')
    first.create_deck('До снимка')

    with first._transaction() as connection:
        initial = connection.execute('SELECT COUNT(*) FROM decks').fetchone()[0]
        second.create_deck('После снимка')
        same_snapshot = connection.execute('SELECT COUNT(*) FROM decks').fetchone()[0]

    assert initial == same_snapshot == 1
    assert len(first.list_decks()) == 2


def test_schema_12_migrates_once_without_losing_data(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    deck = repository.create_deck('До миграции')
    repository.save_cards(deck.id, CardDeck([Card(front='Сохранить')]))
    repository.close()
    _downgrade_current_database_to_schema_12(repository.database_file)

    migrated = SqliteRepository(repository.data_dir)

    assert migrated.get_deck(deck.id).name == 'До миграции'
    assert migrated.load_cards(deck.id).cards[0].front == 'Сохранить'
    with closing(migrated._connect()) as connection:
        assert connection.execute(
            'PRAGMA user_version'
        ).fetchone()[0] == SQLITE_SCHEMA_VERSION
        assert [
            tuple(row) for row in connection.execute(
                'SELECT version FROM schema_migrations ORDER BY version'
            )
        ] == [(13,), (14,)]
    migrated.close()
    backup_files = list((repository.data_dir / 'backups').glob(
        'pre-migration-v12-*.sqlite3'
    ))
    assert len(backup_files) == 1
    assert inspect_database(backup_files[0], expected_schema_version=12).healthy

    opened_again = SqliteRepository(repository.data_dir)
    opened_again.close()
    assert list((repository.data_dir / 'backups').glob(
        'pre-migration-v12-*.sqlite3'
    )) == backup_files


def test_schema_13_migration_scrubs_only_safe_hidden_headers_and_keeps_backup(
    tmp_path,
):
    repository = SqliteRepository(tmp_path / 'data')
    safe = repository.create_deck('Safe legacy')
    advanced = repository.create_deck(
        'Advanced exact',
        render_settings=DeckRenderSettings(authoring_mode='advanced'),
    )
    safe_card = Card(front='Safe body', back='Safe back')
    advanced_card = Card(
        front='Raw\r\nfront',
        back='Raw\rback\n',
        upper_header=' \tTOP\r\n ',
        lower_header='BOTTOM\r',
    )
    repository.save_cards(safe.id, CardDeck([safe_card]))
    repository.save_cards(advanced.id, CardDeck([advanced_card]))
    repository.close()
    with closing(sqlite3.connect(repository.database_file)) as connection:
        typography = json.loads(connection.execute(
            'SELECT typography_json FROM deck_render_settings WHERE deck_id = ?',
            (safe.id,),
        ).fetchone()[0])
        typography.pop('authoring_mode')
        connection.execute(
            'UPDATE deck_render_settings SET typography_json = ? '
            'WHERE deck_id = ?',
            (json.dumps(typography), safe.id),
        )
        connection.execute(
            'UPDATE cards SET upper_header = ?, lower_header = ? WHERE id = ?',
            (' hidden\r\n ', '\t', safe_card.id),
        )
        safe_metadata = connection.execute(
            'SELECT created_at, updated_at, version FROM cards WHERE id = ?',
            (safe_card.id,),
        ).fetchone()
        connection.commit()
    _downgrade_current_database_to_schema_13(repository.database_file)

    migrated = SqliteRepository(repository.data_dir)

    loaded_safe = migrated.load_cards(safe.id).cards[0]
    loaded_advanced = migrated.load_cards(advanced.id).cards[0]
    assert (loaded_safe.upper_header, loaded_safe.lower_header) == ('', '')
    assert (
        loaded_advanced.front,
        loaded_advanced.back,
        loaded_advanced.upper_header,
        loaded_advanced.lower_header,
    ) == (
        advanced_card.front,
        advanced_card.back,
        advanced_card.upper_header,
        advanced_card.lower_header,
    )
    with closing(migrated._connect()) as connection:
        assert tuple(connection.execute(
            'SELECT created_at, updated_at, version FROM cards WHERE id = ?',
            (safe_card.id,),
        ).fetchone()) == safe_metadata
        assert [
            tuple(row) for row in connection.execute(
                'SELECT version, name FROM schema_migrations ORDER BY version'
            )
        ] == [
            (13, 'initial-current-schema'),
            (14, 'enforce-mode-card-fields'),
        ]
    migrated.close()

    backups = list((repository.data_dir / 'backups').glob(
        'pre-migration-v13-*.sqlite3'
    ))
    assert len(backups) == 1
    assert inspect_database(backups[0], expected_schema_version=13).healthy
    with closing(sqlite3.connect(backups[0])) as connection:
        assert connection.execute(
            'SELECT upper_header, lower_header FROM cards WHERE id = ?',
            (safe_card.id,),
        ).fetchone() == (' hidden\r\n ', '\t')

    reopened = SqliteRepository(repository.data_dir)
    reopened.close()
    assert list((repository.data_dir / 'backups').glob(
        'pre-migration-v13-*.sqlite3'
    )) == backups


def test_failed_schema_13_cleanup_rolls_back_and_reuses_stable_backup(
    tmp_path, monkeypatch
):
    repository = SqliteRepository(tmp_path / 'data')
    safe = repository.create_deck('Safe retry')
    card = Card(front='Keep')
    repository.save_cards(safe.id, CardDeck([card]))
    repository.close()
    with closing(sqlite3.connect(repository.database_file)) as connection:
        connection.execute(
            'UPDATE cards SET upper_header = ? WHERE id = ?',
            ('recoverable raw', card.id),
        )
        connection.commit()
    _downgrade_current_database_to_schema_13(repository.database_file)
    real_migrate = sqlite_repository_module.migrate_to_current

    def fail_after_cleanup(connection, version):
        assert version == 13
        connection.execute(
            "UPDATE cards SET upper_header = '', lower_header = ''"
        )
        connection.execute('PRAGMA user_version = 14')
        raise RuntimeError('injected schema-13 migration failure')

    monkeypatch.setattr(
        sqlite_repository_module, 'migrate_to_current', fail_after_cleanup
    )
    with pytest.raises(RuntimeError, match='schema-13 migration failure'):
        SqliteRepository(repository.data_dir)

    assert inspect_database(
        repository.database_file, expected_schema_version=13
    ).healthy
    with closing(sqlite3.connect(repository.database_file)) as connection:
        assert connection.execute(
            'SELECT upper_header FROM cards WHERE id = ?', (card.id,)
        ).fetchone()[0] == 'recoverable raw'
    backups = list((repository.data_dir / 'backups').glob(
        'pre-migration-v13-*.sqlite3'
    ))
    assert len(backups) == 1
    backup_state = _sqlite_family_state(backups[0])
    manifest = backups[0].with_suffix(backups[0].suffix + '.manifest.json')
    manifest_state = _file_state(manifest)

    monkeypatch.setattr(
        sqlite_repository_module, 'migrate_to_current', real_migrate
    )
    migrated = SqliteRepository(repository.data_dir)
    assert migrated.load_cards(safe.id).cards[0].upper_header == ''
    migrated.close()
    assert list((repository.data_dir / 'backups').glob(
        'pre-migration-v13-*.sqlite3'
    )) == backups
    assert _sqlite_family_state(backups[0]) == backup_state
    assert _file_state(manifest) == manifest_state


def test_semantically_incomplete_schema_13_migration_rolls_back(
    tmp_path, monkeypatch
):
    repository = SqliteRepository(tmp_path / 'data')
    safe = repository.create_deck('Safe incomplete migration')
    card = Card(front='Keep')
    repository.save_cards(safe.id, CardDeck([card]))
    repository.close()
    with closing(sqlite3.connect(repository.database_file)) as connection:
        connection.execute(
            'UPDATE cards SET upper_header = ? WHERE id = ?',
            ('must survive rollback', card.id),
        )
        connection.commit()
    _downgrade_current_database_to_schema_13(repository.database_file)
    real_migrate = sqlite_repository_module.migrate_to_current

    def leave_hidden_value(connection, version):
        real_migrate(connection, version)
        connection.execute(
            'UPDATE cards SET upper_header = ? WHERE id = ?',
            ('still hidden', card.id),
        )

    monkeypatch.setattr(
        sqlite_repository_module, 'migrate_to_current', leave_hidden_value
    )

    with pytest.raises(
        UnsupportedSqliteSchemaError,
        match='validation failed during migration.*hidden-safe-card-headers',
    ):
        SqliteRepository(repository.data_dir)

    assert inspect_database(
        repository.database_file, expected_schema_version=13
    ).healthy
    with closing(sqlite3.connect(repository.database_file)) as connection:
        assert connection.execute(
            'PRAGMA user_version'
        ).fetchone()[0] == 13
        assert connection.execute(
            'SELECT upper_header FROM cards WHERE id = ?', (card.id,)
        ).fetchone()[0] == 'must survive rollback'


def test_failed_migration_rolls_back_schema_and_preserves_version_12_data(
    tmp_path, monkeypatch
):
    repository = SqliteRepository(tmp_path / 'data')
    deck = repository.create_deck('Останется на v12')
    repository.save_cards(deck.id, CardDeck([Card(front='Не потерять')]))
    repository.close()
    _downgrade_current_database_to_schema_12(repository.database_file)

    def fail_after_schema_change(connection, version):
        assert version == 12
        connection.execute('CREATE TABLE migration_probe(value TEXT)')
        connection.execute('PRAGMA user_version = 13')
        raise RuntimeError('injected migration failure')

    monkeypatch.setattr(
        sqlite_repository_module, 'migrate_to_current', fail_after_schema_change
    )

    with pytest.raises(RuntimeError, match='injected migration failure'):
        SqliteRepository(repository.data_dir)

    report = inspect_database(
        repository.database_file, expected_schema_version=12
    )
    assert report.healthy is True
    with closing(sqlite3.connect(repository.database_file)) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 12
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type = 'table' AND name = 'migration_probe'"
        ).fetchone()[0] == 0
        assert connection.execute(
            'SELECT name FROM decks WHERE id = ?', (deck.id,)
        ).fetchone()[0] == 'Останется на v12'


def test_repeated_failed_migration_reuses_one_stable_pre_migration_backup(
    tmp_path, monkeypatch
):
    repository = SqliteRepository(tmp_path / 'data')
    deck = repository.create_deck('Повторная миграция')
    repository.close()
    _downgrade_current_database_to_schema_12(repository.database_file)
    calls = 0

    def fail_migration(connection, version):
        nonlocal calls
        calls += 1
        assert version == 12
        connection.execute('CREATE TABLE migration_probe(value TEXT)')
        raise RuntimeError('repeatable migration failure')

    monkeypatch.setattr(
        sqlite_repository_module, 'migrate_to_current', fail_migration
    )

    with pytest.raises(RuntimeError, match='repeatable migration failure'):
        SqliteRepository(repository.data_dir)

    backups = list((repository.data_dir / 'backups').glob(
        'pre-migration-v12-*.sqlite3'
    ))
    assert len(backups) == 1
    manifest = backups[0].with_suffix(
        backups[0].suffix + '.manifest.json'
    )
    first_backup_state = _file_state(backups[0])
    first_manifest_state = _file_state(manifest)

    with pytest.raises(RuntimeError, match='repeatable migration failure'):
        SqliteRepository(repository.data_dir)

    assert calls == 2
    assert list((repository.data_dir / 'backups').glob(
        'pre-migration-v12-*.sqlite3'
    )) == backups
    assert _file_state(backups[0]) == first_backup_state
    assert _file_state(manifest) == first_manifest_state
    assert inspect_database(
        repository.database_file, expected_schema_version=12
    ).healthy
    with closing(sqlite3.connect(repository.database_file)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type = 'table' AND name = 'migration_probe'"
        ).fetchone()[0] == 0
        assert connection.execute(
            'SELECT name FROM decks WHERE id = ?', (deck.id,)
        ).fetchone()[0] == 'Повторная миграция'


def test_future_schema_is_rejected_without_database_mutation(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    repository.create_deck('Будущее')
    repository.close()
    with closing(sqlite3.connect(repository.database_file)) as connection:
        connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        connection.execute('PRAGMA journal_mode = DELETE')
        connection.execute(f'PRAGMA user_version = {SQLITE_SCHEMA_VERSION + 1}')
        connection.commit()
    before = _sqlite_family_state(repository.database_file)

    with pytest.raises(
        UnsupportedSqliteSchemaError,
        match=f'Unsupported SQLite schema {SQLITE_SCHEMA_VERSION + 1}',
    ):
        SqliteRepository(repository.data_dir)

    assert _sqlite_family_state(repository.database_file) == before
    assert not (repository.data_dir / 'backups').exists()


def test_online_backup_contains_committed_wal_data_and_manifest(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    output = tmp_path / 'exports' / 'snapshot.sqlite3'
    with closing(repository._connect()) as keeper:
        keeper.execute('BEGIN')
        keeper.execute('SELECT COUNT(*) FROM decks').fetchone()
        deck = repository.create_deck('WAL')
        repository.save_cards(deck.id, CardDeck([Card(front='В WAL')]))
        wal = repository.database_file.with_name('cards.sqlite3-wal')
        assert wal.exists() and wal.stat().st_size > 0
        result = backup_database(repository.database_file, output)
        source_report = inspect_database(repository.database_file)
    report = inspect_database(output)

    assert result.path == output.resolve()
    assert result.sha256 == _sha256(output)
    assert report.healthy is True
    assert report.counts['decks'] == 1
    assert report.counts['cards'] == 1
    assert report.logical_sha256 == source_report.logical_sha256
    assert output.stat().st_mode & 0o777 == 0o600
    assert result.manifest_path.stat().st_mode & 0o777 == 0o600
    manifest = json.loads(result.manifest_path.read_text(encoding='utf-8'))
    assert manifest['sha256'] == result.sha256
    assert manifest['logical_sha256'] == report.logical_sha256
    assert manifest['schema_version'] == SQLITE_SCHEMA_VERSION


def test_online_backup_does_not_mutate_source_database_or_wal_files(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    deck = repository.create_deck('Не менять источник')
    repository.save_cards(deck.id, CardDeck([Card(front='WAL snapshot')]))
    database = repository.database_file
    before = _sqlite_family_state(database)

    backup_database(database, tmp_path / 'snapshot.sqlite3')

    assert _sqlite_family_state(database) == before


def test_backup_never_overwrites_existing_destination(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    output = tmp_path / 'existing.sqlite3'
    output.write_bytes(b'keep')

    with pytest.raises(StorageDestinationExistsError):
        backup_database(repository.database_file, output)

    assert output.read_bytes() == b'keep'


def test_backup_rejects_manifest_collision_without_creating_database(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    output = tmp_path / 'snapshot.sqlite3'
    manifest = tmp_path / 'snapshot.sqlite3.manifest.json'
    manifest.write_text('keep-manifest', encoding='utf-8')

    with pytest.raises(StorageDestinationExistsError):
        backup_database(repository.database_file, output)

    assert not output.exists()
    assert manifest.read_text(encoding='utf-8') == 'keep-manifest'


def test_backup_rejects_symlink_destination_without_touching_target(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    target = tmp_path / 'target.sqlite3'
    target.write_bytes(b'keep-target')
    output = tmp_path / 'snapshot.sqlite3'
    output.symlink_to(target)

    with pytest.raises(StoragePathError, match='symlink'):
        backup_database(repository.database_file, output)

    assert target.read_bytes() == b'keep-target'
    assert output.is_symlink()


def test_backup_rejects_live_database_as_destination(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')

    with pytest.raises(StoragePathError, match='must differ'):
        backup_database(repository.database_file, repository.database_file)


def test_backup_output_parent_must_be_a_directory(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    parent = tmp_path / 'not-a-directory'
    parent.write_bytes(b'keep-parent')

    with pytest.raises(OSError):
        backup_database(repository.database_file, parent / 'snapshot.sqlite3')

    assert parent.read_bytes() == b'keep-parent'


def test_backup_does_not_change_permissions_of_existing_output_directory(
    tmp_path
):
    repository = SqliteRepository(tmp_path / 'data')
    output_dir = tmp_path / 'shared-exports'
    output_dir.mkdir()
    output_dir.chmod(0o750)

    backup_database(repository.database_file, output_dir / 'snapshot.sqlite3')

    assert output_dir.stat().st_mode & 0o777 == 0o750


def test_concurrent_backups_to_same_destination_publish_exactly_once(
    tmp_path, monkeypatch
):
    repository = SqliteRepository(tmp_path / 'data')
    deck = repository.create_deck('Concurrent backup')
    repository.save_cards(deck.id, CardDeck([Card(front='Atomic')]))
    database = repository.database_file
    source_before = _sqlite_family_state(database)
    output = tmp_path / 'race.sqlite3'
    start_barrier = threading.Barrier(2)
    publish_barrier = threading.Barrier(2)
    real_link = sqlite_storage_module.os.link

    def synchronized_link(source, destination):
        if Path(destination) == output:
            publish_barrier.wait(timeout=5)
        return real_link(source, destination)

    monkeypatch.setattr(sqlite_storage_module.os, 'link', synchronized_link)

    def make_backup():
        start_barrier.wait(timeout=5)
        return backup_database(database, output)

    successes = []
    collisions = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(make_backup) for _ in range(2)]
        for future in futures:
            try:
                successes.append(future.result(timeout=10))
            except StorageDestinationExistsError as error:
                collisions.append(error)

    assert len(successes) == 1
    assert len(collisions) == 1
    assert successes[0].sha256 == _sha256(output)
    assert inspect_database(output).healthy
    manifest = json.loads(
        successes[0].manifest_path.read_text(encoding='utf-8')
    )
    assert manifest['sha256'] == successes[0].sha256
    assert _sqlite_family_state(database) == source_before
    assert not list(tmp_path.glob(f'.{output.name}.*.tmp'))
    assert not list(tmp_path.glob(f'.{output.name}.*.manifest.tmp'))


def test_inspect_reports_missing_settings_orphan_and_position_gap(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    first_deck = repository.create_deck('Без настроек')
    second_deck = repository.create_deck('Пробел')
    cards = [Card(front=f'Карточка {number}') for number in range(3)]
    repository.save_cards(second_deck.id, CardDeck(cards))
    repository.close()
    with closing(sqlite3.connect(repository.database_file)) as connection:
        connection.execute(
            'DELETE FROM deck_render_settings WHERE deck_id = ?',
            (first_deck.id,),
        )
        connection.execute(
            'DELETE FROM deck_cards WHERE deck_id = ? AND position = 1',
            (second_deck.id,),
        )
        connection.commit()

    report = inspect_database(repository.database_file)

    assert report.healthy is False
    assert f'missing-render-settings: {first_deck.id}' in report.issues
    assert f'orphan-card: {cards[1].id}' in report.issues
    assert f'non-contiguous-positions: {second_deck.id}' in report.issues


def test_repository_readiness_reports_orphan_card(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    deck = repository.create_deck('Readiness')
    card = Card(front='Orphan')
    repository.save_cards(deck.id, CardDeck([card]))
    with closing(repository._connect()) as connection:
        connection.execute(
            'DELETE FROM deck_cards WHERE deck_id = ? AND card_id = ?',
            (deck.id, card.id),
        )
        connection.commit()

    assert repository.readiness_check() == [f'orphan-card: {card.id}']
    repository.close()


def test_inspect_reports_card_shared_between_decks(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    source_deck = repository.create_deck('Первая')
    other_deck = repository.create_deck('Вторая')
    card = Card(front='Shared')
    repository.save_cards(source_deck.id, CardDeck([card]))
    repository.close()
    with closing(sqlite3.connect(repository.database_file)) as connection:
        connection.execute(
            'INSERT INTO deck_cards(deck_id, card_id, position) VALUES (?, ?, 0)',
            (other_deck.id, card.id),
        )
        connection.commit()

    report = inspect_database(repository.database_file)

    assert report.healthy is False
    assert f'shared-card: {card.id}' in report.issues


def test_inspect_reports_foreign_key_violation(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    repository.close()
    with closing(sqlite3.connect(repository.database_file)) as connection:
        connection.execute(
            'INSERT INTO deck_cards(deck_id, card_id, position) '
            "VALUES ('missing-deck', 'missing-card', 0)"
        )
        connection.commit()

    report = inspect_database(repository.database_file)

    assert report.healthy is False
    assert any(issue.startswith('foreign-key:') for issue in report.issues)


@pytest.mark.parametrize('typography_json', [
    '{broken json',
    '[]',
    '{"unknown-setting": true}',
])
def test_inspect_reports_invalid_render_settings(tmp_path, typography_json):
    repository = SqliteRepository(tmp_path / 'data')
    deck = repository.create_deck('Настройки')
    repository.close()
    with closing(sqlite3.connect(repository.database_file)) as connection:
        connection.execute(
            'UPDATE deck_render_settings SET typography_json = ? '
            'WHERE deck_id = ?',
            (typography_json, deck.id),
        )
        connection.commit()

    report = inspect_database(repository.database_file)

    assert report.healthy is False
    assert f'invalid-render-settings: {deck.id}' in report.issues


def test_inspect_reports_invalid_trusted_template(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    deck = repository.create_deck('Raw')
    repository.close()
    with closing(sqlite3.connect(repository.database_file)) as connection:
        connection.execute(
            '''
            INSERT INTO trusted_templates(
                id, deck_id, version, source_hash, front_source, back_source,
                provenance, status, origin_template_id, created_at, approved_at
            ) VALUES (?, ?, 1, '', '', '', 'local-author', 'quarantined',
                      NULL, '2026-01-01T00:00:00+00:00', NULL)
            ''',
            ('invalid-template', deck.id),
        )
        connection.commit()

    report = inspect_database(repository.database_file)

    assert report.healthy is False
    assert 'invalid-trusted-template: invalid-template' in report.issues


def test_backup_rejects_semantically_unhealthy_source(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    deck = repository.create_deck('Повреждённая')
    repository.close()
    with closing(sqlite3.connect(repository.database_file)) as connection:
        connection.execute(
            'DELETE FROM deck_render_settings WHERE deck_id = ?', (deck.id,)
        )
        connection.commit()
    output = tmp_path / 'must-not-exist.sqlite3'

    with pytest.raises(StorageValidationError, match='missing-render-settings'):
        backup_database(repository.database_file, output)

    assert not output.exists()


def test_restore_rejects_invalid_source_before_touching_live_database(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    deck = repository.create_deck('Оригинал')
    invalid = tmp_path / 'invalid.sqlite3'
    invalid.write_bytes(b'not sqlite')
    before = repository.get_deck(deck.id)
    repository.close()

    with pytest.raises(StorageValidationError):
        restore_database(repository.database_file, invalid)

    reopened = SqliteRepository(repository.data_dir)
    assert reopened.get_deck(deck.id).name == before.name


@pytest.mark.parametrize('tamper', ['invalid-json', 'wrong-hash'])
def test_restore_rejects_tampered_manifest_without_mutating_live_database(
    tmp_path, tamper
):
    repository = SqliteRepository(tmp_path / 'data')
    deck = repository.create_deck('Источник')
    backup = backup_database(
        repository.database_file, tmp_path / 'snapshot.sqlite3'
    )
    repository.update_deck(deck.id, 'Текущая версия')
    repository.close()
    if tamper == 'invalid-json':
        backup.manifest_path.write_text('{', encoding='utf-8')
    else:
        payload = json.loads(backup.manifest_path.read_text(encoding='utf-8'))
        payload['sha256'] = '0' * 64
        backup.manifest_path.write_text(json.dumps(payload), encoding='utf-8')
    before = _sqlite_family_state(repository.database_file)

    with pytest.raises(StorageValidationError, match='manifest'):
        restore_database(repository.database_file, backup.path)

    assert _sqlite_family_state(repository.database_file) == before
    reopened = SqliteRepository(repository.data_dir)
    assert reopened.get_deck(deck.id).name == 'Текущая версия'


def test_restore_requires_manifest_without_mutating_live_database(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    deck = repository.create_deck('Live')
    backup = backup_database(
        repository.database_file, tmp_path / 'snapshot.sqlite3'
    )
    repository.close()
    backup.manifest_path.unlink()
    before = _sqlite_family_state(repository.database_file)

    with pytest.raises(StorageValidationError, match='manifest is missing'):
        restore_database(repository.database_file, backup.path)

    assert _sqlite_family_state(repository.database_file) == before
    reopened = SqliteRepository(repository.data_dir)
    assert reopened.get_deck(deck.id).name == 'Live'
    reopened.close()


def test_restore_rejects_source_symlink_without_mutating_live_database(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    deck = repository.create_deck('Live')
    backup = backup_database(
        repository.database_file, tmp_path / 'snapshot.sqlite3'
    )
    repository.close()
    link = tmp_path / 'snapshot-link.sqlite3'
    link.symlink_to(backup.path)
    before = _sqlite_family_state(repository.database_file)

    with pytest.raises(StoragePathError, match='symlink'):
        restore_database(repository.database_file, link)

    assert _sqlite_family_state(repository.database_file) == before
    reopened = SqliteRepository(repository.data_dir)
    assert reopened.get_deck(deck.id).name == 'Live'


def test_restore_rejects_live_database_as_source(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    repository.close()

    with pytest.raises(StoragePathError, match='must differ'):
        restore_database(repository.database_file, repository.database_file)


def test_restore_refuses_while_application_holds_runtime_lease(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    backup = backup_database(
        repository.database_file, tmp_path / 'snapshot.sqlite3'
    )

    with pytest.raises(StorageBusyError, match='application is running'):
        restore_database(repository.database_file, backup.path)


def test_restore_refuses_unhealthy_live_database_before_safety_backup(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    deck = repository.create_deck('Live')
    backup = backup_database(
        repository.database_file, tmp_path / 'snapshot.sqlite3'
    )
    repository.close()
    with closing(sqlite3.connect(repository.database_file)) as connection:
        connection.execute(
            'DELETE FROM deck_render_settings WHERE deck_id = ?', (deck.id,)
        )
        connection.commit()
    before = _sqlite_family_state(repository.database_file)

    with pytest.raises(StorageValidationError, match='live database is unhealthy'):
        restore_database(repository.database_file, backup.path)

    assert _sqlite_family_state(repository.database_file) == before
    assert not (repository.data_dir / 'backups').exists()


def test_forensic_restore_allows_unhealthy_live_and_preserves_bundle(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    deck = repository.create_deck('Backup state')
    backup = backup_database(
        repository.database_file, tmp_path / 'snapshot.sqlite3'
    )
    repository.update_deck(deck.id, 'Corrupted live state')
    repository.close()
    with closing(sqlite3.connect(repository.database_file)) as connection:
        connection.execute(
            'DELETE FROM deck_render_settings WHERE deck_id = ?', (deck.id,)
        )
        connection.commit()
    live_hash = _sha256(repository.database_file)
    live_size = repository.database_file.stat().st_size

    restored = restore_database(
        repository.database_file,
        backup.path,
        allow_unhealthy_live=True,
    )

    assert restored.safety_backup is None
    assert restored.forensic_bundle is not None
    bundle = restored.forensic_bundle
    captured_main = bundle / repository.database_file.name
    forensic_manifest = bundle / 'forensic-manifest.json'
    assert captured_main.is_file()
    assert forensic_manifest.is_file()
    assert _sha256(captured_main) == live_hash
    manifest = json.loads(forensic_manifest.read_text(encoding='utf-8'))
    assert manifest['format'] == 'didactic-cards-forensic-bundle-v1'
    assert manifest['source_database'] == repository.database_file.name
    assert manifest['members'][repository.database_file.name] == {
        'sha256': live_hash,
        'size_bytes': live_size,
    }
    assert inspect_database(captured_main).healthy is False
    reopened = SqliteRepository(repository.data_dir)
    assert reopened.get_deck(deck.id).name == 'Backup state'
    reopened.close()


@pytest.mark.parametrize('failure_point', ['replace', 'fsync'])
def test_publish_failure_preserves_previous_logical_live_wal_state(
    tmp_path, monkeypatch, failure_point
):
    repository = SqliteRepository(tmp_path / 'data')
    deck = repository.create_deck('Backup state')
    repository.save_cards(deck.id, CardDeck([Card(front='Live card')]))
    backup = backup_database(
        repository.database_file, tmp_path / 'snapshot.sqlite3'
    )
    database = repository.database_file
    repository.close()
    with closing(sqlite3.connect(database)) as writer:
        writer.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
        writer.execute('PRAGMA wal_autocheckpoint = 0')
        writer.execute(
            'UPDATE decks SET name = ? WHERE id = ?',
            ('Live WAL state', deck.id),
        )
        writer.commit()
    wal = database.with_name(f'{database.name}-wal')
    assert wal.exists() and wal.stat().st_size > 0
    assert inspect_database(database).healthy

    injected = False
    if failure_point == 'replace':
        real_replace = sqlite_storage_module.os.replace

        def fail_staged_replace(source, destination):
            nonlocal injected
            if (
                Path(destination) == database
                and Path(source).name.startswith('.restore-')
            ):
                injected = True
                raise OSError('injected staged replace failure')
            return real_replace(source, destination)

        monkeypatch.setattr(
            sqlite_storage_module.os, 'replace', fail_staged_replace
        )
    else:
        real_fsync_directory = sqlite_storage_module._fsync_directory

        def fail_publish_fsync(directory):
            nonlocal injected
            if Path(directory) == database.parent and not injected:
                injected = True
                raise OSError('injected publish fsync failure')
            return real_fsync_directory(directory)

        monkeypatch.setattr(
            sqlite_storage_module, '_fsync_directory', fail_publish_fsync
        )

    with pytest.raises(OSError, match='injected'):
        restore_database(database, backup.path)

    assert injected is True
    reopened = SqliteRepository(repository.data_dir)
    assert reopened.get_deck(deck.id).name == 'Live WAL state'
    assert reopened.load_cards(deck.id).cards[0].front == 'Live card'
    assert reopened.integrity_check() == []
    reopened.close()
    assert not list(repository.data_dir.glob('.restore-*'))
    assert not list(repository.data_dir.glob('.cards.sqlite3.rollback-*'))


def test_restore_rolls_back_when_published_database_fails_final_validation(
    tmp_path, monkeypatch
):
    repository = SqliteRepository(tmp_path / 'data')
    deck = repository.create_deck('Backup state')
    backup = backup_database(
        repository.database_file, tmp_path / 'snapshot.sqlite3'
    )
    repository.update_deck(deck.id, 'Live state')
    repository.close()
    real_publish = sqlite_storage_module._publish_staged_database
    publish_calls = 0

    def corrupt_first_publish(staged, live):
        nonlocal publish_calls
        publish_calls += 1
        real_publish(staged, live)
        if publish_calls == 1:
            with closing(sqlite3.connect(live)) as connection:
                connection.execute('DELETE FROM deck_render_settings')
                connection.commit()

    monkeypatch.setattr(
        sqlite_storage_module, '_publish_staged_database', corrupt_first_publish
    )

    with pytest.raises(StorageValidationError, match='post-restore validation'):
        restore_database(repository.database_file, backup.path)

    assert publish_calls == 2
    reopened = SqliteRepository(repository.data_dir)
    assert reopened.get_deck(deck.id).name == 'Live state'
    reopened.close()
    assert not list(repository.data_dir.glob('.restore-*'))


def test_restore_round_trip_creates_safety_backup_and_keeps_secret(tmp_path):
    data_dir = tmp_path / 'data'
    repository = SqliteRepository(data_dir)
    deck = repository.create_deck(
        'До backup', render_settings=DeckRenderSettings(
            horizontal_alignment='right'
        )
    )
    repository.save_cards(deck.id, CardDeck([
        Card(front='Первая'), Card(front='Вторая')
    ]))
    repository.save_printer_profile(PrinterProfile(
        key='office', name='Office', back_offset_x_mm=0.75
    ))
    backup = backup_database(
        repository.database_file, tmp_path / 'snapshot.sqlite3'
    )
    backup_before = _sqlite_family_state(backup.path)
    manifest_before = _file_state(backup.manifest_path)
    repository.update_deck(deck.id, 'После backup')
    repository.save_cards(deck.id, CardDeck([Card(front='Новая')]))
    repository.close()
    secret = data_dir / '.secret_key'
    secret.write_text('keep-secret', encoding='utf-8')

    restored = restore_database(repository.database_file, backup.path)

    assert _sqlite_family_state(backup.path) == backup_before
    assert _file_state(backup.manifest_path) == manifest_before
    assert restored.safety_backup.exists()
    assert inspect_database(restored.safety_backup).healthy
    reopened = SqliteRepository(data_dir)
    assert reopened.get_deck(deck.id).name == 'До backup'
    assert [card.front for card in reopened.load_cards(deck.id).cards] == [
        'Первая', 'Вторая'
    ]
    assert reopened.get_render_settings(deck.id).horizontal_alignment.value == 'right'
    assert reopened.list_printer_profiles()[0].key == 'office'
    assert secret.read_text(encoding='utf-8') == 'keep-secret'


def test_restore_schema_12_backup_migrates_staged_copy_without_mutating_source(
    tmp_path
):
    source_repository = SqliteRepository(tmp_path / 'source-data')
    source_deck = source_repository.create_deck('Из v12')
    source_repository.save_cards(
        source_deck.id, CardDeck([Card(front='Сохранено')])
    )
    source_repository.close()
    _downgrade_current_database_to_schema_12(source_repository.database_file)
    source_before_backup = _sqlite_family_state(source_repository.database_file)
    backup = backup_database(
        source_repository.database_file,
        tmp_path / 'schema-12.sqlite3',
        expected_schema_version=12,
    )
    assert _sqlite_family_state(
        source_repository.database_file
    ) == source_before_backup
    backup_before_restore = _sqlite_family_state(backup.path)
    manifest_before_restore = _file_state(backup.manifest_path)

    live_repository = SqliteRepository(tmp_path / 'live-data')
    live_repository.create_deck('Будет заменена')
    live_repository.close()

    restore_database(live_repository.database_file, backup.path)

    assert _sqlite_family_state(backup.path) == backup_before_restore
    assert _file_state(backup.manifest_path) == manifest_before_restore
    assert _sqlite_family_state(
        source_repository.database_file
    ) == source_before_backup
    reopened = SqliteRepository(live_repository.data_dir)
    assert reopened.get_deck(source_deck.id).name == 'Из v12'
    assert reopened.load_cards(source_deck.id).cards[0].front == 'Сохранено'
    with closing(reopened._connect()) as connection:
        assert connection.execute(
            'PRAGMA user_version'
        ).fetchone()[0] == SQLITE_SCHEMA_VERSION
        assert [
            tuple(row) for row in connection.execute(
                'SELECT version, name FROM schema_migrations ORDER BY version'
            )
        ] == [
            (13, 'storage-foundation'),
            (14, 'enforce-mode-card-fields'),
        ]
    reopened.close()


def test_restore_schema_13_backup_scrubs_safe_and_preserves_advanced_raw(
    tmp_path,
):
    source_repository = SqliteRepository(tmp_path / 'source-v13')
    safe = source_repository.create_deck('Safe v13')
    advanced = source_repository.create_deck(
        'Advanced v13',
        render_settings=DeckRenderSettings(authoring_mode='advanced'),
    )
    safe_card = Card(front='Safe')
    advanced_card = Card(
        front='Raw\r\nfront',
        back='Raw\rback',
        upper_header=' \tTOP\n',
        lower_header='BOTTOM\r\n',
    )
    source_repository.save_cards(safe.id, CardDeck([safe_card]))
    source_repository.save_cards(advanced.id, CardDeck([advanced_card]))
    source_repository.close()
    with closing(sqlite3.connect(source_repository.database_file)) as connection:
        connection.execute(
            'UPDATE cards SET upper_header = ?, lower_header = ? WHERE id = ?',
            ('legacy hidden', ' \r\n ', safe_card.id),
        )
        connection.commit()
    _downgrade_current_database_to_schema_13(
        source_repository.database_file
    )
    source_state = _sqlite_family_state(source_repository.database_file)
    backup = backup_database(
        source_repository.database_file,
        tmp_path / 'schema-13.sqlite3',
        expected_schema_version=13,
    )
    backup_state = _sqlite_family_state(backup.path)
    manifest_state = _file_state(backup.manifest_path)

    live_repository = SqliteRepository(tmp_path / 'live-v14')
    live_repository.create_deck('Replaced')
    live_repository.close()
    restore_database(live_repository.database_file, backup.path)

    assert _sqlite_family_state(
        source_repository.database_file
    ) == source_state
    assert _sqlite_family_state(backup.path) == backup_state
    assert _file_state(backup.manifest_path) == manifest_state
    restored = SqliteRepository(live_repository.data_dir)
    restored_safe = restored.load_cards(safe.id).cards[0]
    restored_advanced = restored.load_cards(advanced.id).cards[0]
    assert (restored_safe.upper_header, restored_safe.lower_header) == ('', '')
    assert (
        restored_advanced.front,
        restored_advanced.back,
        restored_advanced.upper_header,
        restored_advanced.lower_header,
    ) == (
        advanced_card.front,
        advanced_card.back,
        advanced_card.upper_header,
        advanced_card.lower_header,
    )
    restored.close()
