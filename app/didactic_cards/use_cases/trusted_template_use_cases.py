from __future__ import annotations

from ..domain.trusted import (
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
        front_source: str,
        back_source: str | None = None,
    ) -> TrustedTemplateVersion:
        self._require_enabled()
        self._require_advanced_deck(deck_id)
        if back_source is None:
            back_source = front_source
        return self.repository.quarantine_trusted_template(
            deck_id,
            front_source,
            back_source,
            provenance=TemplateProvenance.LOCAL_AUTHOR,
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
