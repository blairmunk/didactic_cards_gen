from flask import (Flask, render_template, request, redirect,
                   url_for, send_file, session, jsonify)
import os
import tempfile
import subprocess
import csv
import io
from config import AppConfig
from utils.latex_generator import generate_latex_document

config = AppConfig()

app = Flask(__name__)
app.secret_key = config.secret_key


# ─── Helpers ────────────────────────────────────────────────────────

def get_cards():
    return session.get('cards', [])


def set_cards(cards):
    session['cards'] = cards


def _prepare_cards(cards):
    padded = list(cards)
    per_page = config.layout.cards_per_page
    required = ((len(padded) + per_page - 1) // per_page) * per_page
    for i in range(len(padded), required):
        padded.append({'front': '', 'back': ''})
    return padded


# ─── Страницы ───────────────────────────────────────────────────────

@app.route('/', methods=['GET'])
def index():
    cards = get_cards()
    return render_template('index.html', cards=cards, cards_count=len(cards),
                           cards_per_page=config.layout.cards_per_page)


# ─── CRUD карточек (обычные POST) ──────────────────────────────────

@app.route('/add_card', methods=['POST'])
def add_card():
    front = request.form.get('front', '').strip()
    back = request.form.get('back', '').strip()

    if front or back:
        cards = get_cards()
        cards.append({'front': front, 'back': back})
        set_cards(cards)

    return redirect(url_for('index'))


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
        return render_template('index.html',
                               cards=get_cards(),
                               cards_count=len(get_cards()),
                               cards_per_page=config.layout.cards_per_page,
                               error='Ошибка кодировки файла. Сохраните CSV в UTF-8.')

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


# ─── AJAX API ──────────────────────────────────────────────────────

@app.route('/api/add_card', methods=['POST'])
def api_add_card():
    """Добавление карточки через AJAX, без перезагрузки страницы."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Нет данных'}), 400

    front = data.get('front', '').strip()
    back = data.get('back', '').strip()

    if not front and not back:
        return jsonify({'error': 'Заполните хотя бы одно поле'}), 400

    cards = get_cards()
    card = {'front': front, 'back': back}
    cards.append(card)
    set_cards(cards)

    return jsonify({
        'ok': True,
        'card': card,
        'index': len(cards) - 1,
        'cards_count': len(cards)
    })


@app.route('/api/delete_card/<int:index>', methods=['DELETE'])
def api_delete_card(index):
    """Удаление карточки через AJAX."""
    cards = get_cards()
    if 0 <= index < len(cards):
        cards.pop(index)
        set_cards(cards)
        return jsonify({'ok': True, 'cards_count': len(cards)})
    return jsonify({'error': 'Неверный индекс'}), 404


@app.route('/api/reorder', methods=['POST'])
def api_reorder():
    """Изменение порядка карточек через drag-and-drop."""
    data = request.get_json()
    if not data or 'order' not in data:
        return jsonify({'error': 'Нет данных'}), 400

    order = data['order']  # список индексов в новом порядке
    cards = get_cards()

    # Валидация
    if sorted(order) != list(range(len(cards))):
        return jsonify({'error': 'Некорректный порядок'}), 400

    reordered = [cards[i] for i in order]
    set_cards(reordered)

    return jsonify({'ok': True})


@app.route('/api/edit_card/<int:index>', methods=['PUT'])
def api_edit_card(index):
    """Редактирование карточки через AJAX."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Нет данных'}), 400

    cards = get_cards()
    if index < 0 or index >= len(cards):
        return jsonify({'error': 'Неверный индекс'}), 404

    cards[index]['front'] = data.get('front', '').strip()
    cards[index]['back'] = data.get('back', '').strip()
    set_cards(cards)

    return jsonify({'ok': True, 'card': cards[index]})


# ─── Генерация ─────────────────────────────────────────────────────

@app.route('/generate', methods=['POST'])
def generate():
    cards = get_cards()
    if not cards:
        return render_template('index.html', cards=[], cards_count=0,
                               cards_per_page=config.layout.cards_per_page,
                               error='Добавьте хотя бы одну карточку!')

    padded = _prepare_cards(cards)
    latex_content = generate_latex_document(padded, config.layout)

    with tempfile.TemporaryDirectory() as temp_dir:
        tex_path = os.path.join(temp_dir, 'cards.tex')

        with open(tex_path, 'w', encoding='utf-8') as tex_file:
            tex_file.write(latex_content)

        try:
            result = subprocess.run(
                [config.pdflatex_path, '-interaction=nonstopmode',
                 '-output-directory', temp_dir, tex_path],
                capture_output=True, text=True, timeout=config.pdflatex_timeout
            )

            pdf_path = os.path.join(temp_dir, 'cards.pdf')

            if result.returncode != 0 or not os.path.exists(pdf_path):
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
                                   errors=[f'Компиляция LaTeX превысила лимит времени ({config.pdflatex_timeout} сек).'],
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
                               cards_per_page=config.layout.cards_per_page,
                               error='Добавьте хотя бы одну карточку!')

    padded = _prepare_cards(cards)
    latex_content = generate_latex_document(padded, config.layout)

    return render_template('result.html', latex_content=latex_content)


if __name__ == '__main__':
    app.run(debug=True)