from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class Card:
    front: str = ''
    back: str = ''
    id: str = field(default_factory=_new_id)
    parent_id: Optional[str] = None
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    def is_empty(self) -> bool:
        return not self.front.strip() and not self.back.strip()

    def clone(self, keep_parent: bool = True) -> Card:
        return Card(
            front=self.front,
            back=self.back,
            parent_id=self.id if keep_parent else None,
        )

    def update(self, front: str, back: str) -> None:
        self.front = front
        self.back = back
        self.updated_at = _now()

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'parent_id': self.parent_id,
            'front': self.front,
            'back': self.back,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
        }

    @staticmethod
    def from_dict(data: dict) -> Card:
        return Card(
            id=data.get('id', _new_id()),
            parent_id=data.get('parent_id'),
            front=data.get('front', ''),
            back=data.get('back', ''),
            created_at=datetime.fromisoformat(data['created_at']) if 'created_at' in data else _now(),
            updated_at=datetime.fromisoformat(data['updated_at']) if 'updated_at' in data else _now(),
        )


@dataclass
class Deck:
    name: str = 'Новая колода'
    description: str = ''
    id: str = field(default_factory=_new_id)
    parent_id: Optional[str] = None
    card_ids: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    def __len__(self) -> int:
        return len(self.card_ids)

    def add_card_id(self, card_id: str) -> int:
        self.card_ids.append(card_id)
        self.updated_at = _now()
        return len(self.card_ids) - 1

    def remove_card_id(self, card_id: str) -> bool:
        if card_id in self.card_ids:
            self.card_ids.remove(card_id)
            self.updated_at = _now()
            return True
        return False

    def reorder(self, new_order: list[str]) -> bool:
        if sorted(new_order) != sorted(self.card_ids):
            return False
        self.card_ids = list(new_order)
        self.updated_at = _now()
        return True

    def clone(self, card_clones: dict[str, str] | None = None) -> Deck:
        new_card_ids = (
            [card_clones.get(cid, cid) for cid in self.card_ids]
            if card_clones
            else list(self.card_ids)
        )
        return Deck(
            name=f'{self.name} (копия)',
            description=self.description,
            parent_id=self.id,
            card_ids=new_card_ids,
        )

    def clear(self) -> None:
        self.card_ids.clear()
        self.updated_at = _now()

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'parent_id': self.parent_id,
            'name': self.name,
            'description': self.description,
            'card_ids': list(self.card_ids),
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
        }

    @staticmethod
    def from_dict(data: dict) -> Deck:
        return Deck(
            id=data.get('id', _new_id()),
            parent_id=data.get('parent_id'),
            name=data.get('name', 'Новая колода'),
            description=data.get('description', ''),
            card_ids=data.get('card_ids', []),
            created_at=datetime.fromisoformat(data['created_at']) if 'created_at' in data else _now(),
            updated_at=datetime.fromisoformat(data['updated_at']) if 'updated_at' in data else _now(),
        )


@dataclass
class CardDeck:
    """
    Рабочая коллекция карточек для рендеринга и web-слоя.
    Оперирует объектами Card напрямую (по индексу).
    """
    cards: list[Card] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.cards)

    def add(self, card: Card) -> int:
        self.cards.append(card)
        return len(self.cards) - 1

    def delete(self, index: int) -> bool:
        if 0 <= index < len(self.cards):
            self.cards.pop(index)
            return True
        return False

    def edit(self, index: int, front: str, back: str) -> bool:
        if 0 <= index < len(self.cards):
            self.cards[index].update(front, back)
            return True
        return False

    def reorder(self, new_order: list[int]) -> bool:
        if sorted(new_order) != list(range(len(self.cards))):
            return False
        self.cards = [self.cards[i] for i in new_order]
        return True

    def clear(self) -> None:
        self.cards.clear()

    def padded(self, cards_per_page: int) -> list[Card]:
        total = len(self.cards)
        if total == 0:
            return []
        remainder = total % cards_per_page
        padding = (cards_per_page - remainder) if remainder else 0
        return list(self.cards) + [Card() for _ in range(padding)]

    def to_list(self) -> list[dict]:
        return [c.to_dict() for c in self.cards]

    @staticmethod
    def from_list(data: list[dict]) -> CardDeck:
        return CardDeck(cards=[Card.from_dict(d) for d in data])