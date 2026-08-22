from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context

import pytest

from didactic_cards.adapters.json_repository import (
    JsonRepository,
    SCHEMA_VERSION,
    UnsupportedSchemaError,
)
from didactic_cards.domain.entities import Card, CardDeck
from didactic_cards.domain.interfaces import DeckRepository


def _add_cards_in_process(data_dir, deck_id, start, count):
    from didactic_cards.use_cases.card_use_cases import AddCard

    repository = JsonRepository(data_dir)
    for index in range(start, start + count):
        AddCard(repository).execute(deck_id, f"P{index}", f"A{index}")


@pytest.fixture
def json_repo(tmp_path):
    return JsonRepository(str(tmp_path / "nested" / "data"))


def test_repository_initializes_storage(json_repo):
    assert json_repo.decks_file.read_text(encoding="utf-8") == "[]"
    assert json_repo.cards_dir.is_dir()
    assert json.loads(json_repo.manifest_file.read_text(encoding="utf-8")) == {
        "schema_version": SCHEMA_VERSION
    }
    assert json_repo.integrity_report.healthy


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


def test_process_concurrent_mutations_are_serialized(json_repo):
    deck = json_repo.create_deck("Processes")
    context = get_context("fork")
    processes = [
        context.Process(
            target=_add_cards_in_process,
            args=(json_repo.data_dir, deck.id, worker * 10, 10),
        )
        for worker in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    cards = json_repo.load_cards(deck.id).cards
    assert len(cards) == 40
    assert {card.front for card in cards} == {f"P{index}" for index in range(40)}


def test_legacy_storage_gets_manifest_and_one_time_backups(tmp_path):
    data_dir = tmp_path / "legacy"
    cards_dir = data_dir / "cards"
    cards_dir.mkdir(parents=True)
    decks = data_dir / "decks.json"
    card_file = cards_dir / "legacy.json"
    decks.write_text("[]", encoding="utf-8")
    card_file.write_text("[]", encoding="utf-8")

    repository = JsonRepository(data_dir)
    assert repository.manifest_file.exists()
    assert decks.with_suffix(".json.pre-schema-v1.bak").read_text() == "[]"
    assert card_file.with_suffix(".json.pre-schema-v1.bak").read_text() == "[]"

    before = decks.with_suffix(".json.pre-schema-v1.bak").stat().st_mtime_ns
    JsonRepository(data_dir)
    assert decks.with_suffix(".json.pre-schema-v1.bak").stat().st_mtime_ns == before


def test_integrity_scan_reports_without_repairing(json_repo):
    first = json_repo.create_deck("First")
    second = json_repo.create_deck("Second")
    card = Card(front="Q")
    json_repo.save_cards(first.id, CardDeck([card]))

    metadata = json.loads(json_repo.decks_file.read_text(encoding="utf-8"))
    for item in metadata:
        if item["id"] == first.id:
            item["card_ids"] = ["stale", "stale"]
    metadata.append(dict(metadata[0]))
    metadata.append({"id": "../unsafe"})
    json_repo.decks_file.write_text(json.dumps(metadata), encoding="utf-8")
    json_repo._cards_path(first.id).write_text(
        json.dumps([card.to_dict(), card.to_dict()]), encoding="utf-8"
    )
    json_repo._cards_path(second.id).unlink()
    (json_repo.cards_dir / "orphan.json").write_text("[]", encoding="utf-8")

    before = json_repo.decks_file.read_bytes()
    report = json_repo.scan_integrity()
    codes = {issue.code for issue in report.issues}
    assert {
        "duplicate-deck-id",
        "invalid-deck-id",
        "duplicate-card-id",
        "card-id-mismatch",
        "missing-cards-file",
        "orphan-cards-file",
    } <= codes
    assert not report.healthy
    assert report.to_dict()["schema_version"] == SCHEMA_VERSION
    assert json_repo.decks_file.read_bytes() == before


def test_integrity_scan_reports_cross_deck_ids_and_invalid_cards(json_repo):
    first = json_repo.create_deck("First")
    second = json_repo.create_deck("Second")
    card = Card(front="shared")
    payload = json.dumps([card.to_dict()])
    json_repo._cards_path(first.id).write_text(payload, encoding="utf-8")
    json_repo._cards_path(second.id).write_text(payload, encoding="utf-8")
    report = json_repo.scan_integrity()
    assert "cross-deck-card-id" in {issue.code for issue in report.issues}

    json_repo._cards_path(second.id).write_text("{}", encoding="utf-8")
    report = json_repo.scan_integrity()
    assert "invalid-cards" in {issue.code for issue in report.issues}


def test_unsupported_schema_blocks_operations_and_is_reported(json_repo):
    json_repo.manifest_file.write_text(
        json.dumps({"schema_version": SCHEMA_VERSION + 1}), encoding="utf-8"
    )
    report = json_repo.scan_integrity()
    assert not report.healthy
    assert report.issues[0].code == "schema"
    with pytest.raises(UnsupportedSchemaError):
        json_repo.list_decks()


@pytest.mark.parametrize("manifest", [[], {}, {"schema_version": "one"}])
def test_invalid_schema_manifest_is_reported(json_repo, manifest):
    json_repo.manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    report = json_repo.scan_integrity()
    assert not report.healthy
    assert report.issues[0].code == "schema"


def test_integrity_scan_reports_corrupt_deck_index(json_repo):
    json_repo.decks_file.write_text("{broken", encoding="utf-8")
    report = json_repo.scan_integrity()
    assert [issue.code for issue in report.issues] == ["decks-json"]


def test_integrity_scan_reports_invalid_deck_timestamp(json_repo):
    json_repo.decks_file.write_text(
        json.dumps([{"id": "valid-id", "updated_at": "invalid"}]),
        encoding="utf-8",
    )
    assert "invalid-deck" in {
        issue.code for issue in json_repo.scan_integrity().issues
    }


def test_recovery_restores_backup_and_preserves_broken_file(json_repo):
    deck = json_repo.create_deck("Recovery")
    json_repo.save_cards(deck.id, CardDeck([Card(front="new")]))
    cards_path = json_repo._cards_path(deck.id)
    cards_path.write_text("{broken", encoding="utf-8")

    broken_path = json_repo.recover_from_backup(cards_path.relative_to(json_repo.data_dir))
    assert broken_path.exists()
    assert broken_path.read_text(encoding="utf-8") == "{broken"
    assert json.loads(cards_path.read_text(encoding="utf-8")) == []

    json_repo.load_cards(deck.id)
    assert json_repo.scan_integrity().healthy


def test_recovery_rejects_unsafe_target_and_missing_backup(json_repo):
    with pytest.raises(ValueError, match="repository JSON"):
        json_repo.recover_from_backup("../outside.json")
    with pytest.raises(FileNotFoundError, match="Backup does not exist"):
        json_repo.recover_from_backup("cards/missing.json")


def test_recovery_can_restore_missing_target_and_manifest(json_repo):
    missing = json_repo.cards_dir / "missing.json"
    missing.with_suffix(".json.bak").write_text("[]", encoding="utf-8")
    assert json_repo.recover_from_backup(missing) == missing
    assert json.loads(missing.read_text(encoding="utf-8")) == []

    json_repo._write_json(
        json_repo.manifest_file, {"schema_version": SCHEMA_VERSION}
    )
    json_repo.manifest_file.write_text("{broken", encoding="utf-8")
    broken = json_repo.recover_from_backup("repository.json")
    assert broken.exists()
    assert json.loads(json_repo.manifest_file.read_text()) == {
        "schema_version": SCHEMA_VERSION
    }


def test_recovery_validates_backup_shape(json_repo):
    deck = json_repo.create_deck("Backup shape")
    cards_path = json_repo._cards_path(deck.id)
    cards_path.with_suffix(".json.bak").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="expected a list"):
        json_repo.recover_from_backup(cards_path)

    json_repo.manifest_file.with_suffix(".json.bak").write_text(
        "[]", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="schema manifest"):
        json_repo.recover_from_backup(json_repo.manifest_file)


