from __future__ import annotations

import json
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
from didactic_cards.domain.interfaces import ConcurrentModificationError
from didactic_cards.domain.printing import PrinterProfile
from didactic_cards.domain.rendering import DeckRenderSettings
from didactic_cards.domain.trusted import TemplateStatus, TrustedTemplateVersion
from didactic_cards.use_cases.trusted_template_use_cases import (
    TrustedLatexDisabledError,
    TrustedTemplateService,
)
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
    assert {
        'repository_meta', 'decks', 'cards', 'deck_cards', 'printer_profiles',
        'deck_render_settings',
        'trusted_templates',
    } <= tables
    assert sqlite_repo.integrity_check() == []
    assert sqlite_repo.readiness_check() == []


def test_readiness_stops_on_integrity_error(sqlite_repo, monkeypatch):
    monkeypatch.setattr(sqlite_repo, 'integrity_check', lambda: ['broken'])
    assert sqlite_repo.readiness_check() == ['broken']


def test_integrity_reports_missing_render_settings(sqlite_repo):
    deck = sqlite_repo.create_deck('Broken settings')
    with closing(sqlite_repo._connect()) as connection:
        connection.execute(
            'DELETE FROM deck_render_settings WHERE deck_id = ?', (deck.id,)
        )
        connection.commit()

    assert sqlite_repo.integrity_check() == [
        f'missing-render-settings: {deck.id}'
    ]
    with pytest.raises(ValueError, match='Missing render settings'):
        sqlite_repo.get_deck(deck.id)


@pytest.mark.parametrize(
    'payload',
    [
        'not-json',
        '[]',
        '{"preset": "legacy-top-left"}',
        '{"body_font_family": "\\\\input"}',
    ],
)
def test_integrity_reports_invalid_typography_settings(sqlite_repo, payload):
    deck = sqlite_repo.create_deck('Broken typography')
    with closing(sqlite_repo._connect()) as connection:
        connection.execute(
            'UPDATE deck_render_settings SET typography_json = ? WHERE deck_id = ?',
            (payload, deck.id),
        )
        connection.commit()

    assert sqlite_repo.integrity_check() == [
        f'invalid-render-settings: {deck.id}'
    ]
    assert sqlite_repo.readiness_check() == [
        f'invalid-render-settings: {deck.id}'
    ]


def test_readiness_reports_unavailable_write_transaction(sqlite_repo, monkeypatch):
    class BrokenConnection:
        def execute(self, _statement):
            raise sqlite3.OperationalError('private path')

        def close(self):
            pass

    monkeypatch.setattr(sqlite_repo, 'integrity_check', lambda: [])
    monkeypatch.setattr(sqlite_repo, '_connect', BrokenConnection)
    assert sqlite_repo.readiness_check() == ['write-transaction-unavailable']


def test_printer_profile_crud_round_trip(sqlite_repo):
    original = PrinterProfile(
        key='office-printer',
        name='Office printer',
        duplex_mode='short-edge',
        back_rotation_deg=0,
        front_offset_x_mm=0.25,
        back_offset_x_mm=-1.5,
        back_offset_y_mm=0.75,
        back_border=True,
        registration_marks=True,
    )
    assert sqlite_repo.save_printer_profile(original) == original
    assert sqlite_repo.list_printer_profiles() == [original]

    updated = PrinterProfile(
        key='office-printer',
        name='Office printer updated',
        back_offset_x_mm=1.0,
    )
    sqlite_repo.save_printer_profile(updated)
    assert sqlite_repo.list_printer_profiles() == [updated]
    assert sqlite_repo.delete_printer_profile('missing') is False
    assert sqlite_repo.delete_printer_profile(original.key) is True
    assert sqlite_repo.list_printer_profiles() == []


