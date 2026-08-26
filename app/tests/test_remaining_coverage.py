"""Focused tests for defensive branches that are easy to miss end-to-end."""

from __future__ import annotations

import io
import runpy
import stat
from pathlib import Path
from types import SimpleNamespace

from flask import Flask
import pytest

import config as config_module
from config import AppConfig, load_or_create_local_secret
from didactic_cards.adapters import sandboxed_pdflatex_compiler as sandbox_module
from didactic_cards.adapters.latex_renderer import UnsafeLatexError
from didactic_cards.adapters.sandboxed_pdflatex_compiler import (
    SandboxedPdfLatexCompiler,
)
from didactic_cards.adapters.repository_errors import DeckNotFoundError
from didactic_cards.domain.interfaces import CompileResult
from didactic_cards.domain.rendering import DeckRenderSettings
from didactic_cards.use_cases.card_use_cases import (
    AddCard,
    PreflightDocument,
    compile_error_context,
)
from didactic_cards.web import blueprint as blueprint_module
from run import create_app


def test_local_secret_works_without_nofollow_platform_flag(tmp_path, monkeypatch):
    monkeypatch.delattr(config_module.os, 'O_NOFOLLOW', raising=False)

    secret = load_or_create_local_secret(tmp_path / 'data')

    assert secret
    assert (tmp_path / 'data' / '.secret_key').read_text() == secret


def test_local_secret_rejects_non_regular_open_target(tmp_path, monkeypatch):
    monkeypatch.setattr(
        config_module.os,
        'fstat',
        lambda _descriptor: SimpleNamespace(st_mode=stat.S_IFDIR),
    )

    with pytest.raises(RuntimeError, match='regular file'):
        load_or_create_local_secret(tmp_path / 'data')


def test_sandbox_classifies_ordinary_tex_failure(monkeypatch, tmp_path):
    compiler = SandboxedPdfLatexCompiler(temp_root=tmp_path)
    monkeypatch.setattr(compiler, 'is_available', lambda: True)

    def fail_normally(_command, **kwargs):
        kwargs['stdout'].write(b'ordinary TeX error')
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(sandbox_module.subprocess, 'run', fail_normally)

    result = compiler.compile(r'\documentclass{article}')

    assert result.error_kind == 'compile-error'
    assert result.log == 'ordinary TeX error'


def test_sandbox_prefix_skips_absent_optional_host_paths(monkeypatch, tmp_path):
    compiler = SandboxedPdfLatexCompiler(temp_root=tmp_path)
    original_exists = Path.exists

    def optional_paths_absent(path):
        if str(path).startswith(('/etc/', '/var/')):
            return False
        return original_exists(path)

    monkeypatch.setattr(Path, 'exists', optional_paths_absent)

    command = compiler._sandbox_prefix(tmp_path)

    assert '/etc/texmf' not in command
    assert '/var/lib/texmf' not in command
    assert command[-2:] == ['PATH', '/usr/bin:/bin']


def test_sandbox_applies_every_resource_limit(monkeypatch):
    applied = []
    monkeypatch.setattr(
        sandbox_module.resource,
        'setrlimit',
        lambda resource_id, limits: applied.append((resource_id, limits)),
    )
    compiler = SandboxedPdfLatexCompiler(
        timeout=7,
        memory_limit_mb=64,
        output_limit_mb=2,
        process_limit=3,
    )

    compiler._apply_limits()

    assert applied == [
        (sandbox_module.resource.RLIMIT_CPU, (7, 8)),
        (sandbox_module.resource.RLIMIT_AS, (64 * 1024 * 1024,) * 2),
        (sandbox_module.resource.RLIMIT_FSIZE, (2 * 1024 * 1024,) * 2),
        (sandbox_module.resource.RLIMIT_NPROC, (3, 3)),
        (sandbox_module.resource.RLIMIT_NOFILE, (64, 64)),
    ]


