from __future__ import annotations

import math
import re

from ..domain.entities import Card, CardDeck
from ..domain.interfaces import DocumentRenderer
from ..domain.printing import DuplexMode, build_sheets


PT_TO_CM = 2.54 / 72.27

ALLOWED_MATH_COMMANDS = {
    'alpha', 'beta', 'gamma', 'delta', 'epsilon', 'varepsilon', 'zeta', 'eta',
    'theta', 'vartheta', 'iota', 'kappa', 'lambda', 'mu', 'nu', 'xi', 'pi',
    'varpi', 'rho', 'varrho', 'sigma', 'varsigma', 'tau', 'upsilon', 'phi',
    'varphi', 'chi', 'psi', 'omega', 'Gamma', 'Delta', 'Theta', 'Lambda', 'Xi',
    'Pi', 'Sigma', 'Upsilon', 'Phi', 'Psi', 'Omega',
    'frac', 'dfrac', 'tfrac', 'sqrt', 'pm', 'mp', 'cdot', 'times', 'div',
    'le', 'leq', 'ge', 'geq', 'ne', 'neq', 'approx', 'equiv', 'sim', 'simeq',
    'infty', 'sum', 'prod', 'int', 'iint', 'iiint', 'lim', 'min', 'max',
    'sin', 'cos', 'tan', 'cot', 'arcsin', 'arccos', 'arctan', 'log', 'ln', 'exp',
    'left', 'right', 'big', 'Big', 'bigg', 'Bigg', 'overline', 'underline',
    'vec', 'hat', 'bar', 'dot', 'ddot', 'mathrm', 'mathbf', 'mathit', 'mathbb',
    'mathcal', 'operatorname', 'text', 'quad', 'qquad', 'ldots', 'cdots', 'dots',
}
ALLOWED_MATH_SYMBOL_COMMANDS = {',', ';', ':', '!', ' ', '{', '}', '|', '_'}


class UnsafeLatexError(ValueError):
    """Raised when a formula contains TeX outside the supported math subset."""


def _validate_math(content: str) -> None:
    if '\x00' in content or '%' in content or '#' in content:
        raise UnsafeLatexError('Формула содержит запрещённые TeX-символы')

    command_pattern = re.compile(r'\\([A-Za-z]+|.)', flags=re.DOTALL)
    for match in command_pattern.finditer(content):
        command = match.group(1)
        if command.isalpha():
            allowed = command in ALLOWED_MATH_COMMANDS
        else:
            allowed = command in ALLOWED_MATH_SYMBOL_COMMANDS
        if not allowed:
            raise UnsafeLatexError(f'Команда \\{command} не поддерживается')

    without_commands = command_pattern.sub('', content)
    if '\\' in without_commands:
        raise UnsafeLatexError('Формула содержит незавершённую TeX-команду')

    balance = 0
    for char in without_commands:
        if char == '{':
            balance += 1
        elif char == '}':
            balance -= 1
            if balance < 0:
                raise UnsafeLatexError('В формуле нарушен баланс фигурных скобок')
    if balance:
        raise UnsafeLatexError('В формуле нарушен баланс фигурных скобок')


def escape_latex(text: str) -> str:
    """Экранирование спецсимволов LaTeX, с сохранением математических формул."""
    parts = re.split(r'(\$\$.+?\$\$|\$.+?\$)', text, flags=re.DOTALL)

    result = []
    for part in parts:
        if part.startswith('$$') and part.endswith('$$'):
            _validate_math(part[2:-2])
            result.append(part)
        elif part.startswith('$') and part.endswith('$') and len(part) > 1:
            _validate_math(part[1:-1])
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

    return text.replace('\x00BACKSLASH\x00', r'\textbackslash{}')


def _card_content(text: str) -> str:
    """Возвращает экранированный текст или mbox для пустых карточек."""
    escaped = escape_latex(text) if text.strip() else ''
    return escaped if escaped else r'\mbox{}'


