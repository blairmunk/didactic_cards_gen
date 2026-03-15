import csv
import io
from typing import List, Tuple

from ..domain.entities import Card, CardDeck
from ..domain.interfaces import CardRepository, DocumentRenderer, PdfCompiler, CompileResult


class AddCard:
    def __init__(self, repo: CardRepository):
        self.repo = repo

    def execute(self, front: str, back: str) -> Tuple[Card, int]:
        deck = self.repo.load()
        card = Card(front=front.strip(), back=back.strip())
        index = deck.add(card)
        self.repo.save(deck)
        return card, index


class AddCardsBulk:
    def __init__(self, repo: CardRepository):
        self.repo = repo

    def execute(self, bulk_text: str) -> int:
        deck = self.repo.load()
        count = 0
        for line in bulk_text.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            if '||' in line:
                front, back = line.split('||', 1)
                deck.add(Card(front=front.strip(), back=back.strip()))
            else:
                deck.add(Card(front=line, back=''))
            count += 1
        self.repo.save(deck)
        return count


class ImportCsv:
    def __init__(self, repo: CardRepository):
        self.repo = repo

    def execute(self, file_bytes: bytes, delimiter: str = ';') -> int:
        deck = self.repo.load()
        stream = io.StringIO(file_bytes.decode('utf-8-sig'))
        reader = csv.reader(stream, delimiter=delimiter)
        count = 0
        for row in reader:
            if len(row) >= 2:
                front = row[0].strip()
                back = row[1].strip()
                if front or back:
                    deck.add(Card(front=front, back=back))
                    count += 1
            elif len(row) == 1 and row[0].strip():
                deck.add(Card(front=row[0].strip(), back=''))
                count += 1
        self.repo.save(deck)
        return count


class DeleteCard:
    def __init__(self, repo: CardRepository):
        self.repo = repo

    def execute(self, index: int) -> bool:
        deck = self.repo.load()
        result = deck.delete(index)
        if result:
            self.repo.save(deck)
        return result


class EditCard:
    def __init__(self, repo: CardRepository):
        self.repo = repo

    def execute(self, index: int, front: str, back: str) -> bool:
        deck = self.repo.load()
        result = deck.edit(index, front.strip(), back.strip())
        if result:
            self.repo.save(deck)
        return result


class ReorderCards:
    def __init__(self, repo: CardRepository):
        self.repo = repo

    def execute(self, order: List[int]) -> bool:
        deck = self.repo.load()
        result = deck.reorder(order)
        if result:
            self.repo.save(deck)
        return result


class ResetCards:
    def __init__(self, repo: CardRepository):
        self.repo = repo

    def execute(self) -> None:
        deck = self.repo.load()
        deck.clear()
        self.repo.save(deck)


class GetDeck:
    def __init__(self, repo: CardRepository):
        self.repo = repo

    def execute(self) -> CardDeck:
        return self.repo.load()


class GenerateDocument:
    def __init__(self, repo: CardRepository,
                 renderer: DocumentRenderer,
                 compiler: PdfCompiler,
                 cards_per_page: int = 8):
        self.repo = repo
        self.renderer = renderer
        self.compiler = compiler
        self.cards_per_page = cards_per_page

    def execute(self) -> CompileResult:
        deck = self.repo.load()
        padded = CardDeck(cards=deck.padded(self.cards_per_page))
        source = self.renderer.render(padded)
        return self.compiler.compile(source)


class PreviewDocument:
    def __init__(self, repo: CardRepository,
                 renderer: DocumentRenderer,
                 cards_per_page: int = 8):
        self.repo = repo
        self.renderer = renderer
        self.cards_per_page = cards_per_page

    def execute(self) -> str:
        deck = self.repo.load()
        padded = CardDeck(cards=deck.padded(self.cards_per_page))
        return self.renderer.render(padded)