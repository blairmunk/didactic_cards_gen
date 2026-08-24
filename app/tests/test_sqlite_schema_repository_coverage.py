from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from didactic_cards.adapters import sqlite_repository as repository_module
from didactic_cards.adapters import sqlite_schema as schema_module
from didactic_cards.adapters.sqlite_repository import (
    SQLITE_SCHEMA_VERSION,
    SqliteRepository,
    UnsupportedSqliteSchemaError,
)
from didactic_cards.adapters.sqlite_schema import (
    Migration,
    initialize_current_schema,
    migrate_to_current,
    schema_structure_issues,
    user_tables,
)
from didactic_cards.domain.entities import Card, CardDeck
from didactic_cards.domain.rendering import DeckRenderSettings
from didactic_cards.domain.trusted import (
    TemplateProvenance,
    TrustedTemplateVersion,
)


def _downgrade_to_schema_12(database: str) -> None:
    with closing(sqlite3.connect(database)) as connection:
        connection.execute('DROP TABLE schema_migrations')
        connection.execute('PRAGMA application_id = 0')
        connection.execute('PRAGMA user_version = 12')
        connection.commit()


def test_schema_helper_edge_cases_and_idempotent_current_migration(monkeypatch):
    assert schema_module._normalized_schema_sql(None) == ''
    assert schema_module._expected_schema_sql(SQLITE_SCHEMA_VERSION + 1) == {}

    with closing(sqlite3.connect(':memory:')) as connection:
        initialize_current_schema(connection)
        before = connection.iterdump()
        before_dump = tuple(before)

        migrate_to_current(connection, SQLITE_SCHEMA_VERSION)

        assert tuple(connection.iterdump()) == before_dump

    wrong_step = Migration(
        to_version=SQLITE_SCHEMA_VERSION + 1,
        name='skips-a-version',
        apply=lambda _connection: None,
    )
    monkeypatch.setitem(schema_module.MIGRATIONS, 12, wrong_step)
    with closing(sqlite3.connect(':memory:')) as connection:
        with pytest.raises(ValueError, match='no migration path from SQLite schema 12'):
            migrate_to_current(connection, 12)


def test_schema_13_migration_rejects_non_object_typography(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    deck = repository.create_deck('Invalid migration input')
    database = repository.database_file
    repository.close()
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            'UPDATE deck_render_settings SET typography_json = ? '
            'WHERE deck_id = ?',
            ('[]', deck.id),
        )
        connection.execute('DELETE FROM schema_migrations')
        connection.execute(
            'INSERT INTO schema_migrations(version, name, applied_at) '
            "VALUES (13, 'initial-current-schema', "
            "'2026-08-24T00:00:00+00:00')"
        )
        connection.execute('PRAGMA user_version = 13')

        with pytest.raises(ValueError, match='invalid typography settings'):
            migrate_to_current(connection, 13)


def test_schema_structure_reports_metadata_views_and_triggers(monkeypatch):
    with closing(sqlite3.connect(':memory:')) as connection:
        initialize_current_schema(connection)
        connection.execute('CREATE VIEW deck_names AS SELECT name FROM decks')
        connection.execute(
            'CREATE TRIGGER deck_noop AFTER UPDATE ON decks BEGIN SELECT 1; END'
        )
        connection.execute('PRAGMA user_version = 7')
        connection.execute('PRAGMA application_id = 99')

        issues = schema_structure_issues(connection)

        assert (
            f'schema-version: expected {SQLITE_SCHEMA_VERSION}, got 7'
            in issues
        )
        assert (
            f'application-id: expected {schema_module.SQLITE_APPLICATION_ID}, got 99'
            in issues
        )
        assert 'unexpected-trigger: deck_noop' in issues
        assert 'unexpected-view: deck_names' in issues

        # Exercise the deliberately optional index-contract branch. SQL
        # definitions are still checked independently below that branch.
        monkeypatch.setattr(schema_module, 'CURRENT_INDEXES', frozenset())
        assert isinstance(schema_structure_issues(connection), list)


