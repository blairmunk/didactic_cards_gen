from flask import session
from ..domain.interfaces import CardRepository
from ..domain.entities import CardDeck


class FlaskSessionRepository(CardRepository):
    """Хранит карточки в Flask session."""

    SESSION_KEY = 'cards'

    def load(self) -> CardDeck:
        data = session.get(self.SESSION_KEY, [])
        return CardDeck.from_list(data)

    def save(self, deck: CardDeck) -> None:
        session[self.SESSION_KEY] = deck.to_list()