def test_schema_one_database_migrates_to_current_without_losing_decks(tmp_path):
    data_dir = tmp_path / 'schema-one'
    repository = SqliteRepository(data_dir)
    deck = repository.create_deck('Preserved')
    with closing(repository._connect()) as connection:
        connection.execute('DROP TABLE printer_profiles')
        connection.execute('PRAGMA user_version = 1')
        connection.commit()

    migrated = SqliteRepository(data_dir)

    assert migrated.get_deck(deck.id).name == 'Preserved'
    assert migrated.list_printer_profiles() == []
    with closing(migrated._connect()) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == SQLITE_SCHEMA_VERSION


def test_schema_two_profiles_migrate_rotation_by_duplex_mode(tmp_path):
    data_dir = tmp_path / 'schema-two'
    repository = SqliteRepository(data_dir)
    with closing(repository._connect()) as connection:
        connection.execute('PRAGMA user_version = 2')
        connection.execute('ALTER TABLE printer_profiles RENAME TO printer_profiles_v3')
        connection.execute(
            '''
            CREATE TABLE printer_profiles (
                key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                duplex_mode TEXT NOT NULL,
                front_offset_x_mm REAL NOT NULL,
                front_offset_y_mm REAL NOT NULL,
                back_offset_x_mm REAL NOT NULL,
                back_offset_y_mm REAL NOT NULL,
                back_border INTEGER NOT NULL,
                registration_marks INTEGER NOT NULL
            )
            '''
        )
        connection.executemany(
            '''
            INSERT INTO printer_profiles VALUES (?, ?, ?, 0, 0, 0, 0, 0, 0)
            ''',
            (
                ('old-long', 'Old long edge', 'long-edge'),
                ('old-short', 'Old short edge', 'short-edge'),
            ),
        )
        connection.execute('DROP TABLE printer_profiles_v3')
        connection.commit()

    migrated = SqliteRepository(data_dir)

    profiles = {profile.key: profile for profile in migrated.list_printer_profiles()}
    assert profiles['old-long'].back_rotation_deg == 180
    assert profiles['old-short'].back_rotation_deg == 0
    with closing(migrated._connect()) as connection:
        columns = {
            row['name'] for row in connection.execute(
                'PRAGMA table_info(printer_profiles)'
            )
        }
        assert 'back_rotation_deg' in columns
        assert connection.execute('PRAGMA user_version').fetchone()[0] == (
            SQLITE_SCHEMA_VERSION
        )


def test_schema_three_migrates_sections_and_legacy_render_settings(tmp_path):
    data_dir = tmp_path / 'schema-three'
    repository = SqliteRepository(data_dir)
    deck = repository.create_deck('Existing')
    repository.save_cards(
        deck.id, CardDeck([Card(front='Q', back='A', section='Will be legacy')])
    )
    with closing(repository._connect()) as connection:
        connection.execute('DROP TABLE deck_render_settings')
        connection.execute('ALTER TABLE cards DROP COLUMN section')
        connection.execute('PRAGMA user_version = 3')
        connection.commit()

    migrated = SqliteRepository(data_dir)

    assert migrated.load_cards(deck.id).cards[0].section == ''
    assert migrated.get_render_settings(deck.id) == DeckRenderSettings.legacy()
    with closing(migrated._connect()) as connection:
        card_columns = {
            row['name'] for row in connection.execute('PRAGMA table_info(cards)')
        }
        assert 'section' in card_columns
        assert connection.execute('PRAGMA user_version').fetchone()[0] == (
            SQLITE_SCHEMA_VERSION
        )


