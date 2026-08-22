import io

from flask import (Blueprint, render_template, request, redirect,
                   url_for, jsonify, current_app, send_file)

from ..adapters.latex_renderer import UnsafeLatexError

from ..use_cases.card_use_cases import (
    AddCard, AddCardsBulk, ImportCsv, DeleteCard,
    EditCard, ReorderCards, ResetCards, GetDeck,
    GenerateDocument, PreviewDocument
)
from ..use_cases.deck_use_cases import (
    ListDecks, GetDeckInfo, CreateDeck, UpdateDeck,
    DeleteDeck, CloneDeck
)

cards_bp = Blueprint(
    'cards', __name__,
    template_folder='templates',
    static_folder='static',
    static_url_path='/cards/static'
)


def _repo():
    return current_app.config['REPO']


def _renderer():
    return current_app.config['RENDERER']


def _compiler():
    return current_app.config['COMPILER']


def _cards_per_page():
    return current_app.config['CARDS_PER_PAGE']


# ─── Колоды ─────────────────────────────────────────────────────────

@cards_bp.route('/', methods=['GET'])
def decks_list():
    decks = ListDecks(_repo()).execute()
    return render_template('cards/decks.html', decks=decks)


@cards_bp.route('/create_deck', methods=['POST'])
def create_deck():
    name = request.form.get('name', '').strip()
    desc = request.form.get('description', '').strip()
    CreateDeck(_repo()).execute(name, desc)
    return redirect(url_for('cards.decks_list'))


