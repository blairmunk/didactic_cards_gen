from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional, TypeVar

from .entities import Card, Deck, CardDeck


MutationResult = TypeVar('MutationResult')


@dataclass
class CompileResult:
    success: bool
    pdf_data: bytes
    log: str
    error_kind: str | None = None


class CardRepository(ABC):
    """Легаси-интерфейс для обратной совместимости."""

    @abstractmethod
    def load(self, deck_id: str = 'default') -> CardDeck:
        ...

    @abstractmethod
    def save(self, deck: CardDeck, deck_id: str = 'default') -> None:
        ...


class DeckRepository(ABC):
    """Интерфейс для множественных колод с персистентным хранением."""

    # ─── Колоды ───

    @abstractmethod
    def list_decks(self) -> list[Deck]:
        ...

    @abstractmethod
    def get_deck(self, deck_id: str) -> Optional[Deck]:
        ...

    @abstractmethod
    def create_deck(self, name: str, description: str = '') -> Deck:
        ...

    @abstractmethod
    def update_deck(self, deck_id: str, name: str, description: str = '') -> Optional[Deck]:
        ...

    @abstractmethod
    def delete_deck(self, deck_id: str) -> bool:
        ...

    @abstractmethod
    def clone_deck(self, deck_id: str) -> Optional[Deck]:
        ...

    # ─── Карточки внутри колоды ───

    @abstractmethod
    def load_cards(self, deck_id: str) -> CardDeck:
        ...

    @abstractmethod
    def save_cards(self, deck_id: str, card_deck: CardDeck) -> None:
        ...

    def mutate_cards(
        self,
        deck_id: str,
        mutation: Callable[[CardDeck], tuple[MutationResult, bool]],
    ) -> MutationResult:
        """Apply one read-modify-write operation.

        Adapters with transactional facilities should override this method.
        The default keeps compatibility with simple in-memory test doubles.
        """
        deck = self.load_cards(deck_id)
        result, changed = mutation(deck)
        if changed:
            self.save_cards(deck_id, deck)
        return result


class DocumentRenderer(ABC):

    @abstractmethod
    def render(self, deck: CardDeck) -> str:
        ...


class PdfCompiler(ABC):

    @abstractmethod
    def compile(self, latex_source: str) -> CompileResult:
        ...