def test_compile_context_ignores_marker_outside_real_deck(repo, deck_id):
    deck = repo.load_cards(deck_id)

    assert compile_error_context(
        'DIDACTIC-CARDS-HBOX-BEGIN:99:front:body', deck
    ) is None


def test_preflight_uses_generic_failure_when_log_has_no_card_context(
    repo, deck_id, app
):
    AddCard(repo).execute(deck_id, 'Q', 'A')

    class Compiler:
        def compile(self, _source):
            return CompileResult(False, b'', 'generic failure', 'compile-error')

    report = PreflightDocument(
        repo, app.config['RENDERER'], Compiler(), 8
    ).execute(deck_id)
    issue = next(item for item in report.issues if item.code == 'compile-failed')

    assert issue.card_id is None
    assert issue.card_number is None
    assert issue.side is None
    assert issue.message.startswith('Документ не компилируется')


def test_preflight_deduplicates_markers_and_ignores_padding_slots(
    repo, deck_id, app
):
    card, _ = AddCard(repo).execute(deck_id, 'Q', 'A')

    class Compiler:
        def compile(self, _source):
            return CompileResult(
                True,
                b'%PDF',
                'DIDACTIC-CARDS-OVERFLOW:9:front\n'
                'DIDACTIC-CARDS-OVERFLOW:1:front\n'
                'DIDACTIC-CARDS-OVERFLOW:1:front\n'
                'DIDACTIC-CARDS-AUTOFIT:0:back:small\n'
                'DIDACTIC-CARDS-AUTOFIT:1:back:small\n'
                'DIDACTIC-CARDS-AUTOFIT:1:back:small\n',
            )

    report = PreflightDocument(
        repo, app.config['RENDERER'], Compiler(), 8
    ).execute(deck_id)

    overflow = [item for item in report.issues if item.code == 'vertical-overflow']
    auto_fit = [item for item in report.issues if item.code == 'auto-fit']
    assert [(item.card_id, item.side) for item in overflow] == [(card.id, 'front')]
    assert [(item.card_id, item.side) for item in auto_fit] == [(card.id, 'back')]


def test_profile_helpers_and_missing_deck_defences(app, repo):
    with app.test_request_context(
        '/printer_profiles/save',
        method='POST',
        data={'back_rotation_deg': 'not-an-angle'},
    ):
        with pytest.raises(ValueError, match='0° или 180°'):
            blueprint_module._profile_back_rotation()

    with app.app_context():
        app.config['PRINT_PROFILES'] = {'configured': SimpleNamespace(key='configured')}
        app.config['REPO'] = object()
        assert [profile.key for profile in blueprint_module._print_profiles()] == [
            'configured'
        ]

        app.config['REPO'] = repo
        with pytest.raises(DeckNotFoundError):
            blueprint_module._require_advanced_deck('missing')


def test_disabled_trusted_mode_strips_template_from_advanced_snapshot(
    app, repo
):
    deck = repo.create_deck(
        'Raw', render_settings=DeckRenderSettings(authoring_mode='advanced')
    )
    AddCard(repo).execute(deck.id, r'\textbf{Q}', 'A')
    app.config['TRUSTED_LATEX_ENABLED'] = False
    app.config['TRUSTED_COMPILER'] = None

    with app.app_context():
        compiler, template, snapshot = (
            blueprint_module._print_compiler_and_template(deck.id)
        )
        result = compiler.compile('ignored')

    assert template is None
    assert snapshot is not None
    assert snapshot.trusted_template is None
    assert result.error_kind == 'unavailable'


def test_disabled_trusted_mode_supports_repository_without_snapshot_api(app):
    class RepositoryWithoutSnapshots:
        def get_render_settings(self, _deck_id):
            return DeckRenderSettings(authoring_mode='advanced')

    app.config['REPO'] = RepositoryWithoutSnapshots()
    app.config['TRUSTED_LATEX_ENABLED'] = False
    app.config['TRUSTED_COMPILER'] = None

    with app.app_context():
        compiler, template, snapshot = (
            blueprint_module._print_compiler_and_template('raw-deck')
        )

    assert template is None
    assert snapshot is None
    assert compiler.compile('ignored').error_kind == 'unavailable'


