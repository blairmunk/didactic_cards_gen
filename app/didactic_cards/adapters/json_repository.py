from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional, TypeVar

import fcntl

from ..domain.entities import CardDeck, Deck
from ..domain.interfaces import CardRepository, DeckRepository


MutationResult = TypeVar('MutationResult')
_SAFE_ID = re.compile(r'^[A-Za-z0-9_-]+$')


class RepositoryCorruptionError(ValueError):
    """Raised when persisted JSON exists but cannot be safely decoded."""

    def __init__(self, path: Path, detail: str):
        self.path = path
        self.backup_path = path.with_suffix(path.suffix + '.bak')
        super().__init__(
            f'Corrupt JSON repository file {path}: {detail}. '
            f'Backup candidate: {self.backup_path}'
        )


class DeckNotFoundError(KeyError):
    """Raised when a card operation targets a deck that does not exist."""


class JsonRepository(DeckRepository, CardRepository):
    """Store decks in locked, atomically replaced JSON files."""

    _registry_guard = threading.Lock()
    _thread_locks: dict[Path, threading.RLock] = {}

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.decks_file = self.data_dir / 'decks.json'
        self.cards_dir = self.data_dir / 'cards'
        self.lock_file = self.data_dir / '.repository.lock'
        with self._registry_guard:
            self._thread_lock = self._thread_locks.setdefault(
                self.lock_file, threading.RLock()
            )
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cards_dir.mkdir(parents=True, exist_ok=True)
        with self._transaction():
            if not self.decks_file.exists():
                self._write_json(self.decks_file, [])

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        """Serialize repository operations across threads and processes."""
        with self._thread_lock:
            with self.lock_file.open('a+b') as lock_handle:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                try:
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
        self._save_deck_meta_unlocked(deck)

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

    def create_deck(self, name: str, description: str = '') -> Deck:
        with self._transaction():
            deck = Deck(name=name, description=description)
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
            )
            self._write_json(self._cards_path(new_deck.id), new_cards.to_list())
            try:
                self._save_deck_meta_unlocked(new_deck)
            except Exception:
                self._cards_path(new_deck.id).unlink(missing_ok=True)
                raise
            return new_deck

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
    ) -> MutationResult:
        with self._transaction():
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