def test_schema_four_adds_section_layout_settings_without_changing_behavior(
    tmp_path,
):
    data_dir = tmp_path / 'schema-four'
    repository = SqliteRepository(data_dir)
    deck = repository.create_deck('Existing presentation')
    with closing(repository._connect()) as connection:
        connection.execute(
            'ALTER TABLE deck_render_settings RENAME TO settings_v5'
        )
        connection.execute(
            '''
            CREATE TABLE deck_render_settings (
                deck_id TEXT PRIMARY KEY REFERENCES decks(id) ON DELETE CASCADE,
                preset TEXT NOT NULL,
                horizontal_alignment TEXT NOT NULL,
                vertical_alignment TEXT NOT NULL,
                header_visibility TEXT NOT NULL,
                header_position TEXT NOT NULL,
                header_alignment TEXT NOT NULL
            )
            '''
        )
        connection.execute(
            '''
            INSERT INTO deck_render_settings
            SELECT deck_id, preset, horizontal_alignment, vertical_alignment,
                   header_visibility, header_position, header_alignment
            FROM settings_v5
            '''
        )
        connection.execute('DROP TABLE settings_v5')
        connection.execute('PRAGMA user_version = 4')
        connection.commit()

    migrated = SqliteRepository(data_dir)
    settings = migrated.get_render_settings(deck.id)

    assert settings.header_repeat.value == 'every-card'
    assert settings.section_break.value == 'continuous'
    with closing(migrated._connect()) as connection:
        columns = {
            row['name'] for row in connection.execute(
                'PRAGMA table_info(deck_render_settings)'
            )
        }
        assert {'header_repeat', 'section_break'} <= columns
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 9


def test_schema_five_adds_empty_trusted_template_quarantine(tmp_path):
    data_dir = tmp_path / 'schema-five'
    repository = SqliteRepository(data_dir)
    deck = repository.create_deck('Existing')
    with closing(repository._connect()) as connection:
        connection.execute('DROP TABLE trusted_templates')
        connection.execute('PRAGMA user_version = 5')
        connection.commit()

    migrated = SqliteRepository(data_dir)

    assert migrated.list_trusted_templates(deck.id) == []
    with closing(migrated._connect()) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 9


def test_schema_six_adds_escaped_content_modes_without_activating_template(
    tmp_path,
):
    data_dir = tmp_path / 'schema-six'
    repository = SqliteRepository(data_dir)
    deck = repository.create_deck('Existing trusted')
    template = repository.quarantine_trusted_template(
        deck.id, '{{ content }}'
    )
    with closing(repository._connect()) as connection:
        connection.execute('ALTER TABLE trusted_templates RENAME TO templates_v7')
        connection.execute(
            '''
            CREATE TABLE trusted_templates (
                id TEXT PRIMARY KEY,
                deck_id TEXT NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
                version INTEGER NOT NULL,
                source TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                provenance TEXT NOT NULL,
                status TEXT NOT NULL,
                origin_template_id TEXT,
                created_at TEXT NOT NULL,
                approved_at TEXT,
                UNIQUE(deck_id, version)
            )
            '''
        )
        connection.execute(
            '''
            INSERT INTO trusted_templates
            SELECT id, deck_id, version, source, source_hash, provenance,
                   status, origin_template_id, created_at, approved_at
            FROM templates_v7
            '''
        )
        connection.execute('DROP TABLE templates_v7')
        connection.execute('PRAGMA user_version = 6')
        connection.commit()

    migrated = SqliteRepository(data_dir)
    restored = migrated.list_trusted_templates(deck.id)[0]

    assert restored.id == template.id
    assert restored.front_content_mode.value == 'escaped'
    assert restored.back_content_mode.value == 'escaped'
    assert restored.status is TemplateStatus.QUARANTINED
    with closing(migrated._connect()) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 9


def test_schema_seven_adds_disabled_typography_without_changing_output(tmp_path):
    data_dir = tmp_path / 'schema-seven'
    repository = SqliteRepository(data_dir)
    deck = repository.create_deck('Existing layout')
    with closing(repository._connect()) as connection:
        connection.execute('ALTER TABLE deck_render_settings DROP COLUMN typography_json')
        connection.execute('PRAGMA user_version = 7')
        connection.commit()

    migrated = SqliteRepository(data_dir)

    assert migrated.get_render_settings(deck.id).typography is None
    with closing(migrated._connect()) as connection:
        columns = {
            row['name'] for row in connection.execute(
                'PRAGMA table_info(deck_render_settings)'
            )
        }
        assert 'typography_json' in columns
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 9


