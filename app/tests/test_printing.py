import pytest

from didactic_cards.domain.entities import Card
from didactic_cards.domain.printing import (
    DuplexMode,
    build_print_layout,
    build_sheets,
)


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


def test_new_row_section_break_inserts_physical_slot_before_duplex_mapping():
    cards = [
        Card(front='Q1', back='A1', section='One'),
        Card(front='Q2', back='A2', section='Two'),
        Card(front='Q3', back='A3', section='Two'),
    ]

    layout = build_print_layout(
        cards, rows=4, columns=2, section_break='new-row'
    )
    sheet = build_sheets(layout.cards, rows=4, columns=2)[0]

    assert [card.front for card in layout.cards[:4]] == ['Q1', '', 'Q2', 'Q3']
    assert layout.section_padding == 1
    assert layout.trailing_padding == 4
    assert [card.back for card in sheet.back_slots[:4]] == ['', 'A1', 'A3', 'A2']


@pytest.mark.parametrize('duplex_mode', ['long-edge', 'short-edge'])
def test_new_sheet_section_break_keeps_front_back_on_same_physical_slot(
    duplex_mode,
):
    cards = [
        Card(front='Q1', back='A1', section='One'),
        Card(front='Q2', back='A2', section='Two'),
    ]

    layout = build_print_layout(
        cards, rows=4, columns=2, section_break='new-sheet'
    )
    sheets = build_sheets(
        layout.cards, rows=4, columns=2, duplex_mode=duplex_mode
    )

    assert len(sheets) == 2
    assert sheets[0].front_slots[0].front == 'Q1'
    assert sheets[1].front_slots[0].front == 'Q2'
    for sheet in sheets:
        for front_card in sheet.front_slots:
            matching_back = next(
                card for card in sheet.back_slots if card.id == front_card.id
            )
            assert matching_back.back == front_card.back


def test_continuous_layout_only_pads_the_last_sheet():
    layout = build_print_layout(
        make_cards(3), rows=4, columns=2, section_break='continuous'
    )

    assert [card.front for card in layout.cards[:3]] == ['Q1', 'Q2', 'Q3']
    assert layout.section_padding == 0
    assert layout.trailing_padding == 5


@pytest.mark.parametrize('rows, columns', [(0, 2), (4, 0)])
def test_print_layout_rejects_invalid_grid(rows, columns):
    with pytest.raises(ValueError, match='positive'):
        build_print_layout(make_cards(1), rows=rows, columns=columns)


def test_print_layout_rejects_unknown_section_break():
    with pytest.raises(ValueError, match='unsupported section break'):
        build_print_layout(
            make_cards(1), rows=4, columns=2, section_break='new-column'
        )


@pytest.mark.parametrize('rows, columns', [(0, 2), (4, 0), (-1, 2)])
def test_invalid_grid_is_rejected(rows, columns):
    with pytest.raises(ValueError, match='positive'):
        build_sheets(make_cards(1), rows=rows, columns=columns)


def test_unknown_duplex_mode_is_rejected():
    with pytest.raises(ValueError, match='unsupported duplex mode'):
        build_sheets(make_cards(1), rows=1, columns=1, duplex_mode='diagonal')
