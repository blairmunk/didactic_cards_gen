from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum

from .entities import Card
from .rendering import AuthoringMode, DeckRenderSettings


MAX_TRUSTED_TEMPLATE_BYTES = 64 * 1024
MAX_TRUSTED_JOB_BYTES = 1024 * 1024
TRUSTED_JOB_SCHEMA_VERSION = 1
ALLOWED_PLACEHOLDERS = (
    '{{ content }}',
    '{{ section }}',
    '{{ card_number }}',
    '{{ side }}',
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class TemplateProvenance(str, Enum):
    LOCAL_AUTHOR = 'local-author'
    IMPORTED = 'imported'
    CLONED = 'cloned'


class TemplateStatus(str, Enum):
    QUARANTINED = 'quarantined'
    APPROVED = 'approved'
    REVOKED = 'revoked'


class ContentMode(str, Enum):
    ESCAPED = 'escaped'
    RAW = 'raw'


@dataclass(frozen=True)
class TrustedTemplateVersion:
    deck_id: str
    source: str
    version: int
    provenance: TemplateProvenance | str = TemplateProvenance.LOCAL_AUTHOR
    status: TemplateStatus | str = TemplateStatus.QUARANTINED
    front_content_mode: ContentMode | str = ContentMode.ESCAPED
    back_content_mode: ContentMode | str = ContentMode.ESCAPED
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source_hash: str = ''
    origin_template_id: str | None = None
    created_at: datetime = field(default_factory=_now)
    approved_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.deck_id:
            raise ValueError('trusted template deck_id is required')
        if isinstance(self.version, bool) or self.version <= 0:
            raise ValueError('trusted template version must be positive')
        if not isinstance(self.source, str) or not self.source:
            raise ValueError('trusted template source is required')
        if len(self.source.encode('utf-8')) > MAX_TRUSTED_TEMPLATE_BYTES:
            raise ValueError('trusted template source is too large')
        if '\x00' in self.source:
            raise ValueError('trusted template source contains NUL')
        object.__setattr__(
            self, 'provenance', TemplateProvenance(self.provenance)
        )
        object.__setattr__(self, 'status', TemplateStatus(self.status))
        object.__setattr__(
            self, 'front_content_mode', ContentMode(self.front_content_mode)
        )
        object.__setattr__(
            self, 'back_content_mode', ContentMode(self.back_content_mode)
        )
        expected_hash = _sha256(self.source)
        if self.source_hash and self.source_hash != expected_hash:
            raise ValueError('trusted template source hash mismatch')
        object.__setattr__(self, 'source_hash', expected_hash)
        if self.status is TemplateStatus.APPROVED and self.approved_at is None:
            raise ValueError('approved template requires approved_at')
        if self.status is not TemplateStatus.APPROVED and self.approved_at is not None:
            raise ValueError('only approved template may have approved_at')
        validate_template_source(self.source)

    def approved(self) -> TrustedTemplateVersion:
        return replace(
            self, status=TemplateStatus.APPROVED, approved_at=_now()
        )

    def revoked(self) -> TrustedTemplateVersion:
        return replace(
            self, status=TemplateStatus.REVOKED, approved_at=None
        )


def validate_template_source(source: str) -> None:
    """Validate the deliberately tiny, non-Jinja placeholder language."""
    tokens = re.findall(r'{{.*?}}', source, flags=re.DOTALL)
    unknown = [token for token in tokens if token not in ALLOWED_PLACEHOLDERS]
    if unknown:
        raise ValueError(f'unsupported template placeholder: {unknown[0]}')
    without_tokens = source
    for token in ALLOWED_PLACEHOLDERS:
        without_tokens = without_tokens.replace(token, '')
    if '{{' in without_tokens or '}}' in without_tokens:
        raise ValueError('malformed template placeholder')
    if source.count('{{ content }}') != 1:
        raise ValueError('template must contain {{ content }} exactly once')


def render_trusted_template(
    source: str,
    *,
    content: str,
    section: str,
    card_number: int,
    side: str,
) -> str:
    validate_template_source(source)
    if side not in {'front', 'back'}:
        raise ValueError('template side must be front or back')
    if isinstance(card_number, bool) or card_number < 1:
        raise ValueError('template card_number must be positive')
    values = {
        '{{ content }}': content,
        '{{ section }}': section,
        '{{ card_number }}': str(card_number),
        '{{ side }}': side,
    }
    rendered = source
    for placeholder, value in values.items():
        rendered = rendered.replace(placeholder, value)
    return rendered


@dataclass(frozen=True)
class TrustedCompileJob:
    latex_source: str
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    schema_version: int = TRUSTED_JOB_SCHEMA_VERSION
    source_hash: str = ''

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or self.schema_version != TRUSTED_JOB_SCHEMA_VERSION
        ):
            raise ValueError('unsupported trusted job schema')
        try:
            uuid.UUID(self.job_id)
        except (TypeError, ValueError) as error:
            raise ValueError('trusted job_id must be a UUID') from error
        if not isinstance(self.latex_source, str) or not self.latex_source:
            raise ValueError('trusted job source is required')
        if len(self.latex_source.encode('utf-8')) > MAX_TRUSTED_JOB_BYTES:
            raise ValueError('trusted job source is too large')
        if '\x00' in self.latex_source:
            raise ValueError('trusted job source contains NUL')
        expected_hash = _sha256(self.latex_source)
        if self.source_hash and self.source_hash != expected_hash:
            raise ValueError('trusted job source hash mismatch')
        object.__setattr__(self, 'source_hash', expected_hash)

    def to_dict(self) -> dict[str, str | int]:
        return {
            'schema_version': self.schema_version,
            'job_id': self.job_id,
            'latex_source': self.latex_source,
            'source_hash': self.source_hash,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TrustedCompileJob:
        if not isinstance(data, dict):
            raise ValueError('trusted job must be an object')
        expected = {'schema_version', 'job_id', 'latex_source', 'source_hash'}
        if set(data) != expected:
            raise ValueError('trusted job fields do not match protocol')
        if not isinstance(data['source_hash'], str) or len(data['source_hash']) != 64:
            raise ValueError('trusted job protocol requires source hash')
        return cls(**data)


@dataclass(frozen=True)
class PrintJobSnapshot:
    deck_id: str
    deck_version: int
    cards: tuple[Card, ...]
    render_settings: DeckRenderSettings
    trusted_template: TrustedTemplateVersion | None = None

    def __post_init__(self) -> None:
        if not self.deck_id:
            raise ValueError('print snapshot deck_id is required')
        if (
            isinstance(self.deck_version, bool)
            or not isinstance(self.deck_version, int)
            or self.deck_version <= 0
        ):
            raise ValueError('print snapshot deck version must be positive')
        if not isinstance(self.cards, tuple) or any(
            not isinstance(card, Card) for card in self.cards
        ):
            raise TypeError('print snapshot cards must be a tuple of Card')
        if not isinstance(self.render_settings, DeckRenderSettings):
            raise TypeError('print snapshot settings are invalid')
        if self.trusted_template is not None and (
            not isinstance(self.trusted_template, TrustedTemplateVersion)
            or self.trusted_template.deck_id != self.deck_id
            or self.trusted_template.status is not TemplateStatus.APPROVED
        ):
            raise ValueError('print snapshot template must be approved for deck')
        if (
            self.trusted_template is not None
            and self.render_settings.authoring_mode is not AuthoringMode.ADVANCED
        ):
            raise ValueError(
                'print snapshot template requires an advanced deck'
            )
