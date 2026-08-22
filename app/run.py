from pathlib import Path

from flask import Flask
from config import AppConfig as Config
from didactic_cards.adapters.latex_renderer import LatexRenderer
from didactic_cards.adapters.pdflatex_compiler import PdfLatexCompiler
from didactic_cards.adapters.sqlite_repository import SqliteRepository
from didactic_cards.web.blueprint import cards_bp


def create_app(
    config: Config | None = None,
    *,
    data_dir: str | Path | None = None,
    repo=None,
    renderer=None,
    compiler=None,
) -> Flask:
    """Create an application with optional deployment/test dependencies."""
    cfg = config or Config()
    app = Flask(__name__)
    app.secret_key = cfg.secret_key

    repo = repo if repo is not None else SqliteRepository(
        data_dir=data_dir if data_dir is not None else cfg.data_dir
    )

    layout = cfg.layout
    app.config['REPO'] = repo
    app.config['INTEGRITY_REPORT'] = getattr(repo, 'integrity_report', None)
    app.config['RENDERER'] = renderer if renderer is not None else LatexRenderer(
        card_width_cm=layout.card_width_cm,
        card_height_cm=layout.card_height_cm,
        cards_per_row=layout.cards_per_row,
        rows_per_page=layout.rows_per_page,
        fbox_sep_pt=layout.fbox_sep_pt,
        fbox_rule_pt=layout.fbox_rule_pt,
        back_border=layout.back_border,
        duplex_mode=layout.duplex_mode,
        front_offset_x_mm=layout.front_offset_x_mm,
        front_offset_y_mm=layout.front_offset_y_mm,
        back_offset_x_mm=layout.back_offset_x_mm,
        back_offset_y_mm=layout.back_offset_y_mm,
        registration_marks=layout.registration_marks,
    )
    app.config['COMPILER'] = compiler if compiler is not None else PdfLatexCompiler(
        pdflatex_path=cfg.pdflatex_path,
        timeout=cfg.pdflatex_timeout,
    )
    app.config['CARDS_PER_PAGE'] = layout.cards_per_page
    app.config['MAX_CARDS'] = cfg.max_cards
    app.config['MAX_CONTENT_LENGTH'] = cfg.max_request_bytes
    app.config['CSRF_ENABLED'] = cfg.csrf_enabled

    app.register_blueprint(cards_bp)
    return app


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)
