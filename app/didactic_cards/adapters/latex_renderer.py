from ..domain.interfaces import DocumentRenderer
from ..domain.entities import CardDeck

import re


def escape_latex(text: str) -> str:
    """Экранирование спецсимволов LaTeX, с сохранением математических формул."""
    parts = re.split(r'(\$\$.+?\$\$|\$.+?\$)', text, flags=re.DOTALL)

    result = []
    for part in parts:
        if part.startswith('$$') and part.endswith('$$'):
            result.append(part)
        elif part.startswith('$') and part.endswith('$') and len(part) > 1:
            result.append(part)
        else:
            result.append(_escape_text(part))

    return ''.join(result)


def _escape_text(text: str) -> str:
    """Экранирование спецсимволов в обычном (не-math) тексте."""
    text = text.replace('\\', '\x00BACKSLASH\x00')

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

    text = text.replace('\x00BACKSLASH\x00', r'\textbackslash{}')

    return text


def _card_content(text: str) -> str:
    """Возвращает экранированный текст или mbox для пустых карточек."""
    escaped = escape_latex(text) if text.strip() else ''
    return escaped if escaped else r'\mbox{}'


class LatexRenderer(DocumentRenderer):
    """Генерирует LaTeX-документ из колоды карточек."""

    def __init__(self, card_width_cm=9.3, card_height_cm=6.3,
                 cards_per_row=2, rows_per_page=4, fbox_sep_pt=8,
                 back_border=False):
        self.card_width = card_width_cm
        self.card_height = card_height_cm
        self.cards_per_row = cards_per_row
        self.rows_per_page = rows_per_page
        self.fbox_sep = fbox_sep_pt
        self.back_border = back_border
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
        if self.back_border:
            backcard_def = (
                r"\newcommand{\backcard}[1]{%"  "\n"
                r"    \fbox{%"  "\n"
                r"        \begin{minipage}[t][\cardheight][t]{\cardwidth}"  "\n"
                r"        \vspace{0pt}%"  "\n"
                r"        #1"  "\n"
                r"        \end{minipage}%"  "\n"
                r"    }%"  "\n"
                r"    \vspace{2pt}%"  "\n"
                r"}"
            )
        else:
            backcard_def = (
                r"\newcommand{\backcard}[1]{%"  "\n"
                r"    \fcolorbox{white}{white}{%"  "\n"
                r"        \begin{minipage}[t][\cardheight][t]{\cardwidth}"  "\n"
                r"        \vspace{0pt}%"  "\n"
                r"        #1"  "\n"
                r"        \end{minipage}%"  "\n"
                r"    }%"  "\n"
                r"    \vspace{2pt}%"  "\n"
                r"}"
            )

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

{backcard_def}

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
                    content = _card_content(cards[idx].front) if idx < num_cards else r'\mbox{}'
                    latex += r"\frontcard{" + content + "}\n"
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
                    content = _card_content(cards[idx].back) if idx < num_cards else r'\mbox{}'
                    latex += r"\rotatebox{180}{\backcard{" + content + "}}\n"
                    if col < self.cards_per_row - 1:
                        latex += "%\n"
                if row < self.rows_per_page - 1:
                    latex += "\n"

            if page < num_pages - 1:
                latex += r"\newpage" + "\n"

        return latex