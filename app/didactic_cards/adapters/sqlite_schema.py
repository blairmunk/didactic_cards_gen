from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from contextlib import closing
from functools import lru_cache
import json
import re
import sqlite3
from typing import Callable


SQLITE_APPLICATION_ID = 0x44434731  # "DCG1"
SQLITE_SCHEMA_VERSION = 14
MINIMUM_MIGRATABLE_SCHEMA_VERSION = 12


SCHEMA_12_TABLE_COLUMNS: dict[str, frozenset[str]] = {
    'decks': frozenset({
        'id', 'parent_id', 'name', 'description', 'created_at', 'updated_at',
        'version',
    }),
    'cards': frozenset({
        'id', 'parent_id', 'front', 'back', 'section', 'upper_header',
        'lower_header', 'created_at', 'updated_at', 'version',
    }),
    'deck_cards': frozenset({'deck_id', 'card_id', 'position'}),
    'deck_render_settings': frozenset({
        'deck_id', 'preset', 'horizontal_alignment', 'vertical_alignment',
        'header_visibility', 'header_position', 'header_alignment',
        'header_repeat', 'section_break', 'typography_json',
    }),
    'printer_profiles': frozenset({
        'key', 'name', 'duplex_mode', 'back_rotation_deg',
        'front_offset_x_mm', 'front_offset_y_mm', 'back_offset_x_mm',
        'back_offset_y_mm', 'back_border', 'registration_marks',
    }),
    'trusted_templates': frozenset({
        'id', 'deck_id', 'version', 'source_hash', 'front_source',
        'back_source', 'provenance', 'status', 'origin_template_id',
        'created_at', 'approved_at',
    }),
}

SCHEMA_13_TABLE_COLUMNS = {
    **SCHEMA_12_TABLE_COLUMNS,
    'schema_migrations': frozenset({'version', 'name', 'applied_at'}),
}
CURRENT_TABLE_COLUMNS = SCHEMA_13_TABLE_COLUMNS

SCHEMA_12_INDEXES = frozenset({
    'idx_deck_cards_position',
    'idx_trusted_templates_one_approved',
})

SCHEMA_13_INDEXES = SCHEMA_12_INDEXES
CURRENT_INDEXES = SCHEMA_13_INDEXES


