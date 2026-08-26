import io
import hashlib
import hmac
import secrets
import time
import uuid

from flask import (Blueprint, render_template, request, redirect,
                   url_for, jsonify, current_app, send_file, session, abort, g)

from ..adapters.latex_renderer import UnsafeLatexError
from ..adapters.repository_errors import DeckNotFoundError
from ..domain.entities import (
    Card,
    CardDeck,
    CardModeError,
    validate_card_mode_fields,
)
from ..domain.interfaces import CompileResult, ConcurrentModificationError
from ..domain.printing import PrinterProfile, recommend_back_offsets
from ..domain.rendering import AuthoringMode, DeckRenderSettings, StylePreset
from ..domain.safe_text import safe_single_line, safe_text_paragraphs
from ..domain.trusted import PrintJobSnapshot, TrustedTemplateVersion

from ..use_cases.card_use_cases import (
    AddCard, AddCardsBulk, ImportCsv, DeleteCard,
    EditCard, ReorderCards, ResetCards, GetDeck,
    GenerateDocument, GenerateDocumentSide, PreviewDocument, CardLimitExceeded,
    PreflightDocument, BulkValidationError, CsvValidationError,
    PreparePrintOverlay,
    preview_bulk_import, preview_csv_import,
    compile_error_context,
)
from ..use_cases.deck_use_cases import (
    ListDecks, GetDeckInfo, CreateDeck, UpdateDeck,
    ListTrashedDecks, TrashDeck, RestoreDeck, PurgeDeck,
    CloneDeck, UpdateDeckRenderSettings,
)
from ..use_cases.deck_transfer import (
    DeckTransferError,
    export_card_csv_template,
    export_deck_csv,
    export_deck_json,
    import_deck_json,
)
from ..use_cases.trusted_template_use_cases import TrustedTemplateService
from .observability import run_observed_pdf_compilation

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
    profile = _print_profile_map().get(profile_id)
    factory = current_app.config.get('RENDERER_FACTORY')
    if profile is None or factory is None:
        abort(400, description='Неизвестный профиль принтера')
    return factory(profile)


def _print_renderer_context(profile_id: str | None):
    profile_id = (profile_id or '').strip()
    if not profile_id:
        return _renderer(), 'base', 'Базовая конфигурация'
    profile = _print_profile_map().get(profile_id)
    if profile is None:
        abort(400, description='Неизвестный профиль принтера')
    return _renderer(profile_id), profile.key, profile.name


def _print_profiles():
    profiles = dict(current_app.config.get('PRINT_PROFILES', {}))
    list_saved = getattr(_repo(), 'list_printer_profiles', None)
    if list_saved is not None:
        for profile in list_saved():
            profiles.setdefault(profile.key, profile)
    return tuple(profiles.values())


def _print_profile_map():
    return {profile.key: profile for profile in _print_profiles()}


def _compiler():
    return current_app.config['COMPILER']


def _trusted_enabled() -> bool:
    return current_app.config.get('TRUSTED_LATEX_ENABLED', False) is True


def _trusted_compiler():
    return current_app.config.get('TRUSTED_COMPILER')


def _trusted_sandbox_ready() -> bool:
    compiler = _trusted_compiler()
    if compiler is None:
        return False
    check = getattr(compiler, 'readiness_check', None) or getattr(
        compiler, 'is_available', None
    )
    return check is not None and check()


def _trusted_service() -> TrustedTemplateService:
    if not _trusted_enabled():
        abort(404)
    return TrustedTemplateService(_repo(), enabled=True)


def _active_trusted_template(deck_id: str):
    if not _trusted_enabled():
        return None
    return _trusted_service().active(deck_id)


def _require_advanced_deck(deck_id: str):
    deck = GetDeckInfo(_repo()).execute(deck_id)
    if deck is None:
        raise DeckNotFoundError(deck_id)
    if deck.render_settings.authoring_mode is not AuthoringMode.ADVANCED:
        abort(404)
    return deck


def _print_compiler_and_template(deck_id: str):
    get_snapshot = getattr(_repo(), 'get_print_job_snapshot', None)
    snapshot = get_snapshot(deck_id) if get_snapshot is not None else None
    settings = (
        snapshot.render_settings
        if snapshot is not None
        else _repo().get_render_settings(deck_id)
    )
    advanced = settings.authoring_mode is AuthoringMode.ADVANCED
    if not advanced:
        return _compiler(), None, snapshot
    template = (
        snapshot.trusted_template
        if snapshot is not None else _active_trusted_template(deck_id)
    )
    if not _trusted_enabled():
        template = None
        if snapshot is not None:
            snapshot = PrintJobSnapshot(
                deck_id=snapshot.deck_id,
                deck_version=snapshot.deck_version,
                cards=snapshot.cards,
                render_settings=snapshot.render_settings,
            )
    compiler = _trusted_compiler()
    if compiler is None:
        class UnavailableTrustedCompiler:
            def compile(self, _source):
                return CompileResult(
                    False,
                    b'',
                    'Trusted LaTeX sandbox is unavailable',
                    'unavailable',
                )
        compiler = UnavailableTrustedCompiler()
    return compiler, template, snapshot


def _cards_per_page():
    return current_app.config['CARDS_PER_PAGE']


def _deck_page_context(deck_info, card_deck, **extra):
    cards_per_page = _cards_per_page()
    renderer = _renderer().with_render_settings(deck_info.render_settings)
    layout = renderer.prepare_print_layout(card_deck, cards_per_page)
    context = {
        'deck': deck_info,
        'cards': card_deck.to_list(),
        'cards_count': len(card_deck),
        'cards_per_page': cards_per_page,
        'print_pages': len(layout.cards) // cards_per_page,
        'empty_slots': layout.section_padding + layout.trailing_padding,
        'trusted_enabled': _trusted_enabled(),
        'trusted_active': (
            _active_trusted_template(deck_info.id)
            if deck_info.render_settings.authoring_mode
            is AuthoringMode.ADVANCED
            else None
        ),
        'is_advanced': (
            deck_info.render_settings.authoring_mode
            is AuthoringMode.ADVANCED
        ),
    }
    context.update(extra)
    return context