def test_readiness_fallback_and_throwing_trusted_probe(client, app):
    app.config['REPO'] = SimpleNamespace(
        integrity_report=SimpleNamespace(healthy=True)
    )
    app.config['COMPILER'] = object()

    assert client.get('/health/ready').status_code == 200

    class ThrowingSandbox:
        def readiness_check(self):
            raise RuntimeError('private probe detail')

    app.config['TRUSTED_LATEX_ENABLED'] = True
    app.config['TRUSTED_COMPILER'] = ThrowingSandbox()
    response = client.get('/health/ready')

    assert response.status_code == 503
    assert response.json['components']['trusted-tex-sandbox'] == 'unavailable'
    assert 'private probe detail' not in response.text


def test_csrf_protection_explicitly_bypasses_api_routes(client, app):
    app.config['CSRF_ENABLED'] = True

    response = client.post('/api/deck/missing/preview_bulk', data={'bulk': 'Q||A'})

    assert response.status_code == 404
    assert response.json == {'error': 'Колода не найдена'}


def test_optional_repository_and_renderer_capabilities_fail_explicitly(
    client, app, repo
):
    response = client.post('/printer_profiles/calibration-sheet')
    assert response.status_code == 501

    app.config['REPO'] = object()
    assert client.post('/printer_profiles/save').status_code == 501
    assert client.post('/printer_profiles/custom/delete').status_code == 501

    app.config['REPO'] = repo
    missing_template = client.get('/deck/missing/import-template.csv')
    assert missing_template.status_code == 302
    assert missing_template.headers['Location'].endswith('/')


@pytest.mark.parametrize(
    ('error_kind', 'expected_status'),
    [
        ('timeout', 504),
        ('validation', 422),
        ('compile-error', 422),
        ('unexpected-kind', 500),
    ],
)
def test_calibration_compile_failures_map_to_safe_status(
    client, app, error_kind, expected_status, monkeypatch
):
    class Renderer:
        def render_calibration_sheet(self):
            return 'calibration source'

    class Compiler:
        def compile(self, source):
            assert source == 'calibration source'
            return CompileResult(False, b'', 'private detail', error_kind)

    app.config['RENDERER'] = Renderer()
    app.config['COMPILER'] = Compiler()
    events = []
    monkeypatch.setattr(
        app.logger,
        'info',
        lambda message, *, extra: events.append((message, extra)),
    )

    response = client.post('/printer_profiles/calibration-sheet')

    assert response.status_code == expected_status
    assert 'Не удалось сформировать калибровочный PDF' in response.text
    assert 'private detail' not in response.text
    metrics = [
        extra for message, extra in events if message == 'pdf_compilation'
    ]
    assert len(metrics) == 1
    assert metrics[0]['request_id'] == response.headers['X-Request-ID']
    assert metrics[0]['job_kind'] == 'calibration'
    assert metrics[0]['profile_id'] == 'base'
    assert metrics[0]['status'] == 'failure'
    assert metrics[0]['error_kind'] == error_kind
    assert '/private' not in repr(metrics[0])


def test_calibration_render_validation_is_logged_without_private_details(
    client, app, monkeypatch
):
    class Renderer:
        def render_calibration_sheet(self):
            raise UnsafeLatexError('/private/calibration-source.tex')

    events = []
    app.config['RENDERER'] = Renderer()
    monkeypatch.setattr(
        app.logger,
        'info',
        lambda message, *, extra: events.append((message, extra)),
    )

    response = client.post('/printer_profiles/calibration-sheet')

    assert response.status_code == 422
    assert 'Калибровочный лист не прошёл проверку' in response.text
    assert '/private' not in response.text
    metrics = [
        extra for message, extra in events if message == 'pdf_compilation'
    ]
    assert len(metrics) == 1
    assert metrics[0]['request_id'] == response.headers['X-Request-ID']
    assert metrics[0]['status'] == 'failure'
    assert metrics[0]['error_kind'] == 'validation'
    assert '/private' not in repr(metrics[0])


