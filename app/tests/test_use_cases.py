import pytest
from didactic_cards.domain.entities import Card, CardDeck
from didactic_cards.domain.interfaces import CardRepository
from didactic_cards.use_cases.card_use_cases import (
    AddCard, DeleteCard, EditCard, ReorderCards, ResetCards,
    GetDeck, AddCardsBulk
)


class InMemoryRepository(CardRepository):
    """Тестовый репозиторий в памяти."""
    def __init__(self):
        self._deck = CardDeck()

    def load(self) -> CardDeck:
        return self._deck

    def save(self, deck: CardDeck) -> None:
        self._deck = deck


@pytest.fixture
def repo():
    return InMemoryRepository()


class TestAddCard:
    def test_add(self, repo):
        card, idx = AddCard(repo).execute('вопрос', 'ответ')
        assert idx == 0
        assert card.front == 'вопрос'
        assert len(repo.load()) == 1

    def test_add_strips_whitespace(self, repo):
        card, _ = AddCard(repo).execute('  q  ', '  a  ')
        assert card.front == 'q'
        assert card.back == 'a'


class TestDeleteCard:
    def test_delete(self, repo):
        AddCard(repo).execute('q', 'a')
        assert DeleteCard(repo).execute(0)
        assert len(repo.load()) == 0

    def test_delete_invalid(self, repo):
        assert not DeleteCard(repo).execute(0)


class TestEditCard:
    def test_edit(self, repo):
        AddCard(repo).execute('old', 'old')
        assert EditCard(repo).execute(0, 'new', 'new')
        assert repo.load().cards[0].front == 'new'


class TestReorderCards:
    def test_reorder(self, repo):
        AddCard(repo).execute('A', '')
        AddCard(repo).execute('B', '')
        assert ReorderCards(repo).execute([1, 0])
        assert repo.load().cards[0].front == 'B'


class TestResetCards:
    def test_reset(self, repo):
        AddCard(repo).execute('q', 'a')
        ResetCards(repo).execute()
        assert len(repo.load()) == 0


class TestAddCardsBulk:
    def test_bulk(self, repo):
        text = "q1 || a1\nq2 || a2\nq3 || a3"
        count = AddCardsBulk(repo).execute(text)
        assert count == 3
        assert repo.load().cards[0].front == 'q1'
        assert repo.load().cards[2].back == 'a3'

    def test_bulk_without_separator(self, repo):
        text = "только вопрос"
        count = AddCardsBulk(repo).execute(text)
        assert count == 1
        assert repo.load().cards[0].front == 'только вопрос'
        assert repo.load().cards[0].back == ''

    def test_bulk_skips_empty_lines(self, repo):
        text = "q1 || a1\n\n\nq2 || a2\n   \n"
        count = AddCardsBulk(repo).execute(text)
        assert count == 2

    def test_bulk_empty_input(self, repo):
        count = AddCardsBulk(repo).execute('')
        assert count == 0
        assert len(repo.load()) == 0