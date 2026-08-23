from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional, TypeVar

import fcntl

from ..domain.entities import CardDeck, Deck
from ..domain.interfaces import (
    CardRepository,
    ConcurrentModificationError,
    DeckRepository,
)
from ..domain.rendering import DeckRenderSettings


MutationResult = TypeVar('MutationResult')
_SAFE_ID = re.compile(r'^[A-Za-z0-9_-]+$')
SCHEMA_VERSION = 1


class RepositoryStorageError(ValueError):
    """Base class for errors that make repository writes unsafe."""


class RepositoryCorruptionError(RepositoryStorageError):
    """Raised when persisted JSON exists but cannot be safely decoded."""

    def __init__(self, path: Path, detail: str):
        self.path = path
        self.backup_path = path.with_suffix(path.suffix + '.bak')
        super().__init__(
            f'Corrupt JSON repository file {path}: {detail}. '
            f'Backup candidate: {self.backup_path}'
        )


class UnsupportedSchemaError(RepositoryStorageError):
    """Raised when storage was created by an unsupported application version."""


class DeckNotFoundError(KeyError):
    """Raised when a card operation targets a deck that does not exist."""


@dataclass(frozen=True)
class IntegrityIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class IntegrityReport:
    schema_version: int | None
    issues: tuple[IntegrityIssue, ...]

    @property
    def healthy(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict:
        return {
            'schema_version': self.schema_version,
            'healthy': self.healthy,
            'issues': [issue.to_dict() for issue in self.issues],
        }


class JsonRepository(DeckRepository, CardRepository):
    """Store decks in locked, atomically replaced JSON files."""

    _registry_guard = threading.Lock()
    _thread_locks: dict[Path, threading.RLock] = {}

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.decks_file = self.data_dir / 'decks.json'
        self.cards_dir = self.data_dir / 'cards'
        self.lock_file = self.data_dir / '.repository.lock'
        self.manifest_file = self.data_dir / 'repository.json'
        with self._registry_guard:
            self._thread_lock = self._thread_locks.setdefault(
                self.lock_file, threading.RLock()
            )
        self._ensure_dirs()
        self.integrity_report = self.scan_integrity()

    def _ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cards_dir.mkdir(parents=True, exist_ok=True)
        with self._transaction(validate_schema=False):
            legacy_storage = self.decks_file.exists() and not self.manifest_file.exists()
            if not self.decks_file.exists():
                self._write_json(self.decks_file, [])
            if legacy_storage:
                self._backup_legacy_json_unlocked()
            if not self.manifest_file.exists():
                self._write_json(
                    self.manifest_file,
                    {'schema_version': SCHEMA_VERSION},
                )

    def readiness_check(self) -> list[str]:
        return [issue.code for issue in self.scan_integrity().issues]

    @contextmanager
    def _transaction(self, *, validate_schema: bool = True) -> Iterator[None]:
        """Serialize repository operations across threads and processes."""
        with self._thread_lock:
            with self.lock_file.open('a+b') as lock_handle:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                try:
                    if validate_schema:
                        self._read_schema_unlocked()
                    yield
                finally:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def _read_json(self, path: Path) -> list | dict:
        try:
            with path.open('r', encoding='utf-8') as source:
                return json.load(source)
        except FileNotFoundError as error:
            raise RepositoryCorruptionError(path, 'file is missing') from error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise RepositoryCorruptionError(path, str(error)) from error

    def _write_json(self, path: Path, data) -> None:
        """Durably replace one JSON file while retaining its last version."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        backup_temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='w',
                encoding='utf-8',
                dir=path.parent,
                prefix=f'.{path.name}.',
                suffix='.tmp',
                delete=False,
            ) as destination:
                temporary_path = Path(destination.name)
                json.dump(data, destination, ensure_ascii=False, indent=2)
                destination.flush()
                os.fsync(destination.fileno())

            if path.exists():
                with tempfile.NamedTemporaryFile(
                    mode='wb',
                    dir=path.parent,
                    prefix=f'.{path.name}.backup.',
                    suffix='.tmp',
                    delete=False,
                ) as backup:
                    backup_temporary_path = Path(backup.name)
                    with path.open('rb') as current:
                        shutil.copyfileobj(current, backup)
                    backup.flush()
                    os.fsync(backup.fileno())
                os.replace(
                    backup_temporary_path,
                    path.with_suffix(path.suffix + '.bak'),
                )
                backup_temporary_path = None

            os.replace(temporary_path, path)
            temporary_path = None
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            for candidate in (temporary_path, backup_temporary_path):
                if candidate is not None:
                    candidate.unlink(missing_ok=True)

    def _copy_once(self, source: Path, destination: Path) -> None:
        if destination.exists():
            return
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='wb',
                dir=destination.parent,
                prefix=f'.{destination.name}.',
                suffix='.tmp',
                delete=False,
            ) as backup:
                temporary_path = Path(backup.name)
                with source.open('rb') as current:
                    shutil.copyfileobj(current, backup)
                backup.flush()
                os.fsync(backup.fileno())
            os.replace(temporary_path, destination)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _backup_legacy_json_unlocked(self) -> None:
        for source in (self.decks_file, *sorted(self.cards_dir.glob('*.json'))):
            self._copy_once(
                source,
                source.with_suffix(source.suffix + '.pre-schema-v1.bak'),
            )

    def _read_schema_unlocked(self) -> int:
        manifest = self._read_json(self.manifest_file)
        if not isinstance(manifest, dict):
            raise RepositoryCorruptionError(
                self.manifest_file, 'expected a schema manifest object'
            )
        version = manifest.get('schema_version')
        if not isinstance(version, int):
            raise RepositoryCorruptionError(
                self.manifest_file, 'schema_version must be an integer'
            )
        if version != SCHEMA_VERSION:
            raise UnsupportedSchemaError(
                f'Unsupported repository schema {version}; expected {SCHEMA_VERSION}'
            )
        return version

    def _allowed_recovery_target(self, path: str | Path) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.data_dir / candidate
        candidate = candidate.resolve()
        allowed = candidate in {self.decks_file, self.manifest_file}
        allowed = allowed or (
            candidate.parent == self.cards_dir and candidate.suffix == '.json'
        )
        if not allowed:
            raise ValueError('Recovery target must be a repository JSON file')
        return candidate

    def _cards_path(self, deck_id: str) -> Path:
        if not isinstance(deck_id, str) or not _SAFE_ID.fullmatch(deck_id):
            raise ValueError('Invalid deck id')
        return self.cards_dir / f'{deck_id}.json'

    def _read_decks_unlocked(self) -> list[dict]:
        data = self._read_json(self.decks_file)
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise RepositoryCorruptionError(self.decks_file, 'expected a list of objects')
        return data

    def _get_deck_unlocked(self, deck_id: str) -> Optional[Deck]:
        self._cards_path(deck_id)
        for item in self._read_decks_unlocked():
            if item.get('id') == deck_id:
                try:
                    return Deck.from_dict(item)
                except (KeyError, TypeError, ValueError) as error:
                    raise RepositoryCorruptionError(self.decks_file, str(error)) from error
        return None

    def _save_deck_meta_unlocked(self, deck: Deck) -> None:
        data = self._read_decks_unlocked()
        data = [item for item in data if item.get('id') != deck.id]
        data.append(deck.to_dict())
        self._write_json(self.decks_file, data)

    def _load_cards_unlocked(self, deck_id: str) -> CardDeck:
        deck = self._get_deck_unlocked(deck_id)
        if deck is None:
            raise DeckNotFoundError(deck_id)
        cards_path = self._cards_path(deck_id)
        data = self._read_json(cards_path)
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise RepositoryCorruptionError(cards_path, 'expected a list of objects')
        try:
            card_deck = CardDeck.from_list(data)
        except (KeyError, TypeError, ValueError) as error:
            raise RepositoryCorruptionError(cards_path, str(error)) from error

        card_ids = [card.id for card in card_deck.cards]
        if deck.card_ids != card_ids:
            deck.card_ids = card_ids
            self._save_deck_meta_unlocked(deck)
        return card_deck

    def _save_cards_unlocked(self, deck_id: str, card_deck: CardDeck) -> None:
        deck = self._get_deck_unlocked(deck_id)
        if deck is None:
            raise DeckNotFoundError(deck_id)
        self._write_json(self._cards_path(deck_id), card_deck.to_list())
        deck.card_ids = [card.id for card in card_deck.cards]
        deck.updated_at = datetime.now(timezone.utc)
        deck.version += 1
        self._save_deck_meta_unlocked(deck)

    # Integrity and recovery

    def scan_integrity(self) -> IntegrityReport:
        """Inspect all repository files without repairing or deleting data."""
        issues: list[IntegrityIssue] = []
        schema_version: int | None = None

        def report(code: str, path: Path, message: str) -> None:
            display_path = str(path.relative_to(self.data_dir))
            issues.append(IntegrityIssue(code, display_path, message))

        with self._transaction(validate_schema=False):
            try:
                schema_version = self._read_schema_unlocked()
            except RepositoryStorageError as error:
                report('schema', self.manifest_file, str(error))

            try:
                deck_items = self._read_decks_unlocked()
            except RepositoryStorageError as error:
                report('decks-json', self.decks_file, str(error))
                return IntegrityReport(schema_version, tuple(issues))

            decks: list[Deck] = []
            deck_ids: set[str] = set()
            for item in deck_items:
                deck_id = item.get('id')
                if not isinstance(deck_id, str) or not _SAFE_ID.fullmatch(deck_id):
                    report('invalid-deck-id', self.decks_file, repr(deck_id))
                    continue
                if deck_id in deck_ids:
                    report('duplicate-deck-id', self.decks_file, deck_id)
                    continue
                deck_ids.add(deck_id)
                try:
                    decks.append(Deck.from_dict(item))
                except (KeyError, TypeError, ValueError) as error:
                    report('invalid-deck', self.decks_file, f'{deck_id}: {error}')

            global_card_ids: dict[str, str] = {}
            for deck in decks:
                cards_path = self._cards_path(deck.id)
                if not cards_path.exists():
                    report('missing-cards-file', cards_path, deck.id)
                    continue
                try:
                    card_items = self._read_json(cards_path)
                    if not isinstance(card_items, list) or any(
                        not isinstance(item, dict) for item in card_items
                    ):
                        raise RepositoryCorruptionError(
                            cards_path, 'expected a list of objects'
                        )
                    cards = CardDeck.from_list(card_items).cards
                except (RepositoryStorageError, KeyError, TypeError, ValueError) as error:
                    report('invalid-cards', cards_path, str(error))
                    continue

                card_ids = [card.id for card in cards]
                if len(card_ids) != len(set(card_ids)):
                    report('duplicate-card-id', cards_path, deck.id)
                if deck.card_ids != card_ids:
                    report('card-id-mismatch', cards_path, deck.id)
                for card_id in card_ids:
                    previous_deck = global_card_ids.setdefault(card_id, deck.id)
                    if previous_deck != deck.id:
                        report(
                            'cross-deck-card-id',
                            cards_path,
                            f'{card_id} also occurs in {previous_deck}',
                        )

            for cards_path in sorted(self.cards_dir.glob('*.json')):
                if cards_path.stem not in deck_ids:
                    report('orphan-cards-file', cards_path, cards_path.stem)

        return IntegrityReport(schema_version, tuple(issues))

    def recover_from_backup(self, path: str | Path) -> Path:
        """Restore exactly one repository JSON file and preserve the broken file."""
        target = self._allowed_recovery_target(path)
        backup = target.with_suffix(target.suffix + '.bak')
        broken_path: Path | None = None
        with self._transaction(validate_schema=False):
            if not backup.exists():
                raise FileNotFoundError(f'Backup does not exist: {backup}')
            recovered_data = self._read_json(backup)
            if target == self.manifest_file:
                if not isinstance(recovered_data, dict):
                    raise RepositoryCorruptionError(
                        backup, 'expected a schema manifest object'
                    )
            elif not isinstance(recovered_data, list):
                raise RepositoryCorruptionError(backup, 'expected a list')

            if target.exists():
                stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                broken_path = target.with_name(f'{target.name}.broken-{stamp}')
                os.replace(target, broken_path)
            try:
                self._write_json(target, recovered_data)
                if target == self.manifest_file:
                    self._read_schema_unlocked()
            except Exception:
                target.unlink(missing_ok=True)
                if broken_path is not None:
                    os.replace(broken_path, target)
                raise

        self.integrity_report = self.scan_integrity()
        return broken_path or target

    # DeckRepository: decks

    def list_decks(self) -> list[Deck]:
        with self._transaction():
            try:
                decks = [Deck.from_dict(item) for item in self._read_decks_unlocked()]
            except RepositoryCorruptionError:
                raise
            except (KeyError, TypeError, ValueError) as error:
                raise RepositoryCorruptionError(self.decks_file, str(error)) from error
            decks.sort(key=lambda deck: deck.updated_at, reverse=True)
            return decks

    def get_deck(self, deck_id: str) -> Optional[Deck]:
        with self._transaction():
            return self._get_deck_unlocked(deck_id)

    def create_deck(
        self,
        name: str,
        description: str = '',
        render_settings: DeckRenderSettings | None = None,
    ) -> Deck:
        with self._transaction():
            deck = Deck(
                name=name,
                description=description,
                render_settings=(
                    render_settings or DeckRenderSettings.centered()
                ),
            )
            self._write_json(self._cards_path(deck.id), [])
            try:
                self._save_deck_meta_unlocked(deck)
            except Exception:
                self._cards_path(deck.id).unlink(missing_ok=True)
                raise
            return deck

    def update_deck(
        self, deck_id: str, name: str, description: str = ''
    ) -> Optional[Deck]:
        with self._transaction():
            deck = self._get_deck_unlocked(deck_id)
            if deck is None:
                return None
            deck.name = name
            deck.description = description
            deck.updated_at = datetime.now(timezone.utc)
            deck.version += 1
            self._save_deck_meta_unlocked(deck)
            return deck

    def delete_deck(self, deck_id: str) -> bool:
        with self._transaction():
            self._cards_path(deck_id)
            data = self._read_decks_unlocked()
            new_data = [item for item in data if item.get('id') != deck_id]
            if len(new_data) == len(data):
                return False
            self._write_json(self.decks_file, new_data)
            self._cards_path(deck_id).unlink(missing_ok=True)
            return True

    def clone_deck(self, deck_id: str) -> Optional[Deck]:
        with self._transaction():
            source = self._get_deck_unlocked(deck_id)
            if source is None:
                return None
            source_cards = self._load_cards_unlocked(deck_id)
            new_cards = CardDeck(cards=[card.clone() for card in source_cards.cards])
            new_deck = Deck(
                name=f'{source.name} (копия)',
                description=source.description,
                parent_id=source.id,
                card_ids=[card.id for card in new_cards.cards],
                render_settings=source.render_settings,
            )
            self._write_json(self._cards_path(new_deck.id), new_cards.to_list())
            try:
                self._save_deck_meta_unlocked(new_deck)
            except Exception:
                self._cards_path(new_deck.id).unlink(missing_ok=True)
                raise
            return new_deck

    def create_deck_with_cards(
        self,
        name: str,
        description: str,
        parent_id: str | None,
        cards: CardDeck,
        render_settings: DeckRenderSettings | None = None,
    ) -> Deck:
        with self._transaction():
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
            self._write_json(self._cards_path(deck.id), cards.to_list())
            try:
                self._save_deck_meta_unlocked(deck)
            except Exception:
                self._cards_path(deck.id).unlink(missing_ok=True)
                raise
            return deck

    def get_render_settings(self, deck_id: str) -> DeckRenderSettings:
        with self._transaction():
            deck = self._get_deck_unlocked(deck_id)
            if deck is None:
                raise DeckNotFoundError(deck_id)
            return deck.render_settings

    def save_render_settings(
        self,
        deck_id: str,
        settings: DeckRenderSettings,
        *,
        expected_version: int | None = None,
    ) -> DeckRenderSettings:
        with self._transaction():
            deck = self._get_deck_unlocked(deck_id)
            if deck is None:
                raise DeckNotFoundError(deck_id)
            if expected_version is not None and deck.version != expected_version:
                raise ConcurrentModificationError(expected_version, deck.version)
            deck.render_settings = settings
            deck.updated_at = datetime.now(timezone.utc)
            deck.version += 1
            self._save_deck_meta_unlocked(deck)
            return deck.render_settings

    # DeckRepository: cards

    def load_cards(self, deck_id: str) -> CardDeck:
        with self._transaction():
            return self._load_cards_unlocked(deck_id)

    def save_cards(self, deck_id: str, card_deck: CardDeck) -> None:
        with self._transaction():
            self._save_cards_unlocked(deck_id, card_deck)

    def mutate_cards(
        self,
        deck_id: str,
        mutation: Callable[[CardDeck], tuple[MutationResult, bool]],
        *,
        expected_version: int | None = None,
    ) -> MutationResult:
        with self._transaction():
            deck = self._get_deck_unlocked(deck_id)
            if deck is None:
                raise DeckNotFoundError(deck_id)
            if expected_version is not None and deck.version != expected_version:
                raise ConcurrentModificationError(expected_version, deck.version)
            card_deck = self._load_cards_unlocked(deck_id)
            result, changed = mutation(card_deck)
            if changed:
                self._save_cards_unlocked(deck_id, card_deck)
            return result

    # CardRepository compatibility

    def load(self, deck_id: str = 'default') -> CardDeck:
        return self.load_cards(deck_id)

    def save(self, deck: CardDeck, deck_id: str = 'default') -> None:
        self.save_cards(deck_id, deck)
