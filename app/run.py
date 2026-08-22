from pathlib import Path

from flask import Flask
from config import AppConfig as Config
from didactic_cards.adapters.latex_renderer import LatexRenderer
from didactic_cards.adapters.pdflatex_compiler import PdfLatexCompiler
from didactic_cards.adapters.sqlite_repository import SqliteRepository
from didactic_cards.web.blueprint import cards_bp
from didactic_cards.web.observability import configure_json_logging


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
    app.debug = cfg.debug
    configure_json_logging(app.logger)

    repo = repo if repo is not None else SqliteRepository(
        data_dir=data_dir if data_dir is not None else cfg.data_dir
    )

    layout = cfg.layout

    def build_renderer(profile=None):
        if renderer is not None:
            return renderer
        profile = profile or layout
        return LatexRenderer(
            card_width_cm=layout.card_width_cm,
            card_height_cm=layout.card_height_cm,
            cards_per_row=layout.cards_per_row,
            rows_per_page=layout.rows_per_page,
            fbox_sep_pt=layout.fbox_sep_pt,
            fbox_rule_pt=layout.fbox_rule_pt,
            back_border=profile.back_border,
            duplex_mode=profile.duplex_mode,
            back_rotation_deg=profile.back_rotation_deg,
            front_offset_x_mm=profile.front_offset_x_mm,
            front_offset_y_mm=profile.front_offset_y_mm,
            back_offset_x_mm=profile.back_offset_x_mm,
            back_offset_y_mm=profile.back_offset_y_mm,
            registration_marks=profile.registration_marks,
            auto_fit=layout.auto_fit,
        )

    app.config['REPO'] = repo
    app.config['INTEGRITY_REPORT'] = getattr(repo, 'integrity_report', None)
    app.config['RENDERER'] = build_renderer()
    app.config['RENDERER_FACTORY'] = build_renderer
    app.config['PRINT_PROFILES'] = {
        profile.key: profile for profile in cfg.printer_profiles
    }
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
    runtime_config = Config()
    app = create_app(runtime_config)
    app.run(debug=runtime_config.debug)
