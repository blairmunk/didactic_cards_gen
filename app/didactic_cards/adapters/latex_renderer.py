from __future__ import annotations

import math
import re
from copy import copy

from ..domain.entities import Card, CardDeck
from ..domain.interfaces import DocumentRenderer
from ..domain.printing import (
    DuplexMode,
    PrintLayout,
    PrintPaddingCard,
    build_print_layout,
    build_sheets,
)
from ..domain.rendering import (
    DeckRenderSettings,
    HeaderPosition,
    HeaderRepeat,
    HeaderVisibility,
    HorizontalAlignment,
    StylePreset,
    VerticalAlignment,
)
from ..domain.trusted import (
    ContentMode,
    TrustedTemplateVersion,
    render_trusted_template,
)


PT_TO_CM = 2.54 / 72.27
PAGE_WIDTH_MM = 210.0
PAGE_HEIGHT_MM = 297.0
PAGE_MARGIN_MM = 5.0
HEADER_HEIGHT_CM = 0.48
HEADER_GAP_CM = 0.12

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
        back_rotation_deg: int = 180,
        front_offset_x_mm: float = 0.0,
        front_offset_y_mm: float = 0.0,
        back_offset_x_mm: float = 0.0,
        back_offset_y_mm: float = 0.0,
        registration_marks: bool = False,
        auto_fit: bool = True,
        render_settings: DeckRenderSettings | None = None,
        trusted_template: TrustedTemplateVersion | None = None,
    ):
        if cards_per_row <= 0 or rows_per_page <= 0:
            raise ValueError('cards_per_row and rows_per_page must be positive')
        if card_width_cm <= 0 or card_height_cm <= 0:
            raise ValueError('card dimensions must be positive')
        if fbox_sep_pt < 0 or fbox_rule_pt < 0:
            raise ValueError('frame spacing and rule must not be negative')
        if not isinstance(auto_fit, bool):
            raise ValueError('auto_fit must be boolean')
        if isinstance(back_rotation_deg, bool) or back_rotation_deg not in {0, 180}:
            raise ValueError('back rotation must be 0 or 180 degrees')
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
        self.back_rotation_deg = back_rotation_deg
        self.front_offset = (front_offset_x_mm, front_offset_y_mm)
        self.back_offset = (back_offset_x_mm, back_offset_y_mm)
        self.registration_marks = registration_marks
        self.auto_fit = auto_fit
        self.render_settings = (
            render_settings
            if render_settings is not None
            else DeckRenderSettings.legacy()
        )
        if not isinstance(self.render_settings, DeckRenderSettings):
            raise TypeError('render_settings must be DeckRenderSettings')
        if trusted_template is not None and not isinstance(
            trusted_template, TrustedTemplateVersion
        ):
            raise TypeError('trusted_template must be TrustedTemplateVersion')
        self.trusted_template = trusted_template
        self.cards_per_page = cards_per_row * rows_per_page

    def with_render_settings(
        self, settings: DeckRenderSettings
    ) -> LatexRenderer:
        if not isinstance(settings, DeckRenderSettings):
            raise TypeError('settings must be DeckRenderSettings')
        configured = copy(self)
        configured.render_settings = settings
        return configured

    def with_trusted_template(
        self, template: TrustedTemplateVersion | None
    ) -> LatexRenderer:
        if template is not None and not isinstance(
            template, TrustedTemplateVersion
        ):
            raise TypeError('template must be TrustedTemplateVersion')
        configured = copy(self)
        configured.trusted_template = template
        return configured

    def prepare_print_layout(
        self, deck: CardDeck, cards_per_page: int
    ) -> PrintLayout:
        if cards_per_page != self.cards_per_page:
            raise ValueError('cards_per_page does not match renderer grid')
        return build_print_layout(
            deck.cards,
            rows=self.rows_per_page,
            columns=self.cards_per_row,
            section_break=self.render_settings.section_break,
        )

    def render(self, deck: CardDeck) -> str:
        return self._render_sides(deck, ('front', 'back'))

    def render_fronts(self, deck: CardDeck) -> str:
        return self._render_sides(deck, ('front',))

    def render_backs(self, deck: CardDeck) -> str:
        return self._render_sides(deck, ('back',))

    def render_calibration_sheet(self) -> str:
        """Build a two-page duplex target using this profile's offsets."""
        front_x, front_y = self.front_offset
        back_x, back_y = self.back_offset
        mode = self.duplex_mode.value
        return rf'''\documentclass[a4paper,12pt]{{article}}
\usepackage[T2A]{{fontenc}}
\usepackage[utf8]{{inputenc}}
\usepackage[russian]{{babel}}
\usepackage{{geometry}}
\usepackage{{tikz}}
\usepackage{{xcolor}}
\geometry{{a4paper,margin=12mm}}
\pagestyle{{empty}}
\newcommand{{\calibrationtargets}}[5]{{%
  \noindent\begin{{tikzpicture}}[x=1mm,y=1mm]
    \path[use as bounding box] (0,0) rectangle (186,225);
    \begin{{scope}}[shift={{(#1,-#2)}}]
      \foreach \x/\y in {{18/30,168/30,18/195,168/195,93/112.5}}{{
        \draw[#3,line width=0.35pt] (\x-8,\y) -- (\x+8,\y);
        \draw[#3,line width=0.35pt] (\x,\y-8) -- (\x,\y+8);
        \draw[#3,line width=0.35pt] (\x,\y) circle (2);
      }}
      \draw[#3,line width=0.35pt] (43,12) -- (143,12);
      \foreach \x in {{55,60,...,155}}{{
        \draw[#3,line width=0.25pt] (\x-12,10.5) -- (\x-12,13.5);
      }}
      \node[#3,anchor=south] at (93,15) {{контрольная длина 100 мм}};
      \node[#3,anchor=north] at (93,100) {{#4}};
      \node[#3,rotate=#5] at (93,165) {{\Large $\uparrow$ ВЕРХ КАРТОЧКИ}};
    \end{{scope}}
  \end{{tikzpicture}}%
}}
\begin{{document}}
\noindent\parbox[t][35mm][t]{{\textwidth}}{{\centering
  {{\Large\bfseries Калибровочный лист: лицевая сторона}}\\[2mm]
  Режим переворота: \texttt{{{mode}}}. Масштаб печати: 100\% / Actual size.\\
  Сплошные чёрные мишени должны совпасть с пунктирными мишенями оборота.\\
  На просвет измеряйте расхождение у центрального креста по горизонтали и вертикали.}}
\calibrationtargets{{{front_x}}}{{{front_y}}}{{black}}{{ЛИЦО: сплошная линия}}{{0}}
\newpage
\noindent\parbox[t][35mm][t]{{\textwidth}}{{\centering
  {{\Large\bfseries Калибровочный лист: оборотная сторона}}\\[2mm]
  Профиль: \texttt{{{mode}}}; поворот содержимого: {self.back_rotation_deg}$^\circ$.\\
  Offsets X={back_x} мм, Y={back_y} мм.\\
  Эта страница должна печататься оборотом того же физического листа.\\
  Не используйте Fit, Shrink или дополнительное масштабирование драйвера.}}
\calibrationtargets{{{back_x}}}{{{back_y}}}{{magenta,dashed}}{{ОБОРОТ: пунктирная линия}}{{{self.back_rotation_deg}}}
\end{{document}}'''

    def printable_area_warnings(self) -> tuple[str, ...]:
        grid_width_mm = self.cards_per_row * self.card_width * 10
        grid_height_mm = self.rows_per_page * (
            self.card_height + 2 * PT_TO_CM
        ) * 10
        warnings = []
        for side, (offset_x, offset_y) in (
            ('Лицевая сторона', self.front_offset),
            ('Оборотная сторона', self.back_offset),
        ):
            left = PAGE_MARGIN_MM + offset_x
            right = left + grid_width_mm
            top = PAGE_MARGIN_MM + offset_y
            bottom = top + grid_height_mm
            if left < 0 or right > PAGE_WIDTH_MM:
                warnings.append(
                    f'{side}: горизонтальное смещение выводит сетку за '
                    'границы листа A4'
                )
            if top < 0 or bottom > PAGE_HEIGHT_MM:
                warnings.append(
                    f'{side}: вертикальное смещение выводит сетку за '
                    'границы листа A4'
                )
        return tuple(warnings)

    def _render_sides(self, deck: CardDeck, sides: tuple[str, ...]) -> str:
        card_numbers: dict[int, int] = {}
        logical_number = 0
        for card in deck.cards:
            if isinstance(card, PrintPaddingCard):
                card_numbers[id(card)] = 0
            else:
                logical_number += 1
                card_numbers[id(card)] = logical_number
        section_start_ids: set[int] = set()
        previous_section: str | None = None
        for card in deck.cards:
            if isinstance(card, PrintPaddingCard):
                continue
            if previous_section is None or card.section != previous_section:
                section_start_ids.add(id(card))
            previous_section = card.section
        sheets = build_sheets(
            deck.cards,
            rows=self.rows_per_page,
            columns=self.cards_per_row,
            duplex_mode=self.duplex_mode,
        )
        pages: list[tuple[int, str, tuple[Card, ...]]] = []
        for sheet_index, sheet in enumerate(sheets):
            if 'front' in sides:
                pages.append((sheet_index, 'front', sheet.front_slots))
            if 'back' in sides:
                pages.append((sheet_index, 'back', sheet.back_slots))

        latex = self._preamble()
        for page_index, (sheet_index, side, cards) in enumerate(pages):
            side_label = 'передние стороны' if side == 'front' else 'задние стороны'
            latex += f'\n% ===== Лист {sheet_index + 1}: {side_label} =====\n'
            latex += self._render_page(
                cards,
                side=side,
                card_numbers=card_numbers,
                section_start_ids=section_start_ids,
            )
            if page_index < len(pages) - 1:
                latex += r'\newpage' + '\n'

        latex += '\n\\end{document}'
        return latex

    def _preamble(self) -> str:
        back_frame = r'\fbox' if self.back_border else r'\cardblankframe'
        horizontal_commands = {
            HorizontalAlignment.LEFT: r'\raggedright',
            HorizontalAlignment.CENTER: r'\centering',
            HorizontalAlignment.RIGHT: r'\raggedleft',
        }
        vertical_positions = {
            VerticalAlignment.TOP: 't',
            VerticalAlignment.CENTER: 'c',
            VerticalAlignment.BOTTOM: 'b',
        }
        body_alignment = horizontal_commands[
            self.render_settings.horizontal_alignment
        ]
        header_alignment = horizontal_commands[
            self.render_settings.header_alignment
        ]
        vertical_position = vertical_positions[
            self.render_settings.vertical_alignment
        ]
        if self.auto_fit:
            fit_logic = r'''
    \ifdim\dimexpr\ht\cardcontentbox+\dp\cardcontentbox\relax>#3
        \setcardcontentbox{\small}{#4}{#1}{#2}%
        \ifdim\dimexpr\ht\cardcontentbox+\dp\cardcontentbox\relax>#3
            \setcardcontentbox{\footnotesize}{#4}{#1}{#2}%
            \ifdim\dimexpr\ht\cardcontentbox+\dp\cardcontentbox\relax>#3
                \setcardcontentbox{\scriptsize}{#4}{#1}{#2}%
                \ifdim\dimexpr\ht\cardcontentbox+\dp\cardcontentbox\relax>#3
                    \typeout{DIDACTIC-CARDS-OVERFLOW:#1:#2}%
                \else
                    \typeout{DIDACTIC-CARDS-AUTOFIT:#1:#2:scriptsize}%
                \fi
            \else
                \typeout{DIDACTIC-CARDS-AUTOFIT:#1:#2:footnotesize}%
            \fi
        \else
            \typeout{DIDACTIC-CARDS-AUTOFIT:#1:#2:small}%
        \fi
    \fi'''
        else:
            fit_logic = r'''
    \ifdim\dimexpr\ht\cardcontentbox+\dp\cardcontentbox\relax>#3
        \typeout{DIDACTIC-CARDS-OVERFLOW:#1:#2}%
    \fi'''
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
\usepackage{{eso-pic}}

