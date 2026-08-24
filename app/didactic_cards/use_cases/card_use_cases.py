from __future__ import annotations

import re
from dataclasses import dataclass

from ..domain.interfaces import (
    DeckRepository,
    DocumentRenderer, PdfCompiler, CompileResult,
)
from ..domain.entities import Card, CardDeck
from ..domain.rendering import AuthoringMode, DeckRenderSettings
from ..domain.trusted import PrintJobSnapshot, TrustedTemplateVersion
from .card_import import (
    BulkImportPreview,
    CsvImportPreview,
    preview_bulk_import,
    preview_csv_import,
)


def _print_inputs(
    repo: DeckRepository,
    deck_id: str,
    trusted_template: TrustedTemplateVersion | None,
    snapshot: PrintJobSnapshot | None,
) -> tuple[CardDeck, DeckRenderSettings, TrustedTemplateVersion | None]:
    if snapshot is not None:
        if snapshot.deck_id != deck_id:
            raise ValueError('print snapshot belongs to another deck')
        return (
            CardDeck(list(snapshot.cards)),
            snapshot.render_settings,
            snapshot.trusted_template,
        )
    return (
        repo.load_cards(deck_id),
        repo.get_render_settings(deck_id),
        trusted_template,
    )


def _configure_print_renderer(
    renderer: DocumentRenderer,
    settings: DeckRenderSettings,
    template: TrustedTemplateVersion | None,
) -> DocumentRenderer:
    configured = renderer.with_render_settings(settings)
    return configured.with_trusted_template(
        template
        if settings.authoring_mode is AuthoringMode.ADVANCED
        else None
    )


class CardLimitExceeded(ValueError):
    pass


class CsvValidationError(ValueError):
    def __init__(self, preview: CsvImportPreview):
        self.preview = preview
        super().__init__(
            f'CSV содержит отклонённые строки: {len(preview.rejected_rows)}'
        )


class BulkValidationError(ValueError):
    def __init__(self, preview: BulkImportPreview):
        self.preview = preview
        super().__init__(
            f'Пакетный ввод содержит ошибки: {preview.rejected_count}'
        )


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


COMPILE_CONTEXT_MARKER = re.compile(
    r'DIDACTIC-CARDS-HBOX-BEGIN:(\d+):(front|back)(?::(?:body|header))?'
)


def compile_error_context(
    log: str, deck: CardDeck
) -> tuple[int, str, Card] | None:
    matches = list(COMPILE_CONTEXT_MARKER.finditer(log))
    if not matches:
        return None
    number = int(matches[-1].group(1))
    side = matches[-1].group(2)
    if not 1 <= number <= len(deck.cards):
        return None
    return number, side, deck.cards[number - 1]


def _ensure_capacity(deck: CardDeck, incoming: int, max_cards: int | None) -> None:
    if max_cards is not None and len(deck) + incoming > max_cards:
        raise CardLimitExceeded(f'Максимум карточек в колоде: {max_cards}')


class AddCard:
    def __init__(self, repo: DeckRepository, max_cards: int | None = None):
        self.repo = repo
        self.max_cards = max_cards

    def execute(
        self, deck_id: str, front: str, back: str,
        expected_version: int | None = None,
        section: str = '',
        upper_header: str = '',
        lower_header: str = '',
    ) -> tuple[Card, int]:
        def add(deck: CardDeck):
            _ensure_capacity(deck, 1, self.max_cards)
            card = Card(
                front=front,
                back=back,
                section=section,
                upper_header=upper_header,
                lower_header=lower_header,
            )
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
        mode = self.repo.get_render_settings(deck_id).authoring_mode
        preview = preview_bulk_import(
            bulk_text,
            mode,
            section=section,
            existing_cards=self.repo.load_cards(deck_id).cards,
        )
        if preview.errors:
            raise BulkValidationError(preview)
        new_rows = list(preview.rows)
        def add_all(deck: CardDeck):
            _ensure_capacity(deck, len(new_rows), self.max_cards)
            for row in new_rows:
                deck.add(row.to_card())
            return len(new_rows), bool(new_rows)

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
        encoding: str = 'utf-8',
    ) -> int:
        mode = self.repo.get_render_settings(deck_id).authoring_mode
        preview = preview_csv_import(
            file_bytes,
            delimiter,
            authoring_mode=mode,
            encoding=encoding,
            existing_cards=self.repo.load_cards(deck_id).cards,
        )
        if preview.errors:
            raise CsvValidationError(preview)
        new_rows = list(preview.rows)
        def add_all(deck: CardDeck):
            _ensure_capacity(deck, len(new_rows), self.max_cards)
            for row in new_rows:
                deck.add(row.to_card())
            return len(new_rows), bool(new_rows)

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
        upper_header: str | None = None,
        lower_header: str | None = None,
    ) -> bool:
        def edit(deck: CardDeck):
            result = deck.edit_by_id(
                card_id, front, back, section, upper_header, lower_header
            )
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
                 compiler: PdfCompiler, cards_per_page: int,
                 trusted_template: TrustedTemplateVersion | None = None,
                 snapshot: PrintJobSnapshot | None = None):
        self.repo = repo
        self.renderer = renderer
        self.compiler = compiler
        self.cards_per_page = cards_per_page
        self.trusted_template = trusted_template
        self.snapshot = snapshot

    def execute(self, deck_id: str) -> CompileResult:
        deck, settings, template = _print_inputs(
            self.repo, deck_id, self.trusted_template, self.snapshot
        )
        renderer = _configure_print_renderer(
            self.renderer, settings, template
        )
        layout = renderer.prepare_print_layout(deck, self.cards_per_page)
        padded_deck = CardDeck(cards=list(layout.cards))
        latex = renderer.render(padded_deck)
        return self.compiler.compile(latex)


