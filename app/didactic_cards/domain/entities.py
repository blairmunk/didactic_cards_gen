from dataclasses import dataclass, field
from typing import List
import copy


@dataclass
class Card:
    """Одна дидактическая карточка."""
    front: str = ''
    back: str = ''

    def is_empty(self) -> bool:
        return not self.front.strip() and not self.back.strip()

    def to_dict(self) -> dict:
        return {'front': self.front, 'back': self.back}

    @staticmethod
    def from_dict(data: dict) -> 'Card':
        return Card(
            front=data.get('front', ''),
            back=data.get('back', '')
        )


@dataclass
class CardDeck:
    """Колода карточек с операциями."""
    cards: List[Card] = field(default_factory=list)

    def add(self, card: Card) -> int:
        """Добавляет карточку, возвращает её индекс."""
        self.cards.append(card)
        return len(self.cards) - 1

    def delete(self, index: int) -> bool:
        """Удаляет карточку по индексу. Возвращает True если удалена."""
        if 0 <= index < len(self.cards):
            self.cards.pop(index)
            return True
        return False

    def edit(self, index: int, front: str, back: str) -> bool:
        """Редактирует карточку. Возвращает True если отредактирована."""
        if 0 <= index < len(self.cards):
            self.cards[index].front = front
            self.cards[index].back = back
            return True
        return False

    def reorder(self, new_order: List[int]) -> bool:
        """Переставляет карточки. Возвращает True если порядок валиден."""
        if sorted(new_order) != list(range(len(self.cards))):
            return False
        self.cards = [self.cards[i] for i in new_order]
        return True

    def padded(self, cards_per_page: int) -> List[Card]:
        """Возвращает КОПИЮ списка, дополненную пустыми карточками до кратного cards_per_page."""
        result = copy.deepcopy(self.cards)
        required = ((len(result) + cards_per_page - 1) // cards_per_page) * cards_per_page
        for _ in range(len(result), required):
            result.append(Card())
        return result

    def clear(self):
        self.cards.clear()

    def __len__(self) -> int:
        return len(self.cards)

    def to_list(self) -> List[dict]:
        return [c.to_dict() for c in self.cards]

    @staticmethod
    def from_list(data: List[dict]) -> 'CardDeck':
        return CardDeck(cards=[Card.from_dict(d) for d in data])