CREATE_SCHEMA_STATEMENTS = (
    '''
    CREATE TABLE decks (
        id TEXT PRIMARY KEY,
        parent_id TEXT,
        name TEXT NOT NULL,
        description TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0)
    )
    ''',
    '''
    CREATE TABLE cards (
        id TEXT PRIMARY KEY,
        parent_id TEXT,
        front TEXT NOT NULL,
        back TEXT NOT NULL,
        section TEXT NOT NULL DEFAULT '',
        upper_header TEXT NOT NULL DEFAULT '',
        lower_header TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0)
    )
    ''',
    '''
    CREATE TABLE deck_cards (
        deck_id TEXT NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
        card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
        position INTEGER NOT NULL CHECK (position >= 0),
        PRIMARY KEY (deck_id, card_id),
        UNIQUE (deck_id, position)
    )
    ''',
    '''
    CREATE INDEX idx_deck_cards_position
    ON deck_cards(deck_id, position)
    ''',
    '''
    CREATE TABLE deck_render_settings (
        deck_id TEXT PRIMARY KEY REFERENCES decks(id) ON DELETE CASCADE,
        preset TEXT NOT NULL CHECK (preset IN ('centered', 'custom')),
        horizontal_alignment TEXT NOT NULL CHECK (
            horizontal_alignment IN ('left', 'center', 'right')
        ),
        vertical_alignment TEXT NOT NULL CHECK (
            vertical_alignment IN ('top', 'center', 'bottom')
        ),
        header_visibility TEXT NOT NULL CHECK (
            header_visibility IN ('none', 'front', 'back', 'both')
        ),
        header_position TEXT NOT NULL CHECK (
            header_position IN ('top', 'bottom')
        ),
        header_alignment TEXT NOT NULL CHECK (
            header_alignment IN ('left', 'center', 'right')
        ),
        header_repeat TEXT NOT NULL DEFAULT 'every-card' CHECK (
            header_repeat IN ('every-card', 'section-start')
        ),
        section_break TEXT NOT NULL DEFAULT 'continuous' CHECK (
            section_break IN ('continuous', 'new-row', 'new-sheet')
        ),
        typography_json TEXT NOT NULL DEFAULT '{}'
    )
    ''',
    '''
    CREATE TABLE printer_profiles (
        key TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        duplex_mode TEXT NOT NULL CHECK (
            duplex_mode IN ('long-edge', 'short-edge')
        ),
        back_rotation_deg INTEGER NOT NULL DEFAULT 180 CHECK (
            back_rotation_deg IN (0, 180)
        ),
        front_offset_x_mm REAL NOT NULL,
        front_offset_y_mm REAL NOT NULL,
        back_offset_x_mm REAL NOT NULL,
        back_offset_y_mm REAL NOT NULL,
        back_border INTEGER NOT NULL CHECK (back_border IN (0, 1)),
        registration_marks INTEGER NOT NULL CHECK (
            registration_marks IN (0, 1)
        )
    )
    ''',
    '''
    CREATE TABLE trusted_templates (
        id TEXT PRIMARY KEY,
        deck_id TEXT NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
        version INTEGER NOT NULL CHECK (version > 0),
        source_hash TEXT NOT NULL,
        front_source TEXT NOT NULL,
        back_source TEXT NOT NULL,
        provenance TEXT NOT NULL CHECK (
            provenance IN ('local-author', 'imported', 'cloned')
        ),
        status TEXT NOT NULL CHECK (
            status IN ('quarantined', 'approved', 'revoked')
        ),
        origin_template_id TEXT,
        created_at TEXT NOT NULL,
        approved_at TEXT,
        UNIQUE(deck_id, version)
    )
    ''',
    '''
    CREATE UNIQUE INDEX idx_trusted_templates_one_approved
    ON trusted_templates(deck_id) WHERE status = 'approved'
    ''',
    '''
    CREATE TABLE schema_migrations (
        version INTEGER PRIMARY KEY CHECK (version > 0),
        name TEXT NOT NULL,
        applied_at TEXT NOT NULL
    )
    ''',
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def user_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def initialize_current_schema(connection: sqlite3.Connection) -> None:
    if user_tables(connection):
        raise ValueError('schema 0 is not an empty database')
    for statement in CREATE_SCHEMA_STATEMENTS:
        connection.execute(statement)
    connection.execute(
        'INSERT INTO schema_migrations(version, name, applied_at) '
        'VALUES (?, ?, ?)',
        (SQLITE_SCHEMA_VERSION, 'initial-current-schema', utc_now_iso()),
    )
    connection.execute(f'PRAGMA application_id = {SQLITE_APPLICATION_ID}')
    connection.execute(f'PRAGMA user_version = {SQLITE_SCHEMA_VERSION}')


def _migrate_12_to_13(connection: sqlite3.Connection) -> None:
    connection.execute(
        '''
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY CHECK (version > 0),
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        '''
    )
    connection.execute(
        'INSERT INTO schema_migrations(version, name, applied_at) '
        'VALUES (13, ?, ?)',
        ('storage-foundation', utc_now_iso()),
    )
    connection.execute(f'PRAGMA application_id = {SQLITE_APPLICATION_ID}')


def _migrate_13_to_14(connection: sqlite3.Connection) -> None:
    safe_deck_ids: list[str] = []
    for row in connection.execute(
        'SELECT deck_id, typography_json FROM deck_render_settings '
        'ORDER BY deck_id'
    ):
        typography = json.loads(row[1])
        if not isinstance(typography, dict):
            raise ValueError('invalid typography settings during migration')
        if typography.get('authoring_mode', 'safe') == 'safe':
            safe_deck_ids.append(row[0])
    for deck_id in safe_deck_ids:
        connection.execute(
            '''
            UPDATE cards SET upper_header = '', lower_header = ''
            WHERE id IN (
                SELECT card_id FROM deck_cards WHERE deck_id = ?
            )
            ''',
            (deck_id,),
        )
    connection.execute(
        'INSERT INTO schema_migrations(version, name, applied_at) '
        'VALUES (14, ?, ?)',
        ('enforce-mode-card-fields', utc_now_iso()),
    )


@dataclass(frozen=True)
class Migration:
    to_version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


MIGRATIONS: dict[int, Migration] = {
    12: Migration(13, 'storage-foundation', _migrate_12_to_13),
    13: Migration(14, 'enforce-mode-card-fields', _migrate_13_to_14),
}


def migrate_to_current(connection: sqlite3.Connection, version: int) -> None:
    if version > SQLITE_SCHEMA_VERSION:
        raise ValueError(
            f'cannot migrate future SQLite schema {version} to '
            f'{SQLITE_SCHEMA_VERSION}'
        )
    current = version
    while current < SQLITE_SCHEMA_VERSION:
        migration = MIGRATIONS.get(current)
        if migration is None or migration.to_version != current + 1:
            raise ValueError(f'no migration path from SQLite schema {current}')
        migration.apply(connection)
        current = migration.to_version
        connection.execute(f'PRAGMA user_version = {current}')


def _normalized_schema_sql(source: str | None) -> str:
    if source is None:
        return ''
    return re.sub(r'\s+', '', source).lower()


@lru_cache(maxsize=3)
def _expected_schema_sql(expected_version: int) -> dict[tuple[str, str], str]:
    if expected_version not in {12, 13, SQLITE_SCHEMA_VERSION}:
        return {}
    statements = (
        CREATE_SCHEMA_STATEMENTS[:-1]
        if expected_version == 12
        else CREATE_SCHEMA_STATEMENTS
    )
    with closing(sqlite3.connect(':memory:')) as expected:
        for statement in statements:
            expected.execute(statement)
        return {
            (row[0], row[1]): _normalized_schema_sql(row[2])
            for row in expected.execute(
                "SELECT type, name, sql FROM sqlite_master "
                "WHERE type IN ('table', 'index') "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }


def schema_structure_issues(
    connection: sqlite3.Connection,
    *,
    expected_version: int = SQLITE_SCHEMA_VERSION,
) -> list[str]:
    expected_tables = (
        CURRENT_TABLE_COLUMNS
        if expected_version == SQLITE_SCHEMA_VERSION
        else SCHEMA_13_TABLE_COLUMNS if expected_version == 13
        else SCHEMA_12_TABLE_COLUMNS if expected_version == 12
        else None
    )
    if expected_tables is None:
        return [f'unsupported-schema-version: {expected_version}']

    issues: list[str] = []
    actual_version = connection.execute('PRAGMA user_version').fetchone()[0]
    if actual_version != expected_version:
        issues.append(
            f'schema-version: expected {expected_version}, got {actual_version}'
        )
    application_id = connection.execute('PRAGMA application_id').fetchone()[0]
    expected_application_id = SQLITE_APPLICATION_ID if expected_version >= 13 else 0
    if application_id != expected_application_id:
        issues.append(
            'application-id: expected '
            f'{expected_application_id}, got {application_id}'
        )

    actual_tables = user_tables(connection)
    missing_tables = set(expected_tables) - actual_tables
    unexpected_tables = actual_tables - set(expected_tables)
    issues.extend(f'missing-table: {name}' for name in sorted(missing_tables))
    issues.extend(
        f'unexpected-table: {name}' for name in sorted(unexpected_tables)
    )
    for table, expected_columns in expected_tables.items():
        if table not in actual_tables:
            continue
        actual_columns = {
            row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
        }
        for name in sorted(expected_columns - actual_columns):
            issues.append(f'missing-column: {table}.{name}')
        for name in sorted(actual_columns - expected_columns):
            issues.append(f'unexpected-column: {table}.{name}')

    expected_indexes = (
        CURRENT_INDEXES
        if expected_version == SQLITE_SCHEMA_VERSION
        else SCHEMA_13_INDEXES if expected_version == 13
        else SCHEMA_12_INDEXES
    )
    if expected_indexes:
        actual_indexes = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
            )
        }
        issues.extend(
            f'missing-index: {name}'
            for name in sorted(expected_indexes - actual_indexes)
        )
        issues.extend(
            f'unexpected-index: {name}'
            for name in sorted(actual_indexes - expected_indexes)
        )

    expected_sql = _expected_schema_sql(expected_version)
    actual_sql = {
        (row[0], row[1]): _normalized_schema_sql(row[2])
        for row in connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE type IN ('table', 'index') "
            "AND name NOT LIKE 'sqlite_%'"
        )
    }
    for object_key in sorted(set(expected_sql) & set(actual_sql)):
        if actual_sql[object_key] != expected_sql[object_key]:
            issues.append(
                f'{object_key[0]}-definition: {object_key[1]}'
            )
    for row in connection.execute(
        "SELECT type, name FROM sqlite_master "
        "WHERE type IN ('view', 'trigger') AND name NOT LIKE 'sqlite_%' "
        "ORDER BY type, name"
    ):
        issues.append(f'unexpected-{row[0]}: {row[1]}')
    return issues
