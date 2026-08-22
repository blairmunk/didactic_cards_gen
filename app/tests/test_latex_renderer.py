import pytest
import re
import shutil
import subprocess
from didactic_cards.domain.entities import Card, CardDeck
from didactic_cards.adapters.latex_renderer import (
    LatexRenderer,
    UnsafeLatexError,
    escape_latex,
)
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
            card_width_cm=6.0,
            card_height_cm=5.0,
            cards_per_row=3,
            rows_per_page=3,
            fbox_sep_pt=5,
        )
        deck = self.make_deck(1)
        padded_deck = CardDeck(cards=deck.padded(9))
        result = renderer.render(padded_deck)
        assert '6.0cm' in result
        assert '5.0cm' in result
        assert '5pt' in result

    def test_configured_dimensions_equal_physical_card_dimensions(self):
        renderer = LatexRenderer(card_width_cm=9.3, card_height_cm=6.3, fbox_sep_pt=8)
        pt_cm = 2.54 / 72.27
        rule_pt = 0.4
        physical_width = renderer.card_content_width + 2 * (renderer.fbox_sep + rule_pt) * pt_cm
        physical_height = renderer.card_content_height + 2 * (renderer.fbox_sep + rule_pt) * pt_cm
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
        assert 'передние стороны' in result
        assert 'задние стороны' in result

    def test_back_columns_are_mirrored_for_long_edge_duplex(self):
        renderer = LatexRenderer(cards_per_row=2, rows_per_page=1)
        source = renderer.render(self.make_deck(2))
        back_section = source.split('задние стороны', 1)[1]
        assert back_section.index('A2') < back_section.index('A1')

    def test_duplex_pages_are_interleaved_per_physical_sheet(self):
        renderer = LatexRenderer(cards_per_row=2, rows_per_page=1)
        source = renderer.render(self.make_deck(4))
        pages = source.split(r'\newpage')
        assert 'Q1' in pages[0] and 'Q2' in pages[0]
        assert 'A2' in pages[1] and 'A1' in pages[1]
        assert 'Q3' in pages[2] and 'Q4' in pages[2]
        assert 'A4' in pages[3] and 'A3' in pages[3]

    def test_front_only_output_contains_one_page_per_sheet(self):
        renderer = LatexRenderer(cards_per_row=2, rows_per_page=1)
        pages = renderer.render_fronts(self.make_deck(4)).split(r'\newpage')
        assert len(pages) == 2
        assert 'Q1' in pages[0] and 'Q2' in pages[0]
        assert 'Q3' in pages[1] and 'Q4' in pages[1]
        assert all('задние стороны' not in page for page in pages)
        assert not any(f'A{number}' in ''.join(pages) for number in range(1, 5))

    def test_back_only_output_preserves_duplex_sheet_permutation(self):
        renderer = LatexRenderer(cards_per_row=2, rows_per_page=1)
        pages = renderer.render_backs(self.make_deck(4)).split(r'\newpage')
        assert len(pages) == 2
        assert pages[0].index('A2') < pages[0].index('A1')
        assert pages[1].index('A4') < pages[1].index('A3')
        assert all('передние стороны' not in page for page in pages)
        assert not any(f'Q{number}' in ''.join(pages) for number in range(1, 5))

    def test_long_edge_back_text_is_not_upside_down(self):
        source = LatexRenderer(cards_per_row=2, rows_per_page=1).render(self.make_deck(2))
        back_section = source.split('задние стороны', 1)[1]
        assert r'\rotatebox{180}' not in back_section

    def test_short_edge_back_rows_are_reversed(self):
        source = LatexRenderer(
            cards_per_row=2,
            rows_per_page=2,
            duplex_mode='short-edge',
        ).render(self.make_deck(4))
        back_section = source.split('задние стороны', 1)[1]
        assert back_section.index('A3') < back_section.index('A1')

    @pytest.mark.parametrize(
        'kwargs',
        [
            {'cards_per_row': 0},
            {'rows_per_page': 0},
            {'card_width_cm': 20, 'cards_per_row': 2},
            {'card_height_cm': 10, 'rows_per_page': 3},
            {'card_width_cm': 0.1, 'fbox_sep_pt': 8},
            {'card_width_cm': 0},
            {'fbox_rule_pt': -0.1},
            {'back_offset_x_mm': 11},
            {'front_offset_y_mm': float('nan')},
        ],
    )
    def test_invalid_layout_is_rejected(self, kwargs):
        with pytest.raises(ValueError):
            LatexRenderer(**kwargs)

    def test_calibration_offsets_are_applied_per_side(self):
        source = LatexRenderer(
            cards_per_row=1,
            rows_per_page=1,
            front_offset_x_mm=1.25,
            front_offset_y_mm=-0.5,
            back_offset_x_mm=-2.0,
            back_offset_y_mm=0.75,
        ).render(self.make_deck(1))
        front_source, back_source = source.split('задние стороны', 1)
        assert r'\hspace*{1.25mm}' in front_source
        assert r'\vspace*{-0.5mm}' in front_source
        assert r'\hspace*{-2.0mm}' in back_source
        assert r'\vspace*{0.75mm}' in back_source

    def test_registration_marks_are_opt_in(self):
        without_marks = LatexRenderer(cards_per_row=1, rows_per_page=1).render(
            self.make_deck(1)
        )
        with_marks = LatexRenderer(
            cards_per_row=1,
            rows_per_page=1,
            registration_marks=True,
        ).render(self.make_deck(1))
        assert without_marks.count(r'\registrationmarks') == 1  # macro definition only
        assert with_marks.count(r'\registrationmarks') == 3  # definition + two pages

    def test_printable_area_warnings_include_offset_side_and_axis(self):
        renderer = LatexRenderer(
            front_offset_x_mm=-0.1,
            back_offset_y_mm=-0.1,
        )
        warnings = renderer.printable_area_warnings()
        assert any('Лицевая' in warning and 'горизонтальное' in warning for warning in warnings)
        assert any('Оборотная' in warning and 'вертикальное' in warning for warning in warnings)

    def test_default_layout_has_no_printable_area_warning(self):
        assert LatexRenderer().printable_area_warnings() == ()

    def test_math_input_cannot_close_document_or_read_files(self):
        with pytest.raises(UnsafeLatexError):
            escape_latex(r'$x\end{document}\input{/etc/passwd}$')

    @pytest.mark.parametrize(
        'formula',
        [
            r'$x=\frac{-b\pm\sqrt{D}}{2a}$',
            r'$$\sum_{i=1}^{n} i = \frac{n(n+1)}{2}$$',
            r'$\alpha \leq \beta \quad \text{при } n\ge 1$',
            r'$x\;y$',
        ],
    )
    def test_supported_math_allowlist_is_preserved(self, formula):
        assert escape_latex(formula) == formula

    @pytest.mark.parametrize(
        'formula',
        [
            r'$\write18{touch /tmp/pwned}$',
            r'$\include{/etc/passwd}$',
            r'$\def\x{unsafe}$',
            '$x% hidden command$',
            '$x_{1$',
            '$x}1$',
            '$x\\$',
        ],
    )
    def test_unsupported_or_malformed_math_is_rejected(self, formula):
        with pytest.raises(UnsafeLatexError):
            escape_latex(formula)


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


