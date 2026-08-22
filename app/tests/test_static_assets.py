import hashlib
from pathlib import Path


WEB_ROOT = Path(__file__).parents[1] / 'didactic_cards' / 'web'


def test_drag_drop_builds_order_before_renumbering_rows():
    source = (WEB_ROOT / 'static' / 'cards' / 'deck.js').read_text(encoding='utf-8')
    persistence = source.split('async function persistRowOrder', 1)[1]
    order_position = persistence.index('const order =')
    renumber_position = persistence.index('renumberRows();')
    assert order_position < renumber_position
    assert 'r.dataset.cardId' in persistence
    assert 'restoreRowOrder(previousRows)' in persistence


def test_keyboard_reorder_uses_the_same_persistent_uuid_path():
    source = (WEB_ROOT / 'static' / 'cards' / 'deck.js').read_text(encoding='utf-8')
    assert "event.key !== 'ArrowUp'" in source
    assert 'await persistRowOrder(previousRows)' in source
    assert "tbody.setAttribute('aria-busy', 'true')" in source


def test_templates_have_no_content_after_closing_html():
    for template in (WEB_ROOT / 'templates').rglob('*.html'):
        source = template.read_text(encoding='utf-8').strip()
        assert source.endswith('</html>'), template


def test_formula_preview_has_no_mandatory_external_runtime_dependency():
    templates = (WEB_ROOT / 'templates' / 'cards').glob('*.html')
    combined = '\n'.join(path.read_text(encoding='utf-8') for path in templates)
    assert 'https://cdn.jsdelivr.net/npm/mathjax' not in combined
    assert "vendor/mathjax/tex-mml-chtml.js" in combined


def test_pinned_mathjax_bundle_and_fonts_are_vendored():
    vendor = WEB_ROOT / 'static' / 'vendor' / 'mathjax'
    assert (vendor / 'tex-mml-chtml.js').stat().st_size > 1_000_000
    assert (vendor / 'LICENSE').exists()
    assert len(list((vendor / 'output/chtml/fonts/woff-v2').glob('*.woff'))) >= 20
    digest = hashlib.sha256((vendor / 'tex-mml-chtml.js').read_bytes()).hexdigest()
    assert digest == '300480069078b5892d2363a2b65e2dfbbf30fe5c80f83edbfecf4610fd093862'


def test_icon_actions_and_reorder_have_accessible_names():
    index = (WEB_ROOT / 'templates/cards/index.html').read_text(encoding='utf-8')
    decks = (WEB_ROOT / 'templates/cards/decks.html').read_text(encoding='utf-8')
    assert 'class="drag-handle" tabindex="0" role="button" aria-label=' in index
    assert 'class="delete-btn"' in index and 'aria-label="Удалить карточку' in index
    assert 'aria-label="Редактировать карточку' in index
    assert 'aria-label="Копировать колоду' in decks
    assert 'aria-label="Удалить колоду' in decks


def test_mathjax_has_local_loading_status():
    index = (WEB_ROOT / 'templates/cards/index.html').read_text(encoding='utf-8')
    script = (WEB_ROOT / 'static/cards/deck.js').read_text(encoding='utf-8')
    assert 'id="math-status"' in index
    assert 'Не удалось загрузить локальный MathJax' in script


def test_pdf_preview_uses_generated_pdf_in_a_blob_dialog():
    index = (WEB_ROOT / 'templates/cards/index.html').read_text(encoding='utf-8')
    script = (WEB_ROOT / 'static/cards/deck.js').read_text(encoding='utf-8')
    assert 'id="pdf-preview-dialog"' in index
    assert 'id="pdf-preview-frame"' in index
    assert 'URL.createObjectURL(await response.blob())' in script
    assert 'URL.revokeObjectURL(pdfPreviewUrl)' in script