def test_fresh_initialization_validation_failure_rolls_back_every_ddl(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / 'data'
    monkeypatch.setattr(
        repository_module,
        'schema_structure_issues',
        lambda *_args, **_kwargs: ['synthetic-initialization-drift'],
    )

    with pytest.raises(
        UnsupportedSqliteSchemaError,
        match='validation failed during initialization.*synthetic-initialization-drift',
    ):
        SqliteRepository(data_dir)

    with closing(sqlite3.connect(data_dir / 'cards.sqlite3')) as connection:
        assert user_tables(connection) == set()
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 0


def test_initialization_preserves_error_after_transaction_was_already_ended(
    tmp_path, monkeypatch
):
    def fail_after_explicit_rollback(connection):
        connection.rollback()
        raise RuntimeError('initializer ended its transaction')

    monkeypatch.setattr(
        repository_module,
        'initialize_current_schema',
        fail_after_explicit_rollback,
    )

    with pytest.raises(RuntimeError, match='initializer ended its transaction'):
        SqliteRepository(tmp_path / 'data')


def test_schema_12_pre_migration_validation_failure_stops_before_backup(
    tmp_path, monkeypatch
):
    repository = SqliteRepository(tmp_path / 'data')
    database = repository.database_file
    repository.close()
    _downgrade_to_schema_12(str(database))

    real_validator = schema_structure_issues

    def reject_version_12(connection, *, expected_version=SQLITE_SCHEMA_VERSION):
        if expected_version == 12:
            return ['synthetic-pre-migration-drift']
        return real_validator(connection, expected_version=expected_version)

    monkeypatch.setattr(
        repository_module, 'schema_structure_issues', reject_version_12
    )

    with pytest.raises(
        UnsupportedSqliteSchemaError,
        match='validation failed before migration.*synthetic-pre-migration-drift',
    ):
        SqliteRepository(database.parent)

    assert not (database.parent / 'backups').exists()
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 12


def test_post_migration_validation_failure_rolls_back_migration(
    tmp_path, monkeypatch
):
    repository = SqliteRepository(tmp_path / 'data')
    database = repository.database_file
    repository.close()
    _downgrade_to_schema_12(str(database))

    def reject_migrated_schema(_connection, **_kwargs):
        return ['synthetic-post-migration-drift']

    monkeypatch.setattr(
        repository_module,
        'connection_validation_issues',
        reject_migrated_schema,
    )

    with pytest.raises(
        UnsupportedSqliteSchemaError,
        match='validation failed during migration.*synthetic-post-migration-drift',
    ):
        SqliteRepository(database.parent)

    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 12
        assert 'schema_migrations' not in user_tables(connection)


def test_migration_preserves_error_after_transaction_was_already_ended(
    tmp_path, monkeypatch
):
    repository = SqliteRepository(tmp_path / 'data')
    database = repository.database_file
    repository.close()
    _downgrade_to_schema_12(str(database))

    def fail_after_explicit_rollback(connection, version):
        assert version == 12
        connection.rollback()
        raise RuntimeError('migration ended its transaction')

    monkeypatch.setattr(
        repository_module, 'migrate_to_current', fail_after_explicit_rollback
    )

    with pytest.raises(RuntimeError, match='migration ended its transaction'):
        SqliteRepository(database.parent)

    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 12


def test_final_startup_validation_is_not_skipped(tmp_path, monkeypatch):
    repository = SqliteRepository(tmp_path / 'data')
    data_dir = repository.data_dir
    repository.close()
    calls = 0

    def fail_only_final_validation(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return [] if calls == 1 else ['synthetic-final-drift']

    monkeypatch.setattr(
        repository_module,
        'schema_structure_issues',
        fail_only_final_validation,
    )

    with pytest.raises(
        UnsupportedSqliteSchemaError,
        match='SQLite schema validation failed: synthetic-final-drift',
    ):
        SqliteRepository(data_dir)

    assert calls == 2


def test_close_is_idempotent_and_releases_lease_only_once():
    class RecordingLease:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    repository = object.__new__(SqliteRepository)
    lease = RecordingLease()
    repository._runtime_lease = lease
    repository._closed = False

    repository.close()
    repository.close()

    assert lease.close_calls == 1
    assert repository._runtime_lease is None
    assert repository._closed is True


def test_transaction_does_not_rollback_when_begin_never_opened_one():
    class ConnectionWithoutTransaction:
        in_transaction = False

        def __init__(self):
            self.rollback_calls = 0
            self.closed = False

        def execute(self, _statement):
            return None

        def commit(self):
            raise AssertionError('commit must not be reached')

        def rollback(self):
            self.rollback_calls += 1

        def close(self):
            self.closed = True

    repository = object.__new__(SqliteRepository)
    connection = ConnectionWithoutTransaction()
    repository._connect = lambda: connection

    with pytest.raises(RuntimeError, match='body failed'):
        with repository._transaction():
            raise RuntimeError('body failed')

    assert connection.rollback_calls == 0
    assert connection.closed is True


def test_integrity_operational_error_is_bounded_only_for_quick_checks(
    tmp_path, monkeypatch
):
    repository = SqliteRepository(tmp_path / 'data')

    def unavailable(*_args, **_kwargs):
        raise sqlite3.OperationalError('database is temporarily unavailable')

    monkeypatch.setattr(
        repository_module, 'connection_validation_issues', unavailable
    )
    try:
        assert repository.integrity_check(quick=True) == [
            'readiness-check-timeout'
        ]
        with pytest.raises(sqlite3.OperationalError, match='temporarily unavailable'):
            repository.integrity_check()
    finally:
        repository.close()


@pytest.mark.parametrize(
    ('payload', 'message'),
    [
        (None, 'Invalid typography settings'),
        ('not-json', 'Invalid typography settings'),
        ('[]', 'must be an object'),
        ('{"unknown_field": true}', 'Unknown typography settings'),
    ],
)
def test_settings_row_rejects_every_malformed_typography_shape(payload, message):
    row = {
        'preset': 'centered',
        'horizontal_alignment': 'center',
        'vertical_alignment': 'center',
        'header_visibility': 'none',
        'header_position': 'top',
        'header_alignment': 'left',
        'header_repeat': 'every-card',
        'section_break': 'continuous',
        'typography_json': payload,
    }

    with pytest.raises(ValueError, match=message):
        SqliteRepository._settings_from_row(row)


def test_advanced_deck_and_imported_template_history_are_created_atomically(
    tmp_path,
):
    repository = SqliteRepository(tmp_path / 'data')
    cards = CardDeck([Card(front='raw front', back='raw back')])
    templates = (
        TrustedTemplateVersion(
            deck_id='source-deck',
            front_source='{{ content }}',
            back_source='{{ content }}',
            version=8,
        ),
        TrustedTemplateVersion(
            deck_id='source-deck',
            front_source=r'\fbox{{ content }}',
            back_source=r'\emph{{ content }}',
            version=9,
        ),
    )

    try:
        deck = repository.create_deck_with_cards_and_trusted(
            'Imported raw deck',
            'all fields were prepared elsewhere',
            None,
            cards,
            DeckRenderSettings(authoring_mode='advanced'),
            templates,
        )

        assert repository.load_cards(deck.id).cards == cards.cards
        history = repository.list_trusted_templates(deck.id)
        assert [item.version for item in history] == [1, 2]
        assert all(
            item.provenance is TemplateProvenance.IMPORTED for item in history
        )
        assert all(item.deck_id == deck.id for item in history)
    finally:
        repository.close()
