import pytest
import re
import shutil
import subprocess
from pathlib import Path
from didactic_cards.domain.entities import Card, CardDeck
from didactic_cards.domain.rendering import DeckRenderSettings
from didactic_cards.domain.trusted import TrustedTemplateVersion
from didactic_cards.adapters.latex_renderer import (
    LatexRenderer,
    UnsafeLatexError,
    escape_latex,
)
from didactic_cards.adapters.pdflatex_compiler import PdfLatexCompiler


GOLDEN_DIR = Path(__file__).with_name('golden')


def test_trusted_template_replaces_builtin_body_and_keeps_context_typed():
    template = TrustedTemplateVersion(
        deck_id='deck',
        version=1,
        front_source=(
            r'\vfill\centering '
            r'{{ section }} / {{ card_number }} / {{ side }}: {{ content }}'
            r'\vfill'
        ),
        back_source=(
            r'\raggedleft '
            r'{{ section }} / {{ card_number }} / {{ side }}: {{ content }}'
        ),
    )
    renderer = LatexRenderer(
        cards_per_row=1,
        rows_per_page=1,
        render_settings=DeckRenderSettings(authoring_mode='advanced'),
    ).with_trusted_template(template)

    latex = renderer.render(CardDeck([Card(
        front=r'\textit{RAW FRONT}',
        back=r'\textbf{RAW}',
        section='A&B',
    )]))

    assert r'A\&B / 1 / front: \textit{RAW FRONT}' in latex
    assert r'A\&B / 1 / back: \textbf{RAW}' in latex
    assert r'\checkedcardheader{1}' not in latex
    assert 'DIDACTIC-CARDS-HBOX-BEGIN:1:front:body' in latex
    assert 'DIDACTIC-CARDS-HBOX-BEGIN:1:back:body' in latex


def test_trusted_template_is_copy_configured_and_padding_stays_blank():
    base = LatexRenderer(
        cards_per_row=2,
        rows_per_page=1,
        render_settings=DeckRenderSettings(authoring_mode='advanced'),
    )
    template = TrustedTemplateVersion(
        deck_id='deck', version=1,
        front_source='FRONT-MARK {{ content }}',
        back_source='BACK-MARK {{ content }}',
    )
    configured = base.with_trusted_template(template)
    deck = CardDeck([Card(front='Q', back='A')])
    layout = configured.prepare_print_layout(deck, 2)
    latex = configured.render(CardDeck(list(layout.cards)))

    assert base.trusted_template is None
    assert configured.trusted_template is template
    assert latex.count('FRONT-MARK') == 1
    assert latex.count('BACK-MARK') == 1
    with pytest.raises(TypeError, match='TrustedTemplateVersion'):
        LatexRenderer(trusted_template='bad')
    with pytest.raises(TypeError, match='TrustedTemplateVersion'):
        base.with_trusted_template('bad')


def test_advanced_deck_renders_raw_content_without_optional_wrapper():
    source = LatexRenderer(
        cards_per_row=1,
        rows_per_page=1,
        render_settings=DeckRenderSettings(authoring_mode='advanced'),
    ).render(CardDeck([Card(
        front=r'\vfill\centering RAW \textbf{front}\vfill',
        back=r'\hfill RAW BACK',
    )]))

    assert r'\vfill\centering RAW \textbf{front}\vfill' in source
    assert r'\hfill RAW BACK' in source
    assert r'\textbackslash{}vfill' not in source


def test_advanced_wrapper_receives_headers_and_total_without_padding():
    template = TrustedTemplateVersion(
        deck_id='deck',
        version=1,
        front_source='{{ upper_header }} | {{ content }} | {{ lower_header }}',
        back_source='BACK {{ content }}',
    )
    renderer = LatexRenderer(
        cards_per_row=2,
        rows_per_page=1,
        render_settings=DeckRenderSettings(authoring_mode='advanced'),
        trusted_template=template,
    )
    layout = renderer.prepare_print_layout(
        CardDeck([
            Card(
                front='Q1', section='S',
                upper_header='TOP {{ section }}',
                lower_header='{{ card_number }}/{{ card_count }} {{ side }}',
            ),
            Card(front='Q2', section='S'),
            Card(
                front='Q3', section='T',
                upper_header='TOP {{ section }}',
                lower_header='{{ card_number }}/{{ card_count }} {{ side }}',
            ),
        ]),
        2,
    )

    source = renderer.render_fronts(CardDeck(list(layout.cards)))

    assert 'TOP S | Q1 | 1/3 front' in source
    assert 'TOP T | Q3 | 3/3 front' in source
    assert '/4 front' not in source


def test_safe_deck_ignores_trusted_wrapper_even_if_passed_by_caller():
    template = TrustedTemplateVersion(
        deck_id='deck', version=1,
        front_source='WRAPPER {{ content }}',
        back_source='WRAPPER {{ content }}',
    )
    source = LatexRenderer(
        cards_per_row=1,
        rows_per_page=1,
        render_settings=DeckRenderSettings(authoring_mode='safe'),
        trusted_template=template,
    ).render_fronts(CardDeck([Card(front=r'\input{/etc/passwd}')]))

    assert 'WRAPPER' not in source
    assert r'\textbackslash{}input' in source


