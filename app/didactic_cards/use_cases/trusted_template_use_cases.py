from __future__ import annotations

from ..domain.trusted import (
    ContentMode,
    TemplateProvenance,
    TrustedTemplateVersion,
)
from ..domain.rendering import AuthoringMode


class TrustedLatexDisabledError(PermissionError):
    pass


class TrustedTemplateService:
    """Feature-gated application boundary for quarantined trusted templates."""

    def __init__(self, repository, *, enabled: bool = False) -> None:
        if not isinstance(enabled, bool):
            raise TypeError('trusted LaTeX feature flag must be boolean')
        self.repository = repository
        self.enabled = enabled

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise TrustedLatexDisabledError(
                'Trusted LaTeX is disabled by deployment policy'
            )

    def _require_advanced_deck(self, deck_id: str) -> None:
        settings = self.repository.get_render_settings(deck_id)
        if settings.authoring_mode is not AuthoringMode.ADVANCED:
            raise ValueError(
                'Trusted TeX wrappers belong only to advanced decks'
            )

    def stage_local(
        self,
        deck_id: str,
        source: str,
        *,
        front_content_mode: ContentMode | str = ContentMode.RAW,
        back_content_mode: ContentMode | str = ContentMode.RAW,
    ) -> TrustedTemplateVersion:
        self._require_enabled()
        self._require_advanced_deck(deck_id)
        if (
            ContentMode(front_content_mode) is not ContentMode.RAW
            or ContentMode(back_content_mode) is not ContentMode.RAW
        ):
            raise ValueError('advanced wrapper content mode must be raw')
        return self.repository.quarantine_trusted_template(
            deck_id,
            source,
            provenance=TemplateProvenance.LOCAL_AUTHOR,
            front_content_mode=front_content_mode,
            back_content_mode=back_content_mode,
        )

    def approve(
        self, deck_id: str, template_id: str
    ) -> TrustedTemplateVersion:
        self._require_enabled()
        self._require_advanced_deck(deck_id)
        return self.repository.approve_trusted_template(deck_id, template_id)

    def revoke(
        self, deck_id: str, template_id: str
    ) -> TrustedTemplateVersion:
        self._require_enabled()
        self._require_advanced_deck(deck_id)
        return self.repository.revoke_trusted_template(deck_id, template_id)

    def active(self, deck_id: str) -> TrustedTemplateVersion | None:
        self._require_enabled()
        self._require_advanced_deck(deck_id)
        return self.repository.get_approved_trusted_template(deck_id)