def _deck_print_stats(deck_id: str) -> dict[str, int]:
    deck_info = GetDeckInfo(_repo()).execute(deck_id)
    card_deck = GetDeck(_repo()).execute(deck_id)
    context = _deck_page_context(deck_info, card_deck)
    return {
        'print_pages': context['print_pages'],
        'empty_slots': context['empty_slots'],
    }


def _max_cards():
    return current_app.config.get('MAX_CARDS')


def _trash_retention_days() -> int:
    return current_app.config.get('TRASH_RETENTION_DAYS', 30)


@cards_bp.route('/health/live', methods=['GET'])
def health_live():
    return jsonify({'status': 'ok'})


@cards_bp.route('/health/ready', methods=['GET'])
def health_ready():
    storage_ok = False
    compiler_ok = False
    try:
        readiness_check = getattr(_repo(), 'readiness_check', None)
        if readiness_check is not None:
            storage_ok = not readiness_check()
        else:
            integrity_report = getattr(_repo(), 'integrity_report', None)
            storage_ok = integrity_report is None or integrity_report.healthy
    except Exception:
        storage_ok = False

    try:
        availability_check = getattr(_compiler(), 'is_available', None)
        compiler_ok = availability_check is None or availability_check()
    except Exception:
        compiler_ok = False

    trusted_enabled = current_app.config.get('TRUSTED_LATEX_ENABLED', False)
    trusted_ok = True
    if trusted_enabled:
        try:
            trusted_compiler = current_app.config.get('TRUSTED_COMPILER')
            trusted_check = getattr(
                trusted_compiler, 'readiness_check', None
            ) or getattr(trusted_compiler, 'is_available', None)
            trusted_ok = (
                trusted_compiler is not None
                and trusted_check is not None
                and trusted_check()
            )
        except Exception:
            trusted_ok = False

    ready = storage_ok and compiler_ok and trusted_ok
    components = {
        'storage': 'ok' if storage_ok else 'unavailable',
        'tex': 'ok' if compiler_ok else 'unavailable',
    }
    if trusted_enabled:
        components['trusted-tex-sandbox'] = (
            'ok' if trusted_ok else 'unavailable'
        )
    return jsonify({
        'status': 'ready' if ready else 'unavailable',
        'components': components,
    }), 200 if ready else 503


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


def _required_version(value) -> int:
    version = _optional_version(value)
    if version is None:
        abort(400, description='Не указана версия колоды')
    return version


def _import_preview_token(
    kind: str,
    deck,
    payload: bytes,
    **options,
) -> str:
    secret = str(current_app.config['SECRET_KEY']).encode('utf-8')
    digest = hmac.new(secret, digestmod=hashlib.sha256)
    parts = (
        kind,
        deck.id,
        str(deck.version),
        deck.render_settings.authoring_mode.value,
        hashlib.sha256(payload).hexdigest(),
        *(f'{key}={options[key]}' for key in sorted(options)),
    )
    for part in parts:
        encoded = part.encode('utf-8')
        digest.update(len(encoded).to_bytes(8, 'big'))
        digest.update(encoded)
    return digest.hexdigest()


def _valid_preview_token(submitted: str, expected: str) -> bool:
    return bool(submitted) and hmac.compare_digest(submitted, expected)


def _saved_print_profiles():
    list_saved = getattr(_repo(), 'list_printer_profiles', None)
    return tuple(list_saved()) if list_saved is not None else ()


def _render_printer_profiles(
    error: str | None = None,
    status: int = 200,
    *,
    calibration_result: dict | None = None,
    calibration_form: dict | None = None,
    edit_profile: PrinterProfile | None = None,
):
    return render_template(
        'cards/printer_profiles.html',
        configured_profiles=tuple(
            current_app.config.get('PRINT_PROFILES', {}).values()
        ),
        saved_profiles=_saved_print_profiles(),
        available_profiles=_print_profiles(),
        error=error,
        calibration_result=calibration_result,
        calibration_form=calibration_form or {},
        edit_profile=edit_profile,
    ), status


def _profile_offset(name: str) -> float:
    raw_value = request.form.get(name, '0').strip().replace(',', '.')
    try:
        return float(raw_value)
    except ValueError as error:
        raise ValueError(f'Поле {name} должно быть числом') from error


def _profile_back_rotation() -> int:
    raw_value = request.form.get('back_rotation_deg', '180').strip()
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ValueError('Поворот оборота должен быть 0° или 180°') from error
    if value not in {0, 180}:
        raise ValueError('Поворот оборота должен быть 0° или 180°')
    return value


