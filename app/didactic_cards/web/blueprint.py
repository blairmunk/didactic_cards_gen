import io
import hmac
import secrets

from flask import (Blueprint, render_template, request, redirect,
                   url_for, jsonify, current_app, send_file, session, abort)

from ..adapters.latex_renderer import UnsafeLatexError
from ..adapters.json_repository import DeckNotFoundError, RepositoryStorageError
from ..domain.interfaces import ConcurrentModificationError

from ..use_cases.card_use_cases import (
    AddCard, AddCardsBulk, ImportCsv, DeleteCard,
    EditCard, ReorderCards, ResetCards, GetDeck,
    GenerateDocument, GenerateDocumentSide, PreviewDocument, CardLimitExceeded,
    PreflightDocument, CsvValidationError, preview_csv_import,
)
from ..use_cases.deck_use_cases import (
    ListDecks, GetDeckInfo, CreateDeck, UpdateDeck,
    DeleteDeck, CloneDeck
)
from ..use_cases.deck_transfer import (
    DeckTransferError,
    export_deck_csv,
    export_deck_json,
    import_deck_json,
)

cards_bp = Blueprint(
    'cards', __name__,
    template_folder='templates',
    static_folder='static',
    static_url_path='/cards/static'
)


def _repo():
    return current_app.config['REPO']


def _renderer(profile_id: str | None = None):
    if not profile_id:
        return current_app.config['RENDERER']
    profile = current_app.config.get('PRINT_PROFILES', {}).get(profile_id)
    factory = current_app.config.get('RENDERER_FACTORY')
    if profile is None or factory is None:
        abort(400, description='Неизвестный профиль принтера')
    return factory(profile)


def _print_profiles():
    return tuple(current_app.config.get('PRINT_PROFILES', {}).values())


def _compiler():
    return current_app.config['COMPILER']


def _cards_per_page():
    return current_app.config['CARDS_PER_PAGE']


def _max_cards():
    return current_app.config.get('MAX_CARDS')


def _optional_version(value) -> int | None:
    if value is None or value == '':
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        abort(400, description='Некорректная версия колоды')
    if isinstance(value, str) and not value.isdigit():
        abort(400, description='Некорректная версия колоды')
    version = int(value)
    if version <= 0:
        abort(400, description='Некорректная версия колоды')
    return version


def _csrf_token() -> str:
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['_csrf_token'] = token
    return token


@cards_bp.context_processor
def inject_csrf_token():
    return {
        'csrf_token': _csrf_token,
        'print_profiles': _print_profiles(),
    }


@cards_bp.before_app_request
def protect_html_forms():
    if not current_app.config.get('CSRF_ENABLED', True):
        return None
    if request.blueprint != cards_bp.name or request.path.startswith('/api/'):
        return None
    if request.method not in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        return None

    submitted = request.form.get('_csrf_token', '')
    expected = session.get('_csrf_token', '')
    if not submitted or not expected or not hmac.compare_digest(submitted, expected):
        abort(400, description='Недействительный CSRF-токен')
    return None


@cards_bp.after_app_request
def add_security_headers(response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'no-referrer')
    response.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "font-src 'self' data:; object-src 'none'; "
        "frame-src 'self' blob:; base-uri 'self'; frame-ancestors 'none'",
    )
    return response


@cards_bp.app_errorhandler(DeckNotFoundError)
def handle_missing_deck(_error):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Колода не найдена'}), 404
    return redirect(url_for('cards.decks_list'))


@cards_bp.app_errorhandler(RepositoryStorageError)
def handle_repository_corruption(error):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Хранилище данных повреждено'}), 500
    return render_template(
        'cards/error.html',
        deck=None,
        errors=[
            'Хранилище данных повреждено. Запись остановлена; '
            'восстановите JSON из резервной копии.'
        ],
        full_log=str(error) if current_app.debug else '',
    ), 500


@cards_bp.app_errorhandler(ConcurrentModificationError)
def handle_concurrent_modification(error):
    message = 'Колода уже изменена в другой вкладке. Обновите страницу.'
    if request.path.startswith('/api/'):
        return jsonify({
            'error': message,
            'current_version': error.actual,
        }), 409
    return render_template(
        'cards/error.html', deck=None, errors=[message], full_log=''
    ), 409