def test_add_card_limit_is_reported_on_html_route(client, app, deck_id):
    app.config['MAX_CARDS'] = 0

    response = client.post(
        f'/deck/{deck_id}/add_card', data={'front': 'Q', 'back': 'A'}
    )

    assert response.status_code == 409
    assert 'Максимум карточек' in response.text


def test_bulk_preview_contains_parser_value_errors(
    client, monkeypatch, deck_id
):
    def invalid_bulk(*_args, **_kwargs):
        raise ValueError('invalid bulk options')

    monkeypatch.setattr(
        blueprint_module, 'preview_bulk_import', invalid_bulk
    )

    response = client.post(
        f'/api/deck/{deck_id}/preview_bulk', data={'bulk': 'Q||A'}
    )

    assert response.status_code == 400
    assert response.json == {'error': 'invalid bulk options'}


def test_csv_import_renders_encoding_failure_without_losing_deck_context(
    client, monkeypatch, deck_id
):
    class ImportWithBadEncoding:
        def __init__(self, *_args, **_kwargs):
            pass

        def execute(self, *_args, **_kwargs):
            raise UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'invalid')

    monkeypatch.setattr(blueprint_module, 'ImportCsv', ImportWithBadEncoding)
    monkeypatch.setattr(
        blueprint_module, '_valid_preview_token', lambda *_args: True
    )

    response = client.post(
        f'/deck/{deck_id}/import_csv',
        data={
            'preview_token': 'accepted',
            'csv_file': (io.BytesIO(b'front,back\n\xff,A'), 'cards.csv'),
        },
        content_type='multipart/form-data',
    )

    assert response.status_code == 200
    assert 'Ошибка кодировки' in response.text


def test_csv_import_reports_validation_value_error(
    client, monkeypatch, deck_id
):
    class InvalidImport:
        def __init__(self, *_args, **_kwargs):
            pass

        def execute(self, *_args, **_kwargs):
            raise ValueError('invalid delimiter configuration')

    monkeypatch.setattr(blueprint_module, 'ImportCsv', InvalidImport)
    monkeypatch.setattr(
        blueprint_module, '_valid_preview_token', lambda *_args: True
    )

    response = client.post(
        f'/deck/{deck_id}/import_csv',
        data={
            'preview_token': 'accepted',
            'csv_file': (io.BytesIO(b'front,back\nQ,A'), 'cards.csv'),
        },
        content_type='multipart/form-data',
    )

    assert response.status_code == 400
    assert 'invalid delimiter configuration' in response.text


def test_app_factory_reuses_injected_renderer(app, repo, tmp_path):
    renderer = app.config['RENDERER']
    created = create_app(
        config=AppConfig(secret_key='fixed'),
        data_dir=tmp_path / 'injected',
        repo=repo,
        renderer=renderer,
        compiler=app.config['COMPILER'],
    )

    assert created.config['RENDERER'] is renderer
    assert created.config['RENDERER_FACTORY'](object()) is renderer


def test_run_module_entrypoint_uses_runtime_debug_setting(tmp_path, monkeypatch):
    class RuntimeConfig(AppConfig):
        def __init__(self):
            super().__init__(
                data_dir=tmp_path / 'entrypoint-data',
                secret_key='entrypoint-secret',
                debug=False,
            )

    monkeypatch.setattr(config_module, 'AppConfig', RuntimeConfig)
    calls = []
    monkeypatch.setattr(
        Flask, 'run', lambda _app, **kwargs: calls.append(kwargs)
    )

    namespace = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / 'run.py'),
        run_name='__main__',
    )

    assert calls == [{'debug': False}]
    namespace['app'].config['REPO'].close()
