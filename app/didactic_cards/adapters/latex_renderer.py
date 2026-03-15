from ..domain.interfaces import DocumentRenderer
from ..domain.entities import CardDeck


import re


def escape_latex(text: str) -> str:
    """Экранирование спецсимволов LaTeX, с сохранением математических формул."""
    # Разбиваем текст на части: формулы ($$...$$ и $...$) и обычный текст
    # $$...$$ проверяем первым (жадность)
    parts = re.split(r'(\$\$.+?\$\$|\$.+?\$)', text, flags=re.DOTALL)

    result = []
    for part in parts:
        if part.startswith('$$') and part.endswith('$$'):
            # display math — оставляем как есть
            result.append(part)
        elif part.startswith('$') and part.endswith('$') and len(part) > 1:
            # inline math — оставляем как есть
            result.append(part)
        else:
            # обычный текст — экранируем
            result.append(_escape_text(part))

    return ''.join(result)


def _escape_text(text: str) -> str:
    """Экранирование спецсимволов в обычном (не-math) тексте."""
    # 1. Backslash → плейсхолдер
    text = text.replace('\\', '\x00BACKSLASH\x00')

    # 2. Все остальные спецсимволы
    chars = {
        '&': r'\&',
        '%': r'\%',
        '$': r'\$',
        '#': r'\#',
        '_': r'\_',
        '{': r'\{',
        '}': r'\}',
        '~': r'\textasciitilde{}',
        '^': r'\textasciicircum{}',
    }
    for char, replacement in chars.items():
        text = text.replace(char, replacement)

    # 3. Плейсхолдер → \textbackslash{}
    text = text.replace('\x00BACKSLASH\x00', r'\textbackslash{}')

    return text


class LatexRenderer(DocumentRenderer):
    """Генерирует LaTeX-документ из колоды карточек."""

    def __init__(self, card_width_cm=9.3, card_height_cm=6.3,
                 cards_per_row=2, rows_per_page=4, fbox_sep_pt=8):
        self.card_width = card_width_cm
        self.card_height = card_height_cm
        self.cards_per_row = cards_per_row
        self.rows_per_page = rows_per_page
        self.fbox_sep = fbox_sep_pt
        self.cards_per_page = cards_per_row * rows_per_page

    def render(self, deck: CardDeck) -> str:
        cards = deck.cards
        num_cards = len(cards)
        num_pages = (num_cards + self.cards_per_page - 1) // self.cards_per_page

        latex = self._preamble()
        latex += self._front_pages(cards, num_cards, num_pages)
        latex += self._back_pages(cards, num_cards, num_pages)
        latex += "\n\\end{document}"
        return latex

    def _preamble(self) -> str:
        return rf'''\documentclass[a4paper,12pt]{{extarticle}}
\usepackage{{amsmath}}
\usepackage{{amsfonts}}
\usepackage{{amssymb}}
\usepackage{{amsthm}}
\usepackage{{mathtext}}
\usepackage[T2A]{{fontenc}}
\usepackage[utf8]{{inputenc}}
\usepackage[russian]{{babel}}
\usepackage{{geometry}}
\usepackage{{graphicx}}
\usepackage{{array}}
\usepackage{{enumitem}}
\usepackage{{multicol}}
\usepackage{{xcolor}}

\geometry{{a4paper, margin=0.5cm}}

\newcommand{{\cardwidth}}{{{self.card_width}cm}}
\newcommand{{\cardheight}}{{{self.card_height}cm}}

\setlength{{\fboxsep}}{{{self.fbox_sep}pt}}

\newcommand{{\frontcard}}[1]{{%
    \fbox{{%
        \begin{{minipage}}[t][\cardheight][t]{{\cardwidth}}
        \vspace{{0pt}}%
        #1
        \end{{minipage}}%
    }}%
    \vspace{{2pt}}%
}}

\newcommand{{\backcard}}[1]{{%
    \fcolorbox{{white}}{{white}}{{%
        \begin{{minipage}}[t][\cardheight][t]{{\cardwidth}}
        \vspace{{0pt}}%
        #1
        \end{{minipage}}%
    }}%
    \vspace{{2pt}}%
}}

\pagestyle{{empty}}

\setlist[itemize]{{label={{}}, left=0.5em, itemsep=-2pt, topsep=0.5ex}}
\setlength{{\parindent}}{{0pt}}

\begin{{document}}
'''

    def _front_pages(self, cards, num_cards, num_pages) -> str:
        latex = "\n% ===== Передние стороны карточек (задания) =====\n"

        for page in range(num_pages):
            latex += "\n"
            for row in range(self.rows_per_page):
                for col in range(self.cards_per_row):
                    idx = page * self.cards_per_page + row * self.cards_per_row + col
                    front = escape_latex(cards[idx].front) if idx < num_cards else ''
                    latex += r"\frontcard{" + front + "}\n"
                    if col < self.cards_per_row - 1:
                        latex += "%\n"
                if row < self.rows_per_page - 1:
                    latex += "\n"

            if page < num_pages - 1:
                latex += r"\newpage" + "\n"

        return latex

    def _back_pages(self, cards, num_cards, num_pages) -> str:
        latex = "\n% ===== Задние стороны карточек (решения) =====\n"
        latex += r"\newpage" + "\n"

        for page in range(num_pages):
            latex += "\n"
            for row in range(self.rows_per_page):
                for col in range(self.cards_per_row):
                    mirror_col = self.cards_per_row - 1 - col
                    idx = page * self.cards_per_page + row * self.cards_per_row + mirror_col
                    back = escape_latex(cards[idx].back) if idx < num_cards else ''
                    latex += r"\rotatebox{180}{\backcard{" + back + "}}\n"
                    if col < self.cards_per_row - 1:
                        latex += "%\n"
                if row < self.rows_per_page - 1:
                    latex += "\n"

            if page < num_pages - 1:
                latex += r"\newpage" + "\n"

        return latex