def _render_deck_error(deck_id: str, message: str, status: int = 409):
    deck_info = GetDeckInfo(_repo()).execute(deck_id)
    card_deck = GetDeck(_repo()).execute(deck_id)
    return render_template(
        'cards/index.html',
        deck=deck_info,
        cards=card_deck.to_list(),
        cards_count=len(card_deck),
        cards_per_page=_cards_per_page(),
        error=message,
    ), status


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


@cards_bp.route('/deck/<deck_id>/export.json', methods=['GET'])
def export_deck_as_json(deck_id):
    deck = GetDeckInfo(_repo()).execute(deck_id)
    if deck is None:
        return redirect(url_for('cards.decks_list'))
    return send_file(
        io.BytesIO(export_deck_json(_repo(), deck_id)),
        mimetype='application/json',
        as_attachment=True,
        download_name=f'{deck.name}.didactic-cards.json',
    )


@cards_bp.route('/deck/<deck_id>/export.csv', methods=['GET'])
def export_deck_as_csv(deck_id):
    deck = GetDeckInfo(_repo()).execute(deck_id)
    if deck is None:
        return redirect(url_for('cards.decks_list'))
    return send_file(
        io.BytesIO(export_deck_csv(_repo(), deck_id)),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'{deck.name}.csv',
    )


@cards_bp.route('/import_deck', methods=['POST'])
def import_deck():
    file = request.files.get('deck_file')
    if not file or file.filename == '':
        return render_template(
            'cards/decks.html',
            decks=ListDecks(_repo()).execute(),
            error='Выберите JSON export-файл.',
        ), 400
    try:
        deck = import_deck_json(_repo(), file.stream.read(), _max_cards())
    except DeckTransferError as error:
        return render_template(
            'cards/decks.html',
            decks=ListDecks(_repo()).execute(),
            error=str(error),
        ), 400
    return redirect(url_for('cards.deck_view', deck_id=deck.id))


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
                           cards_per_page=_cards_per_page(),
                           print_profiles=_print_profiles())


@cards_bp.route('/deck/<deck_id>/add_card', methods=['POST'])
def add_card(deck_id):
    front = request.form.get('front', '').strip()
    back = request.form.get('back', '').strip()
    if front or back:
        try:
            AddCard(_repo(), _max_cards()).execute(
                deck_id, front, back, _optional_version(request.form.get('version'))
            )
        except CardLimitExceeded as error:
            return _render_deck_error(deck_id, str(error))
    return redirect(url_for('cards.deck_view', deck_id=deck_id))


@cards_bp.route('/deck/<deck_id>/add_cards_bulk', methods=['POST'])
def add_cards_bulk(deck_id):
    bulk = request.form.get('bulk', '')
    try:
        AddCardsBulk(_repo(), _max_cards()).execute(
            deck_id, bulk, _optional_version(request.form.get('version'))
        )
    except CardLimitExceeded as error:
        return _render_deck_error(deck_id, str(error))
    return redirect(url_for('cards.deck_view', deck_id=deck_id))


@cards_bp.route('/api/deck/<deck_id>/preview_csv', methods=['POST'])
def api_preview_csv(deck_id):
    if GetDeckInfo(_repo()).execute(deck_id) is None:
        raise DeckNotFoundError(deck_id)
    file = request.files.get('csv_file')
    if not file or file.filename == '':
        return jsonify({'error': 'Выберите CSV-файл'}), 400
    try:
        preview = preview_csv_import(
            file.stream.read(),
            request.form.get('delimiter', 'auto'),
            request.form.get('has_header') == 'on',
        )
    except UnicodeDecodeError:
        return jsonify({'error': 'CSV должен быть сохранён в UTF-8'}), 400
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    return jsonify({'ok': True, **preview.to_dict()})


@cards_bp.route('/deck/<deck_id>/import_csv', methods=['POST'])
def import_csv(deck_id):
    file = request.files.get('csv_file')
    if not file or file.filename == '':
        return redirect(url_for('cards.deck_view', deck_id=deck_id))
    try:
        file_bytes = file.stream.read()
        ImportCsv(_repo(), _max_cards()).execute(
            deck_id,
            file_bytes,
            _optional_version(request.form.get('version')),
            delimiter=request.form.get('delimiter', 'auto'),
            has_header=request.form.get('has_header') == 'on',
        )
    except CardLimitExceeded as error:
        return _render_deck_error(deck_id, str(error))
    except UnicodeDecodeError:
        deck_info = GetDeckInfo(_repo()).execute(deck_id)
        card_deck = GetDeck(_repo()).execute(deck_id)
        return render_template('cards/index.html',
                               deck=deck_info,
                               cards=card_deck.to_list(),
                               cards_count=len(card_deck),
                               cards_per_page=_cards_per_page(),
                               error='Ошибка кодировки. Сохраните CSV в UTF-8.')
    except (CsvValidationError, ValueError) as error:
        return _render_deck_error(deck_id, str(error), 400)
    return redirect(url_for('cards.deck_view', deck_id=deck_id))


