import pytest
import shutil
import subprocess
from didactic_cards.domain.entities import Card, CardDeck
from didactic_cards.adapters.latex_renderer import LatexRenderer, escape_latex
from didactic_cards.adapters.pdflatex_compiler import PdfLatexCompiler


class TestEscapeLatex:
    def test_ampersand(self):
        assert escape_latex('A & B') == r'A \& B'

    def test_percent(self):
        assert escape_latex('100%') == r'100\%'

    def test_dollar(self):
        assert escape_latex('$5') == r'\$5'

    def test_hash(self):
        assert escape_latex('#1') == r'\#1'

    def test_underscore(self):
        assert escape_latex('a_b') == r'a\_b'

    def test_braces(self):
        assert escape_latex('{x}') == r'\{x\}'

    def test_tilde(self):
        assert escape_latex('~') == r'\textasciitilde{}'

    def test_caret(self):
        assert escape_latex('^') == r'\textasciicircum{}'

    def test_backslash(self):
        assert escape_latex('\\') == r'\textbackslash{}'

    def test_combined(self):
        result = escape_latex('100% & $5')
        assert r'\%' in result
        assert r'\&' in result
        assert r'\$' in result

    def test_empty_string(self):
        assert escape_latex('') == ''

    def test_no_special_chars(self):
        assert escape_latex('Привет мир') == 'Привет мир'

    def test_inline_math_preserved(self):
        assert escape_latex('Найти $a = \\frac{F}{m}$') == r'Найти $a = \frac{F}{m}$'

    def test_display_math_preserved(self):
        assert escape_latex('Формула: $$E = mc^2$$') == r'Формула: $$E = mc^2$$'

    def test_mixed_text_and_math(self):
        result = escape_latex('При 100% нагрузке $F = ma$ и $E = mc^2$')
        assert r'\%' in result          # процент экранирован
        assert '$F = ma$' in result     # формула нетронута
        assert '$E = mc^2$' in result   # вторая формула нетронута

    def test_dollar_without_closing(self):
        # Одиночный $ без пары — экранируется
        assert escape_latex('цена 5$') == r'цена 5\$'

    def test_underscore_outside_math(self):
        result = escape_latex('имя_файла и $a_1$')
        assert r'имя\_файла' in result  # экранирован
        assert '$a_1$' in result        # внутри формулы — нет


