from ..domain.interfaces import DeckRepository
from ..domain.entities import Deck
from ..domain.rendering import AuthoringMode, DeckRenderSettings

from datetime import datetime, timedelta, timezone
from typing import Callable, Optional


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


class ListTrashedDecks:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(self) -> list[Deck]:
        return self.repo.list_trashed_decks()


class TrashDeck:
    def __init__(
        self,
        repo: DeckRepository,
        retention_days: int,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        if (
            isinstance(retention_days, bool)
            or not isinstance(retention_days, int)
            or retention_days <= 0
        ):
            raise ValueError('Срок хранения корзины должен быть положительным')
        self.repo = repo
        self.retention_days = retention_days
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def execute(self, deck_id: str, expected_version: int) -> bool:
        trashed_at = self.clock()
        if trashed_at.tzinfo is None:
            raise ValueError('Время удаления должно содержать часовой пояс')
        return self.repo.trash_deck(
            deck_id,
            expected_version=expected_version,
            trashed_at=trashed_at,
            purge_after=trashed_at + timedelta(days=self.retention_days),
        )


class RestoreDeck:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(self, deck_id: str, expected_version: int) -> bool:
        return self.repo.restore_deck(
            deck_id, expected_version=expected_version
        )


class PurgeDeck:
    def __init__(self, repo: DeckRepository):
        self.repo = repo

    def execute(self, deck_id: str, expected_version: int) -> bool:
        return self.repo.purge_deck(deck_id, expected_version=expected_version)


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
        current = self.repo.get_render_settings(deck_id)
        if settings.authoring_mode is not current.authoring_mode:
            raise ValueError(
                'Тип Safe/Advanced задаётся при создании колоды '
                'и не может быть изменён.'
            )
        return self.repo.save_render_settings(
            deck_id,
            settings,
            expected_version=expected_version,
        )
