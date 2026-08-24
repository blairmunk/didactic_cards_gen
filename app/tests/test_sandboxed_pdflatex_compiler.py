from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from didactic_cards.adapters.sandboxed_pdflatex_compiler import (
    SandboxedPdfLatexCompiler,
)
from didactic_cards.adapters.latex_renderer import LatexRenderer
from didactic_cards.domain.entities import Card, CardDeck
from didactic_cards.domain.rendering import DeckRenderSettings
from didactic_cards.domain.trusted import (
    TrustedCompileJob,
    TrustedTemplateVersion,
)


SIMPLE_DOCUMENT = r'''\documentclass{article}
\begin{document}
Sandbox works.
\end{document}
'''


def test_sandbox_command_is_deny_by_default_and_mounts_only_job_workspace(tmp_path):
    compiler = SandboxedPdfLatexCompiler(temp_root=tmp_path)
    job_dir = tmp_path / 'job'
    job_dir.mkdir()

    command = compiler._sandbox_command(job_dir)

    assert '--unshare-all' in command
    assert '--clearenv' in command
    assert '--die-with-parent' in command
    assert '--new-session' in command
    assert ['--bind', str(job_dir), '/work'] == command[
        command.index('--bind'):command.index('--bind') + 3
    ]
    assert '/home' not in command
    assert str(Path(__file__).parents[2]) not in command
    assert '-no-shell-escape' in command


def test_compiler_fails_closed_without_bwrap_or_pdflatex(tmp_path):
    compiler = SandboxedPdfLatexCompiler(
        bwrap_path='/missing/bwrap', temp_root=tmp_path
    )

    result = compiler.compile(SIMPLE_DOCUMENT)

    assert result.success is False
    assert result.error_kind == 'unavailable'
    assert compiler.readiness_check() is False
    assert list(tmp_path.iterdir()) == []


def test_readiness_probes_namespaces_and_cleans_workspace(monkeypatch, tmp_path):
    compiler = SandboxedPdfLatexCompiler(temp_root=tmp_path)
    monkeypatch.setattr(compiler, 'is_available', lambda: True)

    def complete(command, **_kwargs):
        assert '--unshare-all' in command
        assert command[-1] == '/bin/true'
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr('subprocess.run', complete)

    assert compiler.readiness_check() is True
    assert list(tmp_path.iterdir()) == []


def test_compiler_validates_job_type_source_and_resource_limits(tmp_path):
    with pytest.raises(ValueError, match='timeout'):
        SandboxedPdfLatexCompiler(timeout=0)
    with pytest.raises(ValueError, match='timeout'):
        SandboxedPdfLatexCompiler(timeout=True)
    with pytest.raises(ValueError, match='limits'):
        SandboxedPdfLatexCompiler(memory_limit_mb=32)
    with pytest.raises(ValueError, match='limits'):
        SandboxedPdfLatexCompiler(output_limit_mb=True)
    compiler = SandboxedPdfLatexCompiler(temp_root=tmp_path)
    with pytest.raises(TypeError, match='TrustedCompileJob'):
        compiler.compile_job(object())
    assert compiler.compile('').error_kind == 'validation'


