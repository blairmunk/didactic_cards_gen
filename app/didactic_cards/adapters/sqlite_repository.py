from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional, TypeVar

from ..domain.entities import Card, CardDeck, Deck
from ..domain.interfaces import (
    CardRepository,
    ConcurrentModificationError,
    DeckRepository,
)
from ..domain.printing import PrinterProfile
from ..domain.rendering import DeckRenderSettings
from ..domain.trusted import (
    ContentMode,
    PrintJobSnapshot,
    TemplateProvenance,
    TemplateStatus,
    TrustedTemplateVersion,
)
from .json_repository import DeckNotFoundError, JsonRepository


MutationResult = TypeVar('MutationResult')
SQLITE_SCHEMA_VERSION = 7


class LegacyMigrationError(ValueError):
    """Raised when legacy JSON cannot be imported without guessing."""


class UnsupportedSqliteSchemaError(ValueError):
    """Raised instead of opening a database from a newer application version."""


class SqliteRepository(DeckRepository, CardRepository):
    """Transactional SQLite persistence with ordered deck membership."""

    def __init__(self, data_dir: str | Path, database_name: str = 'cards.sqlite3'):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database_file = self.data_dir / database_name
        self.legacy_backup_dir = self.data_dir / 'legacy-json-backup-v1'
        self._initialize_database()
        self._migrate_legacy_json_once()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_file, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        connection.execute('PRAGMA busy_timeout = 30000')
        return connection

    @contextmanager
    def _transaction(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            if write:
                connection.execute('BEGIN IMMEDIATE')
            yield connection
            if write:
                connection.commit()
        except Exception:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_database(self) -> None:
        with self._transaction(write=True) as connection:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version not in {0, 1, 2, 3, 4, 5, 6, SQLITE_SCHEMA_VERSION}:
                raise UnsupportedSqliteSchemaError(
                    f'Unsupported SQLite schema {version}; '
                    f'expected {SQLITE_SCHEMA_VERSION}'
                )
            connection.executescript(
                '''
                CREATE TABLE IF NOT EXISTS repository_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decks (
                    id TEXT PRIMARY KEY,
                    parent_id TEXT,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0)
                );
                CREATE TABLE IF NOT EXISTS cards (
                    id TEXT PRIMARY KEY,
                    parent_id TEXT,
                    front TEXT NOT NULL,
                    back TEXT NOT NULL,
                    section TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0)
                );
                CREATE TABLE IF NOT EXISTS deck_cards (
                    deck_id TEXT NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
                    card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL CHECK (position >= 0),
                    PRIMARY KEY (deck_id, card_id),
                    UNIQUE (deck_id, position)
                );
                CREATE INDEX IF NOT EXISTS idx_deck_cards_position
                    ON deck_cards(deck_id, position);
                CREATE TABLE IF NOT EXISTS deck_render_settings (
                    deck_id TEXT PRIMARY KEY
                        REFERENCES decks(id) ON DELETE CASCADE,
                    preset TEXT NOT NULL CHECK (
                        preset IN ('legacy-top-left', 'centered', 'custom')
                    ),
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
                    )
                );
                CREATE TABLE IF NOT EXISTS printer_profiles (
                    key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    duplex_mode TEXT NOT NULL
                        CHECK (duplex_mode IN ('long-edge', 'short-edge')),
                    back_rotation_deg INTEGER NOT NULL DEFAULT 180
                        CHECK (back_rotation_deg IN (0, 180)),
                    front_offset_x_mm REAL NOT NULL,
                    front_offset_y_mm REAL NOT NULL,
                    back_offset_x_mm REAL NOT NULL,
                    back_offset_y_mm REAL NOT NULL,
                    back_border INTEGER NOT NULL CHECK (back_border IN (0, 1)),
                    registration_marks INTEGER NOT NULL
                        CHECK (registration_marks IN (0, 1))
                );
                CREATE TABLE IF NOT EXISTS trusted_templates (
                    id TEXT PRIMARY KEY,
                    deck_id TEXT NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL CHECK (version > 0),
                    source TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    front_content_mode TEXT NOT NULL DEFAULT 'escaped' CHECK (
                        front_content_mode IN ('escaped', 'raw')
                    ),
                    back_content_mode TEXT NOT NULL DEFAULT 'escaped' CHECK (
                        back_content_mode IN ('escaped', 'raw')
                    ),
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
                );
                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_trusted_templates_one_approved
                    ON trusted_templates(deck_id) WHERE status = 'approved';
                '''
            )
            profile_columns = {
                row['name'] for row in connection.execute(
                    'PRAGMA table_info(printer_profiles)'
                )
            }
            if 'back_rotation_deg' not in profile_columns:
                connection.execute(
                    '''
                    ALTER TABLE printer_profiles
                    ADD COLUMN back_rotation_deg INTEGER NOT NULL DEFAULT 0
                        CHECK (back_rotation_deg IN (0, 180))
                    '''
                )
                connection.execute(
                    '''
                    UPDATE printer_profiles SET back_rotation_deg = 180
                    WHERE duplex_mode = 'long-edge'
                    '''
                )
            card_columns = {
                row['name'] for row in connection.execute(
                    'PRAGMA table_info(cards)'
                )
            }
            if 'section' not in card_columns:
                connection.execute(
                    "ALTER TABLE cards ADD COLUMN section TEXT NOT NULL DEFAULT ''"
                )
            if version < 4:
                legacy = DeckRenderSettings.legacy()
                connection.execute(
                    '''
                    INSERT OR IGNORE INTO deck_render_settings(
                        deck_id, preset, horizontal_alignment,
                        vertical_alignment, header_visibility,
                        header_position, header_alignment
                    )
                    SELECT id, ?, ?, ?, ?, ?, ? FROM decks
                    ''',
                    (
                        legacy.preset.value,
                        legacy.horizontal_alignment.value,
                        legacy.vertical_alignment.value,
                        legacy.header_visibility.value,
                        legacy.header_position.value,
                        legacy.header_alignment.value,
                    ),
                )
            settings_columns = {
                row['name'] for row in connection.execute(
                    'PRAGMA table_info(deck_render_settings)'
                )
            }
            if 'header_repeat' not in settings_columns:
                connection.execute(
                    """
                    ALTER TABLE deck_render_settings
                    ADD COLUMN header_repeat TEXT NOT NULL DEFAULT 'every-card'
                        CHECK (header_repeat IN ('every-card', 'section-start'))
                    """
                )
            if 'section_break' not in settings_columns:
                connection.execute(
                    """
                    ALTER TABLE deck_render_settings
                    ADD COLUMN section_break TEXT NOT NULL DEFAULT 'continuous'
                        CHECK (section_break IN ('continuous', 'new-row', 'new-sheet'))
                    """
                )
            template_columns = {
                row['name'] for row in connection.execute(
                    'PRAGMA table_info(trusted_templates)'
                )
            }
            for column in ('front_content_mode', 'back_content_mode'):
                if column not in template_columns:
                    connection.execute(
                        f"""
                        ALTER TABLE trusted_templates
                        ADD COLUMN {column} TEXT NOT NULL DEFAULT 'escaped'
                            CHECK ({column} IN ('escaped', 'raw'))
                        """
                    )
            if version < SQLITE_SCHEMA_VERSION:
                connection.execute(f'PRAGMA user_version = {SQLITE_SCHEMA_VERSION}')
        connection = self._connect()
        try:
            connection.execute('PRAGMA journal_mode = WAL')
        finally:
            connection.close()

    def _meta(self, connection: sqlite3.Connection, key: str) -> str | None:
        row = connection.execute(
            'SELECT value FROM repository_meta WHERE key = ?', (key,)
        ).fetchone()
        return row['value'] if row else None

    def _set_meta(self, connection: sqlite3.Connection, key: str, value: str) -> None:
        connection.execute(
            '''
            INSERT INTO repository_meta(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            ''',
            (key, value),
        )

    def _backup_legacy_json(self, legacy: JsonRepository) -> None:
        self.legacy_backup_dir.mkdir(parents=True, exist_ok=True)
        sources = [legacy.decks_file, legacy.manifest_file]
        sources.extend(sorted(legacy.cards_dir.glob('*.json')))
        with legacy._transaction():
            for source in sources:
                relative = source.relative_to(legacy.data_dir)
                destination = self.legacy_backup_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                legacy._copy_once(source, destination)

    def _migrate_legacy_json_once(self) -> None:
        legacy_decks = self.data_dir / 'decks.json'
        with self._transaction() as connection:
            already_migrated = self._meta(connection, 'legacy_json_migrated_at')
        if already_migrated or not legacy_decks.exists():
            return

        legacy = JsonRepository(self.data_dir)
        report = legacy.scan_integrity()
        issue_codes = {issue.code for issue in report.issues}
        blocking_codes = issue_codes - {'card-id-mismatch'}
        if blocking_codes:
            codes = ', '.join(sorted(blocking_codes))
            raise LegacyMigrationError(
                f'Legacy JSON integrity check failed ({codes}); SQLite import aborted'
            )
        decks = legacy.list_decks()
        self._backup_legacy_json(legacy)
        cards_by_deck = {
            deck.id: CardDeck.from_list(
                legacy._read_json(legacy._cards_path(deck.id))
            )
            for deck in decks
        }

        with self._transaction(write=True) as connection:
            if self._meta(connection, 'legacy_json_migrated_at'):
                return
            if connection.execute('SELECT COUNT(*) FROM decks').fetchone()[0]:
                raise LegacyMigrationError(
                    'SQLite already contains decks; automatic legacy import aborted'
                )
            for deck in decks:
                self._insert_deck(connection, deck)
                self._replace_cards(connection, deck.id, cards_by_deck[deck.id])
            self._set_meta(
                connection,
                'legacy_json_migrated_at',
                datetime.now(timezone.utc).isoformat(),
            )
            if issue_codes:
                self._set_meta(
                    connection,
                    'legacy_json_migration_warnings',
                    ','.join(sorted(issue_codes)),
                )

    @staticmethod
    def _settings_from_row(row: sqlite3.Row) -> DeckRenderSettings:
        return DeckRenderSettings(
            preset=row['preset'],
            horizontal_alignment=row['horizontal_alignment'],
            vertical_alignment=row['vertical_alignment'],
            header_visibility=row['header_visibility'],
            header_position=row['header_position'],
            header_alignment=row['header_alignment'],
            header_repeat=row['header_repeat'],
            section_break=row['section_break'],
        )

    def _get_render_settings(
        self, connection: sqlite3.Connection, deck_id: str
    ) -> DeckRenderSettings:
        row = connection.execute(
            'SELECT * FROM deck_render_settings WHERE deck_id = ?',
            (deck_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f'Missing render settings for deck {deck_id}')
        return self._settings_from_row(row)

    @staticmethod
    def _deck_from_row(
        row: sqlite3.Row,
        card_ids: list[str],
        render_settings: DeckRenderSettings,
    ) -> Deck:
        return Deck(
            id=row['id'],
            parent_id=row['parent_id'],
            name=row['name'],
            description=row['description'],
            card_ids=card_ids,
            render_settings=render_settings,
            created_at=datetime.fromisoformat(row['created_at']),
            updated_at=datetime.fromisoformat(row['updated_at']),
            version=row['version'],
        )

    @staticmethod
    def _card_from_row(row: sqlite3.Row) -> Card:
        return Card(
            id=row['id'],
            parent_id=row['parent_id'],
            front=row['front'],
            back=row['back'],
            section=row['section'],
            created_at=datetime.fromisoformat(row['created_at']),
            updated_at=datetime.fromisoformat(row['updated_at']),
        )

    @staticmethod
    def _trusted_template_from_row(
        row: sqlite3.Row,
    ) -> TrustedTemplateVersion:
        if not row['source_hash']:
            raise ValueError('trusted template source hash is missing')
        return TrustedTemplateVersion(
            id=row['id'],
            deck_id=row['deck_id'],
            version=row['version'],
            source=row['source'],
            source_hash=row['source_hash'],
            front_content_mode=row['front_content_mode'],
            back_content_mode=row['back_content_mode'],
            provenance=row['provenance'],
            status=row['status'],
            origin_template_id=row['origin_template_id'],
            created_at=datetime.fromisoformat(row['created_at']),
            approved_at=(
                datetime.fromisoformat(row['approved_at'])
                if row['approved_at'] else None
            ),
        )

    @staticmethod
    def _insert_trusted_template(
        connection: sqlite3.Connection,
        template: TrustedTemplateVersion,
    ) -> None:
        connection.execute(
            '''
            INSERT INTO trusted_templates(
                id, deck_id, version, source, source_hash, provenance,
                status, origin_template_id, created_at, approved_at,
                front_content_mode, back_content_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                template.id,
                template.deck_id,
                template.version,
                template.source,
                template.source_hash,
                template.provenance.value,
                template.status.value,
                template.origin_template_id,
                template.created_at.isoformat(),
                template.approved_at.isoformat()
                if template.approved_at else None,
                template.front_content_mode.value,
                template.back_content_mode.value,
            ),
        )

    def _card_ids(self, connection: sqlite3.Connection, deck_id: str) -> list[str]:
        rows = connection.execute(
            '''
            SELECT card_id FROM deck_cards
            WHERE deck_id = ? ORDER BY position
            ''',
            (deck_id,),
        ).fetchall()
        return [row['card_id'] for row in rows]

    def _get_deck(
        self, connection: sqlite3.Connection, deck_id: str
    ) -> Optional[Deck]:
        row = connection.execute(
            'SELECT * FROM decks WHERE id = ?', (deck_id,)
        ).fetchone()
        if row is None:
            return None
        return self._deck_from_row(
            row,
            self._card_ids(connection, deck_id),
            self._get_render_settings(connection, deck_id),
        )

    def _load_cards(
        self, connection: sqlite3.Connection, deck_id: str
    ) -> CardDeck:
        if self._get_deck(connection, deck_id) is None:
            raise DeckNotFoundError(deck_id)
        rows = connection.execute(
            '''
            SELECT cards.* FROM cards
            JOIN deck_cards ON deck_cards.card_id = cards.id
            WHERE deck_cards.deck_id = ?
            ORDER BY deck_cards.position
            ''',
            (deck_id,),
        ).fetchall()
        return CardDeck([self._card_from_row(row) for row in rows])

    @staticmethod
    def _insert_deck(connection: sqlite3.Connection, deck: Deck) -> None:
        connection.execute(
            '''
            INSERT INTO decks(
                id, parent_id, name, description, created_at, updated_at, version
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                deck.id,
                deck.parent_id,
                deck.name,
                deck.description,
                deck.created_at.isoformat(),
                deck.updated_at.isoformat(),
                deck.version,
            ),
        )
        settings = deck.render_settings
        connection.execute(
            '''
            INSERT INTO deck_render_settings(
                deck_id, preset, horizontal_alignment,
                vertical_alignment, header_visibility,
                header_position, header_alignment, header_repeat,
                section_break
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                deck.id,
                settings.preset.value,
                settings.horizontal_alignment.value,
                settings.vertical_alignment.value,
                settings.header_visibility.value,
                settings.header_position.value,
                settings.header_alignment.value,
                settings.header_repeat.value,
                settings.section_break.value,
            ),
        )

    def _replace_cards(
        self,
        connection: sqlite3.Connection,
        deck_id: str,
        card_deck: CardDeck,
    ) -> None:
        if self._get_deck(connection, deck_id) is None:
            raise DeckNotFoundError(deck_id)
        card_ids = [card.id for card in card_deck.cards]
        if len(card_ids) != len(set(card_ids)):
            raise ValueError('Duplicate card IDs are not allowed')
        if card_ids:
            placeholders = ','.join('?' for _ in card_ids)
            owner = connection.execute(
                f'''
                SELECT deck_id, card_id FROM deck_cards
                WHERE card_id IN ({placeholders}) AND deck_id != ? LIMIT 1
                ''',
                (*card_ids, deck_id),
            ).fetchone()
            if owner is not None:
                raise ValueError(
                    f"Card {owner['card_id']} already belongs to another deck"
                )

        old_ids = self._card_ids(connection, deck_id)
        connection.execute('DELETE FROM deck_cards WHERE deck_id = ?', (deck_id,))
        for position, card in enumerate(card_deck.cards):
            connection.execute(
                '''
                INSERT INTO cards(
                    id, parent_id, front, back, section, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    parent_id = excluded.parent_id,
                    front = excluded.front,
                    back = excluded.back,
                    section = excluded.section,
                    updated_at = excluded.updated_at,
                    version = cards.version + 1
                ''',
                (
                    card.id,
                    card.parent_id,
                    card.front,
                    card.back,
                    card.section,
                    card.created_at.isoformat(),
                    card.updated_at.isoformat(),
                ),
            )
            connection.execute(
                'INSERT INTO deck_cards(deck_id, card_id, position) VALUES (?, ?, ?)',
                (deck_id, card.id, position),
            )

        removed_ids = set(old_ids) - set(card_ids)
        for card_id in removed_ids:
            connection.execute(
                '''
                DELETE FROM cards WHERE id = ?
                AND NOT EXISTS (
                    SELECT 1 FROM deck_cards WHERE deck_cards.card_id = cards.id
                )
                ''',
                (card_id,),
            )
        connection.execute(
            'UPDATE decks SET updated_at = ?, version = version + 1 WHERE id = ?',
            (datetime.now(timezone.utc).isoformat(), deck_id),
        )

    def list_decks(self) -> list[Deck]:
        with self._transaction() as connection:
            rows = connection.execute(
                'SELECT * FROM decks ORDER BY updated_at DESC'
            ).fetchall()
            return [
                self._deck_from_row(
                    row,
                    self._card_ids(connection, row['id']),
                    self._get_render_settings(connection, row['id']),
                )
                for row in rows
            ]

    def get_deck(self, deck_id: str) -> Optional[Deck]:
        with self._transaction() as connection:
            return self._get_deck(connection, deck_id)

    def create_deck(self, name: str, description: str = '') -> Deck:
        deck = Deck(name=name, description=description)
        with self._transaction(write=True) as connection:
            self._insert_deck(connection, deck)
        return deck

    def update_deck(
        self, deck_id: str, name: str, description: str = ''
    ) -> Optional[Deck]:
        updated_at = datetime.now(timezone.utc)
        with self._transaction(write=True) as connection:
            cursor = connection.execute(
                '''
                UPDATE decks SET name = ?, description = ?, updated_at = ?,
                    version = version + 1
                WHERE id = ?
                ''',
                (name, description, updated_at.isoformat(), deck_id),
            )
            if cursor.rowcount == 0:
                return None
            return self._get_deck(connection, deck_id)

    def delete_deck(self, deck_id: str) -> bool:
        with self._transaction(write=True) as connection:
            card_ids = self._card_ids(connection, deck_id)
            cursor = connection.execute('DELETE FROM decks WHERE id = ?', (deck_id,))
            if cursor.rowcount == 0:
                return False
            for card_id in card_ids:
                connection.execute(
                    'DELETE FROM cards WHERE id = ? '
                    'AND NOT EXISTS (SELECT 1 FROM deck_cards WHERE card_id = ?)',
                    (card_id, card_id),
                )
            return True

    def clone_deck(self, deck_id: str) -> Optional[Deck]:
        with self._transaction(write=True) as connection:
            source = self._get_deck(connection, deck_id)
            if source is None:
                return None
            cards = self._load_cards(connection, deck_id)
            cloned_cards = CardDeck([card.clone() for card in cards.cards])
            clone = Deck(
                name=f'{source.name} (копия)',
                description=source.description,
                parent_id=source.id,
                card_ids=[card.id for card in cloned_cards.cards],
                render_settings=source.render_settings,
            )
            self._insert_deck(connection, clone)
            self._replace_cards(connection, clone.id, cloned_cards)
            template_rows = connection.execute(
                '''
                SELECT * FROM trusted_templates
                WHERE deck_id = ? ORDER BY version
                ''',
                (source.id,),
            ).fetchall()
            for version, row in enumerate(template_rows, start=1):
                self._insert_trusted_template(
                    connection,
                    TrustedTemplateVersion(
                        deck_id=clone.id,
                        version=version,
                        source=row['source'],
                        front_content_mode=row['front_content_mode'],
                        back_content_mode=row['back_content_mode'],
                        provenance=TemplateProvenance.CLONED,
                        origin_template_id=row['id'],
                    ),
                )
            return clone

    def create_deck_with_cards(
        self,
        name: str,
        description: str,
        parent_id: str | None,
        cards: CardDeck,
        render_settings: DeckRenderSettings | None = None,
    ) -> Deck:
        deck = Deck(
            name=name,
            description=description,
            parent_id=parent_id,
            card_ids=[card.id for card in cards.cards],
            render_settings=(
                render_settings
                if render_settings is not None
                else DeckRenderSettings.centered()
            ),
        )
        with self._transaction(write=True) as connection:
            self._insert_deck(connection, deck)
            self._replace_cards(connection, deck.id, cards)
            return self._get_deck(connection, deck.id)

    def create_deck_with_cards_and_trusted(
        self,
        name: str,
        description: str,
        parent_id: str | None,
        cards: CardDeck,
        render_settings: DeckRenderSettings,
        trusted_templates: tuple[TrustedTemplateVersion, ...],
    ) -> Deck:
        deck = Deck(
            name=name,
            description=description,
            parent_id=parent_id,
            card_ids=[card.id for card in cards.cards],
            render_settings=render_settings,
        )
        with self._transaction(write=True) as connection:
            self._insert_deck(connection, deck)
            self._replace_cards(connection, deck.id, cards)
            for version, template in enumerate(trusted_templates, start=1):
                self._insert_trusted_template(
                    connection,
                    TrustedTemplateVersion(
                        id=template.id,
                        deck_id=deck.id,
                        version=version,
                        source=template.source,
                        source_hash=template.source_hash,
                        provenance=TemplateProvenance.IMPORTED,
                        origin_template_id=template.origin_template_id,
                        front_content_mode=template.front_content_mode,
                        back_content_mode=template.back_content_mode,
                    ),
                )
            return self._get_deck(connection, deck.id)

    def get_render_settings(self, deck_id: str) -> DeckRenderSettings:
        with self._transaction() as connection:
            if self._get_deck(connection, deck_id) is None:
                raise DeckNotFoundError(deck_id)
            return self._get_render_settings(connection, deck_id)

    def save_render_settings(
        self,
        deck_id: str,
        settings: DeckRenderSettings,
        *,
        expected_version: int | None = None,
    ) -> DeckRenderSettings:
        with self._transaction(write=True) as connection:
            deck = self._get_deck(connection, deck_id)
            if deck is None:
                raise DeckNotFoundError(deck_id)
            if expected_version is not None and deck.version != expected_version:
                raise ConcurrentModificationError(expected_version, deck.version)
            connection.execute(
                '''
                UPDATE deck_render_settings SET
                    preset = ?, horizontal_alignment = ?,
                    vertical_alignment = ?, header_visibility = ?,
                    header_position = ?, header_alignment = ?,
                    header_repeat = ?, section_break = ?
                WHERE deck_id = ?
                ''',
                (
                    settings.preset.value,
                    settings.horizontal_alignment.value,
                    settings.vertical_alignment.value,
                    settings.header_visibility.value,
                    settings.header_position.value,
                    settings.header_alignment.value,
                    settings.header_repeat.value,
                    settings.section_break.value,
                    deck_id,
                ),
            )
            connection.execute(
                '''
                UPDATE decks SET updated_at = ?, version = version + 1
                WHERE id = ?
                ''',
                (datetime.now(timezone.utc).isoformat(), deck_id),
            )
            return settings

    def load_cards(self, deck_id: str) -> CardDeck:
        with self._transaction() as connection:
            return self._load_cards(connection, deck_id)

    def save_cards(self, deck_id: str, card_deck: CardDeck) -> None:
        with self._transaction(write=True) as connection:
            self._replace_cards(connection, deck_id, card_deck)

    def mutate_cards(
        self,
        deck_id: str,
        mutation: Callable[[CardDeck], tuple[MutationResult, bool]],
        *,
        expected_version: int | None = None,
    ) -> MutationResult:
        with self._transaction(write=True) as connection:
            deck = self._get_deck(connection, deck_id)
            if deck is None:
                raise DeckNotFoundError(deck_id)
            if expected_version is not None and deck.version != expected_version:
                raise ConcurrentModificationError(expected_version, deck.version)
            card_deck = self._load_cards(connection, deck_id)
            result, changed = mutation(card_deck)
            if changed:
                self._replace_cards(connection, deck_id, card_deck)
            return result

    def load(self, deck_id: str = 'default') -> CardDeck:
        return self.load_cards(deck_id)

    def save(self, deck: CardDeck, deck_id: str = 'default') -> None:
        self.save_cards(deck_id, deck)

    @staticmethod
    def _profile_from_row(row: sqlite3.Row) -> PrinterProfile:
        return PrinterProfile(
            key=row['key'],
            name=row['name'],
            duplex_mode=row['duplex_mode'],
            back_rotation_deg=row['back_rotation_deg'],
            front_offset_x_mm=row['front_offset_x_mm'],
            front_offset_y_mm=row['front_offset_y_mm'],
            back_offset_x_mm=row['back_offset_x_mm'],
            back_offset_y_mm=row['back_offset_y_mm'],
            back_border=bool(row['back_border']),
            registration_marks=bool(row['registration_marks']),
        )

    def list_printer_profiles(self) -> list[PrinterProfile]:
        with self._transaction() as connection:
            rows = connection.execute(
                'SELECT * FROM printer_profiles ORDER BY name, key'
            ).fetchall()
            return [self._profile_from_row(row) for row in rows]

    def save_printer_profile(self, profile: PrinterProfile) -> PrinterProfile:
        with self._transaction(write=True) as connection:
            connection.execute(
                '''
                INSERT INTO printer_profiles(
                    key, name, duplex_mode, back_rotation_deg,
                    front_offset_x_mm, front_offset_y_mm,
                    back_offset_x_mm, back_offset_y_mm,
                    back_border, registration_marks
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    name = excluded.name,
                    duplex_mode = excluded.duplex_mode,
                    back_rotation_deg = excluded.back_rotation_deg,
                    front_offset_x_mm = excluded.front_offset_x_mm,
                    front_offset_y_mm = excluded.front_offset_y_mm,
                    back_offset_x_mm = excluded.back_offset_x_mm,
                    back_offset_y_mm = excluded.back_offset_y_mm,
                    back_border = excluded.back_border,
                    registration_marks = excluded.registration_marks
                ''',
                (
                    profile.key,
                    profile.name,
                    profile.duplex_mode.value,
                    profile.back_rotation_deg,
                    profile.front_offset_x_mm,
                    profile.front_offset_y_mm,
                    profile.back_offset_x_mm,
                    profile.back_offset_y_mm,
                    int(profile.back_border),
                    int(profile.registration_marks),
                ),
            )
        return profile

    def delete_printer_profile(self, key: str) -> bool:
        with self._transaction(write=True) as connection:
            cursor = connection.execute(
                'DELETE FROM printer_profiles WHERE key = ?', (key,)
            )
            return cursor.rowcount > 0

    def list_trusted_templates(
        self, deck_id: str
    ) -> list[TrustedTemplateVersion]:
        with self._transaction() as connection:
            if self._get_deck(connection, deck_id) is None:
                raise DeckNotFoundError(deck_id)
            rows = connection.execute(
                '''
                SELECT * FROM trusted_templates
                WHERE deck_id = ? ORDER BY version
                ''',
                (deck_id,),
            ).fetchall()
            return [self._trusted_template_from_row(row) for row in rows]

    def quarantine_trusted_template(
        self,
        deck_id: str,
        source: str,
        *,
        provenance: TemplateProvenance | str = TemplateProvenance.LOCAL_AUTHOR,
        origin_template_id: str | None = None,
        front_content_mode: ContentMode | str = ContentMode.ESCAPED,
        back_content_mode: ContentMode | str = ContentMode.ESCAPED,
    ) -> TrustedTemplateVersion:
        with self._transaction(write=True) as connection:
            if self._get_deck(connection, deck_id) is None:
                raise DeckNotFoundError(deck_id)
            version = connection.execute(
                '''
                SELECT COALESCE(MAX(version), 0) + 1
                FROM trusted_templates WHERE deck_id = ?
                ''',
                (deck_id,),
            ).fetchone()[0]
            template = TrustedTemplateVersion(
                deck_id=deck_id,
                source=source,
                version=version,
                provenance=provenance,
                origin_template_id=origin_template_id,
                front_content_mode=front_content_mode,
                back_content_mode=back_content_mode,
            )
            self._insert_trusted_template(connection, template)
            return template

    def approve_trusted_template(
        self, deck_id: str, template_id: str
    ) -> TrustedTemplateVersion:
        with self._transaction(write=True) as connection:
            row = connection.execute(
                '''
                SELECT * FROM trusted_templates
                WHERE deck_id = ? AND id = ?
                ''',
                (deck_id, template_id),
            ).fetchone()
            if row is None:
                raise KeyError(template_id)
            template = self._trusted_template_from_row(row).approved()
            connection.execute(
                '''
                UPDATE trusted_templates
                SET status = ?, approved_at = NULL
                WHERE deck_id = ? AND status = ?
                ''',
                (
                    TemplateStatus.REVOKED.value,
                    deck_id,
                    TemplateStatus.APPROVED.value,
                ),
            )
            connection.execute(
                '''
                UPDATE trusted_templates SET status = ?, approved_at = ?
                WHERE id = ?
                ''',
                (
                    template.status.value,
                    template.approved_at.isoformat(),
                    template.id,
                ),
            )
            return template

    def revoke_trusted_template(
        self, deck_id: str, template_id: str
    ) -> TrustedTemplateVersion:
        with self._transaction(write=True) as connection:
            row = connection.execute(
                '''
                SELECT * FROM trusted_templates
                WHERE deck_id = ? AND id = ?
                ''',
                (deck_id, template_id),
            ).fetchone()
            if row is None:
                raise KeyError(template_id)
            template = self._trusted_template_from_row(row).revoked()
            connection.execute(
                '''
                UPDATE trusted_templates SET status = ?, approved_at = NULL
                WHERE id = ?
                ''',
                (template.status.value, template.id),
            )
            return template

    def get_approved_trusted_template(
        self, deck_id: str
    ) -> TrustedTemplateVersion | None:
        with self._transaction() as connection:
            if self._get_deck(connection, deck_id) is None:
                raise DeckNotFoundError(deck_id)
            row = connection.execute(
                '''
                SELECT * FROM trusted_templates
                WHERE deck_id = ? AND status = 'approved'
                ''',
                (deck_id,),
            ).fetchone()
            return self._trusted_template_from_row(row) if row else None

    def get_print_job_snapshot(self, deck_id: str) -> PrintJobSnapshot:
        with self._transaction() as connection:
            deck = self._get_deck(connection, deck_id)
            if deck is None:
                raise DeckNotFoundError(deck_id)
            template_row = connection.execute(
                '''
                SELECT * FROM trusted_templates
                WHERE deck_id = ? AND status = 'approved'
                ''',
                (deck_id,),
            ).fetchone()
            return PrintJobSnapshot(
                deck_id=deck.id,
                deck_version=deck.version,
                cards=tuple(self._load_cards(connection, deck_id).cards),
                render_settings=deck.render_settings,
                trusted_template=(
                    self._trusted_template_from_row(template_row)
                    if template_row else None
                ),
            )

    def integrity_check(self) -> list[str]:
        with self._transaction() as connection:
            issues = [row[0] for row in connection.execute('PRAGMA integrity_check')]
            foreign_keys = connection.execute('PRAGMA foreign_key_check').fetchall()
            missing_settings = connection.execute(
                '''
                SELECT decks.id FROM decks
                LEFT JOIN deck_render_settings
                    ON deck_render_settings.deck_id = decks.id
                WHERE deck_render_settings.deck_id IS NULL
                ORDER BY decks.id
                '''
            ).fetchall()
            trusted_rows = connection.execute(
                'SELECT * FROM trusted_templates ORDER BY id'
            ).fetchall()
        if issues == ['ok']:
            issues = []
        issues.extend(f'foreign-key: {tuple(row)}' for row in foreign_keys)
        issues.extend(
            f"missing-render-settings: {row['id']}" for row in missing_settings
        )
        for row in trusted_rows:
            try:
                self._trusted_template_from_row(row)
            except (TypeError, ValueError):
                issues.append(f"invalid-trusted-template: {row['id']}")
        return issues

    def readiness_check(self) -> list[str]:
        issues = self.integrity_check()
        if issues:
            return issues
        connection = self._connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            connection.rollback()
        except sqlite3.Error:
            return ['write-transaction-unavailable']
        finally:
            connection.close()
        return []
