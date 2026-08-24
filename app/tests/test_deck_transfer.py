from __future__ import annotations

import csv
import io
import json
import hashlib

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


def test_versioned_json_round_trip_creates_safe_copy_with_lineage(repo):
    source = repo.create_deck('География', 'Столицы')
    original = Card(front='Франция', back='Париж', section='Европа')
    repo.save_cards(source.id, CardDeck([original]))
    repo.save_render_settings(
        source.id,
        DeckRenderSettings(
            preset='custom',
            horizontal_alignment='right',
            header_visibility='both',
            header_repeat='section-start',
            section_break='new-row',
            typography_profile='book',
            secondary_header_visibility='both',
            secondary_header_source='card-number',
        ),
    )

    exported = export_deck_json(repo, source.id)
    payload = json.loads(exported)
    assert payload['schema_version'] == DECK_EXPORT_SCHEMA_VERSION
    assert payload['deck']['id'] == source.id
    assert payload['cards'][0]['id'] == original.id
    assert payload['cards'][0]['section'] == 'Европа'
    assert payload['deck']['render_settings']['preset'] == 'custom'
    assert payload['trusted_templates'] == []

    imported = import_deck_json(repo, exported, max_cards=10)
    imported_card = repo.load_cards(imported.id).cards[0]
    assert imported.id != source.id
    assert imported.name == 'География (импорт)'
    assert imported.parent_id == source.id
    assert imported_card.id != original.id
    assert imported_card.parent_id == original.id
    assert imported_card.back == 'Париж'
    assert imported_card.section == 'Европа'
    assert repo.get_render_settings(imported.id).horizontal_alignment.value == 'right'
    assert repo.get_render_settings(imported.id).header_visibility.value == 'both'
    assert repo.get_render_settings(imported.id).header_repeat.value == 'section-start'
    assert repo.get_render_settings(imported.id).section_break.value == 'new-row'
    assert repo.get_render_settings(imported.id).typography_profile.value == 'book'
    assert (
        repo.get_render_settings(imported.id).secondary_header_visibility.value
        == 'both'
    )


def test_trusted_export_import_preserves_source_but_never_approval(tmp_path):
    repo = SqliteRepository(tmp_path / 'sqlite-transfer')
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
        front_content_mode='escaped',
        back_content_mode='raw',
    )
    repo.approve_trusted_template(source.id, template.id)

    exported = export_deck_json(repo, source.id)
    payload = json.loads(exported)
    exported_template = payload['trusted_templates'][0]
    assert exported_template['front_source'] == template.front_source
    assert exported_template['back_source'] == template.back_source
    assert exported_template['source_provenance'] == 'local-author'
    assert 'status' not in exported_template
    assert 'approved_at' not in exported_template

    imported = import_deck_json(repo, exported)
    imported_history = repo.list_trusted_templates(imported.id)

    assert len(imported_history) == 1
    assert imported_history[0].front_source == template.front_source
    assert imported_history[0].back_source == template.back_source
    imported_card = repo.load_cards(imported.id).cards[0]
    assert imported_card.upper_header == r'Верх {{ card_number }}'
    assert imported_card.lower_header == r'Низ {{ card_count }}'
    assert imported_history[0].provenance.value == 'imported'
    assert imported_history[0].origin_template_id == template.id
    assert imported_history[0].status is TemplateStatus.QUARANTINED
    assert imported_history[0].back_content_mode.value == 'raw'
    assert repo.get_approved_trusted_template(imported.id) is None
    assert (
        repo.get_render_settings(imported.id).authoring_mode.value
        == 'advanced'
    )


def test_safe_export_never_carries_stale_trusted_history(tmp_path):
    repo = SqliteRepository(tmp_path / 'safe-transfer')
    source = repo.create_deck('Safe')
    repo.quarantine_trusted_template(source.id, '{{ content }}')

    payload = json.loads(export_deck_json(repo, source.id))

    assert payload['deck']['render_settings']['authoring_mode'] == 'safe'
    assert payload['trusted_templates'] == []


def test_schema_six_trusted_import_migrates_shared_wrapper(tmp_path):
    repo = SqliteRepository(tmp_path / 'schema-six-transfer')
    source = repo.create_deck(
        'Old advanced',
        render_settings=DeckRenderSettings(authoring_mode='advanced'),
    )
    repo.quarantine_trusted_template(source.id, '{{ content }}')
    payload = json.loads(export_deck_json(repo, source.id))
    payload['schema_version'] = 6
    item = payload['trusted_templates'][0]
    legacy_source = item.pop('front_source')
    item.pop('back_source')
    item['source'] = legacy_source
    item['source_hash'] = hashlib.sha256(legacy_source.encode()).hexdigest()

    imported = import_deck_json(repo, json.dumps(payload).encode())
    template = repo.list_trusted_templates(imported.id)[0]

    assert template.front_source == '{{ content }}'
    assert template.back_source == '{{ content }}'


