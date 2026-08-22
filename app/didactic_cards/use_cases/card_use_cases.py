from __future__ import annotations

import csv
import io
import re
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


@dataclass(frozen=True)
class PreflightIssue:
    code: str
    severity: str
    message: str
    card_id: str | None = None
    card_number: int | None = None
    side: str | None = None

    def to_dict(self) -> dict:
        return {
            'code': self.code,
            'severity': self.severity,
            'message': self.message,
            'card_id': self.card_id,
            'card_number': self.card_number,
            'side': self.side,
        }


@dataclass(frozen=True)
class PreflightReport:
    ready: bool
    issues: tuple[PreflightIssue, ...]

    def to_dict(self) -> dict:
        return {
            'ready': self.ready,
            'issues': [issue.to_dict() for issue in self.issues],
            'error_count': sum(
                issue.severity == 'error' for issue in self.issues
            ),
            'warning_count': sum(
                issue.severity == 'warning' for issue in self.issues
            ),
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
        if len(row) > 3:
            rejected.append({
                'row': row_index,
                'reason': 'Ожидалось не более трёх колонок',
            })
            continue
        if len(row) == 3:
            section, front, back = (cell.strip() for cell in row)
        else:
            section = ''
            front = row[0].strip() if row else ''
            back = row[1].strip() if len(row) > 1 else ''
        if front or back:
            cards.append(Card(front=front, back=back, section=section))
    return CsvImportPreview(tuple(cards), tuple(rejected), selected_delimiter)


class AddCard:
    def __init__(self, repo: DeckRepository, max_cards: int | None = None):
        self.repo = repo
        self.max_cards = max_cards

    def execute(
        self, deck_id: str, front: str, back: str,
        expected_version: int | None = None,
        section: str = '',
    ) -> tuple[Card, int]:
        def add(deck: CardDeck):
            _ensure_capacity(deck, 1, self.max_cards)
            card = Card(front=front, back=back, section=section)
            return (card, deck.add(card)), True

        return self.repo.mutate_cards(
            deck_id, add, expected_version=expected_version
        )


class AddCardsBulk:
    def __init__(self, repo: DeckRepository, max_cards: int | None = None):
        self.repo = repo
        self.max_cards = max_cards

    def execute(
        self, deck_id: str, bulk_text: str, expected_version: int | None = None,
        section: str = '',
    ) -> int:
        new_cards = []
        for line in bulk_text.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            front, back = _parse_bulk_line(line)
            new_cards.append(Card(front=front, back=back, section=section))
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
        section: str | None = None,
    ) -> bool:
        def edit(deck: CardDeck):
            result = deck.edit_by_id(card_id, front, back, section)
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


class GenerateDocumentSide(GenerateDocument):
    def __init__(
        self,
        repo: DeckRepository,
        renderer: DocumentRenderer,
        compiler: PdfCompiler,
        cards_per_page: int,
        side: str,
    ):
        super().__init__(repo, renderer, compiler, cards_per_page)
        if side not in {'front', 'back'}:
            raise ValueError('side must be front or back')
        self.side = side

    def execute(self, deck_id: str) -> CompileResult:
        deck = self.repo.load_cards(deck_id)
        padded_deck = CardDeck(cards=deck.padded(self.cards_per_page))
        method_name = 'render_fronts' if self.side == 'front' else 'render_backs'
        render_side = getattr(self.renderer, method_name)
        return self.compiler.compile(render_side(padded_deck))


