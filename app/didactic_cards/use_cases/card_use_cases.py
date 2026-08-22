import csv
import io

from ..domain.interfaces import (
    CardRepository, DeckRepository,
    DocumentRenderer, PdfCompiler, CompileResult,
)
from ..domain.entities import Card, CardDeck


class AddCard:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(self, deck_id: str, front: str, back: str) -> tuple[Card, int]:
        deck = self.repo.load_cards(deck_id)
        card = Card(front=front, back=back)
        index = deck.add(card)
        self.repo.save_cards(deck_id, deck)
        return card, index


class AddCardsBulk:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(self, deck_id: str, bulk_text: str) -> int:
        deck = self.repo.load_cards(deck_id)
        count = 0
        for line in bulk_text.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            if '|' in line:
                parts = line.split('|', 1)
                front, back = parts[0].strip(), parts[1].strip()
            else:
                front, back = line, ''
            deck.add(Card(front=front, back=back))
            count += 1
        self.repo.save_cards(deck_id, deck)
        return count


class ImportCsv:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(self, deck_id: str, file_bytes: bytes) -> int:
        deck = self.repo.load_cards(deck_id)
        text = file_bytes.decode('utf-8-sig')
        reader = csv.reader(io.StringIO(text))
        count = 0
        for row in reader:
            if not row:
                continue
            front = row[0].strip() if len(row) > 0 else ''
            back = row[1].strip() if len(row) > 1 else ''
            if front or back:
                deck.add(Card(front=front, back=back))
                count += 1
        self.repo.save_cards(deck_id, deck)
        return count


class DeleteCard:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(self, deck_id: str, index: int) -> bool:
        deck = self.repo.load_cards(deck_id)
        result = deck.delete(index)
        if result:
            self.repo.save_cards(deck_id, deck)
        return result


class EditCard:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(self, deck_id: str, index: int, front: str, back: str) -> bool:
        deck = self.repo.load_cards(deck_id)
        result = deck.edit(index, front, back)
        if result:
            self.repo.save_cards(deck_id, deck)
        return result


class ReorderCards:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(self, deck_id: str, new_order: list[int]) -> bool:
        deck = self.repo.load_cards(deck_id)
        result = deck.reorder(new_order)
        if result:
            self.repo.save_cards(deck_id, deck)
        return result


class ResetCards:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(self, deck_id: str) -> None:
        deck = self.repo.load_cards(deck_id)
        deck.clear()
        self.repo.save_cards(deck_id, deck)


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