from __future__ import annotations

import pytest

from didactic_cards.adapters.latex_renderer import LatexRenderer
from didactic_cards.domain.interfaces import CompileResult, DocumentRenderer
from didactic_cards.domain.rendering import DeckRenderSettings
from didactic_cards.domain.trusted import PrintJobSnapshot, TrustedTemplateVersion
from didactic_cards.domain.entities import Card
from didactic_cards.use_cases.card_use_cases import (
    AddCard,
    AddCardsBulk,
    DeleteCard,
    EditCard,
    GenerateDocument,
    GenerateDocumentSide,
    GetDeck,
    ImportCsv,
    PreviewDocument,
    PreflightDocument,
    ReorderCards,
    ResetCards,
    CardLimitExceeded,
    CsvValidationError,
    preview_csv_import,
)


def test_card_crud_and_reorder(repo, deck_id):
    first, first_index = AddCard(repo).execute(
        deck_id, "A", "1", section='Раздел 1'
    )
    second, second_index = AddCard(repo).execute(deck_id, "B", "2")
    assert (first.front, first_index, second.front, second_index) == ("A", 0, "B", 1)

    assert EditCard(repo).execute(
        deck_id, first.id, "A+", "1+", section='Раздел 2'
    ) is True
    assert ReorderCards(repo).execute(deck_id, [second.id, first.id]) is True
    assert [card.front for card in GetDeck(repo).execute(deck_id).cards] == ["B", "A+"]
    assert GetDeck(repo).execute(deck_id).cards[1].section == 'Раздел 2'

    assert DeleteCard(repo).execute(deck_id, "missing") is False
    assert DeleteCard(repo).execute(deck_id, second.id) is True
    assert [card.front for card in repo.load_cards(deck_id).cards] == ["A+"]

    ResetCards(repo).execute(deck_id)
    assert len(repo.load_cards(deck_id)) == 0


def test_bulk_import_requires_exact_double_pipe(repo, deck_id):
    count = AddCardsBulk(repo).execute(deck_id, "q1 | a1\nq2\n\nq3 | a3")
    cards = repo.load_cards(deck_id).cards
    assert count == 3
    assert [(card.front, card.back) for card in cards] == [
        ("q1 | a1", ""), ("q2", ""), ("q3 | a3", "")
    ]


def test_single_card_limit_is_enforced_without_partial_write(repo, deck_id):
    AddCard(repo, max_cards=1).execute(deck_id, 'Q1', 'A1')
    with pytest.raises(CardLimitExceeded):
        AddCard(repo, max_cards=1).execute(deck_id, 'Q2', 'A2')
    assert [card.front for card in repo.load_cards(deck_id).cards] == ['Q1']


def test_bulk_limit_is_atomic(repo, deck_id):
    AddCard(repo).execute(deck_id, 'existing', '')
    with pytest.raises(CardLimitExceeded):
        AddCardsBulk(repo, max_cards=2).execute(deck_id, 'Q1 | A1\nQ2 | A2')
    assert [card.front for card in repo.load_cards(deck_id).cards] == ['existing']


def test_bulk_import_matches_documented_double_pipe(repo, deck_id):
    AddCardsBulk(repo).execute(
        deck_id, "question || answer", section='Кинематика'
    )
    card = repo.load_cards(deck_id).cards[0]
    assert (card.section, card.front, card.back) == (
        "Кинематика", "question", "answer"
    )


def test_bulk_import_supports_escaped_delimiter_and_backslash(repo, deck_id):
    AddCardsBulk(repo).execute(deck_id, r"question \|| literal || C:\\cards")
    card = repo.load_cards(deck_id).cards[0]
    assert (card.front, card.back) == ("question || literal", r"C:\cards")


def test_csv_import_accepts_comma_and_utf8_bom(repo, deck_id):
    count = ImportCsv(repo).execute(deck_id, "front,back\nвопрос,ответ".encode("utf-8-sig"))
    assert count == 2
    assert repo.load_cards(deck_id).cards[1].back == "ответ"


def test_csv_import_skips_blank_rows_and_empty_cells(repo, deck_id):
    count = ImportCsv(repo).execute(deck_id, b"\n,\nfront-only\n")
    assert count == 1
    assert repo.load_cards(deck_id).cards[0].front == "front-only"


def test_csv_import_matches_documented_semicolon(repo, deck_id):
    ImportCsv(repo).execute(deck_id, "front;back".encode())
    card = repo.load_cards(deck_id).cards[0]
    assert (card.front, card.back) == ("front", "back")