def test_invalid_trusted_import_is_rejected_before_any_write(tmp_path):
    repo = SqliteRepository(tmp_path / 'invalid-trusted-transfer')
    payload = {
        'schema_version': DECK_EXPORT_SCHEMA_VERSION,
        'deck': {'name': 'Untrusted'},
        'cards': [],
        'trusted_templates': [{
            'id': 'source-template',
            'version': 1,
            'front_source': '{{ content }}',
            'back_source': '{{ content }}',
            'source_hash': '0' * 64,
            'source_provenance': 'local-author',
            'front_content_mode': 'escaped',
            'back_content_mode': 'raw',
        }],
    }

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
def test_schema_four_rejects_malformed_trusted_contract_before_write(
    tmp_path, mutation, message
):
    repo = SqliteRepository(tmp_path / message)
    template = TrustedTemplateVersion(
        deck_id='source', version=1, front_source='{{ content }}', back_source='{{ content }}'
    )
    payload = {
        'schema_version': DECK_EXPORT_SCHEMA_VERSION,
        'deck': {'name': 'Deck'},
        'cards': [],
        'trusted_templates': [{
            'id': template.id,
            'version': 1,
            'front_source': template.front_source,
            'back_source': template.back_source,
            'source_hash': template.source_hash,
            'source_provenance': 'local-author',
            'front_content_mode': 'escaped',
            'back_content_mode': 'escaped',
        }],
    }
    mutation(payload)

    with pytest.raises(DeckTransferError, match=message):
        import_deck_json(repo, json.dumps(payload).encode())
    assert repo.list_decks() == []


def test_trusted_import_requires_atomic_capable_repository(repo):
    template = TrustedTemplateVersion(
        deck_id='source', version=1, front_source='{{ content }}', back_source='{{ content }}'
    )
    payload = {
        'schema_version': DECK_EXPORT_SCHEMA_VERSION,
        'deck': {'name': 'Deck'},
        'cards': [],
        'trusted_templates': [{
            'id': template.id,
            'version': 1,
            'front_source': template.front_source,
            'back_source': template.back_source,
            'source_hash': template.source_hash,
            'source_provenance': 'local-author',
            'front_content_mode': 'escaped',
            'back_content_mode': 'escaped',
        }],
    }

    with pytest.raises(DeckTransferError, match='не поддерживает'):
        import_deck_json(repo, json.dumps(payload).encode())
    assert repo.list_decks() == []


def test_schema_two_import_gets_backward_compatible_section_layout_defaults(repo):
    payload = {
        'schema_version': 2,
        'deck': {
            'name': 'Previous export',
            'render_settings': {
                'preset': 'centered',
                'horizontal_alignment': 'center',
                'vertical_alignment': 'center',
                'header_visibility': 'front',
                'header_position': 'top',
                'header_alignment': 'left',
            },
        },
        'cards': [{'front': 'Q', 'back': 'A', 'section': 'One'}],
    }

    imported = import_deck_json(repo, json.dumps(payload).encode())

    settings = repo.get_render_settings(imported.id)
    assert settings.header_repeat.value == 'every-card'
    assert settings.section_break.value == 'continuous'


def test_csv_export_is_bom_semicolon_and_quote_safe(repo):
    deck = repo.create_deck('CSV')
    repo.save_cards(deck.id, CardDeck([
        Card(section='Тема', front='A;B', back='line\nvalue')
    ]))
    exported = export_deck_csv(repo, deck.id)
    assert exported.startswith(b'\xef\xbb\xbf')
    rows = list(csv.reader(io.StringIO(exported.decode('utf-8-sig')), delimiter=';'))
    assert rows == [
        ['section', 'front', 'back'],
        ['Тема', 'A;B', 'line\nvalue'],
    ]


def test_advanced_csv_export_contains_headers_and_round_trips(repo):
    deck = repo.create_deck(
        'Advanced CSV',
        render_settings=DeckRenderSettings(authoring_mode=AuthoringMode.ADVANCED),
    )
    repo.save_cards(deck.id, CardDeck([
        Card(
            section=' Raw ',
            front='  \\vfill\nFront;value  ',
            back='Back "quoted"',
            upper_header='{{ card_number }}',
            lower_header=r'Foot\\line',
        )
    ]))

    exported = export_deck_csv(repo, deck.id)
    rows = list(csv.reader(
        io.StringIO(exported.decode('utf-8-sig'), newline=''),
        delimiter=';',
        strict=True,
    ))

    assert rows == [
        ['section', 'front', 'back', 'upper_header', 'lower_header'],
        [
            ' Raw ', '  \\vfill\nFront;value  ', 'Back "quoted"',
            '{{ card_number }}', r'Foot\\line',
        ],
    ]

    target = repo.create_deck(
        'Advanced CSV target',
        render_settings=DeckRenderSettings(authoring_mode=AuthoringMode.ADVANCED),
    )
    assert ImportCsv(repo).execute(
        target.id,
        exported,
        delimiter='semicolon',
        schema_mode='header',
    ) == 1
    imported = repo.load_cards(target.id).cards[0]
    assert (
        imported.section,
        imported.front,
        imported.back,
        imported.upper_header,
        imported.lower_header,
    ) == (
        ' Raw ',
        '  \\vfill\nFront;value  ',
        'Back "quoted"',
        '{{ card_number }}',
        r'Foot\\line',
    )


def test_schema_one_json_import_remains_supported_with_legacy_defaults(repo):
    payload = {
        'schema_version': 1,
        'deck': {'id': 'legacy-deck', 'name': 'Legacy'},
        'cards': [{'id': 'legacy-card', 'front': 'Q', 'back': 'A'}],
    }

    imported = import_deck_json(repo, json.dumps(payload).encode())

    card = repo.load_cards(imported.id).cards[0]
    assert card.section == ''
    assert repo.get_render_settings(imported.id) == DeckRenderSettings.legacy()


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
        (
            json.dumps({
                'schema_version': DECK_EXPORT_SCHEMA_VERSION,
                'deck': {
                    'name': 'Deck',
                    'render_settings': {'preset': 'dangerous'},
                },
                'cards': [],
            }).encode(),
            'оформлен',
        ),
        (
            json.dumps({
                'schema_version': DECK_EXPORT_SCHEMA_VERSION,
                'deck': {'name': 'Deck'},
                'cards': [{'front': 'Q', 'section': 7}],
            }).encode(),
            'Секция',
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
