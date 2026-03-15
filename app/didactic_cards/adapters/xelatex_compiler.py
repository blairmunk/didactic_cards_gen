from __future__ import annotations

import os
import subprocess
import tempfile

from ..domain.interfaces import CompileResult, PdfCompiler


class XelatexCompiler(PdfCompiler):

    def __init__(self, xelatex_path: str = 'xelatex', timeout: int = 30):
        self.xelatex_path = xelatex_path
        self.timeout = timeout

    def compile(self, latex_source: str) -> CompileResult:
        with tempfile.TemporaryDirectory() as tmpdir:
            tex_path = os.path.join(tmpdir, 'document.tex')
            pdf_path = os.path.join(tmpdir, 'document.pdf')
            log_path = os.path.join(tmpdir, 'document.log')

            with open(tex_path, 'w', encoding='utf-8') as f:
                f.write(latex_source)

            try:
                subprocess.run(
                    [self.xelatex_path, '-interaction=nonstopmode',
                     '-output-directory', tmpdir, tex_path],
                    capture_output=True,
                    timeout=self.timeout,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                return CompileResult(success=False, pdf_data=b'', log=str(e))

            log = ''
            if os.path.exists(log_path):
                with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                    log = f.read()

            if os.path.exists(pdf_path):
                with open(pdf_path, 'rb') as f:
                    pdf_data = f.read()
                return CompileResult(success=True, pdf_data=pdf_data, log=log)

            return CompileResult(success=False, pdf_data=b'', log=log)