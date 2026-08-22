from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from ..domain.interfaces import CompileResult, PdfCompiler


class XelatexCompiler(PdfCompiler):

    def __init__(self, xelatex_path: str = 'xelatex', timeout: int = 30):
        self.xelatex_path = xelatex_path
        self.timeout = timeout

    def is_available(self) -> bool:
        return shutil.which(self.xelatex_path) is not None

    def compile(self, latex_source: str) -> CompileResult:
        with tempfile.TemporaryDirectory() as tmpdir:
            tex_path = os.path.join(tmpdir, 'document.tex')
            pdf_path = os.path.join(tmpdir, 'document.pdf')
            log_path = os.path.join(tmpdir, 'document.log')

            with open(tex_path, 'w', encoding='utf-8') as f:
                f.write(latex_source)

            try:
                completed = subprocess.run(
                    [self.xelatex_path, '-interaction=nonstopmode',
                     '-no-shell-escape', '-halt-on-error', '-file-line-error',
                     '-output-directory', tmpdir, tex_path],
                    capture_output=True,
                    timeout=self.timeout,
                    cwd=tmpdir,
                )
            except subprocess.TimeoutExpired as error:
                return CompileResult(
                    success=False, pdf_data=b'', log=str(error), error_kind='timeout'
                )
            except FileNotFoundError as error:
                return CompileResult(
                    success=False, pdf_data=b'', log=str(error), error_kind='unavailable'
                )

            log = ''
            if os.path.exists(log_path):
                with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                    log = f.read()

            process_output = b'\n'.join(
                output for output in (completed.stdout, completed.stderr)
                if isinstance(output, bytes) and output
            ).decode('utf-8', errors='replace')
            if process_output and not log:
                log = process_output

            if completed.returncode == 0 and os.path.exists(pdf_path):
                with open(pdf_path, 'rb') as f:
                    pdf_data = f.read()
                return CompileResult(success=True, pdf_data=pdf_data, log=log)

            return CompileResult(
                success=False, pdf_data=b'', log=log, error_kind='compile-error'
            )
