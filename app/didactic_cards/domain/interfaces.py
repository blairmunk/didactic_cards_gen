from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from .entities import Card, Deck, CardDeck


@dataclass
class CompileResult:
    success: bool
    pdf_data: bytes
    log: str


class CardRepository(ABC):
    """Хранилище рабочей коллекции (сессия)."""

    @abstractmethod
    def load(self) -> CardDeck:
        ...

    @abstractmethod
    def save(self, deck: CardDeck) -> None:
        ...


class StorageBackend(ABC):
    """Персистентное хранилище карточек и колод (JSON-файл)."""

    @abstractmethod
    def load_all(self) -> dict:
        ...

    @abstractmethod
    def save_all(self, data: dict) -> None:
        ...

    @abstractmethod
    def get_card(self, card_id: str) -> Optional[Card]:
        ...

    @abstractmethod
    def save_card(self, card: Card) -> None:
        ...

    @abstractmethod
    def delete_card(self, card_id: str) -> bool:
        ...

    @abstractmethod
    def list_cards(self) -> list[Card]:
        ...

    @abstractmethod
    def get_deck(self, deck_id: str) -> Optional[Deck]:
        ...

    @abstractmethod
    def save_deck(self, deck: Deck) -> None:
        ...

    @abstractmethod
    def delete_deck(self, deck_id: str) -> bool:
        ...

    @abstractmethod
    def list_decks(self) -> list[Deck]:
        ...


class DocumentRenderer(ABC):
    """Генерация LaTeX-документа из коллекции карточек."""

    @abstractmethod
    def render(self, deck: CardDeck) -> str:
        ...


class PdfCompiler(ABC):
    """Компиляция LaTeX → PDF."""

    @abstractmethod
    def compile(self, latex_source: str) -> tuple[bool, bytes, str]:
        ...