from __future__ import annotations

import csv
import io
import json

import pytest

from didactic_cards.domain.entities import Card, CardDeck
from didactic_cards.use_cases.deck_transfer import (
    DECK_EXPORT_SCHEMA_VERSION,
    DeckTransferError,
    export_deck_csv,
    export_deck_json,
    import_deck_json,
)


def test_versioned_json_round_trip_creates_safe_copy_with_lineage(repo):
    source = repo.create_deck('География', 'Столицы')
    original = Card(front='Франция', back='Париж')
    repo.save_cards(source.id, CardDeck([original]))

    exported = export_deck_json(repo, source.id)
    payload = json.loads(exported)
    assert payload['schema_version'] == DECK_EXPORT_SCHEMA_VERSION
    assert payload['deck']['id'] == source.id
    assert payload['cards'][0]['id'] == original.id

    imported = import_deck_json(repo, exported, max_cards=10)
    imported_card = repo.load_cards(imported.id).cards[0]
    assert imported.id != source.id
    assert imported.name == 'География (импорт)'
    assert imported.parent_id == source.id
    assert imported_card.id != original.id
    assert imported_card.parent_id == original.id
    assert imported_card.back == 'Париж'


def test_csv_export_is_bom_semicolon_and_quote_safe(repo):
    deck = repo.create_deck('CSV')
    repo.save_cards(deck.id, CardDeck([Card(front='A;B', back='line\nvalue')]))
    exported = export_deck_csv(repo, deck.id)
    assert exported.startswith(b'\xef\xbb\xbf')
    rows = list(csv.reader(io.StringIO(exported.decode('utf-8-sig')), delimiter=';'))
    assert rows == [['front', 'back'], ['A;B', 'line\nvalue']]


def test_export_rejects_unknown_deck(repo):
    with pytest.raises(KeyError):
        export_deck_json(repo, 'missing')
    with pytest.raises(KeyError):
        export_deck_csv(repo, 'missing')


@pytest.mark.parametrize(
    ('payload', 'message'),
    [
        (b'{broken', 'JSON'),
        (b'[]', 'Корень'),
        (b'{"schema_version": 99}', 'верс'),
        (b'{"schema_version": 1}', 'deck и cards'),
        (
            json.dumps({
                'schema_version': 1,
                'deck': {'name': ''},
                'cards': [],
            }).encode(),
            'Название',
        ),
        (
            json.dumps({
                'schema_version': 1,
                'deck': {'name': 'Deck', 'description': 7},
                'cards': [],
            }).encode(),
            'Описание',
        ),
        (
            json.dumps({
                'schema_version': 1,
                'deck': {'id': 7, 'name': 'Deck'},
                'cards': [],
            }).encode(),
            'ID исходной',
        ),
        (
            json.dumps({
                'schema_version': 1,
                'deck': {'name': 'Deck'},
                'cards': ['bad'],
            }).encode(),
            'Карточка 1',
        ),
        (
            json.dumps({
                'schema_version': 1,
                'deck': {'name': 'Deck'},
                'cards': [{'front': 7}],
            }).encode(),
            'Стороны',
        ),
        (
            json.dumps({
                'schema_version': 1,
                'deck': {'name': 'Deck'},
                'cards': [{'id': 7, 'front': 'Q'}],
            }).encode(),
            'ID карточки',
        ),
    ],
)
def test_import_rejects_malformed_exports_without_creating_deck(repo, payload, message):
    with pytest.raises(DeckTransferError, match=message):
        import_deck_json(repo, payload)
    assert repo.list_decks() == []


def test_import_enforces_quota_and_duplicate_source_ids(repo):
    payload = {
        'schema_version': 1,
        'deck': {'id': 'source', 'name': 'Deck'},
        'cards': [
            {'id': 'same', 'front': 'A'},
            {'id': 'same', 'front': 'B'},
        ],
    }
    encoded = json.dumps(payload).encode()
    with pytest.raises(DeckTransferError, match='Максимум'):
        import_deck_json(repo, encoded, max_cards=1)
    with pytest.raises(DeckTransferError, match='Повтор ID'):
        import_deck_json(repo, encoded, max_cards=2)
    assert repo.list_decks() == []
