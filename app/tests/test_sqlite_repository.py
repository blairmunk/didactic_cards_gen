from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from multiprocessing import get_context

import pytest

from didactic_cards.adapters.repository_errors import DeckNotFoundError
from didactic_cards.adapters.sqlite_repository import (
    SQLITE_SCHEMA_VERSION,
    SqliteRepository,
    UnsupportedSqliteSchemaError,
)
from didactic_cards.adapters.sqlite_storage import (
    StorageBusyError,
    exclusive_runtime_lock,
)
from didactic_cards.domain.entities import Card, CardDeck, CardModeError
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


def _close_inherited_repository(repository):
    repository.close()


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
        'decks', 'cards', 'deck_cards', 'printer_profiles',
        'deck_render_settings', 'schema_migrations',
        'trusted_templates',
    } <= tables
    assert sqlite_repo.integrity_check() == []
    assert sqlite_repo.readiness_check() == []


def test_closed_repository_fails_before_opening_unleased_connections(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    repository.close()

    with pytest.raises(RuntimeError, match='repository is closed'):
        repository.list_decks()


def test_forked_child_close_does_not_release_parent_runtime_lease(tmp_path):
    repository = SqliteRepository(tmp_path / 'data')
    process = get_context('fork').Process(
        target=_close_inherited_repository, args=(repository,)
    )
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 0

    with pytest.raises(StorageBusyError, match='application is running'):
        with exclusive_runtime_lock(repository.database_file):
            pass

    repository.close()
    with exclusive_runtime_lock(repository.database_file):
        pass


def test_readiness_stops_on_integrity_error(sqlite_repo, monkeypatch):
    monkeypatch.setattr(
        sqlite_repo, 'integrity_check', lambda **_kwargs: ['broken']
    )
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
        '{"authoring_mode": "removed"}',
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

    monkeypatch.setattr(sqlite_repo, 'integrity_check', lambda **_kwargs: [])
    monkeypatch.setattr(sqlite_repo, '_connect', BrokenConnection)
    assert sqlite_repo.readiness_check() == ['write-transaction-unavailable']


def test_readiness_write_probe_is_bounded_by_a_short_busy_timeout(sqlite_repo):
    blocker = sqlite_repo._connect()
    blocker.execute('BEGIN IMMEDIATE')
    started = time.monotonic()
    try:
        assert sqlite_repo.readiness_check() == [
            'write-transaction-unavailable'
        ]
    finally:
        blocker.rollback()
        blocker.close()

    assert time.monotonic() - started < 2


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


def test_clone_lineage_and_delete(sqlite_repo):
    source = sqlite_repo.create_deck('Source')
    original = Card(
        front=' Q\r\nline\rraw\n ',
        back='\nA\n\n',
        section=' Section\rlabel ',
    )
    sqlite_repo.save_cards(source.id, CardDeck([original]))

    clone = sqlite_repo.clone_deck(source.id)
    clone_card = sqlite_repo.load_cards(clone.id).cards[0]
    assert clone.parent_id == source.id
    assert clone_card.id != original.id
    assert clone_card.parent_id == original.id
    assert (
        clone_card.section, clone_card.front, clone_card.back
    ) == (
        original.section, original.front, original.back
    )
    assert sqlite_repo.clone_deck('missing') is None

    assert sqlite_repo.delete_deck(source.id) is True
    assert sqlite_repo.delete_deck(source.id) is False
    assert sqlite_repo.get_deck(source.id) is None
    assert sqlite_repo.load_cards(clone.id).cards[0].front == original.front


def test_sqlite_reopen_preserves_mixed_newlines_in_all_card_fields(sqlite_repo):
    deck = sqlite_repo.create_deck(
        'Mixed newlines',
        render_settings=DeckRenderSettings(authoring_mode='advanced'),
    )
    expected = Card(
        section=' S\r\nsection\r ',
        front=' F\nfront\r\n ',
        back=' B\rback\n ',
        upper_header=' U\r\nupper\n ',
        lower_header=' L\rlower\n ',
    )
    sqlite_repo.save_cards(deck.id, CardDeck([expected]))

    reopened = SqliteRepository(sqlite_repo.data_dir)
    actual = reopened.load_cards(deck.id).cards[0]

    assert (
        actual.section, actual.front, actual.back,
        actual.upper_header, actual.lower_header,
    ) == (
        expected.section, expected.front, expected.back,
        expected.upper_header, expected.lower_header,
    )


def test_safe_repository_writes_reject_hidden_headers_without_partial_change(
    sqlite_repo,
):
    deck = sqlite_repo.create_deck('Safe invariant')
    sqlite_repo.save_cards(deck.id, CardDeck([Card(front='before')]))
    before_version = sqlite_repo.get_deck(deck.id).version

    with pytest.raises(ValueError, match='Advanced'):
        sqlite_repo.save_cards(
            deck.id,
            CardDeck([Card(front='after', upper_header='hidden')]),
        )

    assert sqlite_repo.load_cards(deck.id).cards[0].front == 'before'
    assert sqlite_repo.get_deck(deck.id).version == before_version

    deck_count = len(sqlite_repo.list_decks())
    with pytest.raises(CardModeError, match='Advanced'):
        sqlite_repo.create_deck_with_cards(
            'Rejected atomically',
            '',
            None,
            CardDeck([Card(front='Q', lower_header=' ')]),
            DeckRenderSettings.centered(),
        )
    assert len(sqlite_repo.list_decks()) == deck_count


def test_integrity_reports_hidden_raw_headers_in_safe_deck(sqlite_repo):
    deck = sqlite_repo.create_deck('Tampered Safe')
    card = Card(front='Q')
    sqlite_repo.save_cards(deck.id, CardDeck([card]))
    with closing(sqlite_repo._connect()) as connection:
        connection.execute(
            'UPDATE cards SET upper_header = ? WHERE id = ?',
            ('hidden', card.id),
        )
        connection.commit()

    issue = f'hidden-safe-card-headers: {card.id}'
    assert issue in sqlite_repo.integrity_check()
    assert issue in sqlite_repo.readiness_check()


def test_corrupt_safe_deck_cannot_be_cloned_or_snapshotted(sqlite_repo):
    deck = sqlite_repo.create_deck('Tampered operations')
    card = Card(front='Q')
    sqlite_repo.save_cards(deck.id, CardDeck([card]))
    with closing(sqlite_repo._connect()) as connection:
        connection.execute(
            'UPDATE cards SET lower_header = ? WHERE id = ?',
            ('hidden', card.id),
        )
        connection.commit()
    before_ids = {item.id for item in sqlite_repo.list_decks()}

    with pytest.raises(CardModeError, match='Advanced'):
        sqlite_repo.clone_deck(deck.id)
    with pytest.raises(CardModeError, match='Advanced'):
        sqlite_repo.get_print_job_snapshot(deck.id)

    assert {item.id for item in sqlite_repo.list_decks()} == before_ids


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
            source.id, DeckRenderSettings.centered(), expected_version=initial_version
        )
    clone = sqlite_repo.clone_deck(source.id)
    assert sqlite_repo.get_render_settings(clone.id) == custom


