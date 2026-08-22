import os
import subprocess
from unittest.mock import MagicMock, patch

from didactic_cards.adapters.xelatex_compiler import XelatexCompiler


def test_xelatex_compile_success():
    def fake_run(cmd, **_kwargs):
        output_dir = cmd[cmd.index('-output-directory') + 1]
        with open(os.path.join(output_dir, 'document.pdf'), 'wb') as stream:
            stream.write(b'%PDF-xelatex')
        with open(os.path.join(output_dir, 'document.log'), 'w', encoding='utf-8') as stream:
            stream.write('XeTeX ok')
        return MagicMock(returncode=0)

    with patch('didactic_cards.adapters.xelatex_compiler.subprocess.run', side_effect=fake_run):
        result = XelatexCompiler().compile('source')
    assert result.success is True
    assert result.pdf_data == b'%PDF-xelatex'
    assert result.log == 'XeTeX ok'


def test_xelatex_timeout_is_failure():
    with patch(
        'didactic_cards.adapters.xelatex_compiler.subprocess.run',
        side_effect=subprocess.TimeoutExpired('xelatex', 1),
    ):
        result = XelatexCompiler(timeout=1).compile('source')
    assert result.success is False
    assert result.pdf_data == b''


def test_xelatex_no_output_is_failure():
    with patch(
        'didactic_cards.adapters.xelatex_compiler.subprocess.run',
        return_value=MagicMock(returncode=1),
    ):
        result = XelatexCompiler().compile('broken')
    assert result.success is False
    assert result.log == ''


def test_xelatex_missing_binary_is_failure():
    with patch(
        'didactic_cards.adapters.xelatex_compiler.subprocess.run',
        side_effect=FileNotFoundError('xelatex missing'),
    ):
        result = XelatexCompiler().compile('source')
    assert result.success is False
    assert 'missing' in result.log