class LatexRenderer(DocumentRenderer):
    """Генерирует interleaved front/back LaTeX-страницы физических листов."""

    def __init__(
        self,
        card_width_cm: float = 9.3,
        card_height_cm: float = 6.3,
        cards_per_row: int = 2,
        rows_per_page: int = 4,
        fbox_sep_pt: float = 8,
        fbox_rule_pt: float = 0.4,
        back_border: bool = False,
        duplex_mode: DuplexMode | str = DuplexMode.LONG_EDGE,
        front_offset_x_mm: float = 0.0,
        front_offset_y_mm: float = 0.0,
        back_offset_x_mm: float = 0.0,
        back_offset_y_mm: float = 0.0,
        registration_marks: bool = False,
    ):
        if cards_per_row <= 0 or rows_per_page <= 0:
            raise ValueError('cards_per_row and rows_per_page must be positive')
        if card_width_cm <= 0 or card_height_cm <= 0:
            raise ValueError('card dimensions must be positive')
        if fbox_sep_pt < 0 or fbox_rule_pt < 0:
            raise ValueError('frame spacing and rule must not be negative')
        offsets = (
            front_offset_x_mm,
            front_offset_y_mm,
            back_offset_x_mm,
            back_offset_y_mm,
        )
        if not all(math.isfinite(offset) for offset in offsets):
            raise ValueError('calibration offsets must be finite')
        if any(abs(offset) > 10 for offset in offsets):
            raise ValueError('calibration offsets must be within +/- 10 mm')

        frame_inset_cm = 2 * (fbox_sep_pt + fbox_rule_pt) * PT_TO_CM
        if card_width_cm <= frame_inset_cm or card_height_cm <= frame_inset_cm:
            raise ValueError('card dimensions are too small for frame spacing')
        if cards_per_row * card_width_cm > 20.0:
            raise ValueError('card grid does not fit A4 printable width')
        row_advance_cm = card_height_cm + 2 * PT_TO_CM
        if rows_per_page * row_advance_cm > 28.7:
            raise ValueError('card grid does not fit A4 printable height')

        self.card_width = card_width_cm
        self.card_height = card_height_cm
        self.card_content_width = card_width_cm - frame_inset_cm
        self.card_content_height = card_height_cm - frame_inset_cm
        self.cards_per_row = cards_per_row
        self.rows_per_page = rows_per_page
        self.fbox_sep = fbox_sep_pt
        self.fbox_rule = fbox_rule_pt
        self.back_border = back_border
        self.duplex_mode = DuplexMode(duplex_mode)
        self.front_offset = (front_offset_x_mm, front_offset_y_mm)
        self.back_offset = (back_offset_x_mm, back_offset_y_mm)
        self.registration_marks = registration_marks
        self.cards_per_page = cards_per_row * rows_per_page

    def render(self, deck: CardDeck) -> str:
        sheets = build_sheets(
            deck.cards,
            rows=self.rows_per_page,
            columns=self.cards_per_row,
            duplex_mode=self.duplex_mode,
        )

        latex = self._preamble()
        for sheet_index, sheet in enumerate(sheets):
            latex += f'\n% ===== Лист {sheet_index + 1}: передние стороны =====\n'
            latex += self._render_page(sheet.front_slots, side='front')
            latex += r'\newpage' + '\n'
            latex += f'\n% ===== Лист {sheet_index + 1}: задние стороны =====\n'
            latex += self._render_page(sheet.back_slots, side='back')
            if sheet_index < len(sheets) - 1:
                latex += r'\newpage' + '\n'

        latex += '\n\\end{document}'
        return latex

    def _preamble(self) -> str:
        back_frame = r'\fbox' if self.back_border else r'\cardblankframe'
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
\usepackage{{tikz}}

\geometry{{a4paper, margin=0.5cm}}

\newcommand{{\cardwidth}}{{{self.card_width}cm}}
\newcommand{{\cardheight}}{{{self.card_height}cm}}
\newcommand{{\cardcontentwidth}}{{{self.card_content_width:.6f}cm}}
\newcommand{{\cardcontentheight}}{{{self.card_content_height:.6f}cm}}

\setlength{{\fboxsep}}{{{self.fbox_sep}pt}}
\setlength{{\fboxrule}}{{{self.fbox_rule}pt}}

\newcommand{{\cardblankframe}}[1]{{\fcolorbox{{white}}{{white}}{{#1}}}}
\newcommand{{\cardbox}}[2]{{%
    #1{{%
        \begin{{minipage}}[t][\cardcontentheight][t]{{\cardcontentwidth}}
        \vspace{{0pt}}%
        #2
        \end{{minipage}}%
    }}%
    \vspace{{2pt}}%
}}
\newcommand{{\frontcard}}[1]{{\cardbox{{\fbox}}{{#1}}}}
\newcommand{{\backcard}}[1]{{\cardbox{{{back_frame}}}{{#1}}}}
\newcommand{{\registrationmarks}}{{%
    \begin{{tikzpicture}}[remember picture,overlay,line width=0.2pt]
    \draw ([yshift=-5mm]current page.north) ++(-3mm,0) -- ++(6mm,0);
    \draw ([yshift=-5mm]current page.north) ++(0,-3mm) -- ++(0,6mm);
    \draw ([yshift=5mm]current page.south) ++(-3mm,0) -- ++(6mm,0);
    \draw ([yshift=5mm]current page.south) ++(0,-3mm) -- ++(0,6mm);
    \draw ([xshift=5mm]current page.west) ++(-3mm,0) -- ++(6mm,0);
    \draw ([xshift=5mm]current page.west) ++(0,-3mm) -- ++(0,6mm);
    \draw ([xshift=-5mm]current page.east) ++(-3mm,0) -- ++(6mm,0);
    \draw ([xshift=-5mm]current page.east) ++(0,-3mm) -- ++(0,6mm);
    \end{{tikzpicture}}%
}}

\pagestyle{{empty}}
\setlist[itemize]{{label={{}}, left=0.5em, itemsep=-2pt, topsep=0.5ex}}
\setlength{{\parindent}}{{0pt}}

\begin{{document}}
'''

    def _render_page(self, cards: tuple[Card, ...], *, side: str) -> str:
        command = r'\frontcard' if side == 'front' else r'\backcard'
        offset_x, offset_y = self.front_offset if side == 'front' else self.back_offset
        grid_width = self.cards_per_row * self.card_width
        result = r'\registrationmarks' + '\n' if self.registration_marks else ''
        result += f'\\vspace*{{{offset_y}mm}}%\n'
        result += f'\\noindent\\hspace*{{{offset_x}mm}}%\n'
        result += f'\\begin{{minipage}}[t]{{{grid_width}cm}}\n'
        for row in range(self.rows_per_page):
            for column in range(self.cards_per_row):
                index = row * self.cards_per_row + column
                text = getattr(cards[index], side)
                result += command + '{' + _card_content(text) + '}\n'
                if column < self.cards_per_row - 1:
                    result += '%\n'
            if row < self.rows_per_page - 1:
                result += '\n'
        return result + r'\end{minipage}' + '\n'
