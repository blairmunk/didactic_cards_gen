"""Тесты PdfLatexCompiler с mock subprocess."""

import os
from unittest.mock import patch, MagicMock
from didactic_cards.adapters.pdflatex_compiler import PdfLatexCompiler


class TestPdfLatexCompiler:

    def test_compile_success(self, tmp_path):
        compiler = PdfLatexCompiler(pdflatex_path='pdflatex', timeout=10)

        def fake_run(cmd, **kwargs):
            output_dir = cmd[cmd.index('-output-directory') + 1]
            pdf_path = os.path.join(output_dir, 'document.pdf')
            log_path = os.path.join(output_dir, 'document.log')
            with open(pdf_path, 'wb') as f:
                f.write(b'%PDF-1.4 fake content')
            with open(log_path, 'w') as f:
                f.write('This is pdfTeX')
            return MagicMock(returncode=0)

        with patch('didactic_cards.adapters.pdflatex_compiler.subprocess.run', side_effect=fake_run):
            result = compiler.compile('\\documentclass{article}\\begin{document}Hi\\end{document}')

        assert result.success is True
        assert result.pdf_data.startswith(b'%PDF')
        assert 'pdfTeX' in result.log

    def test_compile_no_pdf_produced(self, tmp_path):
        compiler = PdfLatexCompiler(pdflatex_path='pdflatex', timeout=10)

        def fake_run(cmd, **kwargs):
            output_dir = cmd[cmd.index('-output-directory') + 1]
            log_path = os.path.join(output_dir, 'document.log')
            with open(log_path, 'w') as f:
                f.write('! Fatal error')
            return MagicMock(returncode=1)

        with patch('didactic_cards.adapters.pdflatex_compiler.subprocess.run', side_effect=fake_run):
            result = compiler.compile('bad latex')

        assert result.success is False
        assert result.pdf_data == b''
        assert 'Fatal error' in result.log

    def test_compile_timeout(self):
        compiler = PdfLatexCompiler(pdflatex_path='pdflatex', timeout=1)
        import subprocess
        with patch('didactic_cards.adapters.pdflatex_compiler.subprocess.run',
                   side_effect=subprocess.TimeoutExpired('pdflatex', 1)):
            result = compiler.compile('anything')

        assert result.success is False
        assert result.pdf_data == b''

    def test_compile_file_not_found(self):
        compiler = PdfLatexCompiler(pdflatex_path='/nonexistent/pdflatex', timeout=1)
        with patch('didactic_cards.adapters.pdflatex_compiler.subprocess.run',
                   side_effect=FileNotFoundError('pdflatex not found')):
            result = compiler.compile('anything')

        assert result.success is False
        assert 'not found' in result.log