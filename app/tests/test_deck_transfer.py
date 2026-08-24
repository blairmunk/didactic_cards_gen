from __future__ import annotations

import csv
import io
import json

import pytest

from didactic_cards.adapters.sqlite_repository import SqliteRepository
from didactic_cards.domain.entities import Card, CardDeck
from didactic_cards.domain.rendering import AuthoringMode, DeckRenderSettings
from didactic_cards.domain.trusted import TemplateStatus, TrustedTemplateVersion
from didactic_cards.use_cases.card_use_cases import ImportCsv
from didactic_cards.use_cases.deck_transfer import (
    DECK_EXPORT_SCHEMA_VERSION,
    DeckTransferError,
    export_deck_csv,
    export_deck_json,
    import_deck_json,
)


def _payload(*, name='Deck', cards=None, templates=None, settings=None):
    return {
        'schema_version': DECK_EXPORT_SCHEMA_VERSION,
        'deck': {
            'id': 'source-deck',
            'name': name,
            'description': '',
            'render_settings': (
                settings or DeckRenderSettings.centered()
            ).to_dict(),
        },
        'cards': cards if cards is not None else [],
        'trusted_templates': templates if templates is not None else [],
    }


def test_versioned_json_round_trip_creates_safe_copy_with_lineage(repo):
    source = repo.create_deck('География', 'Столицы')
    original = Card(
        front='Франция\r\nСтолица?\n\nНазовите город',
        back='\nПариж\n',
        section='Европа',
    )
    repo.save_cards(source.id, CardDeck([original]))
    settings = DeckRenderSettings(
        preset='custom',
        horizontal_alignment='right',
        header_visibility='both',
        header_repeat='section-start',
        section_break='new-row',
        typography_profile='book',
        secondary_header_visibility='both',
        secondary_header_source='card-number',
    )
    repo.save_render_settings(source.id, settings)

    exported = export_deck_json(repo, source.id)
    payload = json.loads(exported)
    assert payload['schema_version'] == DECK_EXPORT_SCHEMA_VERSION
    assert payload['deck']['id'] == source.id
    assert payload['cards'][0]['id'] == original.id
    assert payload['trusted_templates'] == []

    imported = import_deck_json(repo, exported, max_cards=10)
    imported_card = repo.load_cards(imported.id).cards[0]
    assert imported.id != source.id
    assert imported.parent_id == source.id
    assert imported_card.parent_id == original.id
    assert imported_card.section == 'Европа'
    assert imported_card.front == original.front
    assert imported_card.back == original.back
    assert repo.get_render_settings(imported.id) == settings


def test_advanced_json_round_trip_preserves_mixed_newlines_in_all_raw_fields(
    repo
):
    source = repo.create_deck(
        'Advanced mixed EOL',
        render_settings=DeckRenderSettings(authoring_mode='advanced'),
    )
    expected = Card(
        section=' Section\r\nlabel ',
        front=' \\vfill\r\nFront\rraw\n ',
        back='Back\n\nraw\r',
        upper_header='Top\r\n{{ card_number }}',
        lower_header='Bottom\r{{ card_count }}\n',
    )
    repo.save_cards(source.id, CardDeck([expected]))

    imported = import_deck_json(repo, export_deck_json(repo, source.id))
    actual = repo.load_cards(imported.id).cards[0]

    assert (
        actual.section, actual.front, actual.back,
        actual.upper_header, actual.lower_header,
    ) == (
        expected.section, expected.front, expected.back,
        expected.upper_header, expected.lower_header,
    )


def test_trusted_export_import_preserves_wrappers_but_never_approval(tmp_path):
    repo = SqliteRepository(tmp_path / 'transfer')
    source = repo.create_deck(
        'Trusted export',
        render_settings=DeckRenderSettings(authoring_mode='advanced'),
    )
    repo.save_cards(source.id, CardDeck([Card(
        front='Q',
        back='A',
        upper_header=r'Верх {{ card_number }}',
        lower_header=r'Низ {{ card_count }}',
    )]))
    template = repo.quarantine_trusted_template(
        source.id,
        r'FRONT {{ upper_header }}\vfill {{ content }}\vfill{{ lower_header }}',
        r'BACK {{ upper_header }}\vfill {{ content }}\vfill{{ lower_header }}',
    )
    repo.approve_trusted_template(source.id, template.id)

    payload = json.loads(export_deck_json(repo, source.id))
    exported_template = payload['trusted_templates'][0]
    assert set(exported_template) == {
        'id', 'version', 'front_source', 'back_source', 'source_hash',
        'source_provenance',
    }
    assert 'status' not in exported_template
    assert 'approved_at' not in exported_template

    imported = import_deck_json(repo, json.dumps(payload).encode())
    history = repo.list_trusted_templates(imported.id)
    assert len(history) == 1
    assert history[0].front_source == template.front_source
    assert history[0].back_source == template.back_source
    assert history[0].provenance.value == 'imported'
    assert history[0].origin_template_id == template.id
    assert history[0].status is TemplateStatus.QUARANTINED
    assert repo.get_approved_trusted_template(imported.id) is None


