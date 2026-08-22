from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from didactic_cards.domain.trusted import (
    MAX_TRUSTED_JOB_BYTES,
    MAX_TRUSTED_TEMPLATE_BYTES,
    TemplateStatus,
    TrustedCompileJob,
    TrustedTemplateVersion,
    render_trusted_template,
    validate_template_source,
)


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


def test_template_record_is_hashed_and_approval_is_explicit():
    template = TrustedTemplateVersion(
        deck_id='deck', source='{{ content }}', version=1
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
            source='{{ content }}',
            version=1,
            source_hash='0' * 64,
        )
    with pytest.raises(ValueError, match='approved_at'):
        TrustedTemplateVersion(
            deck_id='deck',
            source='{{ content }}',
            version=1,
            status='approved',
        )
    with pytest.raises(ValueError, match='only approved'):
        TrustedTemplateVersion(
            deck_id='deck',
            source='{{ content }}',
            version=1,
            approved_at=datetime.now(timezone.utc),
        )


@pytest.mark.parametrize(
    ('kwargs', 'message'),
    [
        ({'deck_id': ''}, 'deck_id'),
        ({'version': 0}, 'version'),
        ({'source': ''}, 'source'),
        ({'source': '{{ content }}\x00'}, 'NUL'),
    ],
)
def test_template_record_rejects_invalid_identity_and_source(kwargs, message):
    values = {'deck_id': 'deck', 'source': '{{ content }}', 'version': 1}
    values.update(kwargs)
    with pytest.raises(ValueError, match=message):
        TrustedTemplateVersion(**values)


def test_template_and_job_size_limits_are_utf8_byte_limits():
    with pytest.raises(ValueError, match='too large'):
        TrustedTemplateVersion(
            deck_id='deck',
            source='я' * (MAX_TRUSTED_TEMPLATE_BYTES // 2 + 1) + '{{ content }}',
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