@cards_bp.route('/deck/<deck_id>/delete_card/<card_id>', methods=['POST'])
def delete_card(deck_id, card_id):
    DeleteCard(_repo()).execute(
        deck_id, card_id, _optional_version(request.form.get('version'))
    )
    return redirect(url_for('cards.deck_view', deck_id=deck_id))


@cards_bp.route('/deck/<deck_id>/edit_card/<card_id>', methods=['GET', 'POST'])
def edit_card(deck_id, card_id):
    deck_info = GetDeckInfo(_repo()).execute(deck_id)
    if not deck_info:
        return redirect(url_for('cards.decks_list'))

    card_deck = GetDeck(_repo()).execute(deck_id)
    index = card_deck.index_of(card_id)
    if index is None:
        return redirect(url_for('cards.deck_view', deck_id=deck_id))

    if request.method == 'POST':
        front = request.form.get('front', '')
        back = request.form.get('back', '')
        EditCard(_repo()).execute(
            deck_id, card_id, front, back,
            _optional_version(request.form.get('version')),
        )
        return redirect(url_for('cards.deck_view', deck_id=deck_id))

    card = card_deck.cards[index].to_dict()
    return render_template('cards/edit_card.html',
                           deck=deck_info, card=card, index=index)


@cards_bp.route('/deck/<deck_id>/reset', methods=['POST'])
def reset(deck_id):
    ResetCards(_repo()).execute(
        deck_id, _optional_version(request.form.get('version'))
    )
    return redirect(url_for('cards.deck_view', deck_id=deck_id))


def _generate_pdf_response(
    deck_id: str,
    *,
    attachment: bool,
    side: str | None = None,
):
    deck_info = GetDeckInfo(_repo()).execute(deck_id)
    card_deck = GetDeck(_repo()).execute(deck_id)

    if not len(card_deck):
        return render_template('cards/index.html',
                               deck=deck_info,
                               cards=[], cards_count=0,
                               cards_per_page=_cards_per_page(),
                               error='Добавьте хотя бы одну карточку!')

    try:
        if side is None:
            generator = GenerateDocument(
                _repo(), _renderer(request.form.get('profile_id')), _compiler(),
                _cards_per_page()
            )
        else:
            generator = GenerateDocumentSide(
                _repo(), _renderer(request.form.get('profile_id')), _compiler(),
                _cards_per_page(), side
            )
        result = generator.execute(deck_id)
    except UnsafeLatexError as error:
        return render_template(
            'cards/error.html', deck=deck_info,
            errors=[str(error)], full_log=''
        ), 422

    if not result.success:
        status_by_kind = {'timeout': 504, 'unavailable': 503, 'compile-error': 422}
        message_by_kind = {
            'timeout': 'Компиляция PDF превысила допустимое время.',
            'unavailable': 'Компилятор PDF недоступен на сервере.',
            'compile-error': 'Не удалось скомпилировать PDF. Проверьте содержимое карточек.',
        }
        status = status_by_kind.get(result.error_kind, 500)
        message = message_by_kind.get(result.error_kind, 'Внутренняя ошибка генерации PDF.')
        return render_template(
            'cards/error.html', deck=deck_info,
            errors=[message],
            full_log=result.log if current_app.debug else ''
        ), status

    suffix = {'front': '-fronts', 'back': '-backs'}.get(side, '')
    filename = f'{deck_info.name}{suffix}.pdf' if deck_info else f'cards{suffix}.pdf'
    return send_file(
        io.BytesIO(result.pdf_data),
        mimetype='application/pdf',
        as_attachment=attachment,
        download_name=filename,
    )


@cards_bp.route('/deck/<deck_id>/generate', methods=['POST'])
def generate(deck_id):
    return _generate_pdf_response(deck_id, attachment=True)


@cards_bp.route('/deck/<deck_id>/preview_pdf', methods=['POST'])
def preview_pdf(deck_id):
    return _generate_pdf_response(deck_id, attachment=False)


@cards_bp.route('/deck/<deck_id>/generate/fronts', methods=['POST'])
def generate_fronts(deck_id):
    return _generate_pdf_response(deck_id, attachment=True, side='front')