def test_safe_export_never_carries_stale_trusted_history(tmp_path):
    repo = SqliteRepository(tmp_path / 'safe-transfer')
    source = repo.create_deck('Safe')
    repo.quarantine_trusted_template(source.id, '{{ content }}')
    payload = json.loads(export_deck_json(repo, source.id))
    assert payload['trusted_templates'] == []


def test_invalid_trusted_hash_is_rejected_before_any_write(tmp_path):
    repo = SqliteRepository(tmp_path / 'invalid-transfer')
    settings = DeckRenderSettings(authoring_mode='advanced')
    template = TrustedTemplateVersion(
        deck_id='source',
        version=1,
        front_source='{{ content }}',
        back_source='{{ content }}',
    )
    payload = _payload(
        settings=settings,
        templates=[{
            'id': template.id,
            'version': template.version,
            'front_source': template.front_source,
            'back_source': template.back_source,
            'source_hash': '0' * 64,
            'source_provenance': 'local-author',
        }],
    )

    with pytest.raises(DeckTransferError, match='hash mismatch'):
        import_deck_json(repo, json.dumps(payload).encode())
    assert repo.list_decks() == []


@pytest.mark.parametrize(
    ('mutation', 'message'),
    [
        (lambda payload: payload.pop('trusted_templates'), 'должен содержать'),
        (lambda payload: payload['trusted_templates'].__setitem__(0, []), 'поля'),
        (lambda payload: payload['trusted_templates'][0].update(id=''), 'ID'),
        (
            lambda payload: payload['trusted_templates'].append(
                dict(payload['trusted_templates'][0])
            ),
            'Повтор',
        ),
    ],
)
def test_current_schema_rejects_malformed_trusted_contract_atomically(
    tmp_path, mutation, message
):
    repo = SqliteRepository(tmp_path / 'malformed')
    template = TrustedTemplateVersion(
        deck_id='source',
        version=1,
        front_source='{{ content }}',
        back_source='{{ content }}',
    )
    payload = _payload(
        settings=DeckRenderSettings(authoring_mode='advanced'),
        templates=[{
            'id': template.id,
            'version': template.version,
            'front_source': template.front_source,
            'back_source': template.back_source,
            'source_hash': template.source_hash,
            'source_provenance': 'local-author',
        }],
    )
    mutation(payload)
    with pytest.raises(DeckTransferError, match=message):
        import_deck_json(repo, json.dumps(payload).encode())
    assert repo.list_decks() == []


def test_csv_export_is_bom_semicolon_and_quote_safe(repo):
    deck = repo.create_deck('CSV')
    repo.save_cards(deck.id, CardDeck([
        Card(
            section='Тема',
            front='A;B\r\nline\rvalue',
            back='line\n\nvalue',
        )
    ]))
    exported = export_deck_csv(repo, deck.id)
    assert exported.startswith(b'\xef\xbb\xbf')
    rows = list(csv.reader(
        io.StringIO(exported.decode('utf-8-sig'), newline=''),
        delimiter=';',
        strict=True,
    ))
    assert rows == [
        ['section', 'front', 'back'],
        ['Тема', 'A;B\r\nline\rvalue', 'line\n\nvalue'],
    ]