def _card_measurement_log(log: str) -> str:
    measured = []
    active = False
    for line in log.splitlines():
        if 'DIDACTIC-CARDS-HBOX-BEGIN:' in line:
            active = True
        if active:
            measured.append(line)
        if 'DIDACTIC-CARDS-HBOX-END:' in line:
            active = False
    return '\n'.join(measured)


def _read_pbm(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    match = re.match(rb'P4\s+(\d+)\s+(\d+)\s', data)
    assert match, f'Invalid raw PBM fixture: {path}'
    return int(match.group(1)), int(match.group(2)), data[match.end():]


def _pixel_difference(first: Path, second: Path) -> float:
    width, height, first_pixels = _read_pbm(first)
    other_width, other_height, second_pixels = _read_pbm(second)
    assert (width, height) == (other_width, other_height)
    assert len(first_pixels) == len(second_pixels)
    differing_bits = sum(
        (left ^ right).bit_count()
        for left, right in zip(first_pixels, second_pixels)
    )
    return differing_bits / (width * height)


def _has_ink_near(path: Path, x: int, y: int, radius: int = 12) -> bool:
    width, height, pixels = _read_pbm(path)
    row_bytes = (width + 7) // 8
    for row in range(max(0, y - radius), min(height, y + radius + 1)):
        for column in range(max(0, x - radius), min(width, x + radius + 1)):
            byte = pixels[row * row_bytes + column // 8]
            if byte & (0x80 >> (column % 8)):
                return True
    return False


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

    def test_calibration_sheet_contains_two_sides_scale_and_offsets(self):
        source = LatexRenderer(
            duplex_mode='short-edge',
            back_rotation_deg=0,
            front_offset_x_mm=0.25,
            back_offset_x_mm=-1.5,
            back_offset_y_mm=0.75,
        ).render_calibration_sheet()

        assert source.count(r'\newpage') == 1
        assert 'контрольная длина 100 мм' in source
        assert r'\calibrationtargets{0.25}{0.0}{black}' in source
        assert r'\calibrationtargets{-1.5}{0.75}{magenta,dashed}' in source
        assert r'{ОБОРОТ: пунктирная линия}{0}' in source
        assert 'ВЕРХ КАРТОЧКИ' in source
        assert r'\texttt{short-edge}' in source
        assert 'remember picture' not in source
        assert r'\path[use as bounding box] (0,0) rectangle (186,225)' in source

    def test_render_contains_card_content(self):
        renderer = LatexRenderer()
        deck = self.make_deck(2)
        padded_deck = CardDeck(cards=deck.padded(8))
        result = renderer.render(padded_deck)
        assert 'Q1' in result
        assert 'Q2' in result
        assert 'A1' in result
        assert 'A2' in result

    def test_safe_newlines_have_explicit_line_and_paragraph_semantics(self):
        source = LatexRenderer(
            cards_per_row=1,
            rows_per_page=1,
            render_settings=DeckRenderSettings(
                typography_profile='custom',
                paragraph_spacing='medium',
            ),
        ).render_fronts(CardDeck([
            Card(front='Первая\r\nВторая\n\nТретья')
        ]))

        assert (
            r'Первая\cardsafelinebreak Вторая'
            r'\cardsafeparagraphbreak Третья'
        ) in source
        assert r'\newcommand{\cardparagraphspacing}{4pt}' in source

    def test_display_math_owns_a_paragraph_without_forced_line_breaks(self):
        source = LatexRenderer(
            cards_per_row=1,
            rows_per_page=1,
        ).render_fronts(CardDeck([Card(front='До\n$$x^2$$\nПосле')]))

        assert (
            r'До\cardsafeparagraphbreak $$x^2$$'
            r'\cardsafeparagraphbreak После'
        ) in source
        assert r'\cardsafelinebreak $$x^2$$' not in source
        assert r'$$x^2$$\cardsafelinebreak' not in source

    def test_safe_newline_commands_are_injected_only_after_escaping(self):
        source = LatexRenderer(
            cards_per_row=1,
            rows_per_page=1,
        ).render_fronts(CardDeck([
            Card(front='100%\n$x = 1$\n\n\\input{/etc/passwd}')
        ]))

        assert (
            r'100\%\cardsafelinebreak $x = 1$'
            r'\cardsafeparagraphbreak \textbackslash{}input\{/etc/passwd\}'
        ) in source
        assert r'\newcommand{\cardsafelinebreak}{\\{}}' in source

    @pytest.mark.parametrize('next_line', ('[30mm]B', '*B', '[', ']'))
    def test_safe_line_cannot_supply_an_optional_argument_to_latex(
        self, next_line
    ):
        source = LatexRenderer(
            cards_per_row=1,
            rows_per_page=1,
        ).render_fronts(CardDeck([Card(front=f'A\n{next_line}')]))

        assert rf'A\cardsafelinebreak {next_line}' in source
        assert r'\newcommand{\cardsafelinebreak}{\\{}}' in source

    def test_advanced_newlines_remain_raw_and_uninterpreted(self):
        raw = '  \\vfill\r\nRAW\n\nCONTENT  '
        source = LatexRenderer(
            cards_per_row=1,
            rows_per_page=1,
            render_settings=DeckRenderSettings(authoring_mode='advanced'),
        ).render_fronts(CardDeck([Card(front=raw)]))

        assert raw in source
        card_fragment = source.split(
            'DIDACTIC-CARDS-HBOX-BEGIN:1:front:body', 1
        )[1]
        assert r'\cardsafelinebreak' not in card_fragment
        assert r'\cardsafeparagraphbreak' not in card_fragment

    @pytest.mark.parametrize(
        ('horizontal', 'command'),
        [
            ('left', r'\raggedright'),
            ('center', r'\centering'),
            ('right', r'\raggedleft'),
        ],
    )
    def test_safe_horizontal_alignment_is_emitted(self, horizontal, command):
        renderer = LatexRenderer(
            render_settings=DeckRenderSettings(
                preset='custom',
                horizontal_alignment=horizontal,
            ),
            cards_per_row=1,
            rows_per_page=1,
        )

        source = renderer.render_fronts(self.make_deck(1))

        assert rf'\newcommand{{\cardbodyalign}}{{{command}}}' in source

    @pytest.mark.parametrize(
        ('vertical', 'position'),
        [('top', 't'), ('center', 'c'), ('bottom', 'b')],
    )
    def test_safe_vertical_alignment_uses_fixed_body_box(
        self, vertical, position
    ):
        renderer = LatexRenderer(
            render_settings=DeckRenderSettings(
                preset='custom', vertical_alignment=vertical
            ),
            cards_per_row=1,
            rows_per_page=1,
        )

        source = renderer.render_fronts(self.make_deck(1))

        assert rf'\begin{{minipage}}[t][#3][{position}]' in source

    def test_header_visibility_and_position_are_side_specific_and_escaped(self):
        deck = CardDeck([
            Card(front='Q', back='A', section='Тема & раздел')
        ])
        top_front = LatexRenderer(
            render_settings=DeckRenderSettings(
                preset='custom',
                header_visibility='front',
                header_position='top',
                header_alignment='center',
            ),
            cards_per_row=1,
            rows_per_page=1,
        ).render(deck)

        front_source, back_source = top_front.split('задние стороны', 1)
        front_card = front_source.split(r'\begin{document}', 1)[1]
        assert r'\checkedcardheader{1}{front}{Тема \& раздел}' in front_source
        assert r'\fitcardcontent{#1}{#2}{#3}{#4}' in top_front
        assert r'\relax>#3' in top_front
        assert front_card.index(r'\checkedcardheader') < front_card.index(
            r'\checkedcardcontent'
        )
        assert 'Тема' not in back_source
        assert r'\newcommand{\cardheaderalign}{\centering}' in top_front

        bottom = LatexRenderer(
            render_settings=DeckRenderSettings(
                preset='custom',
                header_visibility='both',
                secondary_header_visibility='both',
                secondary_header_position='bottom',
                secondary_header_source='section',
            ),
            cards_per_row=1,
            rows_per_page=1,
        ).render_fronts(deck)
        card_source = bottom.split(r'\begin{document}', 1)[1]
        assert card_source.index(r'\checkedcardcontent') < card_source.index(
            r'\checkedcardsecondaryheader'
        )

    def test_header_can_be_rendered_only_at_each_section_start(self):
        deck = CardDeck([
            Card(front='Q1', section='HEADER-ALPHA'),
            Card(front='Q2', section='HEADER-ALPHA'),
            Card(front='Q3', section='HEADER-BETA'),
        ])
        renderer = LatexRenderer(
            render_settings=DeckRenderSettings(
                header_visibility='front',
                header_repeat='section-start',
            ),
            cards_per_row=1,
            rows_per_page=3,
        )

        source = renderer.render_fronts(deck)

        assert source.count('HEADER-ALPHA') == 1
        assert source.count('HEADER-BETA') == 1

    def test_safe_header_and_section_comparison_share_single_line_semantics(self):
        settings = DeckRenderSettings(
            header_visibility='front',
            header_repeat='section-start',
        )
        source = LatexRenderer(
            render_settings=settings,
            cards_per_row=1,
            rows_per_page=2,
        ).render_fronts(CardDeck([
            Card(front='Q1', section='Тема\r\nодин'),
            Card(front='Q2', section='Тема\nодин'),
        ]))

        assert source.count('Тема один') == 1
        assert 'Тема\nодин' not in source

    def test_safe_typography_emits_only_renderer_owned_font_commands(self):
        source = LatexRenderer(
            render_settings=DeckRenderSettings(
                typography_profile='custom',
                body_font_family='sans',
                body_font_size='large',
                body_font_weight='bold',
                body_font_style='italic',
                line_spacing='relaxed',
                paragraph_spacing='medium',
            ),
            cards_per_row=1,
            rows_per_page=1,
        ).render_fronts(CardDeck([Card(front='Текст & формула $x^2$')]))

        assert (
            r'\newcommand{\cardbodyfont}'
            r'{\fontsize{14pt}{20.30pt}\selectfont\sffamily\bfseries\itshape}'
            in source
        )
        assert r'\newcommand{\cardparagraphspacing}{4pt}' in source
        assert r'Текст \& формула $x^2$' in source

    def test_two_headers_have_independent_sources_positions_and_escaping(self):
        settings = DeckRenderSettings(
            header_visibility='both',
            header_position='top',
            header_source='custom',
            header_text='Курс & класс',
            secondary_header_visibility='front',
            secondary_header_position='bottom',
            secondary_header_alignment='right',
            secondary_header_source='card-number',
        )
        source = LatexRenderer(
            render_settings=settings,
            cards_per_row=1,
            rows_per_page=1,
        ).render(CardDeck([Card(front='Q', back='A', section='Section')]))

        front, back = source.split('задние стороны', 1)
        front_card = front.split(r'\begin{document}', 1)[1]
        assert r'\checkedcardheader{1}{front}{Курс \& класс}' in front
        assert r'\checkedcardsecondaryheader{1}{front}{№ 1}' in front
        assert front_card.index(r'\checkedcardheader') < front_card.index(
            r'\checkedcardcontent'
        )
        assert front_card.index(r'\checkedcardcontent') < front_card.index(
            r'\checkedcardsecondaryheader'
        )
        assert r'\checkedcardheader{1}{back}{Курс \& класс}' in back
        assert r'\checkedcardsecondaryheader{1}{back}' not in back
        assert r'\newcommand{\cardsecondaryheaderalign}{\raggedleft}' in source

    def test_padding_cells_do_not_receive_numbered_headers(self):
        source = LatexRenderer(
            render_settings=DeckRenderSettings(
                secondary_header_visibility='both',
                secondary_header_source='card-number',
            ),
            cards_per_row=2,
            rows_per_page=1,
        ).render_fronts(CardDeck([Card(front='Only')]))

        assert source.count(r'\checkedcardsecondaryheader') == 2
        assert r'\checkedcardsecondaryheader{0}' not in source

    def test_custom_header_numbering_uses_real_card_count_and_escapes_text(self):
        renderer = LatexRenderer(
            render_settings=DeckRenderSettings(
                header_visibility='both',
                header_source='custom',
                header_text=(
                    'Явления & карточка '
                    '{{ card_number }}/{{ card_count }}'
                ),
            ),
            cards_per_row=2,
            rows_per_page=1,
        )
        layout = renderer.prepare_print_layout(
            CardDeck([
                Card(front='Q1'), Card(front='Q2'), Card(front='Q3'),
            ]),
            2,
        )

        source = renderer.render_fronts(CardDeck(list(layout.cards)))

        assert r'Явления \& карточка 1/3' in source
        assert r'Явления \& карточка 2/3' in source
        assert r'Явления \& карточка 3/3' in source
        assert 'карточка 0/3' not in source
        assert 'карточка 4/3' not in source

    def test_header_rules_are_renderer_owned_and_reduce_body_height(self):
        source = LatexRenderer(
            render_settings=DeckRenderSettings(
                header_visibility='front',
                header_rule='medium',
                header_rule_spacing='relaxed',
                secondary_header_visibility='front',
                secondary_header_rule='thin',
                secondary_header_rule_spacing='compact',
            ),
            cards_per_row=1,
            rows_per_page=1,
        ).render_fronts(CardDeck([Card(front='Q', section='Тема')]))

        assert r'\hrule height 0.02cm' in source
        assert r'\hrule height 0.01cm' in source
        assert r'\vspace*{0.095cm}' in source
        assert r'\vspace*{0.035cm}' in source
        assert r'\checkedcardcontent{1}{front}{4.459548cm}' in source

    def test_genuine_empty_card_keeps_its_logical_number(self):
        source = LatexRenderer(
            cards_per_row=1, rows_per_page=1
        ).render_fronts(CardDeck([Card()]))

        assert r'\checkedcardcontent{1}{front}' in source
        assert r'{\mbox{}}' in source

    def test_renderer_copy_applies_deck_settings_without_mutating_base(self):
        base = LatexRenderer(cards_per_row=1, rows_per_page=1)
        configured = base.with_render_settings(DeckRenderSettings.centered())

        assert configured is not base
        assert base.render_settings == DeckRenderSettings.centered()
        assert configured.render_settings == DeckRenderSettings.centered()

    def test_print_layout_capacity_must_match_renderer_grid(self):
        with pytest.raises(ValueError, match='does not match'):
            LatexRenderer(cards_per_row=2, rows_per_page=4).prepare_print_layout(
                self.make_deck(1), 6
            )

    @pytest.mark.parametrize('value', [object(), 'centered'])
    def test_renderer_rejects_non_settings_objects(self, value):
        with pytest.raises(TypeError):
            LatexRenderer(render_settings=value)
        with pytest.raises(TypeError):
            LatexRenderer().with_render_settings(value)

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

    def test_default_long_edge_rotates_each_back_card_by_180_degrees(self):
        source = LatexRenderer(cards_per_row=2, rows_per_page=1).render(self.make_deck(2))
        back_section = source.split('задние стороны', 1)[1]
        assert back_section.count(r'\rotatebox{180}{\backcard') == 2

    def test_back_rotation_can_be_disabled_independently_of_duplex_mode(self):
        source = LatexRenderer(
            cards_per_row=2,
            rows_per_page=1,
            duplex_mode='long-edge',
            back_rotation_deg=0,
        ).render(self.make_deck(2))
        back_section = source.split('задние стороны', 1)[1]
        assert r'\rotatebox{180}' not in back_section
        assert back_section.index('A2') < back_section.index('A1')

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
            {'auto_fit': 1},
            {'back_rotation_deg': 90},
            {'back_rotation_deg': True},
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

    @pytest.mark.parametrize(
        'kwargs',
        [
            {'front_offset_x_mm': -5.0},
            {'front_offset_y_mm': -5.0},
            {'back_offset_x_mm': -5.0},
            {'back_offset_y_mm': -5.0},
            {'front_offset_x_mm': 10.0},
            {'front_offset_y_mm': 10.0},
        ],
    )
    def test_printable_area_allows_offsets_while_grid_stays_on_a4(self, kwargs):
        assert LatexRenderer(**kwargs).printable_area_warnings() == ()

    @pytest.mark.parametrize(
        ('kwargs', 'side', 'axis'),
        [
            ({'front_offset_x_mm': -5.01}, 'Лицевая', 'горизонтальное'),
            ({'front_offset_y_mm': -5.01}, 'Лицевая', 'вертикальное'),
            ({'back_offset_x_mm': -5.01}, 'Оборотная', 'горизонтальное'),
            ({'back_offset_y_mm': -5.01}, 'Оборотная', 'вертикальное'),
            (
                {
                    'card_width_cm': 10,
                    'cards_per_row': 2,
                    'front_offset_x_mm': 5.01,
                },
                'Лицевая',
                'горизонтальное',
            ),
            (
                {
                    'card_height_cm': 28.62,
                    'rows_per_page': 1,
                    'back_offset_y_mm': 5.2,
                },
                'Оборотная',
                'вертикальное',
            ),
        ],
    )
    def test_printable_area_warns_only_after_grid_crosses_a4_edge(
        self, kwargs, side, axis
    ):
        warnings = LatexRenderer(**kwargs).printable_area_warnings()
        assert any(side in warning and axis in warning for warning in warnings)

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
def test_real_latex_marks_only_card_scoped_horizontal_overflow():
    if not shutil.which('pdflatex'):
        pytest.skip('pdflatex is required for the overflow integration test')

    compiler = PdfLatexCompiler()
    short = compiler.compile(
        LatexRenderer(cards_per_row=1, rows_per_page=1).render(
            CardDeck([Card(front='2 + 2', back='4')])
        )
    )
    long = compiler.compile(
        LatexRenderer(cards_per_row=1, rows_per_page=1).render(
            CardDeck([Card(front='X' * 500, back='4')])
        )
    )

    assert short.success and long.success
    assert 'Overfull \\hbox' not in _card_measurement_log(short.log)
    assert 'Overfull \\hbox' in _card_measurement_log(long.log)


@pytest.mark.integration
def test_real_pdf_distinguishes_safe_lines_and_paragraph_spacing(tmp_path):
    if not shutil.which('pdflatex') or not shutil.which('pdftotext'):
        pytest.skip('pdflatex/pdftotext are required for safe newline test')

    settings = DeckRenderSettings(
        typography_profile='custom',
        paragraph_spacing='medium',
    )
    source = LatexRenderer(
        cards_per_row=1,
        rows_per_page=1,
        render_settings=settings,
    ).render_fronts(CardDeck([
        Card(front='LINEALPHA\r\nLINEBETA\n\nLINEGAMMA')
    ]))
    result = PdfLatexCompiler().compile(source)

    assert result.success, result.log
    pdf_path = tmp_path / 'safe-newlines.pdf'
    pdf_path.write_bytes(result.pdf_data)
    bbox = subprocess.run(
        ['pdftotext', '-bbox', str(pdf_path), '-'],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    positions = {}
    for marker in ('LINEALPHA', 'LINEBETA', 'LINEGAMMA'):
        match = re.search(
            rf'<word [^>]*yMin="([0-9.]+)"[^>]*>{marker}</word>',
            bbox,
        )
        assert match, bbox
        positions[marker] = float(match.group(1))

    line_gap = positions['LINEBETA'] - positions['LINEALPHA']
    paragraph_gap = positions['LINEGAMMA'] - positions['LINEBETA']
    assert line_gap > 0
    assert paragraph_gap > line_gap + 2


@pytest.mark.integration
def test_real_pdf_safe_line_cannot_apply_latex_optional_spacing(tmp_path):
    if not shutil.which('pdflatex') or not shutil.which('pdftotext'):
        pytest.skip('pdflatex/pdftotext are required for safe newline test')

    def positions(front: str, name: str) -> tuple[float, float]:
        result = PdfLatexCompiler().compile(
            LatexRenderer(cards_per_row=1, rows_per_page=1).render_fronts(
                CardDeck([Card(front=front)])
            )
        )
        assert result.success, result.log
        path = tmp_path / name
        path.write_bytes(result.pdf_data)
        bbox = subprocess.run(
            ['pdftotext', '-bbox', str(path), '-'],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        found = {}
        for marker in ('LINETOP', 'LINEBOTTOM'):
            match = re.search(
                rf'<word [^>]*yMin="([0-9.]+)"[^>]*>{marker}</word>',
                bbox,
            )
            assert match, bbox
            found[marker] = float(match.group(1))
        return found['LINETOP'], found['LINEBOTTOM']

    baseline = positions('LINETOP\nLINEBOTTOM', 'baseline.pdf')
    bracket = positions('LINETOP\n[30mm] LINEBOTTOM', 'bracket.pdf')

    assert bracket[1] - bracket[0] == pytest.approx(
        baseline[1] - baseline[0], abs=0.5
    )


@pytest.mark.integration
def test_real_pdf_compiles_display_math_between_safe_text_paragraphs():
    if not shutil.which('pdflatex'):
        pytest.skip('pdflatex is required for safe display math test')

    source = LatexRenderer(
        cards_per_row=1,
        rows_per_page=1,
    ).render_fronts(CardDeck([
        Card(front='BEFOREMATH\n$$x^2 + y^2$$\nAFTERMATH')
    ]))
    result = PdfLatexCompiler().compile(source)

    assert result.success, result.log
    assert 'DIDACTIC-CARDS-OVERFLOW' not in result.log


@pytest.mark.integration
def test_real_calibration_sheet_is_a_two_page_a4_pdf(tmp_path):
    required = ('pdflatex', 'pdfinfo', 'mutool')
    if not all(shutil.which(command) for command in required):
        pytest.skip('pdflatex, pdfinfo and mutool are required for calibration test')

    result = PdfLatexCompiler().compile(
        LatexRenderer(back_offset_x_mm=1.25).render_calibration_sheet()
    )

    assert result.success, result.log
    pdf_path = tmp_path / 'calibration.pdf'
    pdf_path.write_bytes(result.pdf_data)
    info = subprocess.run(
        ['pdfinfo', str(pdf_path)], capture_output=True, text=True, check=True
    ).stdout
    assert 'Pages:           2' in info
    assert 'A4' in info

    subprocess.run(
        [
            'mutool', 'draw', '-q', '-r', '72', '-F', 'pbm',
            '-o', str(tmp_path / 'calibration-%d.pbm'), str(pdf_path),
        ],
        capture_output=True,
        check=True,
    )
    expected_targets = (
        (85, 218), (510, 218),
        (298, 452),
        (85, 686), (510, 686),
    )
    for page_number in (1, 2):
        raster = tmp_path / f'calibration-{page_number}.pbm'
        assert _read_pbm(raster)[:2] == (596, 842)
        assert all(
            _has_ink_near(raster, x, y) for x, y in expected_targets
        )


@pytest.mark.integration
def test_real_registration_marks_have_four_edge_targets_in_one_tex_pass(tmp_path):
    if not shutil.which('pdflatex') or not shutil.which('mutool'):
        pytest.skip('pdflatex/mutool are required for registration mark test')

    source = LatexRenderer(
        cards_per_row=1,
        rows_per_page=1,
        registration_marks=True,
    ).render(CardDeck([Card(front='ЛИЦО', back='ОБОРОТ')]))
    assert 'remember picture' not in source

    result = PdfLatexCompiler().compile(source)
    assert result.success, result.log
    assert 'Label(s) may have changed' not in result.log

    pdf_path = tmp_path / 'registration.pdf'
    pdf_path.write_bytes(result.pdf_data)
    subprocess.run(
        [
            'mutool', 'draw', '-q', '-r', '72', '-F', 'pbm',
            '-o', str(tmp_path / 'registration-%d.pbm'), str(pdf_path),
        ],
        capture_output=True,
        check=True,
    )
    expected_targets = (
        (298, 14),
        (298, 828),
        (14, 421),
        (581, 421),
    )
    for page_number in (1, 2):
        raster = tmp_path / f'registration-{page_number}.pbm'
        assert _read_pbm(raster)[:2] == (596, 842)
        assert all(_has_ink_near(raster, x, y) for x, y in expected_targets)


@pytest.mark.integration
def test_real_latex_auto_fits_before_minimum_size_overflow():
    if not shutil.which('pdflatex'):
        pytest.skip('pdflatex is required for the auto-fit integration test')

    deck = CardDeck([Card(front='Текст ' * 100, back='Ответ')])
    compiler = PdfLatexCompiler()
    fitted = compiler.compile(
        LatexRenderer(cards_per_row=1, rows_per_page=1).render(deck)
    )
    without_fit = compiler.compile(
        LatexRenderer(
            cards_per_row=1, rows_per_page=1, auto_fit=False
        ).render(deck)
    )

    assert fitted.success, fitted.log
    assert 'DIDACTIC-CARDS-AUTOFIT:1:front:footnotesize' in fitted.log
    assert 'DIDACTIC-CARDS-OVERFLOW:1:front' not in fitted.log
    assert without_fit.success, without_fit.log
    assert 'DIDACTIC-CARDS-AUTOFIT' not in without_fit.log
    assert 'DIDACTIC-CARDS-OVERFLOW:1:front' in without_fit.log


@pytest.mark.integration
def test_real_pdf_compiles_custom_typography_and_two_header_bands():
    if not shutil.which('pdflatex'):
        pytest.skip('pdflatex is required for typography integration test')

    settings = DeckRenderSettings(
        typography_profile='custom',
        body_font_family='sans',
        body_font_size='large',
        body_font_weight='bold',
        body_font_style='italic',
        line_spacing='relaxed',
        paragraph_spacing='medium',
        header_visibility='both',
        header_source='section',
        header_rule='thin',
        header_rule_spacing='compact',
        header_font_family='serif',
        header_font_style='italic',
        secondary_header_visibility='both',
        secondary_header_position='bottom',
        secondary_header_source='custom',
        secondary_header_text=(
            'Курс & группа {{ card_number }}/{{ card_count }}'
        ),
        secondary_header_font_family='mono',
        secondary_header_rule='medium',
        secondary_header_rule_spacing='relaxed',
    )
    source = LatexRenderer(
        cards_per_row=1,
        rows_per_page=1,
        render_settings=settings,
    ).render(CardDeck([Card(front='Задание', back='Ответ', section='Алгебра')]))

    result = PdfLatexCompiler().compile(source)

    assert result.success, result.log
    assert 'DIDACTIC-CARDS-OVERFLOW' not in result.log
    assert 'DIDACTIC-CARDS-HEADER-OVERFLOW' not in result.log
    assert 'DIDACTIC-CARDS-SECONDARY-HEADER-OVERFLOW' not in result.log


@pytest.mark.integration
def test_real_pdf_honours_all_nine_safe_body_alignments(tmp_path):
    if not shutil.which('pdflatex') or not shutil.which('pdftotext'):
        pytest.skip('pdflatex/pdftotext are required for alignment test')

    coordinates = {}
    compiler = PdfLatexCompiler()
    for horizontal in ('left', 'center', 'right'):
        for vertical in ('top', 'center', 'bottom'):
            settings = DeckRenderSettings(
                preset='custom',
                horizontal_alignment=horizontal,
                vertical_alignment=vertical,
            )
            source = LatexRenderer(
                cards_per_row=1,
                rows_per_page=1,
                render_settings=settings,
            ).render_fronts(CardDeck([Card(front='ALIGNMENT')]))
            result = compiler.compile(source)
            assert result.success, result.log
            pdf_path = tmp_path / f'{horizontal}-{vertical}.pdf'
            pdf_path.write_bytes(result.pdf_data)
            bbox = subprocess.run(
                ['pdftotext', '-bbox', str(pdf_path), '-'],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            match = re.search(
                r'<word xMin="([0-9.]+)" yMin="([0-9.]+)"[^>]*>'
                r'ALIGNMENT</word>',
                bbox,
            )
            assert match, bbox
            coordinates[(horizontal, vertical)] = tuple(
                float(value) for value in match.groups()
            )

    for vertical in ('top', 'center', 'bottom'):
        assert (
            coordinates[('left', vertical)][0]
            < coordinates[('center', vertical)][0]
            < coordinates[('right', vertical)][0]
        )
    for horizontal in ('left', 'center', 'right'):
        assert (
            coordinates[(horizontal, 'top')][1]
            < coordinates[(horizontal, 'center')][1]
            < coordinates[(horizontal, 'bottom')][1]
        )


@pytest.mark.integration
def test_real_pdf_compiles_fixed_header_band_on_both_sides(tmp_path):
    if not shutil.which('pdflatex') or not shutil.which('pdftotext'):
        pytest.skip('pdflatex/pdftotext are required for header test')

    settings = DeckRenderSettings(
        preset='custom',
        header_visibility='both',
        header_position='bottom',
        header_alignment='center',
    )
    result = PdfLatexCompiler().compile(
        LatexRenderer(
            cards_per_row=1,
            rows_per_page=1,
            back_rotation_deg=0,
            render_settings=settings,
        ).render(CardDeck([
            Card(front='ЛИЦО', back='ОБОРОТ', section='МЕХАНИКА')
        ]))
    )
    assert result.success, result.log
    assert 'DIDACTIC-CARDS-HEADER-OVERFLOW' not in result.log
    pdf_path = tmp_path / 'headers.pdf'
    pdf_path.write_bytes(result.pdf_data)
    extracted = subprocess.run(
        ['pdftotext', str(pdf_path), '-'],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert extracted.count('МЕХАНИКА') == 2


@pytest.mark.integration
def test_real_section_sheet_break_creates_complete_duplex_sheet_pairs(tmp_path):
    if not shutil.which('pdflatex') or not shutil.which('pdftotext'):
        pytest.skip('pdflatex/pdftotext are required for section break test')

    renderer = LatexRenderer(
        cards_per_row=2,
        rows_per_page=2,
        back_rotation_deg=0,
        render_settings=DeckRenderSettings(
            header_visibility='front',
            header_repeat='section-start',
            section_break='new-sheet',
        ),
    )
    deck = CardDeck([
        Card(front='FIRST-FRONT', back='FIRST-BACK', section='ONE-SECTION'),
        Card(front='SECOND-FRONT', back='SECOND-BACK', section='TWO-SECTION'),
    ])
    layout = renderer.prepare_print_layout(deck, 4)
    result = PdfLatexCompiler().compile(
        renderer.render(CardDeck(list(layout.cards)))
    )

    assert result.success, result.log
    assert layout.section_padding == 3
    assert layout.trailing_padding == 3
    pdf_path = tmp_path / 'section-sheets.pdf'
    pdf_path.write_bytes(result.pdf_data)
    pages = []
    for page_number in range(1, 5):
        pages.append(subprocess.run(
            [
                'pdftotext', '-f', str(page_number), '-l', str(page_number),
                str(pdf_path), '-',
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout)

    assert 'FIRST-FRONT' in pages[0]
    assert 'FIRST-BACK' in pages[1]
    assert 'SECOND-FRONT' in pages[2]
    assert 'SECOND-BACK' in pages[3]
    assert pages[0].count('ONE-SECTION') == 1
    assert pages[2].count('TWO-SECTION') == 1


@pytest.mark.integration
def test_real_header_band_keeps_each_body_inside_its_own_card(tmp_path):
    if not shutil.which('pdflatex') or not shutil.which('pdftotext'):
        pytest.skip('pdflatex/pdftotext are required for header geometry test')

    settings = DeckRenderSettings(
        preset='centered',
        header_visibility='front',
    )
    result = PdfLatexCompiler().compile(
        LatexRenderer(
            cards_per_row=2,
            rows_per_page=1,
            render_settings=settings,
        ).render_fronts(CardDeck([
            Card(front='LEFTBODY', section='LEFTHEADER'),
            Card(front='RIGHTBODY', section='RIGHTHEADER'),
        ]))
    )
    assert result.success, result.log
    pdf_path = tmp_path / 'header-geometry.pdf'
    pdf_path.write_bytes(result.pdf_data)
    bbox = subprocess.run(
        ['pdftotext', '-bbox', str(pdf_path), '-'],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    def x_min(word):
        match = re.search(
            rf'<word xMin="([0-9.]+)"[^>]*>{word}</word>', bbox
        )
        assert match, bbox
        return float(match.group(1))

    assert x_min('LEFTBODY') < 250
    assert x_min('RIGHTBODY') > 300


@pytest.mark.integration
def test_duplex_raster_matches_golden_with_pixel_tolerance(tmp_path):
    if not shutil.which('pdflatex') or not shutil.which('mutool'):
        pytest.skip('pdflatex/mutool are required for raster golden tests')

    deck = CardDeck([
        Card(front=f'ЛИЦО {number} ВЕРХ', back=f'ОБОРОТ {number} ВЕРХ')
        for number in range(1, 5)
    ])
    renderer = LatexRenderer(
        cards_per_row=2,
        rows_per_page=2,
        back_border=True,
        registration_marks=True,
    )
    result = PdfLatexCompiler().compile(renderer.render(deck))
    assert result.success, result.log
    pdf_path = tmp_path / 'duplex.pdf'
    pdf_path.write_bytes(result.pdf_data)
    subprocess.run(
        [
            'mutool', 'draw', '-q', '-r', '72', '-F', 'pbm',
            '-o', str(tmp_path / 'duplex-%d.pbm'), str(pdf_path), '1-2',
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    for page_number in (1, 2):
        difference = _pixel_difference(
            GOLDEN_DIR / f'duplex-{page_number}.pbm',
            tmp_path / f'duplex-{page_number}.pbm',
        )
        assert difference <= 0.002


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
        [
            'mutool', 'draw', '-F', 'svg', '-o', str(svg_pattern),
            str(pdf_path), '1-2',
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    for page_number in (1, 2):
        svg = (tmp_path / f'geometry-{page_number}.svg').read_text(encoding='utf-8')
        horizontal_lengths = [
            float(value) for value in re.findall(r'd="M0 0H([0-9.]+)', svg)
        ]
        vertical_lengths = [
            float(value) for value in re.findall(r'd="M0 0V([0-9.]+)', svg)
        ]
        rule_widths = [
            float(value) for value in re.findall(r'stroke-width="([0-9.]+)', svg)
        ]

        assert horizontal_lengths and vertical_lengths and rule_widths
        width_cm = max(horizontal_lengths) * 2.54 / 72
        height_cm = (max(vertical_lengths) + max(rule_widths)) * 2.54 / 72
        assert width_cm == pytest.approx(9.3, abs=0.01)
        assert height_cm == pytest.approx(6.3, abs=0.01)
