import pytest
from datetime import datetime, timezone

from didactic_cards.domain.entities import Card, CardDeck
from didactic_cards.domain.interfaces import ConcurrentModificationError
from didactic_cards.domain.rendering import DeckRenderSettings
from didactic_cards.use_cases.deck_use_cases import (
    CloneDeck,
    CreateDeck,
    GetDeckInfo,
    GetDeckRenderSettings,
    ListDecks,
    ListTrashedDecks,
    PurgeDeck,
    RestoreDeck,
    TrashDeck,
    UpdateDeck,
    UpdateDeckRenderSettings,
)


def test_deck_lifecycle(repo):
    created = CreateDeck(repo).execute("  Геометрия  ", "Фигуры")
    assert created.name == "Геометрия"
    assert GetDeckInfo(repo).execute(created.id).description == "Фигуры"
    assert created.id in {deck.id for deck in ListDecks(repo).execute()}

    updated = UpdateDeck(repo).execute(created.id, "", "Новая версия")
    assert updated.name == "Новая колода"
    assert updated.description == "Новая версия"

    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    assert TrashDeck(repo, 30, clock=lambda: now).execute(
        created.id, updated.version
    ) is True
    assert GetDeckInfo(repo).execute(created.id) is None
    trashed = ListTrashedDecks(repo).execute()[0]
    assert trashed.trashed_at == now
    assert trashed.purge_after == datetime(2026, 9, 23, tzinfo=timezone.utc)
    assert RestoreDeck(repo).execute(created.id, trashed.version) is True
    restored = GetDeckInfo(repo).execute(created.id)
    assert restored.version == trashed.version + 1

    assert TrashDeck(repo, 30, clock=lambda: now).execute(
        created.id, restored.version
    ) is True
    trashed = ListTrashedDecks(repo).execute()[0]
    assert PurgeDeck(repo).execute(created.id, trashed.version) is True
    assert PurgeDeck(repo).execute(created.id, trashed.version) is False


@pytest.mark.parametrize('retention', [True, 0, -1, 1.5])
def test_trash_use_case_validates_retention(retention, repo):
    with pytest.raises(ValueError, match='Срок хранения'):
        TrashDeck(repo, retention)


def test_trash_use_case_rejects_naive_clock(repo):
    deck = repo.create_deck('Naive')
    operation = TrashDeck(
        repo, 30, clock=lambda: datetime(2026, 8, 24)
    )

    with pytest.raises(ValueError, match='часовой пояс'):
        operation.execute(deck.id, deck.version)

    assert repo.get_deck(deck.id) is not None


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


def test_render_settings_use_cases_apply_optimistic_lock(repo):
    deck = CreateDeck(repo).execute("Styled")
    settings = DeckRenderSettings(
        preset="custom",
        horizontal_alignment="right",
        vertical_alignment="bottom",
    )

    saved = UpdateDeckRenderSettings(repo).execute(
        deck.id, settings, expected_version=deck.version
    )

    assert saved == settings
    assert GetDeckRenderSettings(repo).execute(deck.id) == settings
    with pytest.raises(ConcurrentModificationError):
        UpdateDeckRenderSettings(repo).execute(
            deck.id,
            DeckRenderSettings.centered(),
            expected_version=deck.version,
        )


def test_render_settings_use_case_rejects_authoring_mode_transition(repo):
    deck = CreateDeck(repo).execute('Immutable type')
    before_version = repo.get_deck(deck.id).version

    with pytest.raises(ValueError, match='не может быть изменён'):
        UpdateDeckRenderSettings(repo).execute(
            deck.id,
            DeckRenderSettings(authoring_mode='advanced'),
            expected_version=before_version,
        )

    assert repo.get_render_settings(deck.id).authoring_mode.value == 'safe'
    assert repo.get_deck(deck.id).version == before_version
