from flask import Flask, render_template, request, redirect, url_for, send_file, session
import os
import tempfile
import subprocess
from utils.latex_generator import generate_latex_document

app = Flask(__name__)
app.secret_key = 'change-me-in-production-use-env-variable'


# ─── Helpers для работы с сессией ───────────────────────────────────

def get_cards():
    """Получить список карточек из Flask-сессии."""
    return session.get('cards', [])


def set_cards(cards):
    """Сохранить список карточек в Flask-сессию."""
    session['cards'] = cards


def _prepare_cards(cards):
    """
    Создаёт КОПИЮ списка карточек, дополненную пустыми до кратного 8.
    Оригинальный список НЕ изменяется.
    """
    padded = list(cards)  # копия!
    required = ((len(padded) + 7) // 8) * 8
    for i in range(len(padded), required):
        padded.append({'front': '', 'back': ''})
    return padded


# ─── Маршруты ───────────────────────────────────────────────────────

@app.route('/', methods=['GET'])
def index():
    cards = get_cards()
    return render_template('index.html', cards=cards, cards_count=len(cards))


@app.route('/add_card', methods=['POST'])
def add_card():
    front = request.form.get('front', '').strip()
    back = request.form.get('back', '').strip()

    if front and back:
        cards = get_cards()
        cards.append({'front': front, 'back': back})
        set_cards(cards)

    return redirect(url_for('index'))


@app.route('/delete_card/<int:index>')
def delete_card(index):
    cards = get_cards()
    if 0 <= index < len(cards):
        cards.pop(index)
        set_cards(cards)
    return redirect(url_for('index'))


@app.route('/edit_card/<int:index>', methods=['GET', 'POST'])
def edit_card(index):
    cards = get_cards()
    if index < 0 or index >= len(cards):
        return redirect(url_for('index'))

    if request.method == 'POST':
        cards[index]['front'] = request.form.get('front', '').strip()
        cards[index]['back'] = request.form.get('back', '').strip()
        set_cards(cards)
        return redirect(url_for('index'))

    return render_template('edit_card.html', card=cards[index], index=index)


@app.route('/reset', methods=['POST'])
def reset():
    set_cards([])
    return redirect(url_for('index'))


@app.route('/generate', methods=['POST'])
def generate():
    cards = get_cards()
    if not cards:
        return render_template('index.html', cards=[], cards_count=0,
                               error='Добавьте хотя бы одну карточку!')

    padded = _prepare_cards(cards)
    latex_content = generate_latex_document(padded)

    with tempfile.TemporaryDirectory() as temp_dir:
        tex_path = os.path.join(temp_dir, 'cards.tex')

        with open(tex_path, 'w', encoding='utf-8') as tex_file:
            tex_file.write(latex_content)

        try:
            result = subprocess.run(
                ['pdflatex', '-interaction=nonstopmode',
                 '-output-directory', temp_dir, tex_path],
                capture_output=True, text=True, timeout=30
            )

            pdf_path = os.path.join(temp_dir, 'cards.pdf')

            if result.returncode != 0 or not os.path.exists(pdf_path):
                # Извлекаем строки с ошибками из лога
                error_lines = [l for l in result.stdout.split('\n')
                               if l.startswith('!')]
                log_tail = result.stdout[-3000:] if result.stdout else ''
                return render_template('error.html',
                                       errors=error_lines,
                                       full_log=log_tail)

            return send_file(pdf_path,
                             as_attachment=True,
                             download_name='didactic_cards.pdf')

        except subprocess.TimeoutExpired:
            return render_template('error.html',
                                   errors=['Компиляция LaTeX превысила лимит времени (30 сек).'],
                                   full_log='')

        except FileNotFoundError:
            return render_template('error.html',
                                   errors=['pdflatex не найден. Установите TeX Live или MiKTeX.'],
                                   full_log='')


@app.route('/preview_latex', methods=['POST'])
def preview_latex():
    cards = get_cards()
    if not cards:
        return render_template('index.html', cards=[], cards_count=0,
                               error='Добавьте хотя бы одну карточку!')

    padded = _prepare_cards(cards)
    latex_content = generate_latex_document(padded)

    return render_template('result.html', latex_content=latex_content)



@app.route('/add_cards_bulk', methods=['POST'])
def add_cards_bulk():
    bulk = request.form.get('bulk', '')
    cards = get_cards()

    for line in bulk.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        if '||' in line:
            front, back = line.split('||', 1)
            cards.append({'front': front.strip(), 'back': back.strip()})
        else:
            cards.append({'front': line, 'back': ''})

    set_cards(cards)
    return redirect(url_for('index'))


@app.route('/import_csv', methods=['POST'])
def import_csv():
    import csv
    import io

    file = request.files.get('csv_file')
    if not file or file.filename == '':
        return redirect(url_for('index'))

    cards = get_cards()

    try:
        stream = io.StringIO(file.stream.read().decode('utf-8-sig'))
        reader = csv.reader(stream, delimiter=';')

        for row in reader:
            if len(row) >= 2:
                front = row[0].strip()
                back = row[1].strip()
                if front or back:
                    cards.append({'front': front, 'back': back})
            elif len(row) == 1 and row[0].strip():
                cards.append({'front': row[0].strip(), 'back': ''})

    except UnicodeDecodeError:
        cards_count = len(get_cards())
        return render_template('index.html',
                               cards=get_cards(),
                               cards_count=cards_count,
                               error='Ошибка кодировки файла. Сохраните CSV в UTF-8.')

    set_cards(cards)
    return redirect(url_for('index'))


if __name__ == '__main__':
    app.run(debug=True)