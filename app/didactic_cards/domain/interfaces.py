from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional, TypeVar

from .entities import Card, Deck, CardDeck
from .printing import PrintLayout
from .rendering import DeckRenderSettings


MutationResult = TypeVar('MutationResult')


class ConcurrentModificationError(RuntimeError):
    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual
        super().__init__(
            f'Deck was modified concurrently (expected version {expected}, actual {actual})'
        )


@dataclass
class CompileResult:
    success: bool
    pdf_data: bytes
    log: str
    error_kind: str | None = None


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
    def create_deck(
        self,
        name: str,
        description: str = '',
        render_settings: DeckRenderSettings | None = None,
    ) -> Deck:
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
        *,
        expected_version: int | None = None,
    ) -> MutationResult:
        """Apply one read-modify-write operation.

        Adapters with transactional facilities should override this method.
        The default is suitable for simple in-memory implementations.
        """
        deck_info = self.get_deck(deck_id)
        if deck_info is None:
            raise KeyError(deck_id)
        if expected_version is not None and deck_info.version != expected_version:
            raise ConcurrentModificationError(expected_version, deck_info.version)
        deck = self.load_cards(deck_id)
        result, changed = mutation(deck)
        if changed:
            self.save_cards(deck_id, deck)
        return result

    @abstractmethod
    def create_deck_with_cards(
        self,
        name: str,
        description: str,
        parent_id: str | None,
        cards: CardDeck,
        render_settings: DeckRenderSettings | None = None,
    ) -> Deck:
        """Create a complete deck atomically."""
        ...

    @abstractmethod
    def get_render_settings(self, deck_id: str) -> DeckRenderSettings:
        ...

    @abstractmethod
    def save_render_settings(
        self,
        deck_id: str,
        settings: DeckRenderSettings,
        *,
        expected_version: int | None = None,
    ) -> DeckRenderSettings:
        ...


class DocumentRenderer(ABC):

    def prepare_print_layout(
        self, deck: CardDeck, cards_per_page: int
    ) -> PrintLayout:
        """Return complete physical front slots for one print job."""
        padded = deck.padded(cards_per_page)
        return PrintLayout(
            tuple(padded),
            section_padding=0,
            trailing_padding=len(padded) - len(deck.cards),
        )

    def with_render_settings(
        self, settings: DeckRenderSettings
    ) -> DocumentRenderer:
        """Return a renderer configured for one deck.

        The default keeps stateless renderers concise.
        """
        return self

    def with_trusted_template(self, template) -> DocumentRenderer:
        """Return a renderer configured with one already-approved template."""
        return self

    @abstractmethod
    def render(self, deck: CardDeck) -> str:
        ...

    @abstractmethod
    def render_fronts(self, deck: CardDeck) -> str:
        ...

    @abstractmethod
    def render_backs(self, deck: CardDeck) -> str:
        ...

    @abstractmethod
    def printable_area_warnings(self) -> tuple[str, ...]:
        ...


class PdfCompiler(ABC):

    @abstractmethod
    def is_available(self) -> bool:
        ...

    @abstractmethod
    def compile(self, latex_source: str) -> CompileResult:
        ...
