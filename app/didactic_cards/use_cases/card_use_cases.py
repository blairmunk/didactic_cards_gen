from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from ..domain.interfaces import (
    CardRepository, DeckRepository,
    DocumentRenderer, PdfCompiler, CompileResult,
)
from ..domain.entities import Card, CardDeck


class CardLimitExceeded(ValueError):
    pass


class CsvValidationError(ValueError):
    def __init__(self, preview: CsvImportPreview):
        self.preview = preview
        super().__init__(
            f'CSV содержит отклонённые строки: {len(preview.rejected_rows)}'
        )


@dataclass(frozen=True)
class CsvImportPreview:
    cards: tuple[Card, ...]
    rejected_rows: tuple[dict, ...]
    delimiter: str

    def to_dict(self, preview_limit: int = 20) -> dict:
        return {
            'accepted_count': len(self.cards),
            'rejected_count': len(self.rejected_rows),
            'delimiter': self.delimiter,
            'cards': [card.to_dict() for card in self.cards[:preview_limit]],
            'rejected_rows': list(self.rejected_rows[:preview_limit]),
            'truncated': max(len(self.cards), len(self.rejected_rows)) > preview_limit,
        }


def _parse_bulk_line(line: str) -> tuple[str, str]:
    sides: list[list[str]] = [[], []]
    side = 0
    index = 0
    while index < len(line):
        if line.startswith(r'\||', index):
            sides[side].append('||')
            index += 3
        elif line.startswith(r'\\', index):
            sides[side].append('\\')
            index += 2
        elif side == 0 and line.startswith('||', index):
            side = 1
            index += 2
        else:
            sides[side].append(line[index])
            index += 1
    return ''.join(sides[0]).strip(), ''.join(sides[1]).strip()


def _ensure_capacity(deck: CardDeck, incoming: int, max_cards: int | None) -> None:
    if max_cards is not None and len(deck) + incoming > max_cards:
        raise CardLimitExceeded(f'Максимум карточек в колоде: {max_cards}')


def preview_csv_import(
    file_bytes: bytes,
    delimiter: str = 'auto',
    has_header: bool = False,
) -> CsvImportPreview:
    text = file_bytes.decode('utf-8-sig')
    delimiters = {'comma': ',', 'semicolon': ';', 'tab': '\t'}
    if delimiter == 'auto':
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=',;\t')
            selected_delimiter = dialect.delimiter
        except csv.Error:
            sample = text[:4096]
            selected_delimiter = max(',;\t', key=sample.count)
    elif delimiter in delimiters:
        selected_delimiter = delimiters[delimiter]
    else:
        raise ValueError('Unsupported CSV delimiter')

    cards: list[Card] = []
    rejected: list[dict] = []
    reader = csv.reader(io.StringIO(text), delimiter=selected_delimiter)
    for row_index, row in enumerate(reader, start=1):
        if row_index == 1 and has_header:
            continue
        if not row or not any(cell.strip() for cell in row):
            continue
        if len(row) > 2:
            rejected.append({
                'row': row_index,
                'reason': 'Ожидалось не более двух колонок',
            })
            continue
        front = row[0].strip() if row else ''
        back = row[1].strip() if len(row) > 1 else ''
        if front or back:
            cards.append(Card(front=front, back=back))
    return CsvImportPreview(tuple(cards), tuple(rejected), selected_delimiter)


class AddCard:
    def __init__(self, repo: DeckRepository, max_cards: int | None = None):
        self.repo = repo
        self.max_cards = max_cards

    def execute(
        self, deck_id: str, front: str, back: str,
        expected_version: int | None = None,
    ) -> tuple[Card, int]:
        def add(deck: CardDeck):
            _ensure_capacity(deck, 1, self.max_cards)
            card = Card(front=front, back=back)
            return (card, deck.add(card)), True

        return self.repo.mutate_cards(
            deck_id, add, expected_version=expected_version
        )


class AddCardsBulk:
    def __init__(self, repo: DeckRepository, max_cards: int | None = None):
        self.repo = repo
        self.max_cards = max_cards

    def execute(
        self, deck_id: str, bulk_text: str, expected_version: int | None = None
    ) -> int:
        new_cards = []
        for line in bulk_text.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            front, back = _parse_bulk_line(line)
            new_cards.append(Card(front=front, back=back))
        def add_all(deck: CardDeck):
            _ensure_capacity(deck, len(new_cards), self.max_cards)
            for card in new_cards:
                deck.add(card)
            return len(new_cards), bool(new_cards)

        return self.repo.mutate_cards(
            deck_id, add_all, expected_version=expected_version
        )


class ImportCsv:
    def __init__(self, repo: DeckRepository, max_cards: int | None = None):
        self.repo = repo
        self.max_cards = max_cards

    def execute(
        self, deck_id: str, file_bytes: bytes,
        expected_version: int | None = None,
        delimiter: str = 'auto',
        has_header: bool = False,
    ) -> int:
        preview = preview_csv_import(file_bytes, delimiter, has_header)
        if preview.rejected_rows:
            raise CsvValidationError(preview)
        new_cards = list(preview.cards)
        def add_all(deck: CardDeck):
            _ensure_capacity(deck, len(new_cards), self.max_cards)
            for card in new_cards:
                deck.add(card)
            return len(new_cards), bool(new_cards)

        return self.repo.mutate_cards(
            deck_id, add_all, expected_version=expected_version
        )


class DeleteCard:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(
        self, deck_id: str, card_id: str, expected_version: int | None = None
    ) -> bool:
        def delete(deck: CardDeck):
            result = deck.delete_by_id(card_id)
            return result, result

        return self.repo.mutate_cards(
            deck_id, delete, expected_version=expected_version
        )


class EditCard:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(
        self, deck_id: str, card_id: str, front: str, back: str,
        expected_version: int | None = None,
    ) -> bool:
        def edit(deck: CardDeck):
            result = deck.edit_by_id(card_id, front, back)
            return result, result

        return self.repo.mutate_cards(
            deck_id, edit, expected_version=expected_version
        )


class ReorderCards:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(
        self, deck_id: str, new_order: list[str],
        expected_version: int | None = None,
    ) -> bool:
        def reorder(deck: CardDeck):
            result = deck.reorder_by_ids(new_order)
            return result, result

        return self.repo.mutate_cards(
            deck_id, reorder, expected_version=expected_version
        )


class ResetCards:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(self, deck_id: str, expected_version: int | None = None) -> None:
        def reset(deck: CardDeck):
            changed = bool(deck.cards)
            deck.clear()
            return None, changed

        self.repo.mutate_cards(
            deck_id, reset, expected_version=expected_version
        )


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