def test_failed_recovery_restores_broken_live_file(json_repo, monkeypatch):
    deck = json_repo.create_deck("Failed recovery")
    cards_path = json_repo._cards_path(deck.id)
    json_repo.save_cards(deck.id, CardDeck([Card(front="Q")]))
    cards_path.write_text("{broken", encoding="utf-8")

    def fail_write(*_args):
        raise OSError("recovery write failed")

    monkeypatch.setattr(json_repo, "_write_json", fail_write)
    with pytest.raises(OSError, match="recovery write failed"):
        json_repo.recover_from_backup(cards_path)
    assert cards_path.read_text(encoding="utf-8") == "{broken"


def test_one_time_copy_cleans_temporary_file_on_replace_failure(
    json_repo, tmp_path, monkeypatch
):
    source = tmp_path / "source.json"
    destination = tmp_path / "destination.bak"
    source.write_text("[]", encoding="utf-8")

    def fail_replace(*_args):
        raise OSError("replace failed")

    monkeypatch.setattr(
        "didactic_cards.adapters.json_repository.os.replace", fail_replace
    )
    with pytest.raises(OSError, match="replace failed"):
        json_repo._copy_once(source, destination)
    assert list(tmp_path.glob(".destination.bak.*.tmp")) == []

    destination.write_text("keep", encoding="utf-8")
    json_repo._copy_once(source, destination)
    assert destination.read_text(encoding="utf-8") == "keep"