def _render_settings_from_form(
    authoring_mode: AuthoringMode = AuthoringMode.SAFE,
) -> DeckRenderSettings:
    preset = StylePreset(request.form.get('preset', 'centered'))
    if preset is StylePreset.CENTERED:
        horizontal_alignment = 'center'
        vertical_alignment = 'center'
    else:
        horizontal_alignment = request.form.get(
            'horizontal_alignment', 'left'
        )
        vertical_alignment = request.form.get('vertical_alignment', 'top')
    return DeckRenderSettings(
        authoring_mode=authoring_mode,
        preset=preset,
        horizontal_alignment=horizontal_alignment,
        vertical_alignment=vertical_alignment,
        header_visibility=request.form.get('header_visibility', 'none'),
        header_position='top',
        header_alignment=request.form.get('header_alignment', 'left'),
        header_repeat=request.form.get('header_repeat', 'every-card'),
        section_break=request.form.get('section_break', 'continuous'),
        typography_profile=request.form.get('typography_profile', 'off'),
        body_font_family=request.form.get('body_font_family', 'serif'),
        body_font_size=request.form.get('body_font_size', 'normal'),
        body_font_weight=request.form.get('body_font_weight', 'normal'),
        body_font_style=request.form.get('body_font_style', 'upright'),
        line_spacing=request.form.get('line_spacing', 'normal'),
        paragraph_spacing=request.form.get('paragraph_spacing', 'none'),
        header_source=request.form.get('header_source', 'section'),
        header_text=request.form.get('header_text', '').strip(),
        header_font_family=request.form.get('header_font_family', 'sans'),
        header_font_size=request.form.get('header_font_size', 'small'),
        header_font_weight=request.form.get('header_font_weight', 'normal'),
        header_font_style=request.form.get('header_font_style', 'upright'),
        header_rule=request.form.get('header_rule', 'none'),
        header_rule_spacing=request.form.get(
            'header_rule_spacing', 'normal'
        ),
        secondary_header_visibility=request.form.get(
            'secondary_header_visibility', 'none'
        ),
        secondary_header_position='bottom',
        secondary_header_alignment=request.form.get(
            'secondary_header_alignment', 'right'
        ),
        secondary_header_repeat=request.form.get(
            'secondary_header_repeat', 'every-card'
        ),
        secondary_header_source=request.form.get(
            'secondary_header_source', 'card-number'
        ),
        secondary_header_text=request.form.get(
            'secondary_header_text', ''
        ).strip(),
        secondary_header_font_family=request.form.get(
            'secondary_header_font_family', 'sans'
        ),
        secondary_header_font_size=request.form.get(
            'secondary_header_font_size', 'small'
        ),
        secondary_header_font_weight=request.form.get(
            'secondary_header_font_weight', 'normal'
        ),
        secondary_header_font_style=request.form.get(
            'secondary_header_font_style', 'upright'
        ),
        secondary_header_rule=request.form.get(
            'secondary_header_rule', 'none'
        ),
        secondary_header_rule_spacing=request.form.get(
            'secondary_header_rule_spacing', 'normal'
        ),
    )


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
        'safe_text_paragraphs': safe_text_paragraphs,
        'safe_single_line': safe_single_line,
        'print_profiles': _print_profiles(),
        'trusted_enabled': _trusted_enabled(),
        'request_id': getattr(g, 'request_id', None),
    }


@cards_bp.before_app_request
def start_request_observation():
    g.request_id = str(uuid.uuid4())
    g.request_started = time.perf_counter()


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
    response.headers['X-Request-ID'] = getattr(g, 'request_id', 'unavailable')
    started = getattr(g, 'request_started', None)
    duration_ms = (
        round((time.perf_counter() - started) * 1000, 3)
        if started is not None else None
    )
    current_app.logger.info(
        'request_completed',
        extra={
            'event': 'request_completed',
            'request_id': getattr(g, 'request_id', None),
            'method': request.method,
            'path': request.path,
            'status': response.status_code,
            'duration_ms': duration_ms,
        },
    )
    return response


@cards_bp.app_errorhandler(DeckNotFoundError)
def handle_missing_deck(_error):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Колода не найдена'}), 404
    return redirect(url_for('cards.decks_list'))


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
        **_deck_page_context(deck_info, card_deck, error=message),
    ), status


# ─── Колоды ─────────────────────────────────────────────────────────

def _decks_list_context(**extra):
    context = {
        'decks': ListDecks(_repo()).execute(),
        'trashed_count': len(ListTrashedDecks(_repo()).execute()),
        'trash_retention_days': _trash_retention_days(),
    }
    context.update(extra)
    return context

@cards_bp.route('/', methods=['GET'])
def decks_list():
    return render_template('cards/decks.html', **_decks_list_context())


@cards_bp.route('/trash', methods=['GET'])
def trash_list():
    return render_template(
        'cards/trash.html',
        decks=ListTrashedDecks(_repo()).execute(),
        trash_retention_days=_trash_retention_days(),
    )


@cards_bp.route('/printer_profiles', methods=['GET'])
def printer_profiles():
    edit_key = request.args.get('edit', '').strip()
    if not edit_key:
        return _render_printer_profiles()
    edit_profile = next(
        (
            profile for profile in _saved_print_profiles()
            if profile.key == edit_key
        ),
        None,
    )
    if edit_profile is None:
        return _render_printer_profiles(
            'Сохранённый профиль для редактирования не найден.', 404
        )
    return _render_printer_profiles(edit_profile=edit_profile)


@cards_bp.route('/printer_profiles/calibration-sheet', methods=['POST'])
def calibration_sheet():
    profile_id = request.form.get('profile_id', '')
    renderer, resolved_profile_id, _profile_name = _print_renderer_context(
        profile_id
    )
    render_sheet = getattr(renderer, 'render_calibration_sheet', None)
    if render_sheet is None:
        abort(501, description='Калибровочный лист недоступен')
    try:
        result = run_observed_pdf_compilation(
            lambda: _compiler().compile(render_sheet()),
            logger=current_app.logger,
            request_id=g.request_id,
            job_kind='calibration',
            profile_id=resolved_profile_id,
            side='calibration',
            validation_errors=(UnsafeLatexError,),
        )
    except UnsafeLatexError:
        return _render_printer_profiles(
            'Калибровочный лист не прошёл проверку.', 422
        )
    if not result.success:
        status_by_kind = {
            'timeout': 504,
            'unavailable': 503,
            'sandbox-error': 503,
            'compile-error': 422,
            'validation': 422,
            'output-limit': 422,
        }
        return _render_printer_profiles(
            'Не удалось сформировать калибровочный PDF.',
            status_by_kind.get(result.error_kind, 500),
        )
    suffix = resolved_profile_id
    return send_file(
        io.BytesIO(result.pdf_data),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'printer-calibration-{suffix}.pdf',
    )


