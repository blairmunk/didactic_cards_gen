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
from .json_repository import DeckNotFoundError, JsonRepository


MutationResult = TypeVar('MutationResult')
SQLITE_SCHEMA_VERSION = 1


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
            if version not in {0, SQLITE_SCHEMA_VERSION}:
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
                '''
            )
            if version == 0:
                connection.execute(f'PRAGMA user_version = {SQLITE_SCHEMA_VERSION}')
            connection.execute('PRAGMA journal_mode = WAL')

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
    def _deck_from_row(row: sqlite3.Row, card_ids: list[str]) -> Deck:
        return Deck(
            id=row['id'],
            parent_id=row['parent_id'],
            name=row['name'],
            description=row['description'],
            card_ids=card_ids,
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
            created_at=datetime.fromisoformat(row['created_at']),
            updated_at=datetime.fromisoformat(row['updated_at']),
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
        return self._deck_from_row(row, self._card_ids(connection, deck_id))

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
                    id, parent_id, front, back, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    parent_id = excluded.parent_id,
                    front = excluded.front,
                    back = excluded.back,
                    updated_at = excluded.updated_at,
                    version = cards.version + 1
                ''',
                (
                    card.id,
                    card.parent_id,
                    card.front,
                    card.back,
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
                self._deck_from_row(row, self._card_ids(connection, row['id']))
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
            )
            self._insert_deck(connection, clone)
            self._replace_cards(connection, clone.id, cloned_cards)
            return clone

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

    def integrity_check(self) -> list[str]:
        with self._transaction() as connection:
            issues = [row[0] for row in connection.execute('PRAGMA integrity_check')]
            foreign_keys = connection.execute('PRAGMA foreign_key_check').fetchall()
        if issues == ['ok']:
            issues = []
        issues.extend(f'foreign-key: {tuple(row)}' for row in foreign_keys)
        return issues
