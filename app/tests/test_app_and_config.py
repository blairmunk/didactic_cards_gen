from __future__ import annotations

from pathlib import Path

import pytest

from config import AppConfig, CardLayoutConfig
from didactic_cards.domain.printing import PrinterProfile
from run import create_app


def test_default_layout_fits_inside_a4_printable_area():
    layout = CardLayoutConfig()
    pt_cm = 2.54 / 72.27
    outer_width = layout.card_width_cm
    outer_height = layout.card_height_cm + 2 * pt_cm

    assert layout.cards_per_row * outer_width <= 20.0
    assert layout.rows_per_page * outer_height <= 28.7
    assert layout.cards_per_page == 8


def test_create_app_registers_required_services(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = create_app(data_dir=tmp_path / 'data')
    assert {
        'REPO', 'RENDERER', 'RENDERER_FACTORY', 'PRINT_PROFILES',
        'COMPILER', 'CARDS_PER_PAGE', 'TRUSTED_LATEX_ENABLED',
        'TRUSTED_COMPILER',
    } <= app.config.keys()
    assert app.url_map.bind('').match('/')[0] == 'cards.decks_list'
    assert app.config['REPO'].database_file == (tmp_path / 'data' / 'cards.sqlite3')
    assert app.config['REPO'].database_file.exists()
    assert app.config['REPO'].integrity_check() == []


def test_data_location_is_independent_of_current_working_directory(tmp_path, monkeypatch):
    first_cwd = tmp_path / 'one'
    second_cwd = tmp_path / 'two'
    first_cwd.mkdir()
    second_cwd.mkdir()
    stable_data = tmp_path / 'stable-data'
    monkeypatch.setenv('DIDACTIC_CARDS_DATA_DIR', str(stable_data))

    monkeypatch.chdir(first_cwd)
    first_path = create_app().config['REPO'].database_file.resolve()
    monkeypatch.chdir(second_cwd)
    second_path = create_app().config['REPO'].database_file.resolve()
    assert first_path == second_path


def test_default_data_location_is_next_to_application(monkeypatch):
    monkeypatch.delenv('DIDACTIC_CARDS_DATA_DIR', raising=False)
    assert AppConfig().data_dir == Path(__file__).resolve().parents[1] / 'data'


@pytest.mark.parametrize('value', ['1', 'true', 'YES', 'on'])
def test_debug_environment_accepts_explicit_true(monkeypatch, value):
    monkeypatch.setenv('DIDACTIC_CARDS_DEBUG', value)
    assert AppConfig().debug is True


@pytest.mark.parametrize('value', ['0', 'false', 'NO', 'off'])
def test_debug_environment_accepts_explicit_false(monkeypatch, value):
    monkeypatch.setenv('DIDACTIC_CARDS_DEBUG', value)
    assert AppConfig().debug is False


def test_debug_environment_rejects_ambiguous_value(monkeypatch):
    monkeypatch.setenv('DIDACTIC_CARDS_DEBUG', 'perhaps')
    with pytest.raises(ValueError, match='DIDACTIC_CARDS_DEBUG'):
        AppConfig()


def test_trusted_latex_is_fail_closed_and_requires_explicit_boolean_env(
    monkeypatch,
):
    monkeypatch.delenv('DIDACTIC_CARDS_TRUSTED_LATEX_ENABLED', raising=False)
    assert AppConfig().trusted_latex_enabled is False
    monkeypatch.setenv('DIDACTIC_CARDS_TRUSTED_LATEX_ENABLED', 'true')
    assert AppConfig().trusted_latex_enabled is True
    monkeypatch.setenv('DIDACTIC_CARDS_TRUSTED_LATEX_ENABLED', 'maybe')
    with pytest.raises(ValueError, match='TRUSTED_LATEX'):
        AppConfig()


def test_trusted_latex_configuration_builds_dedicated_sandbox(tmp_path):
    config = AppConfig(
        trusted_latex_enabled=True,
        bwrap_path='/missing/bwrap',
    )
    app = create_app(config=config, data_dir=tmp_path / 'trusted')

    assert app.config['TRUSTED_LATEX_ENABLED'] is True
    assert app.config['TRUSTED_COMPILER'] is not app.config['COMPILER']
    assert app.config['TRUSTED_COMPILER'].is_available() is False


@pytest.mark.parametrize(
    ('kwargs', 'message'),
    [
        ({'trusted_latex_enabled': 1}, 'feature flag'),
        ({'trusted_pdflatex_timeout': 0}, 'timeout'),
        ({'trusted_pdflatex_timeout': True}, 'timeout'),
    ],
)
def test_trusted_latex_configuration_rejects_unsafe_values(kwargs, message):
    with pytest.raises(ValueError, match=message):
        AppConfig(**kwargs)


def test_debug_configuration_must_be_boolean():
    with pytest.raises(ValueError, match='debug'):
        AppConfig(debug=1)


def test_app_debug_is_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv('DIDACTIC_CARDS_DEBUG', raising=False)
    app = create_app(data_dir=tmp_path / 'data')
    assert app.debug is False


@pytest.mark.parametrize(
    'kwargs',
    [
        {'cards_per_row': 0},
        {'rows_per_page': 0},
        {'card_width_cm': float('inf')},
        {'card_height_cm': 0},
        {'fbox_sep_pt': -1},
        {'card_width_cm': 0.1, 'fbox_sep_pt': 8},
        {'card_width_cm': 20, 'cards_per_row': 2},
        {'card_height_cm': 10, 'rows_per_page': 3},
        {'back_offset_x_mm': 11},
        {'front_offset_y_mm': float('nan')},
        {'duplex_mode': 'diagonal'},
        {'back_rotation_deg': 90},
        {'back_rotation_deg': False},
        {'auto_fit': 1},
    ],
)
def test_layout_rejects_invalid_or_oversized_values(kwargs):
    with pytest.raises(ValueError):
        CardLayoutConfig(**kwargs)


def test_app_factory_accepts_configuration_override(tmp_path):
    config = AppConfig(secret_key='override')
    app = create_app(config=config, data_dir=tmp_path / 'data')
    assert app.secret_key == 'override'
    assert app.config['REPO'].data_dir == Path(tmp_path / 'data')


def test_builtin_profiles_restore_legacy_long_edge_rotation():
    profiles = {profile.key: profile for profile in AppConfig().printer_profiles}
    assert profiles['standard-long-edge'].back_rotation_deg == 180
    assert profiles['calibration-long-edge'].back_rotation_deg == 180
    assert profiles['standard-short-edge'].back_rotation_deg == 0


@pytest.mark.parametrize(
    'kwargs',
    [
        {'key': 'Upper', 'name': 'Name'},
        {'key': 'valid', 'name': ''},
        {'key': 'valid', 'name': 'x' * 101},
        {'key': 'valid', 'name': 'Name', 'back_offset_x_mm': 11},
        {'key': 'valid', 'name': 'Name', 'front_offset_y_mm': float('nan')},
        {'key': 'valid', 'name': 'Name', 'duplex_mode': 'diagonal'},
        {'key': 'valid', 'name': 'Name', 'back_rotation_deg': 90},
        {'key': 'valid', 'name': 'Name', 'back_rotation_deg': True},
        {'key': 'valid', 'name': 'Name', 'back_border': 1},
        {'key': 'valid', 'name': 'Name', 'registration_marks': 'yes'},
    ],
)
def test_printer_profile_rejects_invalid_values(kwargs):
    with pytest.raises(ValueError):
        PrinterProfile(**kwargs)


def test_app_config_rejects_duplicate_profile_keys():
    with pytest.raises(ValueError, match='unique'):
        AppConfig(printer_profiles=(
            PrinterProfile('same', 'First'),
            PrinterProfile('same', 'Second'),
        ))


def test_renderer_factory_creates_isolated_profile_renderer(tmp_path):
    profile = PrinterProfile(
        'office-printer',
        'Office printer',
        duplex_mode='short-edge',
        back_rotation_deg=0,
        back_offset_x_mm=1.25,
        registration_marks=True,
    )
    app = create_app(
        config=AppConfig(printer_profiles=(profile,)),
        data_dir=tmp_path / 'data',
    )

    first = app.config['RENDERER_FACTORY'](profile)
    second = app.config['RENDERER_FACTORY'](profile)

    assert first is not second
    assert first.back_offset == (1.25, 0.0)
    assert first.duplex_mode.value == 'short-edge'
    assert first.back_rotation_deg == 0
    assert first.registration_marks is True
    assert list(app.config['PRINT_PROFILES']) == ['office-printer']