@pytest.mark.parametrize(
    ('initial_mode', 'replacement_mode'),
    [('safe', 'advanced'), ('advanced', 'safe')],
)
def test_authoring_mode_is_immutable_at_repository_boundary(
    sqlite_repo, initial_mode, replacement_mode
):
    initial = DeckRenderSettings(authoring_mode=initial_mode)
    deck = sqlite_repo.create_deck('Immutable mode', render_settings=initial)
    version = sqlite_repo.get_deck(deck.id).version

    with pytest.raises(ValueError, match='не может быть изменён'):
        sqlite_repo.save_render_settings(
            deck.id,
            DeckRenderSettings(authoring_mode=replacement_mode),
            expected_version=version,
        )

    assert sqlite_repo.get_render_settings(deck.id) == initial
    assert sqlite_repo.get_deck(deck.id).version == version


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
        r'FRONT {{ upper_header }}\raggedleft {{ content }}{{ lower_header }}',
        r'BACK {{ upper_header }}\raggedleft {{ content }}{{ lower_header }}',
    )

    assert (first.version, second.version) == (1, 2)
    assert first.status is TemplateStatus.QUARANTINED
    assert second.front_source.startswith('FRONT')
    assert second.back_source.startswith('BACK')
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
        source.id,
        'FRONT {{ upper_header }}{{ content }}{{ lower_header }}',
        'BACK {{ upper_header }}{{ content }}{{ lower_header }}',
    )
    sqlite_repo.approve_trusted_template(source.id, template.id)

    clone = sqlite_repo.clone_deck(source.id)
    cloned = sqlite_repo.list_trusted_templates(clone.id)

    assert len(cloned) == 1
    assert cloned[0].status is TemplateStatus.QUARANTINED
    assert cloned[0].provenance.value == 'cloned'
    assert cloned[0].origin_template_id == template.id
    assert cloned[0].front_source == template.front_source
    assert cloned[0].back_source == template.back_source
    assert sqlite_repo.get_approved_trusted_template(clone.id) is None


