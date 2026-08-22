from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from multiprocessing import get_context

import pytest

from didactic_cards.adapters.json_repository import DeckNotFoundError, JsonRepository
from didactic_cards.adapters.sqlite_repository import (
    LegacyMigrationError,
    SQLITE_SCHEMA_VERSION,
    SqliteRepository,
    UnsupportedSqliteSchemaError,
)
from didactic_cards.domain.entities import Card, CardDeck
from didactic_cards.use_cases.card_use_cases import AddCard


@pytest.fixture
def sqlite_repo(tmp_path):
    return SqliteRepository(tmp_path / 'data')


def _sqlite_add_cards(data_dir, deck_id, start, count):
    repository = SqliteRepository(data_dir)
    for index in range(start, start + count):
        AddCard(repository).execute(deck_id, f'P{index}', f'A{index}')


def test_database_initializes_wal_schema_and_foreign_keys(sqlite_repo):
    with closing(sqlite_repo._connect()) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == SQLITE_SCHEMA_VERSION
        assert connection.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        assert connection.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {'repository_meta', 'decks', 'cards', 'deck_cards'} <= tables
    assert sqlite_repo.integrity_check() == []


def test_deck_and_ordered_card_round_trip(sqlite_repo):
    first = sqlite_repo.create_deck('First', 'Description')
    second = sqlite_repo.create_deck('Second')
    cards = CardDeck([Card(front='A'), Card(front='B')])
    sqlite_repo.save_cards(first.id, cards)

    loaded = SqliteRepository(sqlite_repo.data_dir)
    assert [deck.id for deck in loaded.list_decks()][0] == first.id
    assert loaded.get_deck(first.id).card_ids == [card.id for card in cards.cards]
    assert [card.front for card in loaded.load_cards(first.id).cards] == ['A', 'B']

    updated = loaded.update_deck(second.id, 'Second+', 'Changed')
    assert updated.name == 'Second+'
    assert loaded.update_deck('missing', 'No') is None


def test_clone_lineage_delete_and_legacy_aliases(sqlite_repo):
    source = sqlite_repo.create_deck('Source')
    original = Card(front='Q', back='A')
    sqlite_repo.save(CardDeck([original]), source.id)

    clone = sqlite_repo.clone_deck(source.id)
    clone_card = sqlite_repo.load(clone.id).cards[0]
    assert clone.parent_id == source.id
    assert clone_card.id != original.id
    assert clone_card.parent_id == original.id
    assert sqlite_repo.clone_deck('missing') is None

    assert sqlite_repo.delete_deck(source.id) is True
    assert sqlite_repo.delete_deck(source.id) is False
    assert sqlite_repo.get_deck(source.id) is None
    assert sqlite_repo.load_cards(clone.id).cards[0].front == 'Q'


def test_unknown_deck_duplicate_and_cross_deck_cards_are_rejected(sqlite_repo):
    with pytest.raises(DeckNotFoundError):
        sqlite_repo.load_cards('missing')
    with pytest.raises(DeckNotFoundError):
        sqlite_repo.save_cards('missing', CardDeck())

    first = sqlite_repo.create_deck('First')
    second = sqlite_repo.create_deck('Second')
    card = Card(front='Q')
    with pytest.raises(ValueError, match='Duplicate card IDs'):
        sqlite_repo.save_cards(first.id, CardDeck([card, card]))
    sqlite_repo.save_cards(first.id, CardDeck([card]))
    with pytest.raises(ValueError, match='another deck'):
        sqlite_repo.save_cards(second.id, CardDeck([card]))
    assert len(sqlite_repo.load_cards(second.id)) == 0


def test_failed_mutation_rolls_back(sqlite_repo):
    deck = sqlite_repo.create_deck('Rollback')
    sqlite_repo.save_cards(deck.id, CardDeck([Card(front='before')]))

    def fail(card_deck):
        card_deck.add(Card(front='not committed'))
        raise RuntimeError('stop')

    with pytest.raises(RuntimeError, match='stop'):
        sqlite_repo.mutate_cards(deck.id, fail)
    assert [card.front for card in sqlite_repo.load_cards(deck.id).cards] == ['before']