@cards_bp.route('/deck/<deck_id>/generate/backs', methods=['POST'])
def generate_backs(deck_id):
    return _generate_pdf_response(deck_id, attachment=True, side='back')


@cards_bp.route('/api/deck/<deck_id>/preflight', methods=['POST'])
def preflight_document(deck_id):
    try:
        report = PreflightDocument(
            _repo(), _renderer(request.form.get('profile_id')), _compiler(),
            _cards_per_page()
        ).execute(deck_id)
        return jsonify(report.to_dict())
    except UnsafeLatexError as error:
        return jsonify({
            'ready': False,
            'error_count': 1,
            'warning_count': 0,
            'issues': [{
                'code': 'unsupported-formula',
                'severity': 'error',
                'message': str(error),
                'card_id': None,
                'card_number': None,
                'side': None,
            }],
        })


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
        latex = PreviewDocument(
            _repo(), _renderer(request.form.get('profile_id')), _cards_per_page()
        ).execute(deck_id)
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
    if not isinstance(data, dict) or not data:
        return jsonify({'error': 'Нет данных'}), 400

    front_value = data.get('front', '')
    back_value = data.get('back', '')
    if not isinstance(front_value, str) or not isinstance(back_value, str):
        return jsonify({'error': 'Поля карточки должны быть строками'}), 400
    front = front_value.strip()
    back = back_value.strip()
    if not front and not back:
        return jsonify({'error': 'Заполните хотя бы одно поле'}), 400

    try:
        card, index = AddCard(_repo(), _max_cards()).execute(
            deck_id, front, back, _optional_version(data.get('version'))
        )
    except CardLimitExceeded as error:
        return jsonify({'error': str(error)}), 409
    card_deck = GetDeck(_repo()).execute(deck_id)

    return jsonify({
        'ok': True,
        'card': card.to_dict(),
        'index': index,
        'cards_count': len(card_deck),
        'deck_version': GetDeckInfo(_repo()).execute(deck_id).version,
    })


@cards_bp.route('/api/deck/<deck_id>/delete_card/<card_id>', methods=['DELETE'])
def api_delete_card(deck_id, card_id):
    data = request.get_json(silent=True)
    if data is not None and not isinstance(data, dict):
        return jsonify({'error': 'Некорректные данные'}), 400
    result = DeleteCard(_repo()).execute(
        deck_id, card_id, _optional_version((data or {}).get('version'))
    )
    if not result:
        return jsonify({'error': 'Неверный индекс'}), 404
    card_deck = GetDeck(_repo()).execute(deck_id)
    return jsonify({
        'ok': True,
        'cards_count': len(card_deck),
        'deck_version': GetDeckInfo(_repo()).execute(deck_id).version,
    })


@cards_bp.route('/api/deck/<deck_id>/reorder', methods=['POST'])
def api_reorder(deck_id):
    data = request.get_json()
    if not isinstance(data, dict) or 'order' not in data:
        return jsonify({'error': 'Нет данных'}), 400
    order = data['order']
    if not isinstance(order, list) or any(
        not isinstance(card_id, str) for card_id in order
    ):
        return jsonify({'error': 'Некорректный порядок'}), 400
    result = ReorderCards(_repo()).execute(
        deck_id, order, _optional_version(data.get('version'))
    )
    if not result:
        return jsonify({'error': 'Некорректный порядок'}), 400
    return jsonify({
        'ok': True,
        'deck_version': GetDeckInfo(_repo()).execute(deck_id).version,
    })


@cards_bp.route('/api/deck/<deck_id>/edit_card/<card_id>', methods=['PUT'])
def api_edit_card(deck_id, card_id):
    data = request.get_json()
    if not isinstance(data, dict) or not data:
        return jsonify({'error': 'Нет данных'}), 400
    front = data.get('front', '')
    back = data.get('back', '')
    if not isinstance(front, str) or not isinstance(back, str):
        return jsonify({'error': 'Поля карточки должны быть строками'}), 400
    result = EditCard(_repo()).execute(
        deck_id, card_id, front, back, _optional_version(data.get('version')))
    if not result:
        return jsonify({'error': 'Неверный индекс'}), 404
    card_deck = GetDeck(_repo()).execute(deck_id)
    index = card_deck.index_of(card_id)
    return jsonify({
        'ok': True,
        'card': card_deck.cards[index].to_dict(),
        'deck_version': GetDeckInfo(_repo()).execute(deck_id).version,
    })