def test_schema_eight_separates_safe_and_approved_advanced_decks(tmp_path):
    data_dir = tmp_path / 'schema-eight'
    repository = SqliteRepository(data_dir)
    safe = repository.create_deck('Safe')
    advanced = repository.create_deck('Previously approved')
    template = repository.quarantine_trusted_template(
        advanced.id, '{{ content }}'
    )
    repository.approve_trusted_template(advanced.id, template.id)
    with closing(repository._connect()) as connection:
        for deck in (safe, advanced):
            row = connection.execute(
                'SELECT typography_json FROM deck_render_settings '
                'WHERE deck_id = ?',
                (deck.id,),
            ).fetchone()
            payload = json.loads(row['typography_json'])
            payload.pop('authoring_mode', None)
            payload['secondary_header_position'] = 'top'
            connection.execute(
                'UPDATE deck_render_settings '
                "SET header_position = 'bottom', typography_json = ? "
                'WHERE deck_id = ?',
                (json.dumps(payload), deck.id),
            )
        connection.execute('PRAGMA user_version = 8')
        connection.commit()

    migrated = SqliteRepository(data_dir)

    assert migrated.get_render_settings(safe.id).authoring_mode.value == 'safe'
    settings = migrated.get_render_settings(advanced.id)
    assert settings.authoring_mode.value == 'advanced'
    assert settings.header_position.value == 'top'
    assert settings.secondary_header_position.value == 'bottom'


def test_deck_and_ordered_card_round_trip(sqlite_repo):
    first = sqlite_repo.create_deck('First', 'Description')
    second = sqlite_repo.create_deck('Second')
    cards = CardDeck([
        Card(front='A', section='One'), Card(front='B', section='Two')
    ])
    sqlite_repo.save_cards(first.id, cards)

    loaded = SqliteRepository(sqlite_repo.data_dir)
    assert [deck.id for deck in loaded.list_decks()][0] == first.id
    assert loaded.get_deck(first.id).card_ids == [card.id for card in cards.cards]
    assert [card.front for card in loaded.load_cards(first.id).cards] == ['A', 'B']
    assert [card.section for card in loaded.load_cards(first.id).cards] == [
        'One', 'Two'
    ]

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


def test_render_settings_are_versioned_and_cloned(sqlite_repo):
    source = sqlite_repo.create_deck('Styled')
    assert sqlite_repo.get_render_settings(source.id) == DeckRenderSettings.centered()
    initial_version = sqlite_repo.get_deck(source.id).version
    custom = DeckRenderSettings(
        preset='custom',
        horizontal_alignment='right',
        vertical_alignment='bottom',
        header_visibility='both',
        header_repeat='section-start',
        section_break='new-sheet',
        typography_profile='custom',
        body_font_family='mono',
        secondary_header_visibility='front',
        secondary_header_source='custom',
        secondary_header_text='Лабораторная работа',
    )

    saved = sqlite_repo.save_render_settings(
        source.id, custom, expected_version=initial_version
    )

    assert saved == custom
    assert sqlite_repo.get_render_settings(source.id) == custom
    assert sqlite_repo.get_deck(source.id).version == initial_version + 1
    with pytest.raises(ConcurrentModificationError):
        sqlite_repo.save_render_settings(
            source.id, DeckRenderSettings.legacy(), expected_version=initial_version
        )
    clone = sqlite_repo.clone_deck(source.id)
    assert sqlite_repo.get_render_settings(clone.id) == custom


def test_missing_deck_has_no_render_settings(sqlite_repo):
    with pytest.raises(DeckNotFoundError):
        sqlite_repo.get_render_settings('missing')
    with pytest.raises(DeckNotFoundError):
        sqlite_repo.save_render_settings(
            'missing', DeckRenderSettings.centered()
        )


