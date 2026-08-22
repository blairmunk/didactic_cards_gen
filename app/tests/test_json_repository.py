from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from didactic_cards.adapters.json_repository import JsonRepository
from didactic_cards.domain.entities import Card, CardDeck
from didactic_cards.domain.interfaces import DeckRepository


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


def test_load_cards_repairs_all_stale_card_ids(json_repo):
    deck = json_repo.create_deck("Колода")
    card = Card(front="Q")
    json_repo.save_cards(deck.id, CardDeck([card]))
    metadata = json.loads(json_repo.decks_file.read_text(encoding="utf-8"))
    metadata[0]["card_ids"] = ["stale-id"]
    json_repo.decks_file.write_text(json.dumps(metadata), encoding="utf-8")

    json_repo.load_cards(deck.id)
    assert json_repo.get_deck(deck.id).card_ids == [card.id]


def test_corruption_is_reported_instead_of_silently_erased(json_repo):
    json_repo.decks_file.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt|JSON"):
        json_repo.list_decks()


def test_unknown_deck_cannot_create_orphan_cards(json_repo):
    with pytest.raises(KeyError):
        json_repo.save_cards("missing", CardDeck([Card(front="orphan")]))
    assert not json_repo._cards_path("missing").exists()


def test_interrupted_write_preserves_previous_json(json_repo, monkeypatch):
    json_repo.create_deck("Сохранённая")
    before = json_repo.decks_file.read_bytes()

    def interrupted_dump(*_args, **_kwargs):
        raise OSError("simulated interruption")

    monkeypatch.setattr("didactic_cards.adapters.json_repository.json.dump", interrupted_dump)
    with pytest.raises(OSError):
        json_repo.create_deck("Потерянная")
    assert json_repo.decks_file.read_bytes() == before


def test_successful_write_keeps_last_valid_backup(json_repo):
    json_repo.create_deck("Первая")
    before = json_repo.decks_file.read_bytes()
    json_repo.create_deck("Вторая")
    backup = json_repo.decks_file.with_suffix(".json.bak")
    assert backup.read_bytes() == before
    assert json.loads(backup.read_bytes())


def test_deck_id_cannot_escape_cards_directory(json_repo):
    with pytest.raises(ValueError, match="Invalid deck id"):
        json_repo.load_cards("../../outside")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        ["not-an-object"],
        [{"id": "deck", "updated_at": "not-a-timestamp"}],
    ],
)
def test_invalid_deck_schema_is_reported(json_repo, payload):
    json_repo.decks_file.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="Corrupt JSON"):
        json_repo.list_decks()


def test_invalid_matching_deck_is_reported_by_lookup(json_repo):
    json_repo.decks_file.write_text(
        json.dumps([{"id": "broken", "updated_at": "not-a-timestamp"}]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Corrupt JSON"):
        json_repo.get_deck("broken")


def test_invalid_card_schema_and_missing_file_are_reported(json_repo):
    deck = json_repo.create_deck("Broken cards")
    cards_path = json_repo._cards_path(deck.id)
    cards_path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    with pytest.raises(ValueError, match="Corrupt JSON"):
        json_repo.load_cards(deck.id)

    cards_path.write_text(json.dumps([{"updated_at": "invalid"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="Corrupt JSON"):
        json_repo.load_cards(deck.id)

    cards_path.unlink()
    with pytest.raises(ValueError, match="file is missing"):
        json_repo.load_cards(deck.id)


def test_missing_deck_update_and_clone_return_none(json_repo):
    assert json_repo.update_deck("missing", "No") is None
    assert json_repo.clone_deck("missing") is None


def test_create_removes_card_file_when_metadata_write_fails(json_repo, monkeypatch):
    def fail_metadata(_deck):
        raise OSError("metadata write failed")

    monkeypatch.setattr(json_repo, "_save_deck_meta_unlocked", fail_metadata)
    with pytest.raises(OSError, match="metadata write failed"):
        json_repo.create_deck("Rolled back")
    assert json_repo.list_decks() == []
    assert list(json_repo.cards_dir.glob("*.json")) == []


def test_clone_removes_card_file_when_metadata_write_fails(json_repo, monkeypatch):
    source = json_repo.create_deck("Source")
    before = set(json_repo.cards_dir.glob("*.json"))

    def fail_metadata(_deck):
        raise OSError("metadata write failed")

    monkeypatch.setattr(json_repo, "_save_deck_meta_unlocked", fail_metadata)
    with pytest.raises(OSError, match="metadata write failed"):
        json_repo.clone_deck(source.id)
    assert set(json_repo.cards_dir.glob("*.json")) == before


def test_default_mutation_contract_saves_only_changed_data(json_repo, monkeypatch):
    deck = json_repo.create_deck("Base contract")
    saves = []
    original_save = json_repo.save_cards

    def record_save(deck_id, cards):
        saves.append(deck_id)
        original_save(deck_id, cards)

    monkeypatch.setattr(json_repo, "save_cards", record_save)
    assert DeckRepository.mutate_cards(
        json_repo, deck.id, lambda cards: (len(cards), False)
    ) == 0
    DeckRepository.mutate_cards(
        json_repo,
        deck.id,
        lambda cards: (cards.add(Card(front="saved")), True),
    )
    assert saves == [deck.id]


def test_concurrent_card_mutations_do_not_lose_updates(json_repo):
    deck = json_repo.create_deck("Concurrent")
    repositories = [JsonRepository(json_repo.data_dir) for _ in range(8)]

    def add(index):
        from didactic_cards.use_cases.card_use_cases import AddCard

        AddCard(repositories[index % len(repositories)]).execute(
            deck.id, f"Q{index}", f"A{index}"
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(add, range(40)))

    cards = json_repo.load_cards(deck.id).cards
    assert len(cards) == 40
    assert {card.front for card in cards} == {f"Q{index}" for index in range(40)}
