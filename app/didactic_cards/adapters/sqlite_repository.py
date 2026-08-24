from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional, TypeVar

from ..domain.entities import (
    Card,
    CardDeck,
    Deck,
    validate_card_deck_mode,
)
from ..domain.interfaces import (
    ConcurrentModificationError,
    DeckRepository,
)
from ..domain.printing import PrinterProfile
from ..domain.rendering import AuthoringMode, DeckRenderSettings
from ..domain.trusted import (
    PrintJobSnapshot,
    TemplateProvenance,
    TemplateStatus,
    TrustedTemplateVersion,
)
from .repository_errors import DeckNotFoundError
from .sqlite_schema import (
    MINIMUM_MIGRATABLE_SCHEMA_VERSION,
    SQLITE_SCHEMA_VERSION,
    initialize_current_schema,
    migrate_to_current,
    schema_structure_issues,
    user_tables,
)
from .sqlite_storage import (
    RuntimeStorageLease,
    connection_validation_issues,
    ensure_pre_migration_backup,
    exclusive_runtime_lock,
    exclusive_schema_lock,
)


MutationResult = TypeVar('MutationResult')


class UnsupportedSqliteSchemaError(ValueError):
    """Raised instead of opening a database from a newer application version."""


class SqliteRepository(DeckRepository):
    """Transactional SQLite persistence with ordered deck membership."""

    def __init__(self, data_dir: str | Path, database_name: str = 'cards.sqlite3'):
        self.data_dir = Path(data_dir).expanduser().resolve()
        data_dir_existed = self.data_dir.exists()
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not data_dir_existed:
            os.chmod(self.data_dir, 0o700)
        self.database_file = self.data_dir / database_name
        self._runtime_lease: RuntimeStorageLease | None = None
        self._closed = False
        self._initialize_database()

    def close(self) -> None:
        lease = getattr(self, '_runtime_lease', None)
        if lease is not None:
            lease.close()
            self._runtime_lease = None
        self._closed = True

    def __del__(self) -> None:
        self.close()

    def _connect(self) -> sqlite3.Connection:
        if self._closed:
            raise RuntimeError('SQLite repository is closed')
        connection = sqlite3.connect(self.database_file, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        connection.execute('PRAGMA busy_timeout = 30000')
        return connection

    @contextmanager
    def _transaction(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield connection
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_database(self) -> None:
        with exclusive_schema_lock(self.data_dir):
            connection = self._connect()
            try:
                version = connection.execute(
                    'PRAGMA user_version'
                ).fetchone()[0]
                tables = user_tables(connection)
            finally:
                connection.close()

            if version == 0:
                if tables:
                    raise UnsupportedSqliteSchemaError(
                        'Unsupported unrecognized schema 0; existing database '
                        'is not empty'
                    )
                with exclusive_runtime_lock(self.database_file):
                    connection = self._connect()
                    try:
                        connection.execute('BEGIN IMMEDIATE')
                        initialize_current_schema(connection)
                        issues = schema_structure_issues(connection)
                        if issues:
                            raise UnsupportedSqliteSchemaError(
                                'SQLite schema validation failed during '
                                'initialization: ' + '; '.join(issues)
                            )
                        connection.commit()
                    except Exception:
                        if connection.in_transaction:
                            connection.rollback()
                        raise
                    finally:
                        connection.close()
            elif version == SQLITE_SCHEMA_VERSION:
                connection = self._connect()
                try:
                    issues = schema_structure_issues(connection)
                finally:
                    connection.close()
                if issues:
                    raise UnsupportedSqliteSchemaError(
                        'SQLite schema validation failed: ' + '; '.join(issues)
                    )
            elif MINIMUM_MIGRATABLE_SCHEMA_VERSION <= version < SQLITE_SCHEMA_VERSION:
                connection = self._connect()
                try:
                    issues = schema_structure_issues(
                        connection, expected_version=version
                    )
                finally:
                    connection.close()
                if issues:
                    raise UnsupportedSqliteSchemaError(
                        'SQLite schema validation failed before migration: '
                        + '; '.join(issues)
                    )
                with exclusive_runtime_lock(self.database_file):
                    ensure_pre_migration_backup(self.database_file, version)
                    connection = self._connect()
                    try:
                        connection.execute('BEGIN IMMEDIATE')
                        migrate_to_current(connection, version)
                        issues = connection_validation_issues(connection)
                        if issues:
                            raise UnsupportedSqliteSchemaError(
                                'SQLite schema validation failed during '
                                'migration: ' + '; '.join(issues)
                            )
                        connection.commit()
                    except Exception:
                        if connection.in_transaction:
                            connection.rollback()
                        raise
                    finally:
                        connection.close()
            else:
                raise UnsupportedSqliteSchemaError(
                    f'Unsupported SQLite schema {version}; '
                    f'expected {SQLITE_SCHEMA_VERSION}'
                )

            connection = self._connect()
            try:
                issues = schema_structure_issues(connection)
                if issues:
                    raise UnsupportedSqliteSchemaError(
                        'SQLite schema validation failed: ' + '; '.join(issues)
                    )
                os.chmod(self.database_file, 0o600)
                connection.execute('PRAGMA journal_mode = WAL')
            finally:
                connection.close()
            for suffix in ('', '-wal', '-shm'):
                family_member = self.database_file.with_name(
                    f'{self.database_file.name}{suffix}'
                )
                if family_member.exists():
                    os.chmod(family_member, 0o600)
            # Acquire the process-lifetime lease while schema.lock is still
            # held, leaving no gap in which an offline restore can win.
            self._runtime_lease = RuntimeStorageLease(self.database_file)

    @staticmethod
    def _settings_from_row(row: sqlite3.Row) -> DeckRenderSettings:
        data = {
            'preset': row['preset'],
            'horizontal_alignment': row['horizontal_alignment'],
            'vertical_alignment': row['vertical_alignment'],
            'header_visibility': row['header_visibility'],
            'header_position': row['header_position'],
            'header_alignment': row['header_alignment'],
            'header_repeat': row['header_repeat'],
            'section_break': row['section_break'],
        }
        try:
            typography_data = json.loads(row['typography_json'])
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError('Invalid typography settings in database') from error
        if not isinstance(typography_data, dict):
            raise ValueError('Typography settings in database must be an object')
        allowed_typography_fields = set(
            DeckRenderSettings().typography_dict()
        )
        unknown_fields = set(typography_data) - allowed_typography_fields
        if unknown_fields:
            raise ValueError(
                'Unknown typography settings in database: '
                + ', '.join(sorted(unknown_fields))
            )
        data.update(typography_data)
        return DeckRenderSettings.from_dict(data)

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
            trashed_at=(
                datetime.fromisoformat(row['trashed_at'])
                if row['trashed_at'] else None
            ),
            purge_after=(
                datetime.fromisoformat(row['purge_after'])
                if row['purge_after'] else None
            ),
        )

    @staticmethod
    def _card_from_row(row: sqlite3.Row) -> Card:
        return Card(
            id=row['id'],
            parent_id=row['parent_id'],
            front=row['front'],
            back=row['back'],
            section=row['section'],
            upper_header=row['upper_header'],
            lower_header=row['lower_header'],
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
            front_source=row['front_source'],
            back_source=row['back_source'],
            source_hash=row['source_hash'],
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
                id, deck_id, version, source_hash, provenance, status,
                origin_template_id, created_at, approved_at, front_source,
                back_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                template.id,
                template.deck_id,
                template.version,
                template.source_hash,
                template.provenance.value,
                template.status.value,
                template.origin_template_id,
                template.created_at.isoformat(),
                template.approved_at.isoformat()
                if template.approved_at else None,
                template.front_source,
                template.back_source,
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
            'SELECT * FROM decks WHERE id = ? AND trashed_at IS NULL',
            (deck_id,),
        ).fetchone()
        if row is None:
            return None
        return self._deck_from_row(
            row,
            self._card_ids(connection, deck_id),
            self._get_render_settings(connection, deck_id),
        )

    def _get_any_deck(
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
        card_deck = CardDeck([self._card_from_row(row) for row in rows])
        validate_card_deck_mode(
            card_deck, self._get_render_settings(connection, deck_id).authoring_mode
        )
        return card_deck

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
                section_break, typography_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                json.dumps(settings.typography_dict(), ensure_ascii=False),
            ),
        )

    def _replace_cards(
        self,
        connection: sqlite3.Connection,
        deck_id: str,
        card_deck: CardDeck,
    ) -> None:
        deck = self._get_deck(connection, deck_id)
        if deck is None:
            raise DeckNotFoundError(deck_id)
        validate_card_deck_mode(
            card_deck, deck.render_settings.authoring_mode
        )
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
                    id, parent_id, front, back, section, upper_header,
                    lower_header, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    parent_id = excluded.parent_id,
                    front = excluded.front,
                    back = excluded.back,
                    section = excluded.section,
                    upper_header = excluded.upper_header,
                    lower_header = excluded.lower_header,
                    updated_at = excluded.updated_at,
                    version = cards.version + 1
                ''',
                (
                    card.id,
                    card.parent_id,
                    card.front,
                    card.back,
                    card.section,
                    card.upper_header,
                    card.lower_header,
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
                'SELECT * FROM decks WHERE trashed_at IS NULL '
                'ORDER BY updated_at DESC'
            ).fetchall()
            return [
                self._deck_from_row(
                    row,
                    self._card_ids(connection, row['id']),
                    self._get_render_settings(connection, row['id']),
                )
                for row in rows
            ]

    def list_trashed_decks(self) -> list[Deck]:
        with self._transaction() as connection:
            rows = connection.execute(
                'SELECT * FROM decks WHERE trashed_at IS NOT NULL '
                'ORDER BY trashed_at DESC'
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

    def create_deck(
        self,
        name: str,
        description: str = '',
        render_settings: DeckRenderSettings | None = None,
    ) -> Deck:
        deck = Deck(
            name=name,
            description=description,
            render_settings=render_settings or DeckRenderSettings.centered(),
        )
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
                WHERE id = ? AND trashed_at IS NULL
                ''',
                (name, description, updated_at.isoformat(), deck_id),
            )
            if cursor.rowcount == 0:
                return None
            return self._get_deck(connection, deck_id)

    def trash_deck(
        self,
        deck_id: str,
        *,
        expected_version: int,
        trashed_at: datetime,
        purge_after: datetime,
    ) -> bool:
        if (
            trashed_at.tzinfo is None
            or purge_after.tzinfo is None
            or purge_after <= trashed_at
        ):
            raise ValueError('Invalid trash retention timestamps')
        trashed_at = trashed_at.astimezone(timezone.utc)
        purge_after = purge_after.astimezone(timezone.utc)
        with self._transaction(write=True) as connection:
            deck = self._get_any_deck(connection, deck_id)
            if deck is None:
                return False
            if deck.version != expected_version:
                raise ConcurrentModificationError(expected_version, deck.version)
            if deck.is_trashed:
                return False
            connection.execute(
                '''
                UPDATE decks SET trashed_at = ?, purge_after = ?,
                    updated_at = ?, version = version + 1
                WHERE id = ? AND trashed_at IS NULL
                ''',
                (
                    trashed_at.isoformat(),
                    purge_after.isoformat(),
                    trashed_at.isoformat(),
                    deck_id,
                ),
            )
            return True

    def restore_deck(self, deck_id: str, *, expected_version: int) -> bool:
        with self._transaction(write=True) as connection:
            deck = self._get_any_deck(connection, deck_id)
            if deck is None:
                return False
            if deck.version != expected_version:
                raise ConcurrentModificationError(expected_version, deck.version)
            if not deck.is_trashed:
                return False
            connection.execute(
                '''
                UPDATE decks SET trashed_at = NULL, purge_after = NULL,
                    updated_at = ?, version = version + 1
                WHERE id = ? AND trashed_at IS NOT NULL
                ''',
                (datetime.now(timezone.utc).isoformat(), deck_id),
            )
            return True

    def purge_deck(self, deck_id: str, *, expected_version: int) -> bool:
        with self._transaction(write=True) as connection:
            deck = self._get_any_deck(connection, deck_id)
            if deck is None:
                return False
            if deck.version != expected_version:
                raise ConcurrentModificationError(expected_version, deck.version)
            if not deck.is_trashed:
                return False
            card_ids = self._card_ids(connection, deck_id)
            connection.execute(
                'DELETE FROM decks WHERE id = ? AND trashed_at IS NOT NULL',
                (deck_id,),
            )
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
            template_rows = (
                connection.execute(
                    '''
                    SELECT * FROM trusted_templates
                    WHERE deck_id = ? ORDER BY version
                    ''',
                    (source.id,),
                ).fetchall()
                if source.render_settings.authoring_mode
                is AuthoringMode.ADVANCED
                else []
            )
            for version, row in enumerate(template_rows, start=1):
                self._insert_trusted_template(
                    connection,
                    TrustedTemplateVersion(
                        deck_id=clone.id,
                        version=version,
                        front_source=row['front_source'],
                        back_source=row['back_source'],
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
        if render_settings.authoring_mode is not AuthoringMode.ADVANCED:
            raise ValueError(
                'trusted template import requires an advanced deck'
            )
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
                        front_source=template.front_source,
                        back_source=template.back_source,
                        source_hash=template.source_hash,
                        provenance=TemplateProvenance.IMPORTED,
                        origin_template_id=template.origin_template_id,
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
            if (
                settings.authoring_mode
                is not deck.render_settings.authoring_mode
            ):
                raise ValueError(
                    'Тип Safe/Advanced задаётся при создании колоды '
                    'и не может быть изменён.'
                )
            connection.execute(
                '''
                UPDATE deck_render_settings SET
                    preset = ?, horizontal_alignment = ?,
                    vertical_alignment = ?, header_visibility = ?,
                    header_position = ?, header_alignment = ?,
                    header_repeat = ?, section_break = ?, typography_json = ?
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
                    json.dumps(settings.typography_dict(), ensure_ascii=False),
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
        front_source: str,
        back_source: str | None = None,
        *,
        provenance: TemplateProvenance | str = TemplateProvenance.LOCAL_AUTHOR,
        origin_template_id: str | None = None,
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
            if back_source is None:
                back_source = front_source
            template = TrustedTemplateVersion(
                deck_id=deck_id,
                front_source=front_source,
                back_source=back_source,
                version=version,
                provenance=provenance,
                origin_template_id=origin_template_id,
            )
            self._insert_trusted_template(connection, template)
            return template

    def approve_trusted_template(
        self, deck_id: str, template_id: str
    ) -> TrustedTemplateVersion:
        with self._transaction(write=True) as connection:
            if self._get_deck(connection, deck_id) is None:
                raise DeckNotFoundError(deck_id)
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
            if self._get_deck(connection, deck_id) is None:
                raise DeckNotFoundError(deck_id)
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
                    if (
                        template_row
                        and deck.render_settings.authoring_mode
                        is AuthoringMode.ADVANCED
                    )
                    else None
                ),
            )

    def integrity_check(self, *, quick: bool = False) -> list[str]:
        try:
            with self._transaction() as connection:
                if quick:
                    deadline = time.monotonic() + 1.0
                    connection.execute('PRAGMA busy_timeout = 500')
                    connection.set_progress_handler(
                        lambda: int(time.monotonic() >= deadline), 1000
                    )
                try:
                    return connection_validation_issues(
                        connection, quick=quick
                    )
                finally:
                    if quick:
                        connection.set_progress_handler(None, 0)
        except sqlite3.OperationalError:
            if quick:
                return ['readiness-check-timeout']
            raise

    def readiness_check(self) -> list[str]:
        issues = self.integrity_check(quick=True)
        if issues:
            return issues
        connection = self._connect()
        try:
            connection.execute('PRAGMA busy_timeout = 500')
            connection.execute('BEGIN IMMEDIATE')
            connection.rollback()
        except sqlite3.Error:
            return ['write-transaction-unavailable']
        finally:
            connection.close()
        return []
