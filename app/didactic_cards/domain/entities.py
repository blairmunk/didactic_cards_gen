from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .rendering import AuthoringMode, DeckRenderSettings


class CardModeError(ValueError):
    """Raised when mode-specific card fields cross an authoring boundary."""


def validate_card_mode_fields(
    card: Card,
    authoring_mode: AuthoringMode | str,
) -> None:
    """Keep raw per-card headers exclusive to Advanced decks.

    Empty means exactly the empty string. Whitespace and line endings are raw
    content in Advanced mode, so silently trimming them in Safe mode would be
    a lossy and ambiguous conversion.
    """
    mode = AuthoringMode(authoring_mode)
    if mode is AuthoringMode.SAFE and (
        card.upper_header != '' or card.lower_header != ''
    ):
        raise CardModeError(
            'Поля верхнего и нижнего колонтитулов карточки доступны '
            'только в Advanced-колоде.'
        )


def validate_card_deck_mode(
    card_deck: CardDeck,
    authoring_mode: AuthoringMode | str,
) -> None:
    for card in card_deck.cards:
        validate_card_mode_fields(card, authoring_mode)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class Card:
    front: str = ''
    back: str = ''
    section: str = ''
    upper_header: str = ''
    lower_header: str = ''
    id: str = field(default_factory=_new_id)
    parent_id: Optional[str] = None
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    def is_empty(self) -> bool:
        return not any(
            value.strip() for value in (
                self.front, self.back, self.upper_header, self.lower_header
            )
        )

    def clone(self, keep_parent: bool = True) -> Card:
        return Card(
            front=self.front,
            back=self.back,
            section=self.section,
            upper_header=self.upper_header,
            lower_header=self.lower_header,
            parent_id=self.id if keep_parent else None,
        )

    def update(
        self,
        front: str,
        back: str,
        section: str | None = None,
        upper_header: str | None = None,
        lower_header: str | None = None,
    ) -> None:
        self.front = front
        self.back = back
        if section is not None:
            self.section = section
        if upper_header is not None:
            self.upper_header = upper_header
        if lower_header is not None:
            self.lower_header = lower_header
        self.updated_at = _now()

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'parent_id': self.parent_id,
            'front': self.front,
            'back': self.back,
            'section': self.section,
            'upper_header': self.upper_header,
            'lower_header': self.lower_header,
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
            section=data.get('section', ''),
            upper_header=data.get('upper_header', ''),
            lower_header=data.get('lower_header', ''),
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
    render_settings: DeckRenderSettings = field(
        default_factory=DeckRenderSettings.centered
    )
    version: int = 1
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    def __len__(self) -> int:
        return len(self.card_ids)

    def __bool__(self) -> bool:
        return True

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
            render_settings=self.render_settings,
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
            'render_settings': self.render_settings.to_dict(),
            'version': self.version,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
        }

    @staticmethod
    def from_dict(data: dict) -> Deck:
        settings_data = data.get('render_settings')
        if settings_data is None:
            raise ValueError('render_settings is required')
        settings = DeckRenderSettings.from_dict(settings_data)
        return Deck(
            id=data.get('id', _new_id()),
            parent_id=data.get('parent_id'),
            name=data.get('name', 'Новая колода'),
            description=data.get('description', ''),
            card_ids=data.get('card_ids', []),
            render_settings=settings,
            version=data.get('version', 1),
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

    def index_of(self, card_id: str) -> int | None:
        return next(
            (index for index, card in enumerate(self.cards) if card.id == card_id),
            None,
        )

    def delete_by_id(self, card_id: str) -> bool:
        index = self.index_of(card_id)
        return self.delete(index) if index is not None else False

    def edit(
        self,
        index: int,
        front: str,
        back: str,
        section: str | None = None,
        upper_header: str | None = None,
        lower_header: str | None = None,
    ) -> bool:
        if 0 <= index < len(self.cards):
            self.cards[index].update(
                front, back, section, upper_header, lower_header
            )
            return True
        return False

    def edit_by_id(
        self,
        card_id: str,
        front: str,
        back: str,
        section: str | None = None,
        upper_header: str | None = None,
        lower_header: str | None = None,
    ) -> bool:
        index = self.index_of(card_id)
        return (
            self.edit(
                index, front, back, section, upper_header, lower_header
            ) if index is not None else False
        )

    def reorder(self, new_order: list[int]) -> bool:
        if sorted(new_order) != list(range(len(self.cards))):
            return False
        self.cards = [self.cards[i] for i in new_order]
        return True

    def reorder_by_ids(self, new_order: list[str]) -> bool:
        current_ids = [card.id for card in self.cards]
        if len(new_order) != len(current_ids) or set(new_order) != set(current_ids):
            return False
        cards_by_id = {card.id: card for card in self.cards}
        self.cards = [cards_by_id[card_id] for card_id in new_order]
        return True

    def clear(self) -> None:
        self.cards.clear()

    def padded(self, cards_per_page: int) -> list[Card]:
        if (
            isinstance(cards_per_page, bool)
            or not isinstance(cards_per_page, int)
            or cards_per_page <= 0
        ):
            raise ValueError('cards_per_page must be a positive integer')
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
