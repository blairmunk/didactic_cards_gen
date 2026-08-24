from __future__ import annotations

import os
import resource
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..domain.interfaces import CompileResult, PdfCompiler
from ..domain.trusted import TrustedCompileJob


class SandboxedPdfLatexCompiler(PdfCompiler):
    """Fail-closed trusted TeX compiler isolated with bubblewrap."""

    def __init__(
        self,
        *,
        pdflatex_path: str = '/usr/bin/pdflatex',
        bwrap_path: str = '/usr/bin/bwrap',
        timeout: int = 30,
        memory_limit_mb: int = 512,
        output_limit_mb: int = 16,
        process_limit: int = 4096,
        temp_root: str | Path | None = None,
    ) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise ValueError('sandbox timeout must be positive')
        limits = (memory_limit_mb, output_limit_mb, process_limit)
        if (
            any(isinstance(value, bool) or not isinstance(value, int) for value in limits)
            or memory_limit_mb < 64
            or output_limit_mb <= 0
            or process_limit <= 0
        ):
            raise ValueError('sandbox resource limits are invalid')
        self.pdflatex_path = str(Path(pdflatex_path))
        self.bwrap_path = str(Path(bwrap_path))
        self.timeout = timeout
        self.memory_limit_bytes = memory_limit_mb * 1024 * 1024
        self.output_limit_bytes = output_limit_mb * 1024 * 1024
        self.process_limit = process_limit
        self.temp_root = Path(temp_root).resolve() if temp_root else None

    def is_available(self) -> bool:
        return (
            Path(self.bwrap_path).is_file()
            and os.access(self.bwrap_path, os.X_OK)
            and Path(self.pdflatex_path).is_file()
            and os.access(self.pdflatex_path, os.X_OK)
        )

    def readiness_check(self) -> bool:
        """Prove that this host permits the namespace isolation we require."""
        if not self.is_available():
            return False
        temporary_parent = str(self.temp_root) if self.temp_root else None
        with tempfile.TemporaryDirectory(
            prefix='didactic-trusted-probe-', dir=temporary_parent
        ) as temporary:
            try:
                completed = subprocess.run(
                    [*self._sandbox_prefix(Path(temporary)), '/bin/true'],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=min(self.timeout, 2),
                    cwd='/',
                    env={},
                    preexec_fn=self._apply_limits,
                )
            except (subprocess.TimeoutExpired, OSError):
                return False
        return completed.returncode == 0

    def compile(self, latex_source: str) -> CompileResult:
        try:
            job = TrustedCompileJob(latex_source=latex_source)
        except ValueError as error:
            return CompileResult(False, b'', str(error), 'validation')
        return self.compile_job(job)

    def compile_job(self, job: TrustedCompileJob) -> CompileResult:
        if not isinstance(job, TrustedCompileJob):
            raise TypeError('job must be TrustedCompileJob')
        if not self.is_available():
            return CompileResult(
                False,
                b'',
                'Trusted LaTeX sandbox is unavailable',
                'unavailable',
            )

        temporary_parent = str(self.temp_root) if self.temp_root else None
        with tempfile.TemporaryDirectory(
            prefix='didactic-trusted-', dir=temporary_parent
        ) as temporary:
            job_dir = Path(temporary)
            source_path = job_dir / 'document.tex'
            pdf_path = job_dir / 'document.pdf'
            log_path = job_dir / 'document.log'
            process_log_path = job_dir / 'process.log'
            source_path.write_text(job.latex_source, encoding='utf-8')
            command = self._sandbox_command(job_dir)
            try:
                with process_log_path.open('wb') as process_log:
                    completed = subprocess.run(
                        command,
                        stdin=subprocess.DEVNULL,
                        stdout=process_log,
                        stderr=subprocess.STDOUT,
                        timeout=self.timeout,
                        cwd='/',
                        env={},
                        preexec_fn=self._apply_limits,
                    )
            except subprocess.TimeoutExpired:
                return CompileResult(False, b'', 'Sandbox timeout', 'timeout')
            except (FileNotFoundError, PermissionError, OSError) as error:
                return CompileResult(
                    False, b'', self._safe_error(error), 'sandbox-error'
                )

            log = self._read_limited(log_path)
            process_output = self._read_limited(process_log_path)
            if not log:
                log = process_output
            if completed.returncode == 0 and pdf_path.is_file():
                if pdf_path.stat().st_size > self.output_limit_bytes:
                    return CompileResult(
                        False, b'', 'Sandbox PDF output limit exceeded', 'output-limit'
                    )
                return CompileResult(True, pdf_path.read_bytes(), log)
            if process_output.startswith('bwrap:'):
                return CompileResult(False, b'', process_output, 'sandbox-error')
            return CompileResult(False, b'', log, 'compile-error')

    def _sandbox_command(self, job_dir: Path) -> list[str]:
        return [
            *self._sandbox_prefix(job_dir),
            self.pdflatex_path,
            '-interaction=nonstopmode',
            '-no-shell-escape',
            '-halt-on-error',
            '-file-line-error',
            '-output-directory', '/work',
            '/work/document.tex',
        ]

    def _sandbox_prefix(self, job_dir: Path) -> list[str]:
        command = [
            self.bwrap_path,
            '--unshare-all',
            '--die-with-parent',
            '--new-session',
            '--clearenv',
            '--ro-bind', '/usr', '/usr',
            '--ro-bind', '/bin', '/bin',
            '--ro-bind', '/lib', '/lib',
            '--ro-bind', '/lib64', '/lib64',
            '--proc', '/proc',
            '--dev', '/dev',
            '--tmpfs', '/tmp',
        ]
        for path in (
            '/etc/texmf',
            '/etc/fonts',
            '/etc/ld.so.cache',
            '/var/lib/texmf',
            '/var/cache/fontconfig',
        ):
            if Path(path).exists():
                command.extend(('--ro-bind', path, path))
        command.extend((
            '--bind', str(job_dir), '/work',
            '--chdir', '/work',
            '--setenv', 'HOME', '/tmp',
            '--setenv', 'TMPDIR', '/tmp',
            '--setenv', 'TEXMFOUTPUT', '/work',
            '--setenv', 'PATH', '/usr/bin:/bin',
        ))
        return command

    def _apply_limits(self) -> None:
        resource.setrlimit(
            resource.RLIMIT_CPU, (self.timeout, self.timeout + 1)
        )
        resource.setrlimit(
            resource.RLIMIT_AS,
            (self.memory_limit_bytes, self.memory_limit_bytes),
        )
        resource.setrlimit(
            resource.RLIMIT_FSIZE,
            (self.output_limit_bytes, self.output_limit_bytes),
        )
        resource.setrlimit(
            resource.RLIMIT_NPROC, (self.process_limit, self.process_limit)
        )
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))

    def _read_limited(self, path: Path) -> str:
        if not path.is_file():
            return ''
        with path.open('rb') as source:
            return source.read(self.output_limit_bytes).decode(
                'utf-8', errors='replace'
            )

    @staticmethod
    def _safe_error(error: BaseException) -> str:
        return f'{type(error).__name__}: sandbox process could not start'
