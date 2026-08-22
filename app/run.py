from flask import Flask
from config import AppConfig as Config
from didactic_cards.adapters.json_repository import JsonRepository
from didactic_cards.adapters.latex_renderer import LatexRenderer
from didactic_cards.adapters.pdflatex_compiler import PdfLatexCompiler
from didactic_cards.web.blueprint import cards_bp


def create_app() -> Flask:
    cfg = Config()
    app = Flask(__name__)
    app.secret_key = cfg.secret_key

    repo = JsonRepository(data_dir='data')

    layout = cfg.layout
    app.config['REPO'] = repo
    app.config['RENDERER'] = LatexRenderer(
        card_width_cm=layout.card_width_cm,
        card_height_cm=layout.card_height_cm,
        cards_per_row=layout.cards_per_row,
        rows_per_page=layout.rows_per_page,
        fbox_sep_pt=layout.fbox_sep_pt,
        fbox_rule_pt=layout.fbox_rule_pt,
        back_border=layout.back_border,
        duplex_mode=layout.duplex_mode,
    )
    app.config['COMPILER'] = PdfLatexCompiler(
        pdflatex_path=cfg.pdflatex_path,
        timeout=cfg.pdflatex_timeout,
    )
    app.config['CARDS_PER_PAGE'] = layout.cards_per_page

    app.register_blueprint(cards_bp)
    return app


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)
