import pytest

from didactic_cards.domain.entities import Card
from didactic_cards.domain.printing import DuplexMode, build_sheets


def make_cards(count):
    return [Card(front=f'Q{number}', back=f'A{number}') for number in range(1, count + 1)]


def test_long_edge_mirrors_columns_and_preserves_rows():
    sheet = build_sheets(
        make_cards(8), rows=4, columns=2, duplex_mode=DuplexMode.LONG_EDGE
    )[0]
    assert [card.back for card in sheet.back_slots] == [
        'A2', 'A1', 'A4', 'A3', 'A6', 'A5', 'A8', 'A7'
    ]


def test_short_edge_mirrors_rows_and_preserves_columns():
    sheet = build_sheets(
        make_cards(8), rows=4, columns=2, duplex_mode=DuplexMode.SHORT_EDGE
    )[0]
    assert [card.back for card in sheet.back_slots] == [
        'A7', 'A8', 'A5', 'A6', 'A3', 'A4', 'A1', 'A2'
    ]


def test_sheets_are_padded_independently():
    sheets = build_sheets(make_cards(9), rows=4, columns=2)
    assert len(sheets) == 2
    assert [card.front for card in sheets[0].front_slots] == [
        f'Q{number}' for number in range(1, 9)
    ]
    assert sheets[1].front_slots[0].front == 'Q9'
    assert all(card.is_empty() for card in sheets[1].front_slots[1:])
    assert sheets[1].back_slots[1].back == 'A9'


def test_empty_deck_has_no_sheets():
    assert build_sheets([], rows=4, columns=2) == []


@pytest.mark.parametrize('rows, columns', [(0, 2), (4, 0), (-1, 2)])
def test_invalid_grid_is_rejected(rows, columns):
    with pytest.raises(ValueError, match='positive'):
        build_sheets(make_cards(1), rows=rows, columns=columns)


def test_unknown_duplex_mode_is_rejected():
    with pytest.raises(ValueError, match='unsupported duplex mode'):
        build_sheets(make_cards(1), rows=1, columns=1, duplex_mode='diagonal')