def test_csv_import_explicit_dialect_and_header(repo, deck_id):
    ImportCsv(repo).execute(
        deck_id,
        "front\tback\nQ\tA".encode(),
        delimiter="tab",
        has_header=True,
    )
    card = repo.load_cards(deck_id).cards[0]
    assert (card.front, card.back) == ("Q", "A")


def test_csv_import_accepts_section_front_back_and_legacy_two_columns(repo, deck_id):
    ImportCsv(repo).execute(
        deck_id,
        "section;front;back\nГеометрия;Q1;A1\nQ2;A2".encode(),
        delimiter="semicolon",
        has_header=True,
    )
    cards = repo.load_cards(deck_id).cards
    assert (cards[0].section, cards[0].front, cards[0].back) == (
        'Геометрия', 'Q1', 'A1'
    )
    assert (cards[1].section, cards[1].front, cards[1].back) == ('', 'Q2', 'A2')


def test_csv_import_rejects_unknown_dialect(repo, deck_id):
    with pytest.raises(ValueError, match="delimiter"):
        ImportCsv(repo).execute(deck_id, b"Q,A", delimiter="pipes")


def test_csv_preview_reports_rejected_rows_and_import_is_atomic(repo, deck_id):
    source = b"front;back\nQ;A\ninvalid;row;extra;column"
    preview = preview_csv_import(source, has_header=True)
    assert preview.delimiter == ";"
    assert [(card.front, card.back) for card in preview.cards] == [("Q", "A")]
    assert preview.rejected_rows[0]["row"] == 3
    assert preview.to_dict(preview_limit=0)["truncated"] is True

    with pytest.raises(CsvValidationError, match="отклонённые строки"):
        ImportCsv(repo).execute(deck_id, source, has_header=True)
    assert len(repo.load_cards(deck_id)) == 0


def test_csv_import_rejects_non_utf8(repo, deck_id):
    with pytest.raises(UnicodeDecodeError):
        ImportCsv(repo).execute(deck_id, b"\xff\xfe")


def test_csv_limit_is_atomic(repo, deck_id):
    with pytest.raises(CardLimitExceeded):
        ImportCsv(repo, max_cards=1).execute(deck_id, b'Q1,A1\nQ2,A2')
    assert len(repo.load_cards(deck_id)) == 0


def test_generate_and_preview_pad_to_whole_sheet(repo, deck_id, app):
    AddCard(repo).execute(deck_id, "Q", "A")
    renderer = app.config["RENDERER"]
    compiler = app.config["COMPILER"]

    preview = PreviewDocument(repo, renderer, 8).execute(deck_id)
    result = GenerateDocument(repo, renderer, compiler, 8).execute(deck_id)

    assert "documentclass" in preview
    assert result.success is True
    assert [len(seen) for seen in renderer.decks] == [8, 8]
    assert len(compiler.sources) == 1
    assert renderer.render_settings == [
        repo.get_render_settings(deck_id),
        repo.get_render_settings(deck_id),
    ]


def test_preflight_reports_slots_inserted_by_section_break(repo, deck_id):
    AddCard(repo).execute(deck_id, 'Q1', 'A1', section='One')
    AddCard(repo).execute(deck_id, 'Q2', 'A2', section='Two')
    repo.save_render_settings(
        deck_id,
        DeckRenderSettings(
            header_visibility='front',
            section_break='new-row',
        ),
    )
    renderer = LatexRenderer(cards_per_row=2, rows_per_page=4)
    class Compiler:
        def __init__(self):
            self.sources = []

        def compile(self, source):
            self.sources.append(source)
            return CompileResult(True, b'%PDF', '')

    compiler = Compiler()

    report = PreflightDocument(repo, renderer, compiler, 8).execute(deck_id)

    issues = {issue.code: issue for issue in report.issues}
    assert issues['section-break-padding'].message.endswith('1')
    assert issues['partial-sheet'].message.endswith('5 пустых ячеек')
    assert compiler.sources[0].index('Q1') < compiler.sources[0].index('Q2')


def test_document_renderer_default_settings_hook_is_backward_compatible(app):
    renderer = app.config['RENDERER']
    assert DocumentRenderer.with_render_settings(
        renderer, DeckRenderSettings.centered()
    ) is renderer
    assert DocumentRenderer.with_trusted_template(renderer, None) is renderer


@pytest.mark.parametrize(('side', 'expected'), [('front', 'front'), ('back', 'back')])
def test_generate_single_side_pads_and_uses_requested_renderer(
    repo, deck_id, app, side, expected
):
    AddCard(repo).execute(deck_id, "Q", "A")
    renderer = app.config["RENDERER"]
    compiler = app.config["COMPILER"]

    result = GenerateDocumentSide(
        repo, renderer, compiler, 8, side
    ).execute(deck_id)

    assert result.success is True
    assert len(renderer.decks[-1]) == 8
    assert renderer.sides[-1] == expected
    assert len(compiler.sources) == 1


