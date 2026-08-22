import json
import os
from pathlib import Path
from typing import Optional

from ..domain.interfaces import DeckRepository, CardRepository
from ..domain.entities import Deck, Card, CardDeck


class JsonRepository(DeckRepository, CardRepository):
    """Хранит колоды и карточки в JSON-файлах на диске."""

    def __init__(self, data_dir: str = 'data'):
        self.data_dir = Path(data_dir)
        self.decks_file = self.data_dir / 'decks.json'
        self.cards_dir = self.data_dir / 'cards'
        self._ensure_dirs()

    def _ensure_dirs(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cards_dir.mkdir(parents=True, exist_ok=True)
        if not self.decks_file.exists():
            self._write_json(self.decks_file, [])

    def _read_json(self, path: Path) -> list | dict:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _write_json(self, path: Path, data) -> None:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _cards_path(self, deck_id: str) -> Path:
        return self.cards_dir / f'{deck_id}.json'

    # ─── DeckRepository: Колоды ──────────────────────────────────

    def list_decks(self) -> list[Deck]:
        data = self._read_json(self.decks_file)
        decks = [Deck.from_dict(d) for d in data]
        decks.sort(key=lambda d: d.updated_at, reverse=True)
        return decks

    def get_deck(self, deck_id: str) -> Optional[Deck]:
        for d in self._read_json(self.decks_file):
            if d.get('id') == deck_id:
                return Deck.from_dict(d)
        return None

    def _save_deck_meta(self, deck: Deck) -> None:
        data = self._read_json(self.decks_file)
        data = [d for d in data if d.get('id') != deck.id]
        data.append(deck.to_dict())
        self._write_json(self.decks_file, data)

    def create_deck(self, name: str, description: str = '') -> Deck:
        deck = Deck(name=name, description=description)
        self._save_deck_meta(deck)
        self._write_json(self._cards_path(deck.id), [])
        return deck

    def update_deck(self, deck_id: str, name: str, description: str = '') -> Optional[Deck]:
        deck = self.get_deck(deck_id)
        if not deck:
            return None
        deck.name = name
        deck.description = description
        from datetime import datetime, timezone
        deck.updated_at = datetime.now(timezone.utc)
        self._save_deck_meta(deck)
        return deck

    def delete_deck(self, deck_id: str) -> bool:
        data = self._read_json(self.decks_file)
        new_data = [d for d in data if d.get('id') != deck_id]
        if len(new_data) == len(data):
            return False
        self._write_json(self.decks_file, new_data)
        cards_path = self._cards_path(deck_id)
        if cards_path.exists():
            cards_path.unlink()
        return True

    def clone_deck(self, deck_id: str) -> Optional[Deck]:
        source = self.get_deck(deck_id)
        if not source:
            return None
        source_cards = self.load_cards(deck_id)

        new_deck = Deck(
            name=f'{source.name} (копия)',
            description=source.description,
            parent_id=source.id,
        )
        self._save_deck_meta(new_deck)

        new_cards = CardDeck(
            cards=[Card(front=c.front, back=c.back) for c in source_cards.cards]
        )
        self.save_cards(new_deck.id, new_cards)
        return new_deck

    # ─── DeckRepository: Карточки ────────────────────────────────

    def load_cards(self, deck_id: str) -> CardDeck:
        data = self._read_json(self._cards_path(deck_id))
        card_deck = CardDeck.from_list(data) if data else CardDeck()

        # Автосинхронизация card_ids в метаданных колоды
        deck = self.get_deck(deck_id)
        if deck and len(deck.card_ids) != len(card_deck.cards):
            deck.card_ids = [c.id for c in card_deck.cards]
            self._save_deck_meta(deck)

        return card_deck

    def save_cards(self, deck_id: str, card_deck: CardDeck) -> None:
        self._write_json(self._cards_path(deck_id), card_deck.to_list())
        # Обновляем card_ids в метаданных колоды
        deck = self.get_deck(deck_id)
        if deck:
            deck.card_ids = [c.id for c in card_deck.cards]
            from datetime import datetime, timezone
            deck.updated_at = datetime.now(timezone.utc)
            self._save_deck_meta(deck)

    # ─── CardRepository (легаси-совместимость) ───────────────────

    def load(self, deck_id: str = 'default') -> CardDeck:
        return self.load_cards(deck_id)

    def save(self, deck: CardDeck, deck_id: str = 'default') -> None:
        self.save_cards(deck_id, deck)