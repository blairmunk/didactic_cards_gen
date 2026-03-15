from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ..domain.entities import Card, Deck
from ..domain.interfaces import StorageBackend


class JsonFileStorage(StorageBackend):
    """Хранит карточки и колоды в одном JSON-файле."""

    def __init__(self, filepath: str = 'data/storage.json'):
        self.filepath = Path(filepath)
        self._ensure_file()

    def _ensure_file(self) -> None:
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        if not self.filepath.exists():
            self.save_all({'cards': {}, 'decks': {}})

    # ── Низкоуровневый I/O ──

    def load_all(self) -> dict:
        with open(self.filepath, 'r', encoding='utf-8') as f:
            return json.load(f)

    def save_all(self, data: dict) -> None:
        with open(self.filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ── Карточки ──

    def get_card(self, card_id: str) -> Optional[Card]:
        data = self.load_all()
        card_data = data.get('cards', {}).get(card_id)
        return Card.from_dict(card_data) if card_data else None

    def save_card(self, card: Card) -> None:
        data = self.load_all()
        data.setdefault('cards', {})[card.id] = card.to_dict()
        self.save_all(data)

    def delete_card(self, card_id: str) -> bool:
        data = self.load_all()
        if card_id not in data.get('cards', {}):
            return False
        del data['cards'][card_id]
        for deck_data in data.get('decks', {}).values():
            ids = deck_data.get('card_ids', [])
            if card_id in ids:
                ids.remove(card_id)
        self.save_all(data)
        return True

    def list_cards(self) -> list[Card]:
        data = self.load_all()
        return [Card.from_dict(d) for d in data.get('cards', {}).values()]

    # ── Колоды ──

    def get_deck(self, deck_id: str) -> Optional[Deck]:
        data = self.load_all()
        deck_data = data.get('decks', {}).get(deck_id)
        return Deck.from_dict(deck_data) if deck_data else None

    def save_deck(self, deck: Deck) -> None:
        data = self.load_all()
        data.setdefault('decks', {})[deck.id] = deck.to_dict()
        self.save_all(data)

    def delete_deck(self, deck_id: str) -> bool:
        data = self.load_all()
        if deck_id not in data.get('decks', {}):
            return False
        del data['decks'][deck_id]
        self.save_all(data)
        return True

    def list_decks(self) -> list[Deck]:
        data = self.load_all()
        return [Deck.from_dict(d) for d in data.get('decks', {}).values()]

    # ── Удобные методы ──

    def get_deck_cards(self, deck_id: str) -> list[Card]:
        """Карточки колоды в правильном порядке."""
        deck = self.get_deck(deck_id)
        if not deck:
            return []
        data = self.load_all()
        cards_map = data.get('cards', {})
        result = []
        for card_id in deck.card_ids:
            card_data = cards_map.get(card_id)
            if card_data:
                result.append(Card.from_dict(card_data))
        return result

    def clone_card(self, card_id: str) -> Optional[Card]:
        original = self.get_card(card_id)
        if not original:
            return None
        clone = original.clone(keep_parent=True)
        self.save_card(clone)
        return clone

    def clone_deck(self, deck_id: str, deep: bool = True) -> Optional[Deck]:
        original = self.get_deck(deck_id)
        if not original:
            return None
        card_clones = None
        if deep:
            card_clones = {}
            for card_id in original.card_ids:
                cloned_card = self.clone_card(card_id)
                if cloned_card:
                    card_clones[card_id] = cloned_card.id
        new_deck = original.clone(card_clones=card_clones)
        self.save_deck(new_deck)
        return new_deck