def test_noop_mutation_does_not_write(sqlite_repo):
    deck = sqlite_repo.create_deck('Noop')
    before = sqlite_repo.get_deck(deck.id).updated_at
    result = sqlite_repo.mutate_cards(deck.id, lambda cards: ('same', False))
    assert result == 'same'
    assert sqlite_repo.get_deck(deck.id).updated_at == before


def test_threaded_mutations_do_not_lose_updates(sqlite_repo):
    deck = sqlite_repo.create_deck('Threads')

    def add(index):
        AddCard(sqlite_repo).execute(deck.id, f'Q{index}', '')

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(add, range(40)))
    assert {card.front for card in sqlite_repo.load_cards(deck.id).cards} == {
        f'Q{index}' for index in range(40)
    }


def test_process_mutations_do_not_lose_updates(sqlite_repo):
    deck = sqlite_repo.create_deck('Processes')
    context = get_context('fork')
    processes = [
        context.Process(
            target=_sqlite_add_cards,
            args=(sqlite_repo.data_dir, deck.id, worker * 10, 10),
        )
        for worker in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    assert len(sqlite_repo.load_cards(deck.id)) == 40


def test_legacy_json_is_migrated_once_with_backup(tmp_path):
    data_dir = tmp_path / 'legacy'
    legacy = JsonRepository(data_dir)
    deck = legacy.create_deck('Legacy', 'Imported')
    original = Card(front='Old Q', back='Old A')
    legacy.save_cards(deck.id, CardDeck([original]))

    repository = SqliteRepository(data_dir)
    imported = repository.get_deck(deck.id)
    assert imported.name == 'Legacy'
    assert repository.load_cards(deck.id).cards[0].id == original.id
    assert (repository.legacy_backup_dir / 'decks.json').exists()
    assert (repository.legacy_backup_dir / 'cards' / f'{deck.id}.json').exists()

    late_deck = legacy.create_deck('Must not import twice')
    reopened = SqliteRepository(data_dir)
    assert reopened.get_deck(late_deck.id) is None
    with closing(reopened._connect()) as connection:
        assert reopened._meta(connection, 'legacy_json_migrated_at')


def test_stale_legacy_card_ids_are_safely_derived_without_source_mutation(tmp_path):
    data_dir = tmp_path / 'stale-legacy'
    legacy = JsonRepository(data_dir)
    deck = legacy.create_deck('Stale metadata')
    card = Card(front='Canonical card file')
    legacy.save_cards(deck.id, CardDeck([card]))
    metadata = legacy.decks_file.read_text(encoding='utf-8')
    legacy.decks_file.write_text(
        metadata.replace(card.id, 'stale-id'), encoding='utf-8'
    )
    before = legacy.decks_file.read_bytes()

    repository = SqliteRepository(data_dir)
    assert repository.get_deck(deck.id).card_ids == [card.id]
    assert legacy.decks_file.read_bytes() == before
    assert (repository.legacy_backup_dir / 'decks.json').read_bytes() == before
    with closing(repository._connect()) as connection:
        assert repository._meta(
            connection, 'legacy_json_migration_warnings'
        ) == 'card-id-mismatch'


def test_corrupt_legacy_json_aborts_import(tmp_path):
    data_dir = tmp_path / 'corrupt'
    data_dir.mkdir()
    (data_dir / 'decks.json').write_text('{broken', encoding='utf-8')
    with pytest.raises(LegacyMigrationError, match='integrity check failed'):
        SqliteRepository(data_dir)

    database = sqlite3.connect(data_dir / 'cards.sqlite3')
    try:
        assert database.execute('SELECT COUNT(*) FROM decks').fetchone()[0] == 0
    finally:
        database.close()


def test_newer_sqlite_schema_is_not_downgraded(tmp_path):
    data_dir = tmp_path / 'future'
    data_dir.mkdir()
    database_path = data_dir / 'cards.sqlite3'
    database = sqlite3.connect(database_path)
    database.execute(f'PRAGMA user_version = {SQLITE_SCHEMA_VERSION + 1}')
    database.close()

    with pytest.raises(UnsupportedSqliteSchemaError, match='Unsupported SQLite schema'):
        SqliteRepository(data_dir)
    database = sqlite3.connect(database_path)
    try:
        assert database.execute('PRAGMA user_version').fetchone()[0] == (
            SQLITE_SCHEMA_VERSION + 1
        )
    finally:
        database.close()