def test_advanced_csv_export_round_trips_all_raw_fields(repo):
    deck = repo.create_deck(
        'Advanced CSV',
        render_settings=DeckRenderSettings(authoring_mode=AuthoringMode.ADVANCED),
    )
    expected = Card(
        section=' Raw ',
        front='  \\vfill\r\nFront;value, pair\rRaw  ',
        back='Back "quoted"\n\nline',
        upper_header='{{ card_number }}\r\nTop',
        lower_header='Foot\\line\rBottom',
    )
    repo.save_cards(deck.id, CardDeck([expected]))

    exported = export_deck_csv(repo, deck.id)
    target = repo.create_deck(
        'Target',
        render_settings=DeckRenderSettings(authoring_mode=AuthoringMode.ADVANCED),
    )
    assert ImportCsv(repo).execute(target.id, exported) == 1
    imported = repo.load_cards(target.id).cards[0]
    assert (
        imported.section, imported.front, imported.back,
        imported.upper_header, imported.lower_header,
    ) == (
        expected.section, expected.front, expected.back,
        expected.upper_header, expected.lower_header,
    )


def test_export_rejects_unknown_deck(repo):
    with pytest.raises(KeyError):
        export_deck_json(repo, 'missing')
    with pytest.raises(KeyError):
        export_deck_csv(repo, 'missing')


@pytest.mark.parametrize(
    ('source', 'message'),
    [
        (b'{broken', 'JSON'),
        (b'[]', 'Корень'),
        (b'{"schema_version": 7}', 'верс'),
        (json.dumps({'schema_version': 8}).encode(), 'deck и cards'),
        (
            json.dumps(_payload(name='')).encode(),
            'Название',
        ),
        (
            json.dumps({
                **_payload(),
                'deck': {**_payload()['deck'], 'description': 7},
            }).encode(),
            'Описание',
        ),
        (
            json.dumps({
                **_payload(),
                'deck': {**_payload()['deck'], 'id': 7},
            }).encode(),
            'ID исходной',
        ),
        (
            json.dumps({
                **_payload(),
                'deck': {
                    key: value for key, value in _payload()['deck'].items()
                    if key != 'render_settings'
                },
            }).encode(),
            'не содержит настройки',
        ),
        (
            json.dumps({
                **_payload(),
                'deck': {
                    **_payload()['deck'],
                    'render_settings': {'preset': 'dangerous'},
                },
            }).encode(),
            'настройки оформления',
        ),
        (
            json.dumps(_payload(cards=['bad'])).encode(),
            'Карточка 1',
        ),
        (
            json.dumps(_payload(cards=[{'front': 7, 'back': ''}])).encode(),
            'Стороны',
        ),
        (
            json.dumps(_payload(cards=[{
                'front': 'Q', 'back': 'A', 'section': 7
            }])).encode(),
            'Секция',
        ),
        (
            json.dumps(_payload(cards=[{
                'front': 'Q', 'back': 'A', 'upper_header': 7
            }])).encode(),
            'Колонтитулы',
        ),
        (
            json.dumps(_payload(cards=[{
                'id': 7, 'front': 'Q', 'back': 'A'
            }])).encode(),
            'ID карточки',
        ),
    ],
)
def test_import_rejects_malformed_current_exports_without_writing(
    repo, source, message
):
    before = len(repo.list_decks())
    with pytest.raises(DeckTransferError, match=message):
        import_deck_json(repo, source)
    assert len(repo.list_decks()) == before


def test_import_enforces_quota_and_duplicate_source_ids(repo):
    oversized = _payload(cards=[
        {'id': 'one', 'front': 'Q1', 'back': 'A1'},
        {'id': 'two', 'front': 'Q2', 'back': 'A2'},
    ])
    with pytest.raises(DeckTransferError, match='Максимум'):
        import_deck_json(repo, json.dumps(oversized).encode(), max_cards=1)

    duplicate = _payload(cards=[
        {'id': 'same', 'front': 'Q1', 'back': 'A1'},
        {'id': 'same', 'front': 'Q2', 'back': 'A2'},
    ])
    with pytest.raises(DeckTransferError, match='Повтор ID'):
        import_deck_json(repo, json.dumps(duplicate).encode())


def test_trusted_import_requires_atomic_repository_capability():
    template = TrustedTemplateVersion(
        deck_id='source', version=1,
        front_source='{{ content }}', back_source='{{ content }}',
    )
    payload = _payload(
        settings=DeckRenderSettings(authoring_mode='advanced'),
        templates=[{
            'id': template.id,
            'version': template.version,
            'front_source': template.front_source,
            'back_source': template.back_source,
            'source_hash': template.source_hash,
            'source_provenance': 'local-author',
        }],
    )
    with pytest.raises(DeckTransferError, match='не поддерживает'):
        import_deck_json(object(), json.dumps(payload).encode())