class PreflightDocument:
    OVERFLOW_MARKER = re.compile(
        r'DIDACTIC-CARDS-OVERFLOW:(\d+):(front|back)'
    )
    AUTOFIT_MARKER = re.compile(
        r'DIDACTIC-CARDS-AUTOFIT:(\d+):(front|back):([a-z]+)'
    )
    HBOX_MARKER = re.compile(
        r'DIDACTIC-CARDS-HBOX-(BEGIN|END):(\d+):(front|back)'
    )

    def __init__(
        self,
        repo: DeckRepository,
        renderer: DocumentRenderer,
        compiler: PdfCompiler,
        cards_per_page: int,
    ):
        self.repo = repo
        self.renderer = renderer
        self.compiler = compiler
        self.cards_per_page = cards_per_page

    def execute(self, deck_id: str) -> PreflightReport:
        deck = self.repo.load_cards(deck_id)
        padded_deck = CardDeck(cards=deck.padded(self.cards_per_page))
        if not deck.cards:
            return PreflightReport(False, (PreflightIssue(
                code='empty-deck',
                severity='error',
                message='Добавьте хотя бы одну карточку',
            ),))

        issues: list[PreflightIssue] = []
        for number, card in enumerate(deck.cards, start=1):
            for side in ('front', 'back'):
                if not getattr(card, side).strip():
                    issues.append(PreflightIssue(
                        code='empty-side',
                        severity='warning',
                        message=(
                            f'Карточка {number}: '
                            f'{"лицевая" if side == "front" else "оборотная"} '
                            'сторона пуста'
                        ),
                        card_id=card.id,
                        card_number=number,
                        side=side,
                    ))

        remainder = len(deck) % self.cards_per_page
        if remainder:
            empty_slots = self.cards_per_page - remainder
            issues.append(PreflightIssue(
                code='partial-sheet',
                severity='info',
                message=f'Последний лист содержит {empty_slots} пустых ячеек',
            ))

        for message in self.renderer.printable_area_warnings():
            issues.append(PreflightIssue(
                code='printable-area', severity='warning', message=message
            ))

        result = self.compiler.compile(self.renderer.render(padded_deck))
        if not result.success:
            issues.append(PreflightIssue(
                code='compile-failed',
                severity='error',
                message='Документ не компилируется; проверьте содержимое карточек',
            ))
            return PreflightReport(False, tuple(issues))

        seen_overflows: set[tuple[int, str]] = set()
        for match in self.OVERFLOW_MARKER.finditer(result.log):
            number = int(match.group(1))
            side = match.group(2)
            if number > len(deck.cards) or (number, side) in seen_overflows:
                continue
            seen_overflows.add((number, side))
            card = deck.cards[number - 1]
            side_label = 'лицевая' if side == 'front' else 'оборотная'
            issues.append(PreflightIssue(
                code='vertical-overflow',
                severity='error',
                message=f'Карточка {number}: {side_label} сторона не помещается по высоте',
                card_id=card.id,
                card_number=number,
                side=side,
            ))

        seen_autofit: set[tuple[int, str]] = set()
        for match in self.AUTOFIT_MARKER.finditer(result.log):
            number = int(match.group(1))
            side = match.group(2)
            size = match.group(3)
            if number > len(deck.cards) or (number, side) in seen_autofit:
                continue
            seen_autofit.add((number, side))
            card = deck.cards[number - 1]
            side_label = 'лицевая' if side == 'front' else 'оборотная'
            issues.append(PreflightIssue(
                code='auto-fit',
                severity='warning',
                message=(
                    f'Карточка {number}: {side_label} сторона уменьшена до {size}'
                ),
                card_id=card.id,
                card_number=number,
                side=side,
            ))

        horizontal_overflows: set[tuple[int, str]] = set()
        measured_side: tuple[int, str] | None = None
        for line in result.log.splitlines():
            marker = self.HBOX_MARKER.search(line)
            if marker:
                measured_side = (
                    (int(marker.group(2)), marker.group(3))
                    if marker.group(1) == 'BEGIN' else None
                )
            elif measured_side and 'Overfull \\hbox' in line:
                horizontal_overflows.add(measured_side)

        for number, side in sorted(horizontal_overflows):
            if number > len(deck.cards):
                continue
            card = deck.cards[number - 1]
            side_label = 'лицевая' if side == 'front' else 'оборотная'
            issues.append(PreflightIssue(
                code='horizontal-overflow',
                severity='error',
                message=(
                    f'Карточка {number}: {side_label} сторона '
                    'не помещается по ширине'
                ),
                card_id=card.id,
                card_number=number,
                side=side,
            ))
        if 'Missing character:' in result.log:
            issues.append(PreflightIssue(
                code='missing-glyph',
                severity='error',
                message='Выбранный LaTeX-шрифт не содержит один или несколько символов',
            ))

        return PreflightReport(
            not any(issue.severity == 'error' for issue in issues),
            tuple(issues),
        )


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
