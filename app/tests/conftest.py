from __future__ import annotations

import pytest
from flask import Flask

from didactic_cards.adapters.json_repository import JsonRepository
from didactic_cards.domain.entities import CardDeck
from didactic_cards.domain.interfaces import CompileResult, DocumentRenderer, PdfCompiler
from didactic_cards.web.blueprint import cards_bp


class FakeCompiler(PdfCompiler):
    def __init__(self, success: bool = True):
        self.success = success
        self.sources: list[str] = []

    def compile(self, latex_source: str) -> CompileResult:
        self.sources.append(latex_source)
        if self.success:
            return CompileResult(True, b"%PDF-1.7 fake", "")
        return CompileResult(False, b"", "pdflatex internal path", "compile-error")


class FakeRenderer(DocumentRenderer):
    def __init__(self):
        self.decks: list[CardDeck] = []
        self.sides: list[str] = []

    def render(self, deck: CardDeck) -> str:
        self.decks.append(deck)
        self.sides.append("duplex")
        return r"\documentclass{article}\begin{document}fake\end{document}"

    def render_fronts(self, deck: CardDeck) -> str:
        self.decks.append(deck)
        self.sides.append("front")
        return r"\documentclass{article}\begin{document}fake\end{document}"

    def render_backs(self, deck: CardDeck) -> str:
        self.decks.append(deck)
        self.sides.append("back")
        return r"\documentclass{article}\begin{document}fake\end{document}"


def make_test_app(tmp_path, *, compiler_success: bool = True) -> Flask:
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="test-secret",
        REPO=JsonRepository(str(tmp_path / "data")),
        RENDERER=FakeRenderer(),
        COMPILER=FakeCompiler(compiler_success),
        CARDS_PER_PAGE=8,
        MAX_CARDS=200,
        CSRF_ENABLED=False,
    )
    app.register_blueprint(cards_bp)
    return app


@pytest.fixture
def app(tmp_path):
    return make_test_app(tmp_path)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def repo(app) -> JsonRepository:
    return app.config["REPO"]


@pytest.fixture
def deck(repo):
    return repo.create_deck("Физика", "Тестовая колода")


@pytest.fixture
def deck_id(deck) -> str:
    return deck.id


@pytest.fixture
def app_fail_compiler(tmp_path):
    return make_test_app(tmp_path, compiler_success=False)
