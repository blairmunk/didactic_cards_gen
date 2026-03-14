def escape_latex(text):
    """Экранирование всех спецсимволов LaTeX."""
    text = text.replace('\\', r'\textbackslash{}')

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

    return text


def generate_latex_document(cards, layout=None):
    """
    Генерирует LaTeX документ для дидактических карточек.

    Args:
        cards: список карточек (длина кратна cards_per_page).
        layout: объект CardLayoutConfig (или None для значений по умолчанию).

    Returns:
        строка с LaTeX кодом.
    """
    # Значения по умолчанию, если config не передан
    if layout is None:
        card_width = 9.3
        card_height = 6.3
        cards_per_row = 2
        rows_per_page = 4
        fbox_sep = 8
    else:
        card_width = layout.card_width_cm
        card_height = layout.card_height_cm
        cards_per_row = layout.cards_per_row
        rows_per_page = layout.rows_per_page
        fbox_sep = layout.fbox_sep_pt

    cards_per_page = cards_per_row * rows_per_page
    num_cards = len(cards)
    num_pages = (num_cards + cards_per_page - 1) // cards_per_page

    # ─── Преамбула ───────────────────────────────────────────────
    latex = rf'''\documentclass[a4paper,12pt]{{extarticle}}
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

\newcommand{{\cardwidth}}{{{card_width}cm}}
\newcommand{{\cardheight}}{{{card_height}cm}}

\setlength{{\fboxsep}}{{{fbox_sep}pt}}

\newcommand{{\frontcard}}[1]{{%
    \fbox{{%
        \begin{{minipage}}[t][\cardheight][t]{{\cardwidth}}
        #1
        \end{{minipage}}%
    }}%
    \vspace{{2pt}}%
}}

\newcommand{{\backcard}}[1]{{%
    \fcolorbox{{white}}{{white}}{{%
        \begin{{minipage}}[t][\cardheight][t]{{\cardwidth}}
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

    # ─── Передние стороны ────────────────────────────────────────
    latex += "\n% ===== Передние стороны карточек (задания) =====\n"

    for page in range(num_pages):
        latex += "\n"
        for row in range(rows_per_page):
            for col in range(cards_per_row):
                idx = page * cards_per_page + row * cards_per_row + col
                front = escape_latex(cards[idx]['front']) if idx < num_cards else ''
                latex += r"\frontcard{" + front + "}\n"
                if col < cards_per_row - 1:
                    latex += "%\n"
            if row < rows_per_page - 1:
                latex += "\n"

        if page < num_pages - 1:
            latex += r"\newpage" + "\n"

    # ─── Задние стороны ──────────────────────────────────────────
    latex += "\n% ===== Задние стороны карточек (решения) =====\n"
    latex += r"\newpage" + "\n"

    for page in range(num_pages):
        latex += "\n"
        for row in range(rows_per_page):
            for col in range(cards_per_row):
                # Зеркальный порядок в строке для двусторонней печати
                mirror_col = cards_per_row - 1 - col
                idx = page * cards_per_page + row * cards_per_row + mirror_col
                back = escape_latex(cards[idx]['back']) if idx < num_cards else ''
                latex += r"\rotatebox{180}{\backcard{" + back + "}}\n"
                if col < cards_per_row - 1:
                    latex += "%\n"
            if row < rows_per_page - 1:
                latex += "\n"

        if page < num_pages - 1:
            latex += r"\newpage" + "\n"

    latex += "\n" + r"\end{document}"
    return latex