def test_safe_clone_does_not_copy_stale_trusted_history(sqlite_repo):
    source = sqlite_repo.create_deck('Safe source')
    sqlite_repo.quarantine_trusted_template(source.id, '{{ content }}')

    clone = sqlite_repo.clone_deck(source.id)

    assert clone.render_settings.authoring_mode.value == 'safe'
    assert sqlite_repo.list_trusted_templates(clone.id) == []


def test_atomic_trusted_import_requires_advanced_settings(sqlite_repo):
    template = TrustedTemplateVersion(
        deck_id='source', front_source='{{ content }}', back_source='{{ content }}', version=1
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
    staged = enabled.stage_local(deck.id, '{{ content }}')
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


def test_trusted_template_missing_hash_is_rejected(sqlite_repo):
    deck = sqlite_repo.create_deck('Missing hash')
    template = sqlite_repo.quarantine_trusted_template(
        deck.id, '{{ content }}'
    )
    with closing(sqlite_repo._connect()) as connection:
        connection.execute(
            'UPDATE trusted_templates SET source_hash = ? WHERE id = ?',
            ('', template.id),
        )
        connection.commit()
    with pytest.raises(ValueError, match='hash is missing'):
        sqlite_repo.list_trusted_templates(deck.id)


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


def test_trusted_service_rejects_safe_deck(sqlite_repo):
    deck = sqlite_repo.create_deck('Safe')
    service = TrustedTemplateService(sqlite_repo, enabled=True)
    with pytest.raises(ValueError, match='only to advanced'):
        service.stage_local(deck.id, '{{ content }}')


def test_print_job_snapshot_is_one_consistent_deck_style_template_read(
    sqlite_repo,
):
    settings = DeckRenderSettings(authoring_mode='advanced')
    deck = sqlite_repo.create_deck('Snapshot', render_settings=settings)
    card = Card(front='Q', back='A')
    sqlite_repo.save_cards(deck.id, CardDeck([card]))
    template = sqlite_repo.quarantine_trusted_template(deck.id, '{{ content }}')
    sqlite_repo.approve_trusted_template(deck.id, template.id)

    snapshot = sqlite_repo.get_print_job_snapshot(deck.id)

    assert snapshot.deck_id == deck.id
    assert snapshot.deck_version == sqlite_repo.get_deck(deck.id).version
    assert [item.id for item in snapshot.cards] == [card.id]
    assert snapshot.render_settings == settings
    assert snapshot.trusted_template.id == template.id


def test_print_job_snapshot_rejects_missing_deck(sqlite_repo):
    with pytest.raises(DeckNotFoundError):
        sqlite_repo.get_print_job_snapshot('missing')


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
    with pytest.raises(DeckNotFoundError):
        sqlite_repo.mutate_cards(
            'missing', lambda cards: (cards, False)
        )


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