@pytest.mark.parametrize('failure', [OSError('denied'), TimeoutError('late')])
def test_readiness_fails_closed_on_probe_error(monkeypatch, tmp_path, failure):
    compiler = SandboxedPdfLatexCompiler(temp_root=tmp_path)
    monkeypatch.setattr(compiler, 'is_available', lambda: True)
    def fail(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr('subprocess.run', fail)

    assert compiler.readiness_check() is False
    assert list(tmp_path.iterdir()) == []


def test_readiness_fails_closed_on_nonzero_probe(monkeypatch, tmp_path):
    compiler = SandboxedPdfLatexCompiler(temp_root=tmp_path)
    monkeypatch.setattr(compiler, 'is_available', lambda: True)
    monkeypatch.setattr(
        'subprocess.run',
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
    )

    assert compiler.readiness_check() is False


def test_process_start_error_is_sanitized(monkeypatch, tmp_path):
    compiler = SandboxedPdfLatexCompiler(temp_root=tmp_path)
    monkeypatch.setattr(compiler, 'is_available', lambda: True)

    def fail(*_args, **_kwargs):
        raise PermissionError('/private/host/path')

    monkeypatch.setattr('subprocess.run', fail)
    result = compiler.compile(SIMPLE_DOCUMENT)

    assert result.error_kind == 'sandbox-error'
    assert '/private/host/path' not in result.log
    assert list(tmp_path.iterdir()) == []


def test_subprocess_timeout_is_classified_and_workspace_is_cleaned(
    monkeypatch, tmp_path
):
    compiler = SandboxedPdfLatexCompiler(temp_root=tmp_path)
    monkeypatch.setattr(compiler, 'is_available', lambda: True)

    def timeout(*_args, **_kwargs):
        import subprocess
        raise subprocess.TimeoutExpired('bwrap', 1)

    monkeypatch.setattr('subprocess.run', timeout)
    result = compiler.compile(SIMPLE_DOCUMENT)

    assert result.error_kind == 'timeout'
    assert list(tmp_path.iterdir()) == []


def test_success_and_output_limit_paths(monkeypatch, tmp_path):
    compiler = SandboxedPdfLatexCompiler(
        temp_root=tmp_path, output_limit_mb=1
    )
    monkeypatch.setattr(compiler, 'is_available', lambda: True)

    def complete(command, **_kwargs):
        work = Path(command[command.index('--bind') + 1])
        (work / 'document.pdf').write_bytes(b'%PDF')
        (work / 'document.log').write_text('ok', encoding='utf-8')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr('subprocess.run', complete)
    result = compiler.compile(SIMPLE_DOCUMENT)
    assert result.success is True
    assert result.pdf_data == b'%PDF'
    assert result.log == 'ok'

    def oversized(command, **_kwargs):
        work = Path(command[command.index('--bind') + 1])
        (work / 'document.pdf').write_bytes(b'x' * (1024 * 1024 + 1))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr('subprocess.run', oversized)
    assert compiler.compile(SIMPLE_DOCUMENT).error_kind == 'output-limit'
    assert list(tmp_path.iterdir()) == []


def test_process_output_is_file_bounded_and_classifies_bwrap_failure(
    monkeypatch, tmp_path
):
    compiler = SandboxedPdfLatexCompiler(temp_root=tmp_path)
    monkeypatch.setattr(compiler, 'is_available', lambda: True)

    def fail(_command, **kwargs):
        assert 'capture_output' not in kwargs
        kwargs['stdout'].write(b'bwrap: namespace denied')
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr('subprocess.run', fail)

    result = compiler.compile(SIMPLE_DOCUMENT)

    assert result.error_kind == 'sandbox-error'
    assert result.log == 'bwrap: namespace denied'
    assert list(tmp_path.iterdir()) == []


@pytest.mark.integration
def test_real_bwrap_compiles_without_home_project_or_network_mounts(tmp_path):
    if not shutil.which('bwrap') or not shutil.which('pdflatex'):
        pytest.skip('bubblewrap and pdflatex are required')
    compiler = SandboxedPdfLatexCompiler(
        bwrap_path=shutil.which('bwrap'),
        pdflatex_path=shutil.which('pdflatex'),
        temp_root=tmp_path,
    )

    assert compiler.readiness_check() is True
    result = compiler.compile(SIMPLE_DOCUMENT)

    assert result.success, result.log
    assert result.pdf_data.startswith(b'%PDF')
    assert list(tmp_path.iterdir()) == []


@pytest.mark.integration
def test_real_bwrap_compiles_advanced_template_with_contextual_headers(
    tmp_path,
):
    if not shutil.which('bwrap') or not shutil.which('pdflatex'):
        pytest.skip('bubblewrap and pdflatex are required')
    compiler = SandboxedPdfLatexCompiler(
        bwrap_path=shutil.which('bwrap'),
        pdflatex_path=shutil.which('pdflatex'),
        temp_root=tmp_path,
    )
    template = TrustedTemplateVersion(
        deck_id='deck',
        version=1,
        front_source=(
            r'{{ upper_header }}\par\vfill\centering {{ content }}\vfill'
            r'\par{{ lower_header }}'
        ),
        back_source=(
            r'{{ upper_header }}\par\vfill\centering {{ content }}\vfill'
            r'\par{{ lower_header }}'
        ),
        front_content_mode='raw',
        back_content_mode='raw',
    )
    renderer = LatexRenderer(
        cards_per_row=1,
        rows_per_page=1,
        render_settings=DeckRenderSettings(authoring_mode='advanced'),
        trusted_template=template,
    )
    latex = renderer.render(CardDeck([Card(
        front=r'Стоимость 10\% \& ответ',
        back=r'\textbf{Жирный ответ}',
        section='Тема & раздел',
        upper_header=r'\small {{ section }}',
        lower_header='Карточка {{ card_number }}/{{ card_count }}',
    )]))

    result = compiler.compile(latex)

    assert result.success, result.log
    assert result.pdf_data.startswith(b'%PDF')
    assert list(tmp_path.iterdir()) == []


@pytest.mark.integration
def test_real_bwrap_blocks_host_reads_and_host_tmp_writes(tmp_path):
    if not shutil.which('bwrap') or not shutil.which('pdflatex'):
        pytest.skip('bubblewrap and pdflatex are required')
    compiler = SandboxedPdfLatexCompiler(
        bwrap_path=shutil.which('bwrap'),
        pdflatex_path=shutil.which('pdflatex'),
        temp_root=tmp_path,
    )
    host_target = tmp_path / 'must-not-exist.txt'
    hostile_read = SIMPLE_DOCUMENT.replace(
        'Sandbox works.', r'\input{/etc/hostname}'
    )
    hostile_write = SIMPLE_DOCUMENT.replace(
        'Sandbox works.',
        rf'\newwrite\attack\immediate\openout\attack={host_target}'
        r'\immediate\write\attack{escaped}\closeout\attack safe',
    )

    read_result = compiler.compile(hostile_read)
    write_result = compiler.compile(hostile_write)

    assert read_result.success is False
    assert write_result.success or write_result.error_kind == 'compile-error'
    assert not host_target.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.integration
def test_real_bwrap_disables_shell_escape_and_hides_project(tmp_path):
    if not shutil.which('bwrap') or not shutil.which('pdflatex'):
        pytest.skip('bubblewrap and pdflatex are required')
    compiler = SandboxedPdfLatexCompiler(
        bwrap_path=shutil.which('bwrap'),
        pdflatex_path=shutil.which('pdflatex'),
        temp_root=tmp_path,
    )
    project_read = SIMPLE_DOCUMENT.replace(
        'Sandbox works.', rf'\input{{{Path(__file__).parents[2] / "README.md"}}}'
    )
    host_target = tmp_path / 'shell-escape-must-not-exist'
    shell_escape = SIMPLE_DOCUMENT.replace(
        'Sandbox works.', rf'\immediate\write18{{touch {host_target}}}safe'
    )

    read_result = compiler.compile(project_read)
    shell_result = compiler.compile(shell_escape)

    assert read_result.success is False
    assert shell_result.success is True
    assert not host_target.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.integration
def test_real_bwrap_stops_infinite_tex_and_cleans_workspace(tmp_path):
    if not shutil.which('bwrap') or not shutil.which('pdflatex'):
        pytest.skip('bubblewrap and pdflatex are required')
    compiler = SandboxedPdfLatexCompiler(
        bwrap_path=shutil.which('bwrap'),
        pdflatex_path=shutil.which('pdflatex'),
        timeout=1,
        temp_root=tmp_path,
    )
    recursive = SIMPLE_DOCUMENT.replace(
        'Sandbox works.', r'\def\forever{\forever}\forever'
    )

    result = compiler.compile(recursive)

    assert result.success is False
    assert result.error_kind in {'compile-error', 'timeout'}
    assert list(tmp_path.iterdir()) == []
