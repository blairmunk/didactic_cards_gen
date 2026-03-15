import pytest
import tempfile
import os
from didactic_cards.domain.entities import Card, Deck
from didactic_cards.adapters.json_storage import JsonFileStorage


@pytest.fixture
def storage(tmp_path):
    filepath = str(tmp_path / 'test_storage.json')
    return JsonFileStorage(filepath=filepath)


class TestCardStorage:
    def test_save_and_get(self, storage):
        card = Card(front='Q1', back='A1')
        storage.save_card(card)
        loaded = storage.get_card(card.id)
        assert loaded is not None
        assert loaded.front == 'Q1'
        assert loaded.back == 'A1'
        assert loaded.id == card.id

    def test_get_nonexistent(self, storage):
        assert storage.get_card('nonexistent') is None

    def test_delete(self, storage):
        card = Card(front='Q1', back='A1')
        storage.save_card(card)
        assert storage.delete_card(card.id) is True
        assert storage.get_card(card.id) is None

    def test_delete_nonexistent(self, storage):
        assert storage.delete_card('nonexistent') is False

    def test_list_cards(self, storage):
        storage.save_card(Card(front='Q1', back='A1'))
        storage.save_card(Card(front='Q2', back='A2'))
        cards = storage.list_cards()
        assert len(cards) == 2

    def test_update_card(self, storage):
        card = Card(front='old', back='old')
        storage.save_card(card)
        card.update('new', 'new')
        storage.save_card(card)
        loaded = storage.get_card(card.id)
        assert loaded.front == 'new'
        assert loaded.back == 'new'


class TestDeckStorage:
    def test_save_and_get(self, storage):
        deck = Deck(name='Физика')
        storage.save_deck(deck)
        loaded = storage.get_deck(deck.id)
        assert loaded is not None
        assert loaded.name == 'Физика'

    def test_deck_with_cards(self, storage):
        card1 = Card(front='Q1', back='A1')
        card2 = Card(front='Q2', back='A2')
        storage.save_card(card1)
        storage.save_card(card2)

        deck = Deck(name='Тест')
        deck.add_card_id(card1.id)
        deck.add_card_id(card2.id)
        storage.save_deck(deck)

        cards = storage.get_deck_cards(deck.id)
        assert len(cards) == 2
        assert cards[0].front == 'Q1'
        assert cards[1].front == 'Q2'

    def test_delete_deck(self, storage):
        deck = Deck(name='Удаляемая')
        storage.save_deck(deck)
        assert storage.delete_deck(deck.id) is True
        assert storage.get_deck(deck.id) is None

    def test_list_decks(self, storage):
        storage.save_deck(Deck(name='Д1'))
        storage.save_deck(Deck(name='Д2'))
        decks = storage.list_decks()
        assert len(decks) == 2

    def test_delete_card_removes_from_deck(self, storage):
        card = Card(front='Q', back='A')
        storage.save_card(card)

        deck = Deck(name='Тест')
        deck.add_card_id(card.id)
        storage.save_deck(deck)

        storage.delete_card(card.id)
        loaded_deck = storage.get_deck(deck.id)
        assert card.id not in loaded_deck.card_ids

    def test_reorder(self, storage):
        card1 = Card(front='Q1', back='A1')
        card2 = Card(front='Q2', back='A2')
        storage.save_card(card1)
        storage.save_card(card2)

        deck = Deck(name='Тест')
        deck.add_card_id(card1.id)
        deck.add_card_id(card2.id)
        storage.save_deck(deck)

        deck.reorder([card2.id, card1.id])
        storage.save_deck(deck)

        cards = storage.get_deck_cards(deck.id)
        assert cards[0].front == 'Q2'
        assert cards[1].front == 'Q1'


class TestCloning:
    def test_clone_card(self, storage):
        original = Card(front='Q', back='A')
        storage.save_card(original)

        clone = storage.clone_card(original.id)
        assert clone is not None
        assert clone.id != original.id
        assert clone.parent_id == original.id
        assert clone.front == 'Q'
        assert clone.back == 'A'

        # Клон сохранён в хранилище
        assert storage.get_card(clone.id) is not None

    def test_clone_card_nonexistent(self, storage):
        assert storage.clone_card('nonexistent') is None

    def test_clone_deck_deep(self, storage):
        card1 = Card(front='Q1', back='A1')
        card2 = Card(front='Q2', back='A2')
        storage.save_card(card1)
        storage.save_card(card2)

        deck = Deck(name='Оригинал')
        deck.add_card_id(card1.id)
        deck.add_card_id(card2.id)
        storage.save_deck(deck)

        clone = storage.clone_deck(deck.id, deep=True)
        assert clone is not None
        assert clone.id != deck.id
        assert clone.parent_id == deck.id
        assert clone.name == 'Оригинал (копия)'
        assert len(clone.card_ids) == 2

        # Карточки — новые UUID
        assert clone.card_ids[0] != card1.id
        assert clone.card_ids[1] != card2.id

        # Клонированные карточки ссылаются на оригиналы
        cloned_card1 = storage.get_card(clone.card_ids[0])
        assert cloned_card1.parent_id == card1.id
        assert cloned_card1.front == 'Q1'

    def test_clone_deck_shallow(self, storage):
        card = Card(front='Q', back='A')
        storage.save_card(card)

        deck = Deck(name='Оригинал')
        deck.add_card_id(card.id)
        storage.save_deck(deck)

        clone = storage.clone_deck(deck.id, deep=False)
        assert clone is not None
        # Те же карточки
        assert clone.card_ids[0] == card.id

    def test_clone_deck_nonexistent(self, storage):
        assert storage.clone_deck('nonexistent') is None

    def test_total_cards_after_deep_clone(self, storage):
        card = Card(front='Q', back='A')
        storage.save_card(card)

        deck = Deck(name='Тест')
        deck.add_card_id(card.id)
        storage.save_deck(deck)

        storage.clone_deck(deck.id, deep=True)

        # Оригинал + клон = 2 карточки
        assert len(storage.list_cards()) == 2
        # 2 колоды
        assert len(storage.list_decks()) == 2