@cards_bp.route('/printer_profiles/calibration-calculate', methods=['POST'])
def calculate_calibration():
    profile_id = request.form.get('profile_id', '').strip()
    form_values = {
        'profile_id': profile_id,
        'measured_x_mm': request.form.get('measured_x_mm', '').strip(),
        'measured_y_mm': request.form.get('measured_y_mm', '').strip(),
    }
    profile = _print_profile_map().get(profile_id)
    if profile is None:
        return _render_printer_profiles(
            'Выберите существующий профиль для расчёта.',
            400,
            calibration_form=form_values,
        )
    try:
        measured_x = _profile_offset('measured_x_mm')
        measured_y = _profile_offset('measured_y_mm')
        corrected_x, corrected_y = recommend_back_offsets(
            profile, measured_x, measured_y
        )
    except ValueError as error:
        return _render_printer_profiles(
            str(error), 400, calibration_form=form_values
        )
    result = {
        'profile': profile,
        'measured_x_mm': measured_x,
        'measured_y_mm': measured_y,
        'back_offset_x_mm': corrected_x,
        'back_offset_y_mm': corrected_y,
        'within_limits': abs(corrected_x) <= 10 and abs(corrected_y) <= 10,
    }
    return _render_printer_profiles(
        calibration_result=result,
        calibration_form=form_values,
    )


@cards_bp.route('/printer_profiles/save', methods=['POST'])
def save_printer_profile():
    save_profile = getattr(_repo(), 'save_printer_profile', None)
    if save_profile is None:
        abort(501, description='Хранилище профилей недоступно')
    try:
        key = request.form.get('key', '').strip()
        if key in current_app.config.get('PRINT_PROFILES', {}):
            raise ValueError('Встроенный профиль нельзя перезаписать')
        profile = PrinterProfile(
            key=key,
            name=request.form.get('name', '').strip(),
            duplex_mode=request.form.get('duplex_mode', 'long-edge'),
            back_rotation_deg=_profile_back_rotation(),
            front_offset_x_mm=_profile_offset('front_offset_x_mm'),
            front_offset_y_mm=_profile_offset('front_offset_y_mm'),
            back_offset_x_mm=_profile_offset('back_offset_x_mm'),
            back_offset_y_mm=_profile_offset('back_offset_y_mm'),
            back_border=request.form.get('back_border') == 'on',
            registration_marks=request.form.get('registration_marks') == 'on',
        )
        save_profile(profile)
    except ValueError as error:
        return _render_printer_profiles(str(error), 400)
    return redirect(url_for('cards.printer_profiles'))


@cards_bp.route('/printer_profiles/<key>/delete', methods=['POST'])
def delete_printer_profile(key):
    if key in current_app.config.get('PRINT_PROFILES', {}):
        abort(400, description='Встроенный профиль нельзя удалить')
    delete_profile = getattr(_repo(), 'delete_printer_profile', None)
    if delete_profile is None:
        abort(501, description='Хранилище профилей недоступно')
    delete_profile(key)
    return redirect(url_for('cards.printer_profiles'))


@cards_bp.route('/create_deck', methods=['POST'])
def create_deck():
    name = request.form.get('name', '').strip()
    desc = request.form.get('description', '').strip()
    try:
        mode = AuthoringMode(request.form.get('authoring_mode', 'safe'))
    except ValueError:
        return render_template(
            'cards/decks.html',
            **_decks_list_context(error='Неизвестный тип колоды.'),
        ), 400
    if mode is AuthoringMode.ADVANCED and not _trusted_enabled():
        return render_template(
            'cards/decks.html',
            **_decks_list_context(error=(
                'Advanced-колоду нельзя создать: trusted LaTeX '
                'выключен при запуске сервера.'
            )),
        ), 503
    CreateDeck(_repo()).execute(name, desc, mode)
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
def trash_deck(deck_id):
    TrashDeck(_repo(), _trash_retention_days()).execute(
        deck_id, _required_version(request.form.get('version'))
    )
    return redirect(url_for('cards.decks_list'))


@cards_bp.route('/deck/<deck_id>/restore', methods=['POST'])
def restore_deck(deck_id):
    RestoreDeck(_repo()).execute(
        deck_id, _required_version(request.form.get('version'))
    )
    return redirect(url_for('cards.trash_list'))


@cards_bp.route('/deck/<deck_id>/purge', methods=['POST'])
def purge_deck(deck_id):
    PurgeDeck(_repo()).execute(
        deck_id, _required_version(request.form.get('version'))
    )
    return redirect(url_for('cards.trash_list'))


@cards_bp.route('/deck/<deck_id>/clone', methods=['POST'])
def clone_deck(deck_id):
    CloneDeck(_repo()).execute(deck_id)
    return redirect(url_for('cards.decks_list'))


@cards_bp.route('/deck/<deck_id>/export.json', methods=['GET'])
def export_deck_as_json(deck_id):
    deck = GetDeckInfo(_repo()).execute(deck_id)
    if deck is None:
        return redirect(url_for('cards.decks_list'))
    try:
        payload = export_deck_json(_repo(), deck_id)
    except DeckTransferError as error:
        return render_template(
            'cards/error.html', deck=deck, errors=[str(error)], full_log='',
            error_title='Ошибка экспорта колоды',
        ), 409
    return send_file(
        io.BytesIO(payload),
        mimetype='application/json',
        as_attachment=True,
        download_name=f'{deck.name}.didactic-cards.json',
    )


