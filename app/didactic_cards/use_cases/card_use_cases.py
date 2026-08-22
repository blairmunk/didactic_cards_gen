import csv
import io

from ..domain.interfaces import (
    CardRepository, DeckRepository,
    DocumentRenderer, PdfCompiler, CompileResult,
)
from ..domain.entities import Card, CardDeck


class CardLimitExceeded(ValueError):
    pass


def _ensure_capacity(deck: CardDeck, incoming: int, max_cards: int | None) -> None:
    if max_cards is not None and len(deck) + incoming > max_cards:
        raise CardLimitExceeded(f'Максимум карточек в колоде: {max_cards}')


class AddCard:
    def __init__(self, repo: DeckRepository, max_cards: int | None = None):
        self.repo = repo
        self.max_cards = max_cards

    def execute(self, deck_id: str, front: str, back: str) -> tuple[Card, int]:
        def add(deck: CardDeck):
            _ensure_capacity(deck, 1, self.max_cards)
            card = Card(front=front, back=back)
            return (card, deck.add(card)), True

        return self.repo.mutate_cards(deck_id, add)


class AddCardsBulk:
    def __init__(self, repo: DeckRepository, max_cards: int | None = None):
        self.repo = repo
        self.max_cards = max_cards

    def execute(self, deck_id: str, bulk_text: str) -> int:
        new_cards = []
        for line in bulk_text.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            if '|' in line:
                parts = line.split('|', 1)
                front, back = parts[0].strip(), parts[1].strip()
            else:
                front, back = line, ''
            new_cards.append(Card(front=front, back=back))
        def add_all(deck: CardDeck):
            _ensure_capacity(deck, len(new_cards), self.max_cards)
            for card in new_cards:
                deck.add(card)
            return len(new_cards), bool(new_cards)

        return self.repo.mutate_cards(deck_id, add_all)


class ImportCsv:
    def __init__(self, repo: DeckRepository, max_cards: int | None = None):
        self.repo = repo
        self.max_cards = max_cards

    def execute(self, deck_id: str, file_bytes: bytes) -> int:
        text = file_bytes.decode('utf-8-sig')
        reader = csv.reader(io.StringIO(text))
        new_cards = []
        for row in reader:
            if not row:
                continue
            front = row[0].strip() if len(row) > 0 else ''
            back = row[1].strip() if len(row) > 1 else ''
            if front or back:
                new_cards.append(Card(front=front, back=back))
        def add_all(deck: CardDeck):
            _ensure_capacity(deck, len(new_cards), self.max_cards)
            for card in new_cards:
                deck.add(card)
            return len(new_cards), bool(new_cards)

        return self.repo.mutate_cards(deck_id, add_all)


class DeleteCard:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(self, deck_id: str, index: int) -> bool:
        def delete(deck: CardDeck):
            result = deck.delete(index)
            return result, result

        return self.repo.mutate_cards(deck_id, delete)


class EditCard:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(self, deck_id: str, index: int, front: str, back: str) -> bool:
        def edit(deck: CardDeck):
            result = deck.edit(index, front, back)
            return result, result

        return self.repo.mutate_cards(deck_id, edit)


class ReorderCards:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(self, deck_id: str, new_order: list[int]) -> bool:
        def reorder(deck: CardDeck):
            result = deck.reorder(new_order)
            return result, result

        return self.repo.mutate_cards(deck_id, reorder)


class ResetCards:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(self, deck_id: str) -> None:
        def reset(deck: CardDeck):
            changed = bool(deck.cards)
            deck.clear()
            return None, changed

        self.repo.mutate_cards(deck_id, reset)


class GetDeck:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(self, deck_id: str) -> CardDeck:
        return self.repo.load_cards(deck_id)


class GenerateDocument:
    def __init__(self, repo: DeckRepository, renderer: DocumentRenderer,
                 compiler: PdfCompiler, cards_per_page: int):
        self.repo = repo
        self.renderer = renderer
        self.compiler = compiler
        self.cards_per_page = cards_per_page

    def execute(self, deck_id: str) -> CompileResult:
        deck = self.repo.load_cards(deck_id)
        padded_deck = CardDeck(cards=deck.padded(self.cards_per_page))
        latex = self.renderer.render(padded_deck)
        return self.compiler.compile(latex)


class PreviewDocument:
    def __init__(self, repo: DeckRepository, renderer: DocumentRenderer,
                 cards_per_page: int):
        self.repo = repo
        self.renderer = renderer
        self.cards_per_page = cards_per_page

    def execute(self, deck_id: str) -> str:
        deck = self.repo.load_cards(deck_id)
        padded_deck = CardDeck(cards=deck.padded(self.cards_per_page))
        return self.renderer.render(padded_deck)