def test_generate_single_side_rejects_unknown_side(repo, app):
    with pytest.raises(ValueError, match="front or back"):
        GenerateDocumentSide(
            repo, app.config["RENDERER"], app.config["COMPILER"], 8, "both"
        )


def test_preflight_reports_card_and_compiler_issues(repo, deck_id, app):
    card, _ = AddCard(repo).execute(deck_id, "Q", "")

    class ReportingCompiler:
        def compile(self, _source):
            from didactic_cards.domain.interfaces import CompileResult
            return CompileResult(
                True,
                b'%PDF',
                'DIDACTIC-CARDS-OVERFLOW:1:front\n'
                'DIDACTIC-CARDS-HBOX-BEGIN:1:front\n'
                'Overfull \\hbox\n'
                'DIDACTIC-CARDS-HBOX-END:1:front\n'
                'DIDACTIC-CARDS-HEADER-OVERFLOW:1:back\n'
                'DIDACTIC-CARDS-HBOX-BEGIN:1:back:header\n'
                'Overfull \\hbox\n'
                'DIDACTIC-CARDS-HBOX-END:1:back:header\n'
                'Missing character:',
            )

    report = PreflightDocument(
        repo, app.config['RENDERER'], ReportingCompiler(), 8
    ).execute(deck_id)
    by_code = {issue.code: issue for issue in report.issues}

    assert report.ready is False
    assert by_code['empty-side'].card_id == card.id
    assert by_code['partial-sheet'].severity == 'info'
    assert by_code['vertical-overflow'].side == 'front'
    assert by_code['horizontal-overflow'].severity == 'error'
    assert by_code['header-vertical-overflow'].side == 'back'
    assert by_code['header-horizontal-overflow'].side == 'back'
    assert by_code['missing-glyph'].severity == 'error'
    assert report.to_dict()['error_count'] == 5
    assert report.to_dict()['warning_count'] == 1


def test_preflight_ignores_layout_hbox_outside_card_measurement(repo, deck_id, app):
    AddCard(repo).execute(deck_id, '2 + 2', '4')

    class LayoutWarningCompiler:
        def compile(self, _source):
            from didactic_cards.domain.interfaces import CompileResult
            return CompileResult(
                True,
                b'%PDF',
                'DIDACTIC-CARDS-HBOX-BEGIN:1:front\n'
                'DIDACTIC-CARDS-HBOX-END:1:front\n'
                'Overfull \\hbox (2.61038pt too wide)',
            )

    report = PreflightDocument(
        repo, app.config['RENDERER'], LayoutWarningCompiler(), 8
    ).execute(deck_id)

    assert report.ready is True
    assert 'horizontal-overflow' not in {issue.code for issue in report.issues}


def test_preflight_compile_failure_is_safe(repo, deck_id, app):
    AddCard(repo).execute(deck_id, 'Q', 'A')

    class FailingCompiler:
        def compile(self, _source):
            from didactic_cards.domain.interfaces import CompileResult
            return CompileResult(
                False,
                b'',
                'DIDACTIC-CARDS-HBOX-BEGIN:1:back:body\n/private/path',
                'compile-error',
            )

    report = PreflightDocument(
        repo, app.config['RENDERER'], FailingCompiler(), 8
    ).execute(deck_id)
    issue = next(issue for issue in report.issues if issue.code == 'compile-failed')
    assert report.ready is False
    assert '/private/path' not in issue.message
    assert issue.card_number == 1
    assert issue.side == 'back'
    assert issue.card_id is not None


def test_preflight_rejects_empty_deck_without_compilation(repo, deck_id, app):
    report = PreflightDocument(
        repo, app.config['RENDERER'], app.config['COMPILER'], 8
    ).execute(deck_id)
    assert report.ready is False
    assert report.issues[0].code == 'empty-deck'
    assert app.config['COMPILER'].sources == []


def test_preflight_rejects_non_positive_page_capacity(repo, deck_id, app):
    with pytest.raises(ValueError, match='cards_per_page'):
        PreflightDocument(
            repo, app.config['RENDERER'], app.config['COMPILER'], 0
        ).execute(deck_id)