def test_trusted_templates_are_versioned_quarantined_and_explicitly_approved(
    sqlite_repo,
):
    deck = sqlite_repo.create_deck('Trusted')
    first = sqlite_repo.quarantine_trusted_template(
        deck.id, r'\centering {{ content }}'
    )
    second = sqlite_repo.quarantine_trusted_template(
        deck.id,
        r'\raggedleft {{ content }}',
        front_content_mode='raw',
        back_content_mode='escaped',
    )

    assert (first.version, second.version) == (1, 2)
    assert first.status is TemplateStatus.QUARANTINED
    assert second.front_content_mode.value == 'raw'
    assert second.back_content_mode.value == 'escaped'
    approved_first = sqlite_repo.approve_trusted_template(deck.id, first.id)
    assert approved_first.status is TemplateStatus.APPROVED
    assert sqlite_repo.get_approved_trusted_template(deck.id).id == first.id
    sqlite_repo.approve_trusted_template(deck.id, second.id)
    history = sqlite_repo.list_trusted_templates(deck.id)
    assert [item.status for item in history] == [
        TemplateStatus.REVOKED,
        TemplateStatus.APPROVED,
    ]
    sqlite_repo.revoke_trusted_template(deck.id, second.id)
    assert sqlite_repo.get_approved_trusted_template(deck.id) is None


def test_cloned_trusted_history_never_inherits_approval(sqlite_repo):
    source = sqlite_repo.create_deck(
        'Source',
        render_settings=DeckRenderSettings(authoring_mode='advanced'),
    )
    template = sqlite_repo.quarantine_trusted_template(
        source.id, '{{ content }}'
    )
    sqlite_repo.approve_trusted_template(source.id, template.id)

    clone = sqlite_repo.clone_deck(source.id)
    cloned = sqlite_repo.list_trusted_templates(clone.id)

    assert len(cloned) == 1
    assert cloned[0].status is TemplateStatus.QUARANTINED
    assert cloned[0].provenance.value == 'cloned'
    assert cloned[0].origin_template_id == template.id
    assert sqlite_repo.get_approved_trusted_template(clone.id) is None


def test_safe_clone_does_not_copy_stale_trusted_history(sqlite_repo):
    source = sqlite_repo.create_deck('Safe source')
    sqlite_repo.quarantine_trusted_template(source.id, '{{ content }}')

    clone = sqlite_repo.clone_deck(source.id)

    assert clone.render_settings.authoring_mode.value == 'safe'
    assert sqlite_repo.list_trusted_templates(clone.id) == []


def test_atomic_trusted_import_requires_advanced_settings(sqlite_repo):
    template = TrustedTemplateVersion(
        deck_id='source', source='{{ content }}', version=1
    )

    with pytest.raises(ValueError, match='advanced deck'):
        sqlite_repo.create_deck_with_cards_and_trusted(
            'Wrong mode',
            '',
            None,
            CardDeck(),
            DeckRenderSettings.centered(),
            (template,),
        )

    assert sqlite_repo.list_decks() == []


def test_trusted_service_denies_every_operation_until_feature_is_enabled(
    sqlite_repo,
):
    deck = sqlite_repo.create_deck(
        'Disabled',
        render_settings=DeckRenderSettings(authoring_mode='advanced'),
    )
    disabled = TrustedTemplateService(sqlite_repo)
    with pytest.raises(TrustedLatexDisabledError):
        disabled.stage_local(deck.id, '{{ content }}')

    enabled = TrustedTemplateService(sqlite_repo, enabled=True)
    with pytest.raises(ValueError, match='must be raw'):
        enabled.stage_local(
            deck.id,
            '{{ content }}',
            front_content_mode='escaped',
        )
    staged = enabled.stage_local(deck.id, '{{ content }}')
    assert staged.front_content_mode.value == 'raw'
    assert staged.back_content_mode.value == 'raw'
    assert enabled.active(deck.id) is None
    assert enabled.approve(deck.id, staged.id).status is TemplateStatus.APPROVED
    assert enabled.active(deck.id).id == staged.id
    assert enabled.revoke(deck.id, staged.id).status is TemplateStatus.REVOKED


