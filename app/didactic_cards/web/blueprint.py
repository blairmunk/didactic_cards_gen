from flask import (Blueprint, render_template, request, redirect,
                   url_for, make_response, jsonify)
from ..domain.interfaces import CardRepository, DocumentRenderer, PdfCompiler
from ..use_cases.card_use_cases import (
    AddCard, AddCardsBulk, ImportCsv, DeleteCard,
    EditCard, ReorderCards, ResetCards, GetDeck,
    GenerateDocument, PreviewDocument
)

cards_bp = Blueprint(
    'cards', __name__,
    template_folder='templates',
    static_folder='static',
    static_url_path='/cards/static'
)

# Зависимости — инжектятся при регистрации Blueprint
_repo: CardRepository = None
_renderer: DocumentRenderer = None
_compiler: PdfCompiler = None
_cards_per_page: int = 8


def init_blueprint(repo: CardRepository, renderer: DocumentRenderer,
                   compiler: PdfCompiler, cards_per_page: int = 8):
    """Инъекция зависимостей в Blueprint."""
    global _repo, _renderer, _compiler, _cards_per_page
    _repo = repo
    _renderer = renderer
    _compiler = compiler
    _cards_per_page = cards_per_page


# ─── Страницы ───────────────────────────────────────────────────────

@cards_bp.route('/', methods=['GET'])
def index():
    deck = GetDeck(_repo).execute()
    return render_template('cards/index.html',
                           cards=deck.to_list(),
                           cards_count=len(deck),
                           cards_per_page=_cards_per_page)


@cards_bp.route('/add_card', methods=['POST'])
def add_card():
    front = request.form.get('front', '').strip()
    back = request.form.get('back', '').strip()
    if front or back:
        AddCard(_repo).execute(front, back)
    return redirect(url_for('cards.index'))


@cards_bp.route('/add_cards_bulk', methods=['POST'])
def add_cards_bulk():
    bulk = request.form.get('bulk', '')
    AddCardsBulk(_repo).execute(bulk)
    return redirect(url_for('cards.index'))


@cards_bp.route('/import_csv', methods=['POST'])
def import_csv():
    file = request.files.get('csv_file')
    if not file or file.filename == '':
        return redirect(url_for('cards.index'))
    try:
        file_bytes = file.stream.read()
        ImportCsv(_repo).execute(file_bytes)
    except UnicodeDecodeError:
        deck = GetDeck(_repo).execute()
        return render_template('cards/index.html',
                               cards=deck.to_list(),
                               cards_count=len(deck),
                               cards_per_page=_cards_per_page,
                               error='Ошибка кодировки. Сохраните CSV в UTF-8.')
    return redirect(url_for('cards.index'))


@cards_bp.route('/delete_card/<int:index>')
def delete_card(index):
    DeleteCard(_repo).execute(index)
    return redirect(url_for('cards.index'))


@cards_bp.route('/edit_card/<int:index>', methods=['GET', 'POST'])
def edit_card(index):
    deck = GetDeck(_repo).execute()
    if index < 0 or index >= len(deck):
        return redirect(url_for('cards.index'))

    if request.method == 'POST':
        front = request.form.get('front', '')
        back = request.form.get('back', '')
        EditCard(_repo).execute(index, front, back)
        return redirect(url_for('cards.index'))

    card = deck.cards[index].to_dict()
    return render_template('cards/edit_card.html', card=card, index=index)


@cards_bp.route('/reset', methods=['POST'])
def reset():
    ResetCards(_repo).execute()
    return redirect(url_for('cards.index'))


@cards_bp.route('/generate', methods=['POST'])
def generate():
    deck = GetDeck(_repo).execute()
    if not len(deck):
        return render_template('cards/index.html',
                               cards=[], cards_count=0,
                               cards_per_page=_cards_per_page,
                               error='Добавьте хотя бы одну карточку!')

    result = GenerateDocument(_repo, _renderer, _compiler, _cards_per_page).execute()

    if not result.success:
        return render_template('cards/error.html',
                               errors=[result.log],
                               full_log=result.log)

    response = make_response(result.pdf_data)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = 'attachment; filename=didactic_cards.pdf'
    return response



@cards_bp.route('/preview_latex', methods=['POST'])
def preview_latex():
    deck = GetDeck(_repo).execute()
    if not len(deck):
        return render_template('cards/index.html',
                               cards=[], cards_count=0,
                               cards_per_page=_cards_per_page,
                               error='Добавьте хотя бы одну карточку!')

    latex = PreviewDocument(_repo, _renderer, _cards_per_page).execute()
    return render_template('cards/result.html', latex_content=latex)


# ─── AJAX API ───────────────────────────────────────────────────────

@cards_bp.route('/api/add_card', methods=['POST'])
def api_add_card():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Нет данных'}), 400

    front = data.get('front', '').strip()
    back = data.get('back', '').strip()
    if not front and not back:
        return jsonify({'error': 'Заполните хотя бы одно поле'}), 400

    card, index = AddCard(_repo).execute(front, back)
    deck = GetDeck(_repo).execute()

    return jsonify({
        'ok': True,
        'card': card.to_dict(),
        'index': index,
        'cards_count': len(deck)
    })


@cards_bp.route('/api/delete_card/<int:index>', methods=['DELETE'])
def api_delete_card(index):
    result = DeleteCard(_repo).execute(index)
    if not result:
        return jsonify({'error': 'Неверный индекс'}), 404
    deck = GetDeck(_repo).execute()
    return jsonify({'ok': True, 'cards_count': len(deck)})


@cards_bp.route('/api/reorder', methods=['POST'])
def api_reorder():
    data = request.get_json()
    if not data or 'order' not in data:
        return jsonify({'error': 'Нет данных'}), 400
    result = ReorderCards(_repo).execute(data['order'])
    if not result:
        return jsonify({'error': 'Некорректный порядок'}), 400
    return jsonify({'ok': True})


@cards_bp.route('/api/edit_card/<int:index>', methods=['PUT'])
def api_edit_card(index):
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Нет данных'}), 400
    result = EditCard(_repo).execute(
        index, data.get('front', ''), data.get('back', ''))
    if not result:
        return jsonify({'error': 'Неверный индекс'}), 404
    deck = GetDeck(_repo).execute()
    return jsonify({'ok': True, 'card': deck.cards[index].to_dict()})