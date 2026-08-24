from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from didactic_cards.domain.trusted import (
    MAX_TRUSTED_JOB_BYTES,
    MAX_TRUSTED_TEMPLATE_BYTES,
    TemplateStatus,
    PrintJobSnapshot,
    TrustedCompileJob,
    TrustedTemplateVersion,
    render_trusted_template,
    validate_template_source,
)
from didactic_cards.domain.entities import Card
from didactic_cards.domain.rendering import DeckRenderSettings


def test_strict_template_substitution_supports_all_placeholders_and_unicode():
    source = (
        r'\begin{minipage}{\linewidth}'
        '{{ section }} / {{ card_number }} / {{ side }}: {{ content }}'
        r'\end{minipage}'
    )

    rendered = render_trusted_template(
        source,
        content=r'Сила $F=ma$',
        section='Механика',
        card_number=7,
        side='front',
    )

    assert rendered == (
        r'\begin{minipage}{\linewidth}'
        r'Механика / 7 / front: Сила $F=ma$'
        r'\end{minipage}'
    )


@pytest.mark.parametrize(
    ('source', 'message'),
    [
        ('plain text', 'content'),
        ('{{ content }} + {{ content }}', 'exactly once'),
        ('{{content}}', 'unsupported'),
        ('{{ content }} {{ danger }}', 'unsupported'),
        ('{{ content }} {{ broken', 'malformed'),
    ],
)
def test_template_language_rejects_missing_repeated_or_malformed_tokens(
    source, message
):
    with pytest.raises(ValueError, match=message):
        validate_template_source(source)


def test_optional_placeholder_may_repeat_deterministically():
    assert render_trusted_template(
        '{{ section }}: {{ content }} — {{ section }}',
        content='Q',
        section='One',
        card_number=1,
        side='back',
    ) == 'One: Q — One'


def test_trusted_headers_are_contextual_raw_fragments():
    source = (
        r'\vbox{{ upper_header }}\par {{ content }}\par '
        r'{{ lower_header }}'
    )
    rendered = render_trusted_template(
        source,
        content=r'\textbf{Q}',
        section='Явления',
        card_number=7,
        card_count=21,
        side='front',
        upper_header=r'{{\small {{ section }}}}',
        lower_header='Карточка {{ card_number }}/{{ card_count }}',
    )

    assert rendered == (
        r'\vbox{{\small Явления}}\par \textbf{Q}\par Карточка 7/21'
    )


@pytest.mark.parametrize(
    ('header', 'expected'),
    [
        (r'{{\bfseries Raw group}}', r'{{\bfseries Raw group}}'),
        ('{{ custom_macro }}', '{{ custom_macro }}'),
        ('{{ content }}', '{{ content }}'),
        ('{{ card_count }', '{{ card_count }'),
    ],
)
def test_trusted_header_values_keep_unknown_double_braces_as_raw_tex(
    header, expected
):
    assert render_trusted_template(
        '{{ upper_header }} {{ content }}',
        content='Q',
        section='',
        card_number=1,
        side='front',
        upper_header=header,
    ) == f'{expected} Q'


def test_trusted_template_record_keeps_both_side_wrappers_in_one_version():
    template = TrustedTemplateVersion(
        deck_id='deck',
        front_source='FRONT {{ upper_header }}{{ content }}{{ lower_header }}',
        back_source='BACK {{ upper_header }}{{ content }}{{ lower_header }}',
        version=1,
    )

    assert template.front_source.startswith('FRONT')
    assert template.back_source.startswith('BACK')
    assert template.state_hash == template.source_hash
    assert replace(
        template, back_source='OTHER {{ content }}', source_hash=''
    ).state_hash != (
        template.state_hash
    )


@pytest.mark.parametrize(
    ('kwargs', 'message'),
    [
        ({'side': 'middle'}, 'side'),
        ({'card_number': 0}, 'card_number'),
    ],
)
def test_template_render_context_is_typed(kwargs, message):
    context = {'content': 'Q', 'section': '', 'card_number': 1, 'side': 'front'}
    context.update(kwargs)
    with pytest.raises(ValueError, match=message):
        render_trusted_template('{{ content }}', **context)


def test_template_rejects_total_smaller_than_card_number():
    with pytest.raises(ValueError, match='include card_number'):
        render_trusted_template(
            '{{ content }}', content='Q', section='', card_number=2,
            card_count=1, side='front',
        )


def test_template_record_is_hashed_and_approval_is_explicit():
    template = TrustedTemplateVersion(
        deck_id='deck', front_source='{{ content }}', back_source='{{ content }}', version=1
    )

    assert template.status is TemplateStatus.QUARANTINED
    assert len(template.source_hash) == 64
    approved = template.approved()
    assert approved.status is TemplateStatus.APPROVED
    assert approved.approved_at is not None
    revoked = approved.revoked()
    assert revoked.status is TemplateStatus.REVOKED
    assert revoked.approved_at is None