class TestLatexRenderer:
    def make_deck(self, n):
        cards = [Card(front=f'Q{i+1}', back=f'A{i+1}') for i in range(n)]
        return CardDeck(cards=cards)

    def test_render_returns_string(self):
        renderer = LatexRenderer()
        deck = self.make_deck(1)
        padded_deck = CardDeck(cards=deck.padded(8))
        result = renderer.render(padded_deck)
        assert isinstance(result, str)

    def test_render_contains_document(self):
        renderer = LatexRenderer()
        deck = self.make_deck(1)
        padded_deck = CardDeck(cards=deck.padded(8))
        result = renderer.render(padded_deck)
        assert r'\begin{document}' in result
        assert r'\end{document}' in result

    def test_render_contains_card_content(self):
        renderer = LatexRenderer()
        deck = self.make_deck(2)
        padded_deck = CardDeck(cards=deck.padded(8))
        result = renderer.render(padded_deck)
        assert 'Q1' in result
        assert 'Q2' in result
        assert 'A1' in result
        assert 'A2' in result

    def test_render_escapes_special_chars(self):
        deck = CardDeck(cards=[Card(front='100% & $5', back='a_b')])
        padded_deck = CardDeck(cards=deck.padded(8))
        renderer = LatexRenderer()
        result = renderer.render(padded_deck)
        assert r'\%' in result
        assert r'\&' in result
        assert r'\$' in result
        assert r'\_' in result

    def test_render_custom_layout(self):
        renderer = LatexRenderer(
            card_width_cm=7.0,
            card_height_cm=5.0,
            cards_per_row=3,
            rows_per_page=3,
            fbox_sep_pt=5,
        )
        deck = self.make_deck(1)
        padded_deck = CardDeck(cards=deck.padded(9))
        result = renderer.render(padded_deck)
        assert '7.0cm' in result
        assert '5.0cm' in result
        assert '5pt' in result

    @pytest.mark.xfail(
        strict=True,
        reason='BUG-PRINT-003: configured dimensions describe the inner minipage, not the cut card',
    )
    def test_configured_dimensions_equal_physical_card_dimensions(self):
        renderer = LatexRenderer(card_width_cm=9.3, card_height_cm=6.3, fbox_sep_pt=8)
        pt_cm = 2.54 / 72.27
        rule_pt = 0.4
        physical_width = renderer.card_width + 2 * (renderer.fbox_sep + rule_pt) * pt_cm
        physical_height = renderer.card_height + 2 * (renderer.fbox_sep + rule_pt) * pt_cm
        assert physical_width == pytest.approx(9.3)
        assert physical_height == pytest.approx(6.3)

    def test_padded_deck_exact_multiple(self):
        deck = self.make_deck(8)
        padded = deck.padded(8)
        assert len(padded) == 8

    def test_padded_deck_rounds_up(self):
        deck = self.make_deck(9)
        padded = deck.padded(8)
        assert len(padded) == 16

    def test_front_and_back_sections_present(self):
        renderer = LatexRenderer()
        deck = self.make_deck(1)
        padded_deck = CardDeck(cards=deck.padded(8))
        result = renderer.render(padded_deck)
        assert 'Передние стороны' in result
        assert 'Задние стороны' in result

    def test_back_columns_are_mirrored_for_long_edge_duplex(self):
        renderer = LatexRenderer(cards_per_row=2, rows_per_page=1)
        source = renderer.render(self.make_deck(2))
        back_section = source.split('Задние стороны', 1)[1]
        assert back_section.index('A2') < back_section.index('A1')

    @pytest.mark.xfail(
        strict=True,
        reason='BUG-PRINT-001: all fronts are emitted before all backs, mixing sheets in duplex mode',
    )
    def test_duplex_pages_are_interleaved_per_physical_sheet(self):
        renderer = LatexRenderer(cards_per_row=2, rows_per_page=1)
        source = renderer.render(self.make_deck(4))
        pages = source.split(r'\newpage')
        assert 'Q1' in pages[0] and 'Q2' in pages[0]
        assert 'A2' in pages[1] and 'A1' in pages[1]
        assert 'Q3' in pages[2] and 'Q4' in pages[2]
        assert 'A4' in pages[3] and 'A3' in pages[3]

    @pytest.mark.xfail(
        strict=True,
        reason='BUG-PRINT-002: back content is always rotated 180° although long-edge layout only mirrors columns',
    )
    def test_long_edge_back_text_is_not_upside_down(self):
        source = LatexRenderer(cards_per_row=2, rows_per_page=1).render(self.make_deck(2))
        back_section = source.split('Задние стороны', 1)[1]
        assert r'\rotatebox{180}' not in back_section

    @pytest.mark.xfail(
        strict=True,
        reason='BUG-SEC-001: arbitrary TeX commands are preserved inside user math delimiters',
    )
    def test_math_input_cannot_close_document_or_read_files(self):
        escaped = escape_latex(r'$x\end{document}\input{/etc/passwd}$')
        assert r'\end{document}' not in escaped
        assert r'\input' not in escaped


@pytest.mark.integration
def test_real_pdflatex_build_has_two_pages_for_one_sheet(tmp_path):
    if not shutil.which('pdflatex') or not shutil.which('pdfinfo'):
        pytest.skip('pdflatex/pdfinfo are required for the print integration test')

    deck = CardDeck([
        Card(front=f'Вопрос {number}: $x_{number}^2$', back=f'Ответ {number}')
        for number in range(1, 9)
    ])
    result = PdfLatexCompiler().compile(LatexRenderer(back_border=True).render(deck))
    assert result.success, result.log

    pdf_path = tmp_path / 'cards.pdf'
    pdf_path.write_bytes(result.pdf_data)
    info = subprocess.run(
        ['pdfinfo', str(pdf_path)], capture_output=True, text=True, check=True
    ).stdout
    assert 'Pages:           2' in info
