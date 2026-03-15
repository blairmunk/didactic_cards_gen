"""Тесты FlaskSessionRepository."""

from flask import Flask
from didactic_cards.adapters.session_repository import FlaskSessionRepository
from didactic_cards.domain.entities import Card, CardDeck


def _make_app():
    a = Flask(__name__)
    a.secret_key = 'test'
    a.config['TESTING'] = True
    return a


class TestFlaskSessionRepository:

    def test_load_empty(self):
        app = _make_app()
        repo = FlaskSessionRepository()
        with app.test_request_context():
            deck = repo.load()
            assert len(deck) == 0

    def test_save_and_load(self):
        app = _make_app()
        repo = FlaskSessionRepository()
        with app.test_request_context():
            deck = CardDeck()
            deck.add(Card(front='Q', back='A'))
            repo.save(deck)

            loaded = repo.load()
            assert len(loaded) == 1
            assert loaded.cards[0].front == 'Q'

    def test_save_overwrites(self):
        app = _make_app()
        repo = FlaskSessionRepository()
        with app.test_request_context():
            deck1 = CardDeck()
            deck1.add(Card(front='First', back='1'))
            repo.save(deck1)

            deck2 = CardDeck()
            deck2.add(Card(front='Second', back='2'))
            deck2.add(Card(front='Third', back='3'))
            repo.save(deck2)

            loaded = repo.load()
            assert len(loaded) == 2
            assert loaded.cards[0].front == 'Second'

    def test_roundtrip_preserves_data(self):
        app = _make_app()
        repo = FlaskSessionRepository()
        with app.test_request_context():
            deck = CardDeck()
            deck.add(Card(front='Hello', back='World'))
            deck.add(Card(front='Foo', back='Bar'))
            repo.save(deck)

            loaded = repo.load()
            assert len(loaded) == 2
            assert loaded.cards[0].front == 'Hello'
            assert loaded.cards[0].back == 'World'
            assert loaded.cards[1].front == 'Foo'
            assert loaded.cards[1].back == 'Bar'

    def test_clear_and_save(self):
        app = _make_app()
        repo = FlaskSessionRepository()
        with app.test_request_context():
            deck = CardDeck()
            deck.add(Card(front='Q', back='A'))
            repo.save(deck)

            deck.clear()
            repo.save(deck)

            loaded = repo.load()
            assert len(loaded) == 0