def test_template_record_rejects_tampered_hash_and_invalid_status_timestamp():
    with pytest.raises(ValueError, match='hash mismatch'):
        TrustedTemplateVersion(
            deck_id='deck',
            front_source='{{ content }}', back_source='{{ content }}',
            version=1,
            source_hash='0' * 64,
        )
    with pytest.raises(ValueError, match='approved_at'):
        TrustedTemplateVersion(
            deck_id='deck',
            front_source='{{ content }}', back_source='{{ content }}',
            version=1,
            status='approved',
        )
    with pytest.raises(ValueError, match='only approved'):
        TrustedTemplateVersion(
            deck_id='deck',
            front_source='{{ content }}', back_source='{{ content }}',
            version=1,
            approved_at=datetime.now(timezone.utc),
        )


@pytest.mark.parametrize(
    ('kwargs', 'message'),
    [
        ({'deck_id': ''}, 'deck_id'),
        ({'version': 0}, 'version'),
        ({'front_source': ''}, 'front_source'),
        ({'back_source': '{{ content }}\x00'}, 'NUL'),
    ],
)
def test_template_record_rejects_invalid_identity_and_source(kwargs, message):
    values = {
        'deck_id': 'deck',
        'front_source': '{{ content }}',
        'back_source': '{{ content }}',
        'version': 1,
    }
    values.update(kwargs)
    with pytest.raises(ValueError, match=message):
        TrustedTemplateVersion(**values)


def test_template_and_job_size_limits_are_utf8_byte_limits():
    with pytest.raises(ValueError, match='too large'):
        TrustedTemplateVersion(
            deck_id='deck',
            front_source=(
                'я' * (MAX_TRUSTED_TEMPLATE_BYTES // 2 + 1)
                + '{{ content }}'
            ),
            back_source='{{ content }}',
            version=1,
        )
    with pytest.raises(ValueError, match='too large'):
        TrustedCompileJob(latex_source='я' * (MAX_TRUSTED_JOB_BYTES // 2 + 1))


def test_job_protocol_round_trip_detects_unknown_fields_and_tampering():
    job = TrustedCompileJob(latex_source=r'\documentclass{article}')
    assert TrustedCompileJob.from_dict(job.to_dict()) == job

    with pytest.raises(ValueError, match='fields'):
        TrustedCompileJob.from_dict({**job.to_dict(), 'command': 'shell'})
    with pytest.raises(ValueError, match='hash mismatch'):
        TrustedCompileJob.from_dict({**job.to_dict(), 'source_hash': '0' * 64})
    with pytest.raises(ValueError, match='schema'):
        TrustedCompileJob.from_dict({**job.to_dict(), 'schema_version': 99})
    with pytest.raises(ValueError, match='schema'):
        TrustedCompileJob.from_dict({**job.to_dict(), 'schema_version': True})
    with pytest.raises(ValueError, match='requires source hash'):
        TrustedCompileJob.from_dict({**job.to_dict(), 'source_hash': ''})
    with pytest.raises(ValueError, match='UUID'):
        replace(job, job_id='not-a-uuid')
    with pytest.raises(ValueError, match='object'):
        TrustedCompileJob.from_dict([])
    with pytest.raises(ValueError, match='NUL'):
        TrustedCompileJob(latex_source='bad\x00source')


def test_print_snapshot_requires_matching_approved_template():
    approved = TrustedTemplateVersion(
        deck_id='deck', front_source='{{ content }}', back_source='{{ content }}', version=1
    ).approved()
    snapshot = PrintJobSnapshot(
        deck_id='deck',
        deck_version=2,
        cards=(Card(front='Q'),),
        render_settings=DeckRenderSettings(authoring_mode='advanced'),
        trusted_template=approved,
    )
    assert snapshot.trusted_template.source_hash == approved.source_hash

    with pytest.raises(ValueError, match='approved'):
        replace(
            snapshot,
            trusted_template=TrustedTemplateVersion(
                deck_id='deck', front_source='{{ content }}', back_source='{{ content }}', version=2
            ),
        )
    with pytest.raises(ValueError, match='approved'):
        replace(snapshot, trusted_template=replace(approved, deck_id='another'))
    with pytest.raises(ValueError, match='advanced deck'):
        replace(snapshot, render_settings=DeckRenderSettings.centered())


@pytest.mark.parametrize(
    ('changes', 'error'),
    [
        ({'deck_id': ''}, ValueError),
        ({'deck_version': True}, ValueError),
        ({'cards': [Card()]}, TypeError),
        ({'render_settings': 'bad'}, TypeError),
    ],
)
def test_print_snapshot_rejects_incomplete_or_untyped_state(changes, error):
    values = {
        'deck_id': 'deck',
        'deck_version': 1,
        'cards': (),
        'render_settings': DeckRenderSettings.centered(),
    }
    values.update(changes)
    with pytest.raises(error):
        PrintJobSnapshot(**values)
