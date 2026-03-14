from flask import Flask
from config import AppConfig
from didactic_cards.adapters.session_repository import FlaskSessionRepository
from didactic_cards.adapters.latex_renderer import LatexRenderer
from didactic_cards.adapters.pdflatex_compiler import PdfLatexCompiler
from didactic_cards.web.blueprint import cards_bp, init_blueprint


def create_app(config: AppConfig = None) -> Flask:
    """Фабрика приложения Flask."""
    if config is None:
        config = AppConfig()

    app = Flask(__name__)
    app.secret_key = config.secret_key

    # Создаём зависимости
    repo = FlaskSessionRepository()

    renderer = LatexRenderer(
        card_width_cm=config.layout.card_width_cm,
        card_height_cm=config.layout.card_height_cm,
        cards_per_row=config.layout.cards_per_row,
        rows_per_page=config.layout.rows_per_page,
        fbox_sep_pt=config.layout.fbox_sep_pt,
    )

    compiler = PdfLatexCompiler(
        pdflatex_path=config.pdflatex_path,
        timeout=config.pdflatex_timeout,
    )

    # Инжектим зависимости в Blueprint
    init_blueprint(repo, renderer, compiler, config.layout.cards_per_page)

    # Регистрируем Blueprint
    app.register_blueprint(cards_bp, url_prefix='/')

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)