def test_storage_readiness_detects_tampered_trusted_template(sqlite_repo):
    deck = sqlite_repo.create_deck('Tampered')
    template = sqlite_repo.quarantine_trusted_template(
        deck.id, '{{ content }}'
    )
    with closing(sqlite_repo._connect()) as connection:
        connection.execute(
            'UPDATE trusted_templates SET source_hash = ? WHERE id = ?',
            ('0' * 64, template.id),
        )
        connection.commit()

    assert sqlite_repo.integrity_check() == [
        f'invalid-trusted-template: {template.id}'
    ]
    assert sqlite_repo.readiness_check() == [
        f'invalid-trusted-template: {template.id}'
    ]


def test_trusted_template_repository_rejects_missing_deck_and_version(
    sqlite_repo,
):
    with pytest.raises(DeckNotFoundError):
        sqlite_repo.list_trusted_templates('missing')
    with pytest.raises(DeckNotFoundError):
        sqlite_repo.quarantine_trusted_template('missing', '{{ content }}')
    with pytest.raises(DeckNotFoundError):
        sqlite_repo.get_approved_trusted_template('missing')

    deck = sqlite_repo.create_deck('Present')
    with pytest.raises(KeyError):
        sqlite_repo.approve_trusted_template(deck.id, 'missing')
    with pytest.raises(KeyError):
        sqlite_repo.revoke_trusted_template(deck.id, 'missing')


def test_trusted_service_flag_must_be_boolean(sqlite_repo):
    with pytest.raises(TypeError, match='feature flag'):
        TrustedTemplateService(sqlite_repo, enabled=1)


def test_print_job_snapshot_is_one_consistent_deck_style_template_read(
    sqlite_repo,
):
    settings = DeckRenderSettings(authoring_mode='advanced')
    deck = sqlite_repo.create_deck('Snapshot', render_settings=settings)
    card = Card(front='Q', back='A')
    sqlite_repo.save_cards(deck.id, CardDeck([card]))
    template = sqlite_repo.quarantine_trusted_template(
        deck.id, '{{ content }}', back_content_mode='raw'
    )
    sqlite_repo.approve_trusted_template(deck.id, template.id)

    snapshot = sqlite_repo.get_print_job_snapshot(deck.id)

    assert snapshot.deck_id == deck.id
    assert snapshot.deck_version == sqlite_repo.get_deck(deck.id).version
    assert [item.id for item in snapshot.cards] == [card.id]
    assert snapshot.render_settings == settings
    assert snapshot.trusted_template.id == template.id
    assert snapshot.trusted_template.back_content_mode.value == 'raw'


def test_create_deck_with_cards_is_one_transaction(sqlite_repo, monkeypatch):
    source_card = Card(front='Imported')
    created = sqlite_repo.create_deck_with_cards(
        'Imported deck', 'Description', 'source-deck', CardDeck([source_card])
    )
    assert created.parent_id == 'source-deck'
    assert created.card_ids == [source_card.id]

    original_replace = sqlite_repo._replace_cards

    def fail_replace(*_args):
        raise RuntimeError('rollback import')

    monkeypatch.setattr(sqlite_repo, '_replace_cards', fail_replace)
    with pytest.raises(RuntimeError, match='rollback import'):
        sqlite_repo.create_deck_with_cards('Failed', '', None, CardDeck())
    monkeypatch.setattr(sqlite_repo, '_replace_cards', original_replace)
    assert [deck.name for deck in sqlite_repo.list_decks()] == ['Imported deck']


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


def test_optimistic_version_prevents_stale_mutation(sqlite_repo):
    deck = sqlite_repo.create_deck('Versioned')
    AddCard(sqlite_repo).execute(deck.id, 'first', '', deck.version)
    current = sqlite_repo.get_deck(deck.id)
    assert current.version > deck.version
    with pytest.raises(ConcurrentModificationError) as raised:
        AddCard(sqlite_repo).execute(deck.id, 'stale', '', deck.version)
    assert raised.value.actual == current.version
    assert [card.front for card in sqlite_repo.load_cards(deck.id).cards] == ['first']
    with pytest.raises(DeckNotFoundError):
        AddCard(sqlite_repo).execute('missing', 'Q', '')


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
