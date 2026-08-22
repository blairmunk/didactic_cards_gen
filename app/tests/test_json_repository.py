from __future__ import annotations

import json

import pytest

from didactic_cards.adapters.json_repository import JsonRepository
from didactic_cards.domain.entities import Card, CardDeck


@pytest.fixture
def json_repo(tmp_path):
    return JsonRepository(str(tmp_path / "nested" / "data"))


def test_repository_initializes_storage(json_repo):
    assert json_repo.decks_file.read_text(encoding="utf-8") == "[]"
    assert json_repo.cards_dir.is_dir()


def test_deck_and_card_persistence_round_trip(json_repo):
    deck = json_repo.create_deck("История", "Даты")
    cards = CardDeck([Card(front="1242", back="Ледовое побоище")])
    json_repo.save_cards(deck.id, cards)

    reloaded = JsonRepository(str(json_repo.data_dir))
    loaded_deck = reloaded.get_deck(deck.id)
    loaded_cards = reloaded.load_cards(deck.id)

    assert loaded_deck.card_ids == [cards.cards[0].id]
    assert loaded_cards.cards[0].back == "Ледовое побоище"


def test_update_sort_clone_and_delete(json_repo):
    first = json_repo.create_deck("Первая")
    second = json_repo.create_deck("Вторая")
    json_repo.save_cards(first.id, CardDeck([Card(front="Q", back="A")]))
    json_repo.update_deck(second.id, "Вторая+", "Описание")

    listed = json_repo.list_decks()
    assert listed[0].id == second.id

    clone = json_repo.clone_deck(first.id)
    assert clone.parent_id == first.id
    assert json_repo.load_cards(clone.id).cards[0].id != json_repo.load_cards(first.id).cards[0].id

    assert json_repo.delete_deck(first.id) is True
    assert json_repo.delete_deck(first.id) is False
    assert not json_repo._cards_path(first.id).exists()


@pytest.mark.xfail(
    strict=True,
    reason="BUG-DATA-005: deep-cloned cards lose ancestry even though Card supports parent_id",
)
def test_cloned_cards_reference_their_originals(json_repo):
    source = json_repo.create_deck("Source")
    original = Card(front="Q", back="A")
    json_repo.save_cards(source.id, CardDeck([original]))
    clone = json_repo.clone_deck(source.id)
    assert json_repo.load_cards(clone.id).cards[0].parent_id == original.id


def test_legacy_load_save_aliases(json_repo):
    deck = json_repo.create_deck("Совместимость")
    json_repo.save(CardDeck([Card(front="Q")]), deck.id)
    assert json_repo.load(deck.id).cards[0].front == "Q"


def test_load_cards_repairs_metadata_when_card_count_changed(json_repo):
    deck = json_repo.create_deck("Repair")
    card = Card(front="Q")
    json_repo._cards_path(deck.id).write_text(
        json.dumps([card.to_dict()]), encoding="utf-8"
    )
    assert json_repo.get_deck(deck.id).card_ids == []
    json_repo.load_cards(deck.id)
    assert json_repo.get_deck(deck.id).card_ids == [card.id]


@pytest.mark.xfail(
    strict=True,
    reason="BUG-DATA-001: equal-length but different card_ids are never synchronized",
)
def test_load_cards_repairs_all_stale_card_ids(json_repo):
    deck = json_repo.create_deck("Колода")
    card = Card(front="Q")
    json_repo.save_cards(deck.id, CardDeck([card]))
    metadata = json.loads(json_repo.decks_file.read_text(encoding="utf-8"))
    metadata[0]["card_ids"] = ["stale-id"]
    json_repo.decks_file.write_text(json.dumps(metadata), encoding="utf-8")

    json_repo.load_cards(deck.id)
    assert json_repo.get_deck(deck.id).card_ids == [card.id]


@pytest.mark.xfail(
    strict=True,
    reason="BUG-DATA-002: corrupted JSON is silently treated as empty data",
)
def test_corruption_is_reported_instead_of_silently_erased(json_repo):
    json_repo.decks_file.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt|JSON"):
        json_repo.list_decks()


@pytest.mark.xfail(
    strict=True,
    reason="BUG-DATA-003: saving cards for an unknown deck creates an orphan file",
)
def test_unknown_deck_cannot_create_orphan_cards(json_repo):
    with pytest.raises(KeyError):
        json_repo.save_cards("missing", CardDeck([Card(front="orphan")]))
    assert not json_repo._cards_path("missing").exists()


@pytest.mark.xfail(
    strict=True,
    reason="BUG-DATA-004: writes truncate the live file instead of using atomic replace",
)
def test_interrupted_write_preserves_previous_json(json_repo, monkeypatch):
    json_repo.create_deck("Сохранённая")
    before = json_repo.decks_file.read_bytes()

    def interrupted_dump(*_args, **_kwargs):
        raise OSError("simulated interruption")

    monkeypatch.setattr("didactic_cards.adapters.json_repository.json.dump", interrupted_dump)
    with pytest.raises(OSError):
        json_repo.create_deck("Потерянная")
    assert json_repo.decks_file.read_bytes() == before
