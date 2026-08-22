from ..domain.interfaces import DeckRepository
from ..domain.entities import Deck

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

    def execute(self, name: str, description: str = '') -> Deck:
        name = name.strip() or 'Новая колода'
        return self.repo.create_deck(name, description)


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