\geometry{{a4paper, margin=0.5cm}}

\newcommand{{\cardwidth}}{{{self.card_width}cm}}
\newcommand{{\cardheight}}{{{self.card_height}cm}}
\newcommand{{\cardcontentwidth}}{{{self.card_content_width:.6f}cm}}
\newcommand{{\cardcontentheight}}{{{self.card_content_height:.6f}cm}}
\newcommand{{\cardheaderheight}}{{{HEADER_HEIGHT_CM}cm}}
\newcommand{{\cardbodyalign}}{{{body_alignment}}}
\newcommand{{\cardheaderalign}}{{{header_alignment}}}

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
\newbox\cardcontentbox
\newbox\cardheaderbox
\newcommand{{\setcardcontentbox}}[4]{{%
    \typeout{{DIDACTIC-CARDS-HBOX-BEGIN:#3:#4:body}}%
    \setbox\cardcontentbox=\vbox{{%
        \hsize=\cardcontentwidth #1\cardbodyalign\noindent #2\par
    }}%
    \typeout{{DIDACTIC-CARDS-HBOX-END:#3:#4:body}}%
}}
\newcommand{{\fitcardcontent}}[4]{{%
    \setcardcontentbox{{\normalsize}}{{#4}}{{#1}}{{#2}}%{fit_logic}
}}
\newcommand{{\legacycheckedcardcontent}}[3]{{%
    \fitcardcontent{{#1}}{{#2}}{{\cardcontentheight}}{{#3}}%
    \box\cardcontentbox
}}
\newcommand{{\checkedcardcontent}}[4]{{%
    \fitcardcontent{{#1}}{{#2}}{{#3}}{{#4}}%
    \begin{{minipage}}[t][#3][{vertical_position}]{{\cardcontentwidth}}%
        \box\cardcontentbox
    \end{{minipage}}%
}}
\newcommand{{\setcardheaderbox}}[4]{{%
    \typeout{{DIDACTIC-CARDS-HBOX-BEGIN:#3:#4:header}}%
    \setbox\cardheaderbox=\vbox{{%
        \hsize=\cardcontentwidth #1\cardheaderalign\noindent #2\par
    }}%
    \typeout{{DIDACTIC-CARDS-HBOX-END:#3:#4:header}}%
}}
\newcommand{{\checkedcardheader}}[3]{{%
    \setcardheaderbox{{\footnotesize}}{{#3}}{{#1}}{{#2}}%
    \ifdim\dimexpr\ht\cardheaderbox+\dp\cardheaderbox\relax>\cardheaderheight
        \setcardheaderbox{{\scriptsize}}{{#3}}{{#1}}{{#2}}%
        \ifdim\dimexpr\ht\cardheaderbox+\dp\cardheaderbox\relax>\cardheaderheight
            \typeout{{DIDACTIC-CARDS-HEADER-OVERFLOW:#1:#2}}%
        \else
            \typeout{{DIDACTIC-CARDS-HEADER-AUTOFIT:#1:#2:scriptsize}}%
        \fi
    \fi
    \begin{{minipage}}[t][\cardheaderheight][c]{{\cardcontentwidth}}%
        \box\cardheaderbox
    \end{{minipage}}%
}}
\newcommand{{\registrationmarks}}{{%
    \AddToShipoutPictureFG*{{%
      \AtPageUpperLeft{{%
        \linethickness{{0.35pt}}%
        \put(\LenToUnit{{102mm}},\LenToUnit{{-5mm}}){{\line(1,0){{\LenToUnit{{6mm}}}}}}%
        \put(\LenToUnit{{105mm}},\LenToUnit{{-8mm}}){{\line(0,1){{\LenToUnit{{6mm}}}}}}%
        \put(\LenToUnit{{102mm}},\LenToUnit{{-292mm}}){{\line(1,0){{\LenToUnit{{6mm}}}}}}%
        \put(\LenToUnit{{105mm}},\LenToUnit{{-295mm}}){{\line(0,1){{\LenToUnit{{6mm}}}}}}%
        \put(\LenToUnit{{2mm}},\LenToUnit{{-148.5mm}}){{\line(1,0){{\LenToUnit{{6mm}}}}}}%
        \put(\LenToUnit{{5mm}},\LenToUnit{{-151.5mm}}){{\line(0,1){{\LenToUnit{{6mm}}}}}}%
        \put(\LenToUnit{{202mm}},\LenToUnit{{-148.5mm}}){{\line(1,0){{\LenToUnit{{6mm}}}}}}%
        \put(\LenToUnit{{205mm}},\LenToUnit{{-151.5mm}}){{\line(0,1){{\LenToUnit{{6mm}}}}}}%
      }}%
    }}%
}}

\pagestyle{{empty}}
\setlist[itemize]{{label={{}}, left=0.5em, itemsep=-2pt, topsep=0.5ex}}
\setlength{{\parindent}}{{0pt}}

\begin{{document}}
'''

    def _render_page(
        self,
        cards: tuple[Card, ...],
        *,
        side: str,
        card_numbers: dict[int, int],
        section_start_ids: set[int],
    ) -> str:
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
                card = cards[index]
                text = getattr(card, side)
                card_number = card_numbers.get(id(card), 0)
                checked_content = self._render_card_content(
                    card,
                    side=side,
                    card_number=card_number,
                    text=text,
                    is_section_start=id(card) in section_start_ids,
                )
                rendered_card = command + '{' + checked_content + '}'
                if side == 'back' and self.back_rotation_deg == 180:
                    rendered_card = r'\rotatebox{180}{' + rendered_card + '}'
                result += rendered_card + '\n'
                if column < self.cards_per_row - 1:
                    result += '%\n'
            if row < self.rows_per_page - 1:
                result += '\n'
        return result + r'\end{minipage}' + '\n'

    def _header_is_visible(self, side: str, *, is_section_start: bool) -> bool:
        visibility = self.render_settings.header_visibility
        visible_on_side = visibility is HeaderVisibility.BOTH or (
            visibility is HeaderVisibility.FRONT and side == 'front'
        ) or (
            visibility is HeaderVisibility.BACK and side == 'back'
        )
        if not visible_on_side:
            return False
        return (
            self.render_settings.header_repeat is HeaderRepeat.EVERY_CARD
            or is_section_start
        )

    def _render_card_content(
        self,
        card: Card,
        *,
        side: str,
        card_number: int,
        text: str,
        is_section_start: bool,
    ) -> str:
        if self.trusted_template is not None and card_number:
            mode = (
                self.trusted_template.front_content_mode
                if side == 'front'
                else self.trusted_template.back_content_mode
            )
            content = text if mode is ContentMode.RAW else _card_content(text)
            fragment = render_trusted_template(
                self.trusted_template.source,
                content=content,
                section=_card_content(card.section),
                card_number=card_number,
                side=side,
            )
            return (
                f'\\typeout{{DIDACTIC-CARDS-HBOX-BEGIN:{card_number}:{side}:body}}%\n'
                + fragment
                + f'\n\\typeout{{DIDACTIC-CARDS-HBOX-END:{card_number}:{side}:body}}'
            )
        settings = self.render_settings
        legacy_layout = (
            settings.preset is StylePreset.LEGACY_TOP_LEFT
            and settings.horizontal_alignment is HorizontalAlignment.LEFT
            and settings.vertical_alignment is VerticalAlignment.TOP
            and settings.header_visibility is HeaderVisibility.NONE
        )
        content = _card_content(text)
        if legacy_layout:
            return (
                r'\legacycheckedcardcontent'
                f'{{{card_number}}}{{{side}}}{{{content}}}'
            )

        header_visible = self._header_is_visible(
            side, is_section_start=is_section_start
        )
        reserved_height = (
            HEADER_HEIGHT_CM + HEADER_GAP_CM if header_visible else 0.0
        )
        body_height = self.card_content_height - reserved_height
        body = (
            r'\checkedcardcontent'
            f'{{{card_number}}}{{{side}}}{{{body_height:.6f}cm}}'
            f'{{{content}}}'
        )
        if not header_visible:
            return body

        header = (
            r'\checkedcardheader'
            f'{{{card_number}}}{{{side}}}{{{_card_content(card.section)}}}'
        )
        gap = (
            r'\par\nointerlineskip'
            rf'\vspace*{{{HEADER_GAP_CM}cm}}\noindent'
        )
        if settings.header_position is HeaderPosition.TOP:
            return header + gap + body
        return body + gap + header
