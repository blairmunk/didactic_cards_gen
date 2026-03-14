import pytest
from didactic_cards.domain.entities import Card, CardDeck
from didactic_cards.adapters.latex_renderer import LatexRenderer, escape_latex


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