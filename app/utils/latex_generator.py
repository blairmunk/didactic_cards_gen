def escape_latex(text):
    """
    Экранирование всех спецсимволов LaTeX.
    Порядок важен: сначала бэкслеш, потом остальные.
    """
    # Бэкслеш — первым, иначе заэкранируем добавленные бэкслеши
    text = text.replace('\\', r'\textbackslash{}')
    
    chars = {
        '&':  r'\&',
        '%':  r'\%',
        '$':  r'\$',
        '#':  r'\#',
        '_':  r'\_',
        '{':  r'\{',
        '}':  r'\}',
        '~':  r'\textasciitilde{}',
        '^':  r'\textasciicircum{}',
    }
    for char, replacement in chars.items():
        text = text.replace(char, replacement)
    
    return text


def generate_latex_document(cards):
    """
    Генерирует LaTeX документ для дидактических карточек.
    
    Args:
        cards: список карточек, где каждая карточка — словарь с ключами 'front' и 'back'.
               Длина списка должна быть кратна 8.
    
    Returns:
        строка с LaTeX кодом
    """
    num_cards = len(cards)
    num_pages = (num_cards + 7) // 8

    # ─── Преамбула ───────────────────────────────────────────────────
    latex = r'''\documentclass[a4paper,12pt]{extarticle}
\usepackage{amsmath}
\usepackage{amsfonts}
\usepackage{amssymb}
\usepackage{amsthm}
\usepackage{mathtext}
\usepackage[T2A]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage[russian]{babel}
\usepackage{geometry}
\usepackage{graphicx}
\usepackage{array}
\usepackage{enumitem}
\usepackage{multicol}
\usepackage{xcolor}

% Настройка размера страницы с минимальными полями
\geometry{a4paper, margin=0.5cm}

% Определение размера карточки (A7) в горизонтальной ориентации
\newcommand{\cardwidth}{9.3cm}
\newcommand{\cardheight}{6.3cm}

% Настройка отступа вокруг содержимого в рамке
\setlength{\fboxsep}{8pt}

% Стили для карточек
\newcommand{\frontcard}[1]{%
    \fbox{%
        \begin{minipage}[t][\cardheight][t]{\cardwidth}
        #1
        \end{minipage}%
    }%
    \vspace{2pt}%
}

\newcommand{\backcard}[1]{%
    \fcolorbox{white}{white}{%
        \begin{minipage}[t][\cardheight][t]{\cardwidth}
        #1
        \end{minipage}%
    }%
    \vspace{2pt}%
}

\pagestyle{empty}

\setlist[itemize]{label={}, left=0.5em, itemsep=-2pt, topsep=0.5ex}
\setlength{\parindent}{0pt}

\begin{document}
'''

    # ─── Передние стороны ────────────────────────────────────────────
    latex += "\n% ===== Передние стороны карточек (задания) =====\n"

    for page in range(num_pages):
        latex += "\n"
        for row in range(4):
            idx1 = page * 8 + row * 2
            idx2 = page * 8 + row * 2 + 1

            front1 = escape_latex(cards[idx1]['front']) if idx1 < num_cards else ''
            front2 = escape_latex(cards[idx2]['front']) if idx2 < num_cards else ''

            latex += r"\frontcard{" + front1 + "}\n%\n"
            latex += r"\frontcard{" + front2 + "}\n"
            if row < 3:
                latex += "\n"

        if page < num_pages - 1:
            latex += r"\newpage" + "\n"

    # ─── Задние стороны ──────────────────────────────────────────────
    latex += "\n% ===== Задние стороны карточек (решения) =====\n"
    latex += r"\newpage" + "\n"

    for page in range(num_pages):
        latex += "\n"
        for row in range(4):
            # Swap: правая карточка печатается первой (зеркальный порядок)
            idx1 = page * 8 + row * 2 + 1
            idx2 = page * 8 + row * 2

            back1 = escape_latex(cards[idx1]['back']) if idx1 < num_cards else ''
            back2 = escape_latex(cards[idx2]['back']) if idx2 < num_cards else ''

            latex += r"\rotatebox{180}{\backcard{" + back1 + "}}\n%\n"
            latex += r"\rotatebox{180}{\backcard{" + back2 + "}}\n"
            if row < 3:
                latex += "\n"

        if page < num_pages - 1:
            latex += r"\newpage" + "\n"

    latex += "\n" + r"\end{document}"
    return latex