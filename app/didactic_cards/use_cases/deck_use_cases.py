from ..domain.interfaces import DeckRepository
from ..domain.entities import Deck
from ..domain.rendering import AuthoringMode, DeckRenderSettings

from typing import Optional


class ListDecks:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(self) -> list[Deck]:
        return self.repo.list_decks()


class GetDeckInfo:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(self, deck_id: str) -> Optional[Deck]:
        return self.repo.get_deck(deck_id)


class CreateDeck:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(
        self,
        name: str,
        description: str = '',
        authoring_mode: AuthoringMode | str = AuthoringMode.SAFE,
    ) -> Deck:
        name = name.strip() or 'Новая колода'
        settings = DeckRenderSettings(authoring_mode=authoring_mode)
        return self.repo.create_deck(name, description, settings)


class UpdateDeck:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(self, deck_id: str, name: str, description: str = '') -> Optional[Deck]:
        name = name.strip() or 'Новая колода'
        return self.repo.update_deck(deck_id, name, description)


class DeleteDeck:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(self, deck_id: str) -> bool:
        return self.repo.delete_deck(deck_id)


class CloneDeck:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(self, deck_id: str) -> Optional[Deck]:
        return self.repo.clone_deck(deck_id)


class GetDeckRenderSettings:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(self, deck_id: str) -> DeckRenderSettings:
        return self.repo.get_render_settings(deck_id)


class UpdateDeckRenderSettings:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(
        self,
        deck_id: str,
        settings: DeckRenderSettings,
        expected_version: int | None = None,
    ) -> DeckRenderSettings:
        return self.repo.save_render_settings(
            deck_id,
            settings,
            expected_version=expected_version,
        )
