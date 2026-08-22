from didactic_cards.domain.entities import Card, CardDeck
from didactic_cards.use_cases.deck_use_cases import (
    CloneDeck,
    CreateDeck,
    DeleteDeck,
    GetDeckInfo,
    ListDecks,
    UpdateDeck,
)


def test_deck_lifecycle(repo):
    created = CreateDeck(repo).execute("  Геометрия  ", "Фигуры")
    assert created.name == "Геометрия"
    assert GetDeckInfo(repo).execute(created.id).description == "Фигуры"
    assert created.id in {deck.id for deck in ListDecks(repo).execute()}

    updated = UpdateDeck(repo).execute(created.id, "", "Новая версия")
    assert updated.name == "Новая колода"
    assert updated.description == "Новая версия"

    assert DeleteDeck(repo).execute(created.id) is True
    assert DeleteDeck(repo).execute(created.id) is False
    assert GetDeckInfo(repo).execute(created.id) is None


def test_clone_deck_is_deep(repo):
    source = repo.create_deck("Алгебра", "Квадратные уравнения")
    original_card = Card(front="x²=4", back="x=±2")
    repo.save_cards(source.id, CardDeck([original_card]))

    clone = CloneDeck(repo).execute(source.id)
    cloned_card = repo.load_cards(clone.id).cards[0]

    assert clone.parent_id == source.id
    assert clone.name == "Алгебра (копия)"
    assert cloned_card.id != original_card.id
    assert cloned_card.front == original_card.front


def test_unknown_deck_operations_return_none(repo):
    assert GetDeckInfo(repo).execute("missing") is None
    assert UpdateDeck(repo).execute("missing", "X") is None
    assert CloneDeck(repo).execute("missing") is None