def test_preflight_clean_full_sheet_and_layout_warning(repo, deck_id, app):
    for number in range(8):
        AddCard(repo).execute(deck_id, f'Q{number}', f'A{number}')
    renderer = app.config['RENDERER']
    renderer.printable_area_warnings = lambda: ('Сетка вне области',)

    report = PreflightDocument(
        repo, renderer, app.config['COMPILER'], 8
    ).execute(deck_id)

    assert report.ready is True
    assert [(issue.code, issue.severity) for issue in report.issues] == [
        ('printable-area', 'warning')
    ]


def test_preflight_reports_auto_fit_as_addressable_warning(repo, deck_id, app):
    card, _ = AddCard(repo).execute(deck_id, 'Long', 'Answer')

    class AutoFitCompiler:
        def compile(self, _source):
            from didactic_cards.domain.interfaces import CompileResult
            return CompileResult(
                True, b'%PDF', 'DIDACTIC-CARDS-AUTOFIT:1:front:footnotesize'
            )

    report = PreflightDocument(
        repo, app.config['RENDERER'], AutoFitCompiler(), 8
    ).execute(deck_id)
    issue = next(issue for issue in report.issues if issue.code == 'auto-fit')
    assert report.ready is True
    assert issue.severity == 'warning'
    assert issue.card_id == card.id
    assert issue.side == 'front'
    assert 'footnotesize' in issue.message


def test_preflight_reports_header_autofit_once_and_ignores_padding(
    repo, deck_id, app
):
    card, _ = AddCard(repo).execute(deck_id, 'Q', 'A', section='Long header')

    class HeaderAutoFitCompiler:
        def compile(self, _source):
            from didactic_cards.domain.interfaces import CompileResult
            return CompileResult(
                True,
                b'%PDF',
                'DIDACTIC-CARDS-HEADER-AUTOFIT:1:front:scriptsize\n'
                'DIDACTIC-CARDS-HEADER-AUTOFIT:1:front:scriptsize\n'
                'DIDACTIC-CARDS-HEADER-AUTOFIT:9:back:scriptsize\n'
                'DIDACTIC-CARDS-HEADER-OVERFLOW:9:back\n'
                'DIDACTIC-CARDS-HBOX-BEGIN:9:back:header\n'
                'Overfull \\hbox\n'
                'DIDACTIC-CARDS-HBOX-END:9:back:header\n',
            )

    report = PreflightDocument(
        repo, app.config['RENDERER'], HeaderAutoFitCompiler(), 8
    ).execute(deck_id)
    issues = [issue for issue in report.issues if issue.code == 'header-auto-fit']
    assert len(issues) == 1
    assert issues[0].card_id == card.id
    assert issues[0].side == 'front'


def test_generate_rejects_non_positive_page_capacity(repo, deck_id, app):
    AddCard(repo).execute(deck_id, "Q", "A")
    with pytest.raises(ValueError, match="cards_per_page"):
        PreviewDocument(repo, app.config["RENDERER"], 0).execute(deck_id)


def test_generate_uses_immutable_snapshot_without_rereading_repository(
    repo, deck_id, app, monkeypatch
):
    template = TrustedTemplateVersion(
        deck_id=deck_id, source='{{ content }}', version=1
    ).approved()
    snapshot = PrintJobSnapshot(
        deck_id=deck_id,
        deck_version=7,
        cards=(Card(front='snapshot Q', back='snapshot A'),),
        render_settings=DeckRenderSettings.centered(),
        trusted_template=template,
    )

    monkeypatch.setattr(
        repo, 'load_cards', lambda _deck_id: pytest.fail('snapshot reread cards')
    )
    monkeypatch.setattr(
        repo,
        'get_render_settings',
        lambda _deck_id: pytest.fail('snapshot reread settings'),
    )

    result = GenerateDocument(
        repo,
        app.config['RENDERER'],
        app.config['COMPILER'],
        8,
        snapshot=snapshot,
    ).execute(deck_id)

    assert result.success is True
    assert app.config['RENDERER'].decks[-1].cards[0].front == 'snapshot Q'
    assert app.config['RENDERER'].trusted_templates[-1] is template

    PreviewDocument(
        repo,
        app.config['RENDERER'],
        8,
        snapshot=PrintJobSnapshot(
            deck_id=deck_id,
            deck_version=8,
            cards=(),
            render_settings=DeckRenderSettings.centered(),
        ),
    ).execute(deck_id)
    assert app.config['RENDERER'].trusted_templates[-1] is None

    with pytest.raises(ValueError, match='another deck'):
        GenerateDocument(
            repo,
            app.config['RENDERER'],
            app.config['COMPILER'],
            8,
            snapshot=PrintJobSnapshot(
                deck_id='different',
                deck_version=1,
                cards=(),
                render_settings=DeckRenderSettings.centered(),
            ),
        ).execute(deck_id)
