from __future__ import annotations

import pytest

from didactic_cards.use_cases.card_use_cases import (
    AddCard,
    AddCardsBulk,
    DeleteCard,
    EditCard,
    GenerateDocument,
    GetDeck,
    ImportCsv,
    PreviewDocument,
    ReorderCards,
    ResetCards,
    CardLimitExceeded,
)


def test_card_crud_and_reorder(repo, deck_id):
    first, first_index = AddCard(repo).execute(deck_id, "A", "1")
    second, second_index = AddCard(repo).execute(deck_id, "B", "2")
    assert (first.front, first_index, second.front, second_index) == ("A", 0, "B", 1)

    assert EditCard(repo).execute(deck_id, first.id, "A+", "1+") is True
    assert ReorderCards(repo).execute(deck_id, [second.id, first.id]) is True
    assert [card.front for card in GetDeck(repo).execute(deck_id).cards] == ["B", "A+"]

    assert DeleteCard(repo).execute(deck_id, "missing") is False
    assert DeleteCard(repo).execute(deck_id, second.id) is True
    assert [card.front for card in repo.load_cards(deck_id).cards] == ["A+"]

    ResetCards(repo).execute(deck_id)
    assert len(repo.load_cards(deck_id)) == 0


def test_bulk_import_accepts_single_pipe(repo, deck_id):
    count = AddCardsBulk(repo).execute(deck_id, "q1 | a1\nq2\n\nq3 | a3")
    cards = repo.load_cards(deck_id).cards
    assert count == 3
    assert [(card.front, card.back) for card in cards] == [
        ("q1", "a1"), ("q2", ""), ("q3", "a3")
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


@pytest.mark.xfail(
    strict=True,
    reason="BUG-IMP-001: UI documents '||', but the parser leaves one pipe in the back",
)
def test_bulk_import_matches_documented_double_pipe(repo, deck_id):
    AddCardsBulk(repo).execute(deck_id, "question || answer")
    card = repo.load_cards(deck_id).cards[0]
    assert (card.front, card.back) == ("question", "answer")


def test_csv_import_accepts_comma_and_utf8_bom(repo, deck_id):
    count = ImportCsv(repo).execute(deck_id, "front,back\nвопрос,ответ".encode("utf-8-sig"))
    assert count == 2
    assert repo.load_cards(deck_id).cards[1].back == "ответ"


def test_csv_import_skips_blank_rows_and_empty_cells(repo, deck_id):
    count = ImportCsv(repo).execute(deck_id, b"\n,\nfront-only\n")
    assert count == 1
    assert repo.load_cards(deck_id).cards[0].front == "front-only"


@pytest.mark.xfail(
    strict=True,
    reason="BUG-IMP-002: UI promises semicolon CSV while csv.reader uses comma",
)
def test_csv_import_matches_documented_semicolon(repo, deck_id):
    ImportCsv(repo).execute(deck_id, "front;back".encode())
    card = repo.load_cards(deck_id).cards[0]
    assert (card.front, card.back) == ("front", "back")


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


@pytest.mark.xfail(
    strict=True,
    reason="BUG-VAL-001: cards_per_page is not validated before modulo",
)
def test_generate_rejects_non_positive_page_capacity(repo, deck_id, app):
    AddCard(repo).execute(deck_id, "Q", "A")
    with pytest.raises(ValueError, match="cards_per_page"):
        PreviewDocument(repo, app.config["RENDERER"], 0).execute(deck_id)