@pytest.mark.integration
def test_real_pdf_interleaves_two_physical_sheets(tmp_path):
    if not shutil.which('pdflatex') or not shutil.which('pdftotext'):
        pytest.skip('pdflatex/pdftotext are required for the duplex integration test')

    deck = CardDeck([
        Card(front=f'FRONT-{number}', back=f'BACK-{number}')
        for number in range(1, 5)
    ])
    renderer = LatexRenderer(cards_per_row=2, rows_per_page=1, back_border=True)
    result = PdfLatexCompiler().compile(renderer.render(deck))
    assert result.success, result.log

    pdf_path = tmp_path / 'two-sheets.pdf'
    pdf_path.write_bytes(result.pdf_data)
    pages = []
    for page_number in range(1, 5):
        page_text = subprocess.run(
            [
                'pdftotext', '-f', str(page_number), '-l', str(page_number),
                str(pdf_path), '-'
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        pages.append(page_text)

    assert 'FRONT-1' in pages[0] and 'FRONT-2' in pages[0]
    assert 'BACK-2' in pages[1] and 'BACK-1' in pages[1]
    assert 'FRONT-3' in pages[2] and 'FRONT-4' in pages[2]
    assert 'BACK-4' in pages[3] and 'BACK-3' in pages[3]


@pytest.mark.integration
def test_real_split_pdfs_have_one_page_per_physical_sheet(tmp_path):
    if not shutil.which('pdflatex') or not shutil.which('pdfinfo'):
        pytest.skip('pdflatex/pdfinfo are required for the split PDF integration test')

    deck = CardDeck([
        Card(front=f'FRONT-{number}', back=f'BACK-{number}')
        for number in range(1, 17)
    ])
    renderer = LatexRenderer(back_border=True)
    compiler = PdfLatexCompiler()

    for side, source in (
        ('fronts', renderer.render_fronts(deck)),
        ('backs', renderer.render_backs(deck)),
    ):
        result = compiler.compile(source)
        assert result.success, result.log
        pdf_path = tmp_path / f'{side}.pdf'
        pdf_path.write_bytes(result.pdf_data)
        info = subprocess.run(
            ['pdfinfo', str(pdf_path)], capture_output=True, text=True, check=True
        ).stdout
        assert 'Pages:           2' in info


@pytest.mark.integration
def test_real_latex_log_marks_vertical_card_overflow():
    if not shutil.which('pdflatex'):
        pytest.skip('pdflatex is required for the overflow integration test')

    deck = CardDeck([Card(front='Очень длинный текст ' * 500, back='Ответ')])
    result = PdfLatexCompiler().compile(
        LatexRenderer(cards_per_row=1, rows_per_page=1).render(deck)
    )
    assert result.success, result.log
    assert 'DIDACTIC-CARDS-OVERFLOW:1:front' in result.log


@pytest.mark.integration
def test_real_pdf_card_frame_matches_configured_cut_size(tmp_path):
    if not shutil.which('pdflatex') or not shutil.which('mutool'):
        pytest.skip('pdflatex/mutool are required for the geometry integration test')

    renderer = LatexRenderer(
        card_width_cm=9.3,
        card_height_cm=6.3,
        cards_per_row=1,
        rows_per_page=1,
        back_border=True,
        registration_marks=True,
    )
    result = PdfLatexCompiler().compile(
        renderer.render(CardDeck([Card(front='FRAME-FRONT', back='FRAME-BACK')]))
    )
    assert result.success, result.log

    pdf_path = tmp_path / 'geometry.pdf'
    pdf_path.write_bytes(result.pdf_data)
    svg_pattern = tmp_path / 'geometry-%d.svg'
    subprocess.run(
        ['mutool', 'draw', '-F', 'svg', '-o', str(svg_pattern), str(pdf_path), '1'],
        capture_output=True,
        text=True,
        check=True,
    )
    svg = (tmp_path / 'geometry-1.svg').read_text(encoding='utf-8')
    horizontal_lengths = [float(value) for value in re.findall(r'd="M0 0H([0-9.]+)', svg)]
    vertical_lengths = [float(value) for value in re.findall(r'd="M0 0V([0-9.]+)', svg)]
    rule_widths = [float(value) for value in re.findall(r'stroke-width="([0-9.]+)', svg)]

    assert horizontal_lengths and vertical_lengths and rule_widths
    width_cm = max(horizontal_lengths) * 2.54 / 72
    height_cm = (max(vertical_lengths) + max(rule_widths)) * 2.54 / 72
    assert width_cm == pytest.approx(9.3, abs=0.01)
    assert height_cm == pytest.approx(6.3, abs=0.01)