class GenerateDocumentSide(GenerateDocument):
    def __init__(
        self,
        repo: DeckRepository,
        renderer: DocumentRenderer,
        compiler: PdfCompiler,
        cards_per_page: int,
        side: str,
        trusted_template: TrustedTemplateVersion | None = None,
        snapshot: PrintJobSnapshot | None = None,
    ):
        super().__init__(
            repo, renderer, compiler, cards_per_page, trusted_template, snapshot
        )
        if side not in {'front', 'back'}:
            raise ValueError('side must be front or back')
        self.side = side

    def execute(self, deck_id: str) -> CompileResult:
        deck, settings, template = _print_inputs(
            self.repo, deck_id, self.trusted_template, self.snapshot
        )
        method_name = 'render_fronts' if self.side == 'front' else 'render_backs'
        renderer = _configure_print_renderer(
            self.renderer, settings, template
        )
        layout = renderer.prepare_print_layout(deck, self.cards_per_page)
        padded_deck = CardDeck(cards=list(layout.cards))
        render_side = getattr(renderer, method_name)
        return self.compiler.compile(render_side(padded_deck))


class PreflightDocument:
    OVERFLOW_MARKER = re.compile(
        r'DIDACTIC-CARDS-OVERFLOW:(\d+):(front|back)'
    )
    AUTOFIT_MARKER = re.compile(
        r'DIDACTIC-CARDS-AUTOFIT:(\d+):(front|back):([a-z]+)'
    )
    HEADER_OVERFLOW_MARKER = re.compile(
        r'DIDACTIC-CARDS-HEADER-OVERFLOW:(\d+):(front|back)'
    )
    HEADER_AUTOFIT_MARKER = re.compile(
        r'DIDACTIC-CARDS-HEADER-AUTOFIT:(\d+):(front|back):([a-z]+)'
    )
    HBOX_MARKER = re.compile(
        r'DIDACTIC-CARDS-HBOX-(BEGIN|END):(\d+):(front|back)'
        r'(?::(body|header))?'
    )

    def __init__(
        self,
        repo: DeckRepository,
        renderer: DocumentRenderer,
        compiler: PdfCompiler,
        cards_per_page: int,
        trusted_template: TrustedTemplateVersion | None = None,
        snapshot: PrintJobSnapshot | None = None,
    ):
        self.repo = repo
        self.renderer = renderer
        self.compiler = compiler
        self.cards_per_page = cards_per_page
        self.trusted_template = trusted_template
        self.snapshot = snapshot

    def execute(self, deck_id: str) -> PreflightReport:
        deck, settings, template = _print_inputs(
            self.repo, deck_id, self.trusted_template, self.snapshot
        )
        renderer = _configure_print_renderer(
            self.renderer, settings, template
        )
        layout = renderer.prepare_print_layout(deck, self.cards_per_page)
        padded_deck = CardDeck(cards=list(layout.cards))
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

        if layout.section_padding:
            issues.append(PreflightIssue(
                code='section-break-padding',
                severity='info',
                message=(
                    'Разрывы секций добавили пустых ячеек: '
                    f'{layout.section_padding}'
                ),
            ))

        if layout.trailing_padding:
            issues.append(PreflightIssue(
                code='partial-sheet',
                severity='info',
                message=(
                    'Последний лист содержит '
                    f'{layout.trailing_padding} пустых ячеек'
                ),
            ))

        for message in renderer.printable_area_warnings():
            issues.append(PreflightIssue(
                code='printable-area', severity='warning', message=message
            ))

        result = self.compiler.compile(renderer.render(padded_deck))
        if not result.success:
            context = compile_error_context(result.log, deck)
            if context is not None:
                number, side, card = context
                side_label = 'лицевая' if side == 'front' else 'оборотная'
                message = (
                    f'Карточка {number}: {side_label} сторона '
                    'не компилируется'
                )
            else:
                number = None
                side = None
                card = None
                message = (
                    'Документ не компилируется; проверьте содержимое карточек'
                )
            issues.append(PreflightIssue(
                code='compile-failed',
                severity='error',
                message=message,
                card_id=card.id if card else None,
                card_number=number,
                side=side,
            ))
            return PreflightReport(False, tuple(issues))

        seen_overflows: set[tuple[int, str]] = set()
        for match in self.OVERFLOW_MARKER.finditer(result.log):
            number = int(match.group(1))
            side = match.group(2)
            if not 1 <= number <= len(deck.cards) or (
                number, side
            ) in seen_overflows:
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
            if not 1 <= number <= len(deck.cards) or (
                number, side
            ) in seen_autofit:
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

        seen_header_overflows: set[tuple[int, str]] = set()
        for match in self.HEADER_OVERFLOW_MARKER.finditer(result.log):
            number = int(match.group(1))
            side = match.group(2)
            if (
                not 1 <= number <= len(deck.cards)
                or (number, side) in seen_header_overflows
            ):
                continue
            seen_header_overflows.add((number, side))
            card = deck.cards[number - 1]
            side_label = 'лицевой' if side == 'front' else 'оборотной'
            issues.append(PreflightIssue(
                code='header-vertical-overflow',
                severity='error',
                message=(
                    f'Карточка {number}: колонтитул {side_label} стороны '
                    'не помещается по высоте'
                ),
                card_id=card.id,
                card_number=number,
                side=side,
            ))

        seen_header_autofit: set[tuple[int, str]] = set()
        for match in self.HEADER_AUTOFIT_MARKER.finditer(result.log):
            number = int(match.group(1))
            side = match.group(2)
            size = match.group(3)
            if (
                not 1 <= number <= len(deck.cards)
                or (number, side) in seen_header_autofit
            ):
                continue
            seen_header_autofit.add((number, side))
            card = deck.cards[number - 1]
            side_label = 'лицевой' if side == 'front' else 'оборотной'
            issues.append(PreflightIssue(
                code='header-auto-fit',
                severity='warning',
                message=(
                    f'Карточка {number}: колонтитул {side_label} стороны '
                    f'уменьшен до {size}'
                ),
                card_id=card.id,
                card_number=number,
                side=side,
            ))

        horizontal_overflows: set[tuple[int, str, str]] = set()
        measured_side: tuple[int, str, str] | None = None
        for line in result.log.splitlines():
            marker = self.HBOX_MARKER.search(line)
            if marker:
                measured_side = (
                    (
                        int(marker.group(2)),
                        marker.group(3),
                        marker.group(4) or 'body',
                    )
                    if marker.group(1) == 'BEGIN' else None
                )
            elif measured_side and 'Overfull \\hbox' in line:
                horizontal_overflows.add(measured_side)

        for number, side, component in sorted(horizontal_overflows):
            if not 1 <= number <= len(deck.cards):
                continue
            card = deck.cards[number - 1]
            side_label = 'лицевая' if side == 'front' else 'оборотная'
            header_side_label = 'лицевой' if side == 'front' else 'оборотной'
            is_header = component == 'header'
            issues.append(PreflightIssue(
                code=(
                    'header-horizontal-overflow'
                    if is_header else 'horizontal-overflow'
                ),
                severity='error',
                message=(
                    f'Карточка {number}: '
                    + (
                        f'колонтитул {header_side_label} стороны '
                        if is_header else f'{side_label} сторона '
                    )
                    + 'не помещается по ширине'
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
                 cards_per_page: int,
                 trusted_template: TrustedTemplateVersion | None = None,
                 snapshot: PrintJobSnapshot | None = None):
        self.repo = repo
        self.renderer = renderer
        self.cards_per_page = cards_per_page
        self.trusted_template = trusted_template
        self.snapshot = snapshot

    def execute(self, deck_id: str) -> str:
        deck, settings, template = _print_inputs(
            self.repo, deck_id, self.trusted_template, self.snapshot
        )
        renderer = _configure_print_renderer(
            self.renderer, settings, template
        )
        layout = renderer.prepare_print_layout(deck, self.cards_per_page)
        padded_deck = CardDeck(cards=list(layout.cards))
        return renderer.render(padded_deck)
