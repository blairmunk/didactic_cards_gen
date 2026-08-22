"""Regenerate deterministic monochrome duplex raster fixtures."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))

from didactic_cards.adapters.latex_renderer import LatexRenderer  # noqa: E402
from didactic_cards.adapters.pdflatex_compiler import PdfLatexCompiler  # noqa: E402
from didactic_cards.domain.entities import Card, CardDeck  # noqa: E402


def golden_deck() -> CardDeck:
    return CardDeck([
        Card(front=f'ЛИЦО {number} ВЕРХ', back=f'ОБОРОТ {number} ВЕРХ')
        for number in range(1, 5)
    ])


def golden_renderer() -> LatexRenderer:
    return LatexRenderer(
        cards_per_row=2,
        rows_per_page=2,
        back_border=True,
        registration_marks=True,
    )


def main() -> None:
    if not shutil.which('pdflatex') or not shutil.which('mutool'):
        raise RuntimeError('pdflatex and mutool are required')
    output_dir = ROOT / 'app' / 'tests' / 'golden'
    output_dir.mkdir(parents=True, exist_ok=True)
    result = PdfLatexCompiler().compile(golden_renderer().render(golden_deck()))
    if not result.success:
        raise RuntimeError(result.log)
    with tempfile.TemporaryDirectory() as temporary_dir:
        pdf_path = Path(temporary_dir) / 'duplex.pdf'
        pdf_path.write_bytes(result.pdf_data)
        subprocess.run(
            [
                'mutool', 'draw', '-q', '-r', '72', '-F', 'pbm',
                '-o', str(output_dir / 'duplex-%d.pbm'), str(pdf_path), '1-2',
            ],
            check=True,
        )


if __name__ == '__main__':
    main()
