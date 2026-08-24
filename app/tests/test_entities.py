import pytest
from didactic_cards.domain.entities import Card, Deck, CardDeck
from didactic_cards.domain.rendering import DeckRenderSettings


class TestCard:
    def test_defaults(self):
        card = Card()
        assert card.front == ''
        assert card.back == ''
        assert card.section == ''
        assert card.upper_header == ''
        assert card.lower_header == ''
        assert card.id is not None
        assert card.parent_id is None
        assert card.created_at is not None
        assert card.updated_at is not None

    def test_is_empty(self):
        assert Card().is_empty()
        assert Card(front='  ', back='  ').is_empty()
        assert not Card(front='Q').is_empty()
        assert not Card(upper_header=r'\small Header').is_empty()

    def test_update(self):
        card = Card(front='old', back='old')
        old_updated = card.updated_at
        card.update('new', 'new', upper_header='Top', lower_header='Bottom')
        assert card.front == 'new'
        assert card.back == 'new'
        assert card.upper_header == 'Top'
        assert card.lower_header == 'Bottom'
        assert card.updated_at >= old_updated

    def test_clone(self):
        original = Card(
            front='Q', back='A', section='Механика',
            upper_header='Top', lower_header='Bottom',
        )
        clone = original.clone()
        assert clone.id != original.id
        assert clone.parent_id == original.id
        assert clone.front == 'Q'
        assert clone.back == 'A'
        assert clone.section == 'Механика'
        assert clone.upper_header == 'Top'
        assert clone.lower_header == 'Bottom'

    def test_clone_no_parent(self):
        original = Card(front='Q', back='A')
        clone = original.clone(keep_parent=False)
        assert clone.parent_id is None

    def test_round_trip(self):
        card = Card(
            front='Q', back='A', section='Алгебра',
            upper_header='Top', lower_header='Bottom',
        )
        restored = Card.from_dict(card.to_dict())
        assert restored.id == card.id
        assert restored.front == card.front
        assert restored.back == card.back
        assert restored.section == card.section
        assert restored.upper_header == card.upper_header
        assert restored.lower_header == card.lower_header
        assert restored.parent_id == card.parent_id


class TestDeck:
    def test_defaults(self):
        deck = Deck()
        assert deck.name == 'Новая колода'
        assert len(deck) == 0
        assert deck.render_settings == DeckRenderSettings.centered()

    def test_add_card_id(self):
        deck = Deck()
        pos = deck.add_card_id('card-1')
        assert pos == 0
        assert len(deck) == 1
        assert deck.card_ids[0] == 'card-1'

    def test_remove_card_id(self):
        deck = Deck(card_ids=['card-1', 'card-2'])
        assert deck.remove_card_id('card-1') is True
        assert len(deck) == 1
        assert deck.remove_card_id('nonexistent') is False

    def test_reorder(self):
        deck = Deck(card_ids=['a', 'b', 'c'])
        assert deck.reorder(['c', 'a', 'b']) is True
        assert deck.card_ids == ['c', 'a', 'b']

    def test_reorder_invalid(self):
        deck = Deck(card_ids=['a', 'b'])
        assert deck.reorder(['a', 'b', 'c']) is False
        assert deck.card_ids == ['a', 'b']

    def test_clone_shallow(self):
        deck = Deck(name='Физика', card_ids=['c1', 'c2'])
        clone = deck.clone()
        assert clone.id != deck.id
        assert clone.parent_id == deck.id
        assert clone.name == 'Физика (копия)'
        assert clone.card_ids == ['c1', 'c2']

    def test_clone_with_card_mapping(self):
        deck = Deck(card_ids=['old-1', 'old-2'])
        clone = deck.clone(card_clones={'old-1': 'new-1', 'old-2': 'new-2'})
        assert clone.card_ids == ['new-1', 'new-2']

    def test_clear(self):
        deck = Deck(card_ids=['a', 'b'])
        deck.clear()
        assert len(deck) == 0

    def test_round_trip(self):
        deck = Deck(
            name='Тест',
            card_ids=['c1', 'c2'],
            render_settings=DeckRenderSettings(
                header_visibility='both', header_position='bottom'
            ),
        )
        restored = Deck.from_dict(deck.to_dict())
        assert restored.id == deck.id
        assert restored.name == deck.name
        assert restored.card_ids == deck.card_ids
        assert restored.render_settings == deck.render_settings

    def test_legacy_dict_without_render_settings_keeps_old_layout(self):
        restored = Deck.from_dict({'name': 'Legacy'})
        assert restored.render_settings == DeckRenderSettings.legacy()


class TestCardDeck:
    def test_add_and_len(self):
        cd = CardDeck()
        pos = cd.add(Card(front='Q', back='A'))
        assert pos == 0
        assert len(cd) == 1

    def test_delete(self):
        cd = CardDeck(cards=[Card(front='Q1'), Card(front='Q2')])
        assert cd.delete(0) is True
        assert len(cd) == 1
        assert cd.cards[0].front == 'Q2'

    def test_delete_invalid(self):
        cd = CardDeck()
        assert cd.delete(0) is False
        assert cd.delete(-1) is False

    def test_edit(self):
        cd = CardDeck(cards=[Card(front='old', back='old')])
        assert cd.edit(0, 'new', 'new') is True
        assert cd.cards[0].front == 'new'

    def test_edit_invalid(self):
        cd = CardDeck()
        assert cd.edit(0, 'x', 'x') is False

    def test_reorder(self):
        cd = CardDeck(cards=[Card(front='A'), Card(front='B'), Card(front='C')])
        assert cd.reorder([2, 0, 1]) is True
        assert cd.cards[0].front == 'C'
        assert cd.cards[1].front == 'A'
        assert cd.cards[2].front == 'B'

    def test_reorder_invalid(self):
        cd = CardDeck(cards=[Card(), Card()])
        assert cd.reorder([0, 0]) is False

    def test_uuid_edit_delete_and_reorder(self):
        first, second = Card(front='A'), Card(front='B')
        cd = CardDeck([first, second])
        assert cd.index_of(second.id) == 1
        assert cd.edit_by_id(first.id, 'A+', '') is True
        assert cd.reorder_by_ids([second.id, first.id]) is True
        assert [card.id for card in cd.cards] == [second.id, first.id]
        assert cd.delete_by_id(second.id) is True
        assert cd.index_of('missing') is None
        assert cd.edit_by_id('missing', '', '') is False
        assert cd.delete_by_id('missing') is False

    def test_uuid_reorder_rejects_missing_or_duplicate_ids(self):
        first, second = Card(), Card()
        cd = CardDeck([first, second])
        assert cd.reorder_by_ids([first.id]) is False
        assert cd.reorder_by_ids([first.id, first.id]) is False

    def test_padded(self):
        cd = CardDeck(cards=[Card(front='Q1')])
        padded = cd.padded(4)
        assert len(padded) == 4
        assert padded[0].front == 'Q1'
        assert padded[1].is_empty()

    def test_padded_empty(self):
        cd = CardDeck()
        assert cd.padded(4) == []

    def test_padded_exact(self):
        cd = CardDeck(cards=[Card() for _ in range(8)])
        assert len(cd.padded(4)) == 8

    def test_clear(self):
        cd = CardDeck(cards=[Card(), Card()])
        cd.clear()
        assert len(cd) == 0

    def test_round_trip(self):
        cd = CardDeck(cards=[Card(front='Q', back='A')])
        restored = CardDeck.from_list(cd.to_list())
        assert len(restored) == 1
        assert restored.cards[0].front == 'Q'
