from __future__ import annotations

from pathlib import Path

import pytest

from config import AppConfig, CardLayoutConfig
from run import create_app


def test_default_layout_fits_inside_a4_printable_area():
    layout = CardLayoutConfig()
    pt_cm = 2.54 / 72.27
    rule_pt = 0.4
    outer_width = layout.card_width_cm + 2 * (layout.fbox_sep_pt + rule_pt) * pt_cm
    outer_height = layout.card_height_cm + 2 * (layout.fbox_sep_pt + rule_pt) * pt_cm + 2 * pt_cm

    assert layout.cards_per_row * outer_width <= 20.0
    assert layout.rows_per_page * outer_height <= 28.7
    assert layout.cards_per_page == 8


def test_create_app_registers_required_services(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = create_app()
    assert {'REPO', 'RENDERER', 'COMPILER', 'CARDS_PER_PAGE'} <= app.config.keys()
    assert app.url_map.bind('').match('/')[0] == 'cards.decks_list'
    assert (tmp_path / 'data' / 'decks.json').exists()


@pytest.mark.xfail(
    strict=True,
    reason='BUG-CONF-001: data_dir is relative to process CWD, so launch location changes the database',
)
def test_data_location_is_independent_of_current_working_directory(tmp_path, monkeypatch):
    first_cwd = tmp_path / 'one'
    second_cwd = tmp_path / 'two'
    first_cwd.mkdir()
    second_cwd.mkdir()

    monkeypatch.chdir(first_cwd)
    first_path = create_app().config['REPO'].decks_file.resolve()
    monkeypatch.chdir(second_cwd)
    second_path = create_app().config['REPO'].decks_file.resolve()
    assert first_path == second_path


@pytest.mark.xfail(
    strict=True,
    reason='BUG-CONF-002: invalid/oversized layouts are accepted without fail-fast validation',
)
def test_layout_rejects_values_that_cannot_fit_a4():
    with pytest.raises(ValueError):
        CardLayoutConfig(card_width_cm=20, cards_per_row=2)


@pytest.mark.xfail(
    strict=True,
    reason='BUG-CONF-003: the factory cannot receive test/deployment configuration',
)
def test_app_factory_accepts_configuration_override(tmp_path):
    config = AppConfig(secret_key='override')
    app = create_app(config=config, data_dir=tmp_path / 'data')
    assert app.secret_key == 'override'
    assert app.config['REPO'].data_dir == Path(tmp_path / 'data')
