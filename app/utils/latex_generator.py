def generate_latex_document(cards):
    """
    Генерирует LaTeX документ для дидактических карточек.
    
    Args:
        cards: список карточек, где каждая карточка - словарь с ключами 'front' и 'back'
    
    Returns:
        строка с LaTeX кодом
    """
    # Подготовка и проверка данных
    num_cards = len(cards)
    num_pages = (num_cards + 7) // 8
    
    # Базовый шаблон LaTeX документа
    latex = r'''\documentclass[a4paper,12pt]{extarticle}
\usepackage{amsmath}
\usepackage{amsfonts}
\usepackage{amssymb}
\usepackage{amsthm} % в т.ч. для оформления доказательств
\usepackage{mathtext} % Поддержка кириллицы в формулах
\usepackage[T2A]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage[russian]{babel}
\usepackage{geometry}
\usepackage{array}
\usepackage{enumitem}
\usepackage{graphicx}
\usepackage{multicol}
\usepackage{xcolor}

% Настройка размера страницы с минимальными полями
\geometry{a4paper, margin=0.3cm, top=0.4cm, bottom=0.4cm}

% Определение размера карточки (A7) в горизонтальной ориентации
\newcommand{\cardwidth}{9.3cm}
\newcommand{\cardheight}{6.3cm}

% Настройка отступа вокруг содержимого в рамке
\setlength{\fboxsep}{8pt}
\setlength{\tabcolsep}{2pt}

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
    % Невидимая рамка с такими же отступами как у fbox
    \fcolorbox{white}{white}{%
        \begin{minipage}[t][\cardheight][t]{\cardwidth}
        #1
        \end{minipage}%
    }%
    \vspace{2pt}%
}

\pagestyle{empty}

\setlist[itemize]{label={}, left=0.5em, itemsep=-2pt, topsep=0.5ex}

\begin{document}
'''
    
    # Генерация страниц с передними сторонами карточек
    latex += "\n% Передние стороны карточек (задания)\n"
    
    for page in range(num_pages):
        latex += r"\noindent" + "\n"
        latex += r"\begin{tabular}{@{}cc@{}}" + "\n"
        
        for row in range(4):  # 4 ряда по 2 карточки
            idx1 = page * 8 + row * 2
            idx2 = page * 8 + row * 2 + 1
            
            if idx1 < num_cards and idx2 < num_cards:
                latex += r"\frontcard{" + cards[idx1]['front'].replace('#', r'\#') + "} & " + \
                         r"\frontcard{" + cards[idx2]['front'].replace('#', r'\#') + r"} \\" + "\n"
                if row < 3:  # Уменьшенный отступ между рядами
                    latex += r"[0.1cm]" + "\n"
            elif idx1 < num_cards:
                latex += r"\frontcard{" + cards[idx1]['front'].replace('#', r'\#') + "} & " + \
                         r"\frontcard{} \\" + "\n"
                if row < 3:
                    latex += r"[0.1cm]" + "\n"
            else:
                latex += r"\frontcard{} & \frontcard{} \\" + "\n"
                if row < 3:
                    latex += r"[0.1cm]" + "\n"
        
        latex += r"\end{tabular}" + "\n"
        
        if page < num_pages - 1:
            latex += r"\newpage" + "\n"
    
    # Генерация страниц с задними сторонами карточек (в обратном порядке)
    latex += "\n% Задние стороны карточек (решения) в обратном порядке\n"
    latex += r"\newpage" + "\n"
    
    for page in range(num_pages):
        latex += r"\noindent" + "\n"
        latex += r"\begin{tabular}{@{}cc@{}}" + "\n"
        
        for row in range(4):  # 4 ряда по 2 карточки, но в обратном порядке
            # Рассчитываем индексы с учетом переворота страницы
            # Для каждой страницы начинаем с нижнего ряда и двигаемся вверх
            reverse_row = 3 - row
            idx1 = page * 8 + reverse_row * 2
            idx2 = page * 8 + reverse_row * 2 + 1
            
            if idx1 < num_cards and idx2 < num_cards:
                latex += r"\backcard{" + cards[idx1]['back'].replace('#', r'\#') + "} & " + \
                         r"\backcard{" + cards[idx2]['back'].replace('#', r'\#') + r"} \\" + "\n"
                if row < 3:  # Уменьшенный отступ между рядами
                    latex += r"[0.1cm]" + "\n"
            elif idx1 < num_cards:
                latex += r"\backcard{" + cards[idx1]['back'].replace('#', r'\#') + "} & " + \
                         r"\backcard{} \\" + "\n"
                if row < 3:
                    latex += r"[0.1cm]" + "\n"
            else:
                latex += r"\backcard{} & \backcard{} \\" + "\n"
                if row < 3:
                    latex += r"[0.1cm]" + "\n"
        
        latex += r"\end{tabular}" + "\n"
        
        if page < num_pages - 1:
            latex += r"\newpage" + "\n"
    
    # Закрываем документ
    latex += r"\end{document}"
    
    return latex

