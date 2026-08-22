from pathlib import Path

import pytest


WEB_ROOT = Path(__file__).parents[1] / 'didactic_cards' / 'web'


def test_drag_drop_builds_order_before_renumbering_rows():
    source = (WEB_ROOT / 'static' / 'cards' / 'deck.js').read_text(encoding='utf-8')
    drop_handler = source.split("row.addEventListener('drop'", 1)[1]
    order_position = drop_handler.index('const order =')
    renumber_position = drop_handler.index('renumberRows();')
    assert order_position < renumber_position


@pytest.mark.xfail(
    strict=True,
    reason='BUG-UI-002: decks.html contains visible stray text after the closing HTML tag',
)
def test_templates_have_no_content_after_closing_html():
    for template in (WEB_ROOT / 'templates').rglob('*.html'):
        source = template.read_text(encoding='utf-8').strip()
        assert source.endswith('</html>'), template


@pytest.mark.xfail(
    strict=True,
    reason='BUG-UI-003: formula preview depends entirely on an external MathJax CDN',
)
def test_formula_preview_has_no_mandatory_external_runtime_dependency():
    templates = (WEB_ROOT / 'templates' / 'cards').glob('*.html')
    combined = '\n'.join(path.read_text(encoding='utf-8') for path in templates)
    assert 'https://cdn.jsdelivr.net/npm/mathjax' not in combined