@cards_bp.route('/deck/<deck_id>/edit', methods=['GET', 'POST'])
def edit_deck(deck_id):
    deck_info = GetDeckInfo(_repo()).execute(deck_id)
    if not deck_info:
        return redirect(url_for('cards.decks_list'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        desc = request.form.get('description', '').strip()
        UpdateDeck(_repo()).execute(deck_id, name, desc)
        return redirect(url_for('cards.decks_list'))

    return render_template('cards/edit_deck.html', deck=deck_info)


@cards_bp.route('/deck/<deck_id>/delete', methods=['POST'])
def delete_deck(deck_id):
    DeleteDeck(_repo()).execute(deck_id)
    return redirect(url_for('cards.decks_list'))


@cards_bp.route('/deck/<deck_id>/clone', methods=['POST'])
def clone_deck(deck_id):
    CloneDeck(_repo()).execute(deck_id)
    return redirect(url_for('cards.decks_list'))


# ─── Карточки внутри колоды ─────────────────────────────────────────

@cards_bp.route('/deck/<deck_id>', methods=['GET'])
def deck_view(deck_id):
    deck_info = GetDeckInfo(_repo()).execute(deck_id)
    if not deck_info:
        return redirect(url_for('cards.decks_list'))

    card_deck = GetDeck(_repo()).execute(deck_id)
    return render_template('cards/index.html',
                           deck=deck_info,
                           cards=card_deck.to_list(),
                           cards_count=len(card_deck),
                           cards_per_page=_cards_per_page())


@cards_bp.route('/deck/<deck_id>/add_card', methods=['POST'])
def add_card(deck_id):
    front = request.form.get('front', '').strip()
    back = request.form.get('back', '').strip()
    if front or back:
        AddCard(_repo()).execute(deck_id, front, back)
    return redirect(url_for('cards.deck_view', deck_id=deck_id))


@cards_bp.route('/deck/<deck_id>/add_cards_bulk', methods=['POST'])
def add_cards_bulk(deck_id):
    bulk = request.form.get('bulk', '')
    AddCardsBulk(_repo()).execute(deck_id, bulk)
    return redirect(url_for('cards.deck_view', deck_id=deck_id))


@cards_bp.route('/deck/<deck_id>/import_csv', methods=['POST'])
def import_csv(deck_id):
    file = request.files.get('csv_file')
    if not file or file.filename == '':
        return redirect(url_for('cards.deck_view', deck_id=deck_id))
    try:
        file_bytes = file.stream.read()
        ImportCsv(_repo()).execute(deck_id, file_bytes)
    except UnicodeDecodeError:
        deck_info = GetDeckInfo(_repo()).execute(deck_id)
        card_deck = GetDeck(_repo()).execute(deck_id)
        return render_template('cards/index.html',
                               deck=deck_info,
                               cards=card_deck.to_list(),
                               cards_count=len(card_deck),
                               cards_per_page=_cards_per_page(),
                               error='Ошибка кодировки. Сохраните CSV в UTF-8.')
    return redirect(url_for('cards.deck_view', deck_id=deck_id))


@cards_bp.route('/deck/<deck_id>/delete_card/<int:index>')
def delete_card(deck_id, index):
    DeleteCard(_repo()).execute(deck_id, index)
    return redirect(url_for('cards.deck_view', deck_id=deck_id))


@cards_bp.route('/deck/<deck_id>/edit_card/<int:index>', methods=['GET', 'POST'])
def edit_card(deck_id, index):
    deck_info = GetDeckInfo(_repo()).execute(deck_id)
    if not deck_info:
        return redirect(url_for('cards.decks_list'))

    card_deck = GetDeck(_repo()).execute(deck_id)
    if index < 0 or index >= len(card_deck):
        return redirect(url_for('cards.deck_view', deck_id=deck_id))

    if request.method == 'POST':
        front = request.form.get('front', '')
        back = request.form.get('back', '')
        EditCard(_repo()).execute(deck_id, index, front, back)
        return redirect(url_for('cards.deck_view', deck_id=deck_id))

    card = card_deck.cards[index].to_dict()
    return render_template('cards/edit_card.html',
                           deck=deck_info, card=card, index=index)


@cards_bp.route('/deck/<deck_id>/reset', methods=['POST'])
def reset(deck_id):
    ResetCards(_repo()).execute(deck_id)
    return redirect(url_for('cards.deck_view', deck_id=deck_id))


@cards_bp.route('/deck/<deck_id>/generate', methods=['POST'])
def generate(deck_id):
    deck_info = GetDeckInfo(_repo()).execute(deck_id)
    card_deck = GetDeck(_repo()).execute(deck_id)

    if not len(card_deck):
        return render_template('cards/index.html',
                               deck=deck_info,
                               cards=[], cards_count=0,
                               cards_per_page=_cards_per_page(),
                               error='Добавьте хотя бы одну карточку!')

    try:
        result = GenerateDocument(
            _repo(), _renderer(), _compiler(), _cards_per_page()
        ).execute(deck_id)
    except UnsafeLatexError as error:
        return render_template(
            'cards/error.html', deck=deck_info,
            errors=[str(error)], full_log=''
        ), 422

    if not result.success:
        return render_template('cards/error.html', deck=deck_info,
                               errors=[result.log],
                               full_log=result.log), 422

    filename = f'{deck_info.name}.pdf' if deck_info else 'cards.pdf'
    return send_file(
        io.BytesIO(result.pdf_data),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename,
    )


@cards_bp.route('/deck/<deck_id>/preview_latex', methods=['POST'])
def preview_latex(deck_id):
    deck_info = GetDeckInfo(_repo()).execute(deck_id)
    card_deck = GetDeck(_repo()).execute(deck_id)

    if not len(card_deck):
        return render_template('cards/index.html',
                               deck=deck_info,
                               cards=[], cards_count=0,
                               cards_per_page=_cards_per_page(),
                               error='Добавьте хотя бы одну карточку!')

    try:
        latex = PreviewDocument(_repo(), _renderer(), _cards_per_page()).execute(deck_id)
    except UnsafeLatexError as error:
        return render_template(
            'cards/error.html', deck=deck_info,
            errors=[str(error)], full_log=''
        ), 422
    return render_template('cards/result.html', deck=deck_info, latex_content=latex)


# ─── AJAX API ───────────────────────────────────────────────────────

@cards_bp.route('/api/deck/<deck_id>/add_card', methods=['POST'])
def api_add_card(deck_id):
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Нет данных'}), 400

    front = data.get('front', '').strip()
    back = data.get('back', '').strip()
    if not front and not back:
        return jsonify({'error': 'Заполните хотя бы одно поле'}), 400

    card, index = AddCard(_repo()).execute(deck_id, front, back)
    card_deck = GetDeck(_repo()).execute(deck_id)

    return jsonify({
        'ok': True,
        'card': card.to_dict(),
        'index': index,
        'cards_count': len(card_deck)
    })


@cards_bp.route('/api/deck/<deck_id>/delete_card/<int:index>', methods=['DELETE'])
def api_delete_card(deck_id, index):
    result = DeleteCard(_repo()).execute(deck_id, index)
    if not result:
        return jsonify({'error': 'Неверный индекс'}), 404
    card_deck = GetDeck(_repo()).execute(deck_id)
    return jsonify({'ok': True, 'cards_count': len(card_deck)})


@cards_bp.route('/api/deck/<deck_id>/reorder', methods=['POST'])
def api_reorder(deck_id):
    data = request.get_json()
    if not data or 'order' not in data:
        return jsonify({'error': 'Нет данных'}), 400
    result = ReorderCards(_repo()).execute(deck_id, data['order'])
    if not result:
        return jsonify({'error': 'Некорректный порядок'}), 400
    return jsonify({'ok': True})


@cards_bp.route('/api/deck/<deck_id>/edit_card/<int:index>', methods=['PUT'])
def api_edit_card(deck_id, index):
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Нет данных'}), 400
    result = EditCard(_repo()).execute(
        deck_id, index, data.get('front', ''), data.get('back', ''))
    if not result:
        return jsonify({'error': 'Неверный индекс'}), 404
    card_deck = GetDeck(_repo()).execute(deck_id)
    return jsonify({'ok': True, 'card': card_deck.cards[index].to_dict()})
