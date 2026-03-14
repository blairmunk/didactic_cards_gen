import os
import tempfile
import subprocess
from ..domain.interfaces import PdfCompiler, CompileResult


class PdfLatexCompiler(PdfCompiler):
    """Компилирует LaTeX-код в PDF через pdflatex."""

    def __init__(self, pdflatex_path: str = 'pdflatex', timeout: int = 30):
        self.pdflatex_path = pdflatex_path
        self.timeout = timeout

    def compile(self, source: str) -> CompileResult:
        temp_dir = tempfile.mkdtemp()
        tex_path = os.path.join(temp_dir, 'cards.tex')
        pdf_path = os.path.join(temp_dir, 'cards.pdf')

        with open(tex_path, 'w', encoding='utf-8') as f:
            f.write(source)

        try:
            result = subprocess.run(
                [self.pdflatex_path, '-interaction=nonstopmode',
                 '-output-directory', temp_dir, tex_path],
                capture_output=True, text=True, timeout=self.timeout
            )

            if result.returncode != 0 or not os.path.exists(pdf_path):
                error_lines = [l for l in result.stdout.split('\n')
                               if l.startswith('!')]
                log_tail = result.stdout[-3000:] if result.stdout else ''
                return CompileResult(
                    success=False,
                    errors=error_lines,
                    log=log_tail
                )

            return CompileResult(success=True, pdf_path=pdf_path)

        except subprocess.TimeoutExpired:
            return CompileResult(
                success=False,
                errors=[f'Компиляция превысила лимит времени ({self.timeout} сек).']
            )

        except FileNotFoundError:
            return CompileResult(
                success=False,
                errors=['pdflatex не найден. Установите TeX Live или MiKTeX.']
            )