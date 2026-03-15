import os
import pytest

from didactic_cards.domain.interfaces import CompileResult, PdfCompiler, DocumentRenderer
from didactic_cards.domain.entities import CardDeck
from didactic_cards.web.blueprint import cards_bp
from didactic_cards.adapters.session_repository import FlaskSessionRepository

from flask import Flask


class FakeCompiler(PdfCompiler):
    def __init__(self, success=True):
        self._success = success

    def compile(self, latex_source: str) -> CompileResult:
        if self._success:
            return CompileResult(success=True, pdf_data=b'%PDF-fake', log='')
        return CompileResult(success=False, pdf_data=b'', log='pdflatex error')


class FakeRenderer(DocumentRenderer):
    def render(self, deck: CardDeck) -> str:
        return '\\documentclass{article}\\begin{document}fake\\end{document}'


def _create_test_app(compiler_success=True):
    """Создаёт Flask app с правильными путями к шаблонам blueprint."""
    # Корень пакета didactic_cards/web — там лежат templates/
    web_dir = os.path.join(os.path.dirname(__file__),
                           '..', 'didactic_cards', 'web')
    web_dir = os.path.abspath(web_dir)

    app = Flask(__name__,
                template_folder=os.path.join(web_dir, 'templates'))
    app.secret_key = 'test-secret'
    app.config['TESTING'] = True

    app.config['REPO'] = FlaskSessionRepository()
    app.config['RENDERER'] = FakeRenderer()
    app.config['COMPILER'] = FakeCompiler(success=compiler_success)
    app.config['CARDS_PER_PAGE'] = 8

    app.register_blueprint(cards_bp, url_prefix='/')

    return app


@pytest.fixture
def app():
    return _create_test_app(compiler_success=True)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def app_fail_compiler():
    return _create_test_app(compiler_success=False)