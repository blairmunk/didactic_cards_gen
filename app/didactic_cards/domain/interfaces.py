from abc import ABC, abstractmethod
from typing import List
from .entities import CardDeck


class CardRepository(ABC):
    """Абстракция хранилища карточек."""

    @abstractmethod
    def load(self) -> CardDeck:
        ...

    @abstractmethod
    def save(self, deck: CardDeck) -> None:
        ...


class DocumentRenderer(ABC):
    """Абстракция рендерера (LaTeX, HTML, Typst и т.д.)."""

    @abstractmethod
    def render(self, deck: CardDeck) -> str:
        """Возвращает текст документа (LaTeX-код, HTML и т.п.)."""
        ...


class PdfCompiler(ABC):
    """Абстракция компилятора документа в PDF."""

    @abstractmethod
    def compile(self, source: str) -> 'CompileResult':
        ...


class CompileResult:
    """Результат компиляции."""

    def __init__(self, success: bool, pdf_path: str = '',
                 errors: List[str] = None, log: str = ''):
        self.success = success
        self.pdf_path = pdf_path
        self.errors = errors or []
        self.log = log