@cards_bp.route('/deck/<deck_id>/export.csv', methods=['GET'])
def export_deck_as_csv(deck_id):
    deck = GetDeckInfo(_repo()).execute(deck_id)
    if deck is None:
        return redirect(url_for('cards.decks_list'))
    try:
        payload = export_deck_csv(_repo(), deck_id)
    except DeckTransferError as error:
        return render_template(
            'cards/error.html', deck=deck, errors=[str(error)], full_log='',
            error_title='Ошибка экспорта колоды',
        ), 409
    return send_file(
        io.BytesIO(payload),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'{deck.name}.csv',
    )


@cards_bp.route('/deck/<deck_id>/import-template.csv', methods=['GET'])
def download_card_csv_template(deck_id):
    deck = GetDeckInfo(_repo()).execute(deck_id)
    if deck is None:
        return redirect(url_for('cards.decks_list'))
    return send_file(
        io.BytesIO(export_card_csv_template(
            deck.render_settings.authoring_mode
        )),
        mimetype='text/csv',
        as_attachment=True,
        download_name=(
            'advanced-cards-template.csv'
            if deck.render_settings.authoring_mode is AuthoringMode.ADVANCED
            else 'cards-template.csv'
        ),
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
    return render_template(
        'cards/index.html',
        **_deck_page_context(
            deck_info, card_deck, print_profiles=_print_profiles()
        ),
    )


def _render_trusted_page(
    deck_id: str,
    *,
    error: str | None = None,
    status: int = 200,
    draft_front_source: str | None = None,
    draft_back_source: str | None = None,
):
    _trusted_service()
    deck = _require_advanced_deck(deck_id)
    templates = _repo().list_trusted_templates(deck_id)
    latest = templates[-1] if templates else None
    default_source = r'{{ content }}'
    return render_template(
        'cards/trusted_latex.html',
        deck=deck,
        templates=tuple(reversed(templates)),
        active=_trusted_service().active(deck_id),
        draft_front_source=(
            draft_front_source
            if draft_front_source is not None
            else latest.front_source if latest else default_source
        ),
        draft_back_source=(
            draft_back_source
            if draft_back_source is not None
            else latest.back_source if latest else default_source
        ),
        sandbox_ready=_trusted_sandbox_ready(),
        error=error,
    ), status


def _trusted_draft_from_form(deck_id: str) -> TrustedTemplateVersion:
    return TrustedTemplateVersion(
        deck_id=deck_id,
        version=1,
        front_source=request.form.get('front_source', ''),
        back_source=request.form.get('back_source', ''),
    )


def _compile_trusted_template(deck_id: str, template: TrustedTemplateVersion):
    compiler = _trusted_compiler()
    if compiler is None or not _trusted_sandbox_ready():
        return None
    cards = _repo().load_cards(deck_id)
    if not cards.cards:
        cards = CardDeck([Card(
            front='Пример лицевой стороны',
            back='Пример оборотной стороны',
            section='Пример секции',
        )])
    renderer = _renderer().with_render_settings(
        _repo().get_render_settings(deck_id)
    ).with_trusted_template(template)
    layout = renderer.prepare_print_layout(cards, _cards_per_page())
    latex = renderer.render(CardDeck(list(layout.cards)))
    return compiler.compile(latex)


@cards_bp.route('/deck/<deck_id>/advanced', methods=['GET'])
def trusted_latex_editor(deck_id):
    return _render_trusted_page(deck_id)


@cards_bp.route('/deck/<deck_id>/advanced/test', methods=['POST'])
def test_trusted_latex(deck_id):
    _require_advanced_deck(deck_id)
    _trusted_service()
    try:
        template = _trusted_draft_from_form(deck_id)
        result = _compile_trusted_template(deck_id, template)
    except ValueError as error:
        return _render_trusted_page(
            deck_id,
            error=str(error),
            status=400,
            draft_front_source=request.form.get('front_source', ''),
            draft_back_source=request.form.get('back_source', ''),
        )
    if result is None:
        return _render_trusted_page(
            deck_id,
            error='Изолированный compiler worker не прошёл readiness-проверку.',
            status=503,
            draft_front_source=template.front_source,
            draft_back_source=template.back_source,
        )
    if not result.success:
        context = compile_error_context(
            result.log, _repo().load_cards(deck_id)
        )
        location = (
            f' Карточка {context[0]}, '
            f'{"лицевая" if context[1] == "front" else "оборотная"} сторона.'
            if context is not None else ''
        )
        return _render_trusted_page(
            deck_id,
            error='Тестовая компиляция шаблона завершилась ошибкой.' + location,
            status=422,
            draft_front_source=template.front_source,
            draft_back_source=template.back_source,
        )
    return send_file(
        io.BytesIO(result.pdf_data),
        mimetype='application/pdf',
        as_attachment=False,
        download_name='trusted-template-test.pdf',
    )


@cards_bp.route('/deck/<deck_id>/advanced/stage', methods=['POST'])
def stage_trusted_latex(deck_id):
    _require_advanced_deck(deck_id)
    service = _trusted_service()
    try:
        template = _trusted_draft_from_form(deck_id)
        service.stage_local(
            deck_id,
            template.front_source,
            template.back_source,
        )
    except ValueError as error:
        return _render_trusted_page(
            deck_id,
            error=str(error),
            status=400,
            draft_front_source=request.form.get('front_source', ''),
            draft_back_source=request.form.get('back_source', ''),
        )
    return redirect(url_for('cards.trusted_latex_editor', deck_id=deck_id))


@cards_bp.route(
    '/deck/<deck_id>/advanced/<template_id>/approve', methods=['POST']
)
def approve_trusted_latex(deck_id, template_id):
    _require_advanced_deck(deck_id)
    service = _trusted_service()
    if request.form.get('confirm_trusted') != 'yes':
        return _render_trusted_page(
            deck_id,
            error='Подтвердите, что шаблон является доверенным кодом.',
            status=400,
        )
    templates = {item.id: item for item in _repo().list_trusted_templates(deck_id)}
    template = templates.get(template_id)
    if template is None:
        abort(404)
    result = _compile_trusted_template(deck_id, template)
    if result is None:
        return _render_trusted_page(
            deck_id,
            error='Sandbox недоступен; шаблон не активирован.',
            status=503,
        )
    if not result.success:
        context = compile_error_context(
            result.log, _repo().load_cards(deck_id)
        )
        location = (
            f' Карточка {context[0]}, '
            f'{"лицевая" if context[1] == "front" else "оборотная"} сторона.'
            if context is not None else ''
        )
        return _render_trusted_page(
            deck_id,
            error=(
                'Шаблон не активирован: test compile завершился ошибкой.'
                + location
            ),
            status=422,
        )
    service.approve(deck_id, template_id)
    return redirect(url_for('cards.trusted_latex_editor', deck_id=deck_id))


@cards_bp.route('/deck/<deck_id>/advanced/reset', methods=['POST'])
def reset_trusted_latex(deck_id):
    _require_advanced_deck(deck_id)
    service = _trusted_service()
    active = service.active(deck_id)
    if active is not None:
        service.revoke(deck_id, active.id)
    return redirect(url_for('cards.trusted_latex_editor', deck_id=deck_id))


@cards_bp.route('/deck/<deck_id>/render_settings', methods=['POST'])
def update_render_settings(deck_id):
    try:
        current = _repo().get_render_settings(deck_id)
        if current.authoring_mode is AuthoringMode.ADVANCED:
            return _render_deck_error(
                deck_id,
                'У Advanced-колоды нет встроенных настроек оформления.',
                409,
            )
        settings = _render_settings_from_form(current.authoring_mode)
        UpdateDeckRenderSettings(_repo()).execute(
            deck_id,
            settings,
            _optional_version(request.form.get('version')),
        )
    except ValueError as error:
        return _render_deck_error(
            deck_id, f'Некорректные настройки оформления: {error}', 400
        )
    return redirect(url_for('cards.deck_view', deck_id=deck_id))


@cards_bp.route('/deck/<deck_id>/add_card', methods=['POST'])
def add_card(deck_id):
    front = request.form.get('front', '')
    back = request.form.get('back', '')
    section = request.form.get('section', '').strip()
    upper_header = request.form.get('upper_header', '')
    lower_header = request.form.get('lower_header', '')
    try:
        validate_card_mode_fields(
            Card(upper_header=upper_header, lower_header=lower_header),
            _repo().get_render_settings(deck_id).authoring_mode,
        )
    except CardModeError as error:
        return _render_deck_error(deck_id, str(error), 400)
    if front.strip() or back.strip() or upper_header.strip() or lower_header.strip():
        try:
            AddCard(_repo(), _max_cards()).execute(
                deck_id,
                front,
                back,
                _optional_version(request.form.get('version')),
                section=section,
                upper_header=upper_header,
                lower_header=lower_header,
            )
        except CardLimitExceeded as error:
            return _render_deck_error(deck_id, str(error))
        except CardModeError as error:
            return _render_deck_error(deck_id, str(error), 400)
    return redirect(url_for('cards.deck_view', deck_id=deck_id))


@cards_bp.route('/deck/<deck_id>/add_cards_bulk', methods=['POST'])
def add_cards_bulk(deck_id):
    deck = GetDeckInfo(_repo()).execute(deck_id)
    if deck is None:
        raise DeckNotFoundError(deck_id)
    bulk = request.form.get('bulk', '')
    section = request.form.get('section', '')
    expected_token = _import_preview_token(
        'bulk',
        deck,
        bulk.encode('utf-8'),
        section=section,
    )
    if not _valid_preview_token(
        request.form.get('preview_token', ''), expected_token
    ):
        return _render_deck_error(
            deck_id,
            'Сначала проверьте пакетный ввод; после изменений preview нужно повторить.',
            400,
        )
    try:
        AddCardsBulk(_repo(), _max_cards()).execute(
            deck_id,
            bulk,
            _optional_version(request.form.get('version')),
            section=section,
        )
    except (BulkValidationError, CardLimitExceeded, ValueError) as error:
        return _render_deck_error(deck_id, str(error), 400)
    return redirect(url_for('cards.deck_view', deck_id=deck_id))


@cards_bp.route('/api/deck/<deck_id>/preview_bulk', methods=['POST'])
def api_preview_bulk(deck_id):
    deck = GetDeckInfo(_repo()).execute(deck_id)
    if deck is None:
        raise DeckNotFoundError(deck_id)
    bulk = request.form.get('bulk', '')
    section = request.form.get('section', '')
    try:
        preview = preview_bulk_import(
            bulk,
            deck.render_settings.authoring_mode,
            section=section,
            existing_cards=GetDeck(_repo()).execute(deck_id).cards,
        )
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    token = (
        _import_preview_token(
            'bulk',
            deck,
            bulk.encode('utf-8'),
            section=section,
        )
        if preview.accepted_count and not preview.errors
        else ''
    )
    return jsonify({'ok': True, 'preview_token': token, **preview.to_dict()})


@cards_bp.route('/api/deck/<deck_id>/preview_csv', methods=['POST'])
def api_preview_csv(deck_id):
    deck = GetDeckInfo(_repo()).execute(deck_id)
    if deck is None:
        raise DeckNotFoundError(deck_id)
    file = request.files.get('csv_file')
    if not file or file.filename == '':
        return jsonify({'error': 'Выберите CSV-файл'}), 400
    file_bytes = file.stream.read()
    delimiter = request.form.get('delimiter', 'auto')
    encoding = request.form.get('encoding', 'utf-8')
    try:
        preview = preview_csv_import(
            file_bytes,
            delimiter,
            authoring_mode=deck.render_settings.authoring_mode,
            encoding=encoding,
            existing_cards=GetDeck(_repo()).execute(deck_id).cards,
        )
    except UnicodeDecodeError:
        return jsonify({
            'error': 'CSV не соответствует выбранной кодировке'
        }), 400
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    token = (
        _import_preview_token(
            'csv',
            deck,
            file_bytes,
            delimiter=delimiter,
            encoding=encoding,
        )
        if preview.accepted_count and not preview.errors
        else ''
    )
    return jsonify({'ok': True, 'preview_token': token, **preview.to_dict()})


@cards_bp.route('/deck/<deck_id>/import_csv', methods=['POST'])
def import_csv(deck_id):
    deck = GetDeckInfo(_repo()).execute(deck_id)
    if deck is None:
        raise DeckNotFoundError(deck_id)
    file = request.files.get('csv_file')
    if not file or file.filename == '':
        return redirect(url_for('cards.deck_view', deck_id=deck_id))
    delimiter = request.form.get('delimiter', 'auto')
    encoding = request.form.get('encoding', 'utf-8')
    try:
        file_bytes = file.stream.read()
        expected_token = _import_preview_token(
            'csv',
            deck,
            file_bytes,
            delimiter=delimiter,
            encoding=encoding,
        )
        if not _valid_preview_token(
            request.form.get('preview_token', ''), expected_token
        ):
            return _render_deck_error(
                deck_id,
                'Сначала проверьте CSV; после изменения файла или настроек preview нужно повторить.',
                400,
            )
        if (
            deck.render_settings.authoring_mode is AuthoringMode.ADVANCED
            and request.form.get('trust_raw_csv') != 'on'
        ):
            return _render_deck_error(
                deck_id,
                'Подтвердите доверие raw TeX из CSV-файла.',
                400,
            )
        ImportCsv(_repo(), _max_cards()).execute(
            deck_id,
            file_bytes,
            _optional_version(request.form.get('version')),
            delimiter=delimiter,
            encoding=encoding,
        )
    except CardLimitExceeded as error:
        return _render_deck_error(deck_id, str(error), 400)
    except UnicodeDecodeError:
        deck_info = GetDeckInfo(_repo()).execute(deck_id)
        card_deck = GetDeck(_repo()).execute(deck_id)
        return render_template(
            'cards/index.html',
            **_deck_page_context(
                deck_info,
                card_deck,
                error='Ошибка кодировки. Проверьте выбранную кодировку CSV.',
            ),
        )
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
        section = request.form.get('section', '').strip()
        upper_header = request.form.get('upper_header')
        lower_header = request.form.get('lower_header')
        try:
            EditCard(_repo()).execute(
                deck_id, card_id, front, back,
                _optional_version(request.form.get('version')),
                section=section,
                upper_header=upper_header,
                lower_header=lower_header,
            )
        except CardModeError as error:
            return _render_deck_error(deck_id, str(error), 400)
        return redirect(url_for('cards.deck_view', deck_id=deck_id))

    card = card_deck.cards[index].to_dict()
    return render_template('cards/edit_card.html',
                           deck=deck_info, card=card, index=index,
                           cards_count=len(card_deck),
                           previous_section=(
                               None if index == 0
                               else card_deck.cards[index - 1].section
                           ))


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
        return render_template(
            'cards/index.html',
            **_deck_page_context(
                deck_info, card_deck, error='Добавьте хотя бы одну карточку!'
            ),
        )

    try:
        selected_compiler, trusted_template, snapshot = _print_compiler_and_template(
            deck_id
        )
        selected_renderer, profile_id, profile_name = _print_renderer_context(
            request.form.get('profile_id')
        )
        print_job = PreparePrintOverlay(
            _repo(), selected_renderer, _cards_per_page(),
            profile_id, profile_name, trusted_template, snapshot,
        ).execute(deck_id)
        if side is None:
            generator = GenerateDocument(
                _repo(), selected_renderer,
                selected_compiler, _cards_per_page(), trusted_template, snapshot
            )
        else:
            generator = GenerateDocumentSide(
                _repo(), selected_renderer,
                selected_compiler, _cards_per_page(), side, trusted_template,
                snapshot
            )
        result = run_observed_pdf_compilation(
            lambda: generator.execute(deck_id),
            logger=current_app.logger,
            request_id=g.request_id,
            job_kind='deck',
            profile_id=profile_id,
            deck_id=deck_id,
            side=side or 'duplex',
            validation_errors=(UnsafeLatexError,),
        )
    except UnsafeLatexError as error:
        return render_template(
            'cards/error.html', deck=deck_info,
            errors=[str(error)], full_log=''
        ), 422

    if not result.success:
        status_by_kind = {
            'timeout': 504,
            'unavailable': 503,
            'sandbox-error': 503,
            'compile-error': 422,
            'validation': 422,
            'output-limit': 422,
        }
        message_by_kind = {
            'timeout': 'Компиляция PDF превысила допустимое время.',
            'unavailable': 'Компилятор PDF недоступен на сервере.',
            'sandbox-error': 'Изолированный compiler worker недоступен.',
            'compile-error': 'Не удалось скомпилировать PDF. Проверьте содержимое карточек.',
            'validation': 'Trusted print job не прошёл проверку.',
            'output-limit': 'Trusted PDF превысил допустимый размер.',
        }
        status = status_by_kind.get(result.error_kind, 500)
        message = message_by_kind.get(result.error_kind, 'Внутренняя ошибка генерации PDF.')
        if (
            snapshot is not None
            and snapshot.render_settings.authoring_mode
            is AuthoringMode.ADVANCED
        ):
            context = compile_error_context(result.log, card_deck)
            if context is not None:
                number, failed_side, _card = context
                side_label = (
                    'лицевая' if failed_side == 'front' else 'оборотная'
                )
                message += (
                    f' Ошибка относится к карточке {number}, '
                    f'{side_label} сторона.'
                )
        return render_template(
            'cards/error.html', deck=deck_info,
            errors=[message],
            full_log=result.log if current_app.debug else ''
        ), status

    suffix = {'front': '-fronts', 'back': '-backs'}.get(side, '')
    filename = f'{deck_info.name}{suffix}.pdf' if deck_info else f'cards{suffix}.pdf'
    response = send_file(
        io.BytesIO(result.pdf_data),
        mimetype='application/pdf',
        as_attachment=attachment,
        download_name=filename,
    )
    response.headers['X-Print-Job-ID'] = print_job.job_id
    response.headers['X-Print-Deck-Version'] = str(print_job.deck_version)
    response.headers['X-Print-Duplex-Mode'] = (
        print_job.geometry.duplex_transform.duplex_mode.value
    )
    response.headers['X-Print-Transform-Matrix'] = ','.join(
        str(value) for value in print_job.geometry.duplex_transform.matrix
    )
    return response


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
        selected_compiler, trusted_template, snapshot = _print_compiler_and_template(
            deck_id
        )
        report = PreflightDocument(
            _repo(), _renderer(request.form.get('profile_id')),
            selected_compiler, _cards_per_page(), trusted_template, snapshot
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


@cards_bp.route('/api/deck/<deck_id>/print_overlay', methods=['POST'])
def print_overlay(deck_id):
    _selected_compiler, trusted_template, snapshot = _print_compiler_and_template(
        deck_id
    )
    selected_renderer, profile_id, profile_name = _print_renderer_context(
        request.form.get('profile_id')
    )
    job = PreparePrintOverlay(
        _repo(), selected_renderer, _cards_per_page(),
        profile_id, profile_name, trusted_template, snapshot,
    ).execute(deck_id)
    return jsonify(job.to_dict())


@cards_bp.route('/deck/<deck_id>/preview_latex', methods=['POST'])
def preview_latex(deck_id):
    deck_info = GetDeckInfo(_repo()).execute(deck_id)
    card_deck = GetDeck(_repo()).execute(deck_id)

    if not len(card_deck):
        return render_template(
            'cards/index.html',
            **_deck_page_context(
                deck_info, card_deck, error='Добавьте хотя бы одну карточку!'
            ),
        )

    try:
        _selected_compiler, trusted_template, snapshot = _print_compiler_and_template(
            deck_id
        )
        latex = PreviewDocument(
            _repo(), _renderer(request.form.get('profile_id')),
            _cards_per_page(), trusted_template, snapshot
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
    section_value = data.get('section', '')
    upper_header_value = data.get('upper_header', '')
    lower_header_value = data.get('lower_header', '')
    if (
        not isinstance(front_value, str)
        or not isinstance(back_value, str)
        or not isinstance(section_value, str)
        or not isinstance(upper_header_value, str)
        or not isinstance(lower_header_value, str)
    ):
        return jsonify({'error': 'Поля карточки должны быть строками'}), 400
    front = front_value
    back = back_value
    section = section_value.strip()
    try:
        validate_card_mode_fields(
            Card(
                upper_header=upper_header_value,
                lower_header=lower_header_value,
            ),
            _repo().get_render_settings(deck_id).authoring_mode,
        )
    except CardModeError as error:
        return jsonify({'error': str(error)}), 400
    if not any((
        front.strip(),
        back.strip(),
        upper_header_value.strip(),
        lower_header_value.strip(),
    )):
        return jsonify({'error': 'Заполните хотя бы одно поле'}), 400

    try:
        card, index = AddCard(_repo(), _max_cards()).execute(
            deck_id,
            front,
            back,
            _optional_version(data.get('version')),
            section=section,
            upper_header=upper_header_value,
            lower_header=lower_header_value,
        )
    except CardLimitExceeded as error:
        return jsonify({'error': str(error)}), 409
    except CardModeError as error:
        return jsonify({'error': str(error)}), 400
    card_deck = GetDeck(_repo()).execute(deck_id)
    stats = _deck_print_stats(deck_id)

    return jsonify({
        'ok': True,
        'card': card.to_dict(),
        'index': index,
        'cards_count': len(card_deck),
        **stats,
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
    stats = _deck_print_stats(deck_id)
    return jsonify({
        'ok': True,
        'cards_count': len(card_deck),
        **stats,
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
    stats = _deck_print_stats(deck_id)
    return jsonify({
        'ok': True,
        **stats,
        'deck_version': GetDeckInfo(_repo()).execute(deck_id).version,
    })


@cards_bp.route('/api/deck/<deck_id>/edit_card/<card_id>', methods=['PUT'])
def api_edit_card(deck_id, card_id):
    data = request.get_json()
    if not isinstance(data, dict) or not data:
        return jsonify({'error': 'Нет данных'}), 400
    front = data.get('front', '')
    back = data.get('back', '')
    section = data.get('section')
    upper_header = data.get('upper_header')
    lower_header = data.get('lower_header')
    if (
        not isinstance(front, str)
        or not isinstance(back, str)
        or (section is not None and not isinstance(section, str))
        or (upper_header is not None and not isinstance(upper_header, str))
        or (lower_header is not None and not isinstance(lower_header, str))
    ):
        return jsonify({'error': 'Поля карточки должны быть строками'}), 400
    try:
        result = EditCard(_repo()).execute(
            deck_id,
            card_id,
            front,
            back,
            _optional_version(data.get('version')),
            section=section.strip() if section is not None else None,
            upper_header=upper_header,
            lower_header=lower_header,
        )
    except CardModeError as error:
        return jsonify({'error': str(error)}), 400
    if not result:
        return jsonify({'error': 'Неверный индекс'}), 404
    card_deck = GetDeck(_repo()).execute(deck_id)
    index = card_deck.index_of(card_id)
    return jsonify({
        'ok': True,
        'card': card_deck.cards[index].to_dict(),
        'deck_version': GetDeckInfo(_repo()).execute(deck_id).version,
    })
