"""Contract tests for HTML routes and the JSON API."""

from __future__ import annotations

import io
import uuid

import pytest

from config import AppConfig
from didactic_cards.domain.printing import PrinterProfile
from didactic_cards.adapters.latex_renderer import LatexRenderer
from didactic_cards.adapters.sqlite_repository import SqliteRepository
from didactic_cards.domain.entities import Card, CardDeck
from didactic_cards.domain.interfaces import CompileResult
from didactic_cards.domain.rendering import DeckRenderSettings
from didactic_cards.domain.trusted import TemplateStatus
from run import create_app


class RecordingTrustedCompiler:
    def __init__(self, *, ready=True, success=True):
        self.ready = ready
        self.success = success
        self.sources = []

    def readiness_check(self):
        return self.ready

    def compile(self, source):
        self.sources.append(source)
        return CompileResult(
            self.success,
            b'%PDF trusted' if self.success else b'',
            '' if self.success else 'private sandbox log',
            None if self.success else 'compile-error',
        )


def _enable_trusted(app, tmp_path, *, compiler=None):
    repository = SqliteRepository(tmp_path / 'trusted-data')
    app.config['REPO'] = repository
    app.config['TRUSTED_LATEX_ENABLED'] = True
    app.config['TRUSTED_COMPILER'] = compiler or RecordingTrustedCompiler()
    deck = repository.create_deck(
        'Advanced',
        render_settings=DeckRenderSettings(authoring_mode='advanced'),
    )
    repository.save_cards(
        deck.id, CardDeck([Card(front='10% safe', back=r'\textbf{raw}')])
    )
    return repository, deck


def _preview_bulk(client, deck_id, bulk, *, section=''):
    data = {
        'bulk': bulk,
        'section': section,
    }
    response = client.post(f'/api/deck/{deck_id}/preview_bulk', data=data)
    return response, data


def _preview_csv(
    client,
    deck_id,
    source,
    *,
    delimiter='auto',
    encoding='utf-8',
):
    options = {
        'delimiter': delimiter,
        'encoding': encoding,
    }
    response = client.post(
        f'/api/deck/{deck_id}/preview_csv',
        data={
            **options,
            'csv_file': (io.BytesIO(source), 'cards.csv'),
        },
        content_type='multipart/form-data',
    )
    return response, options


def test_deck_crud_pages(client, repo):
    assert client.get("/").status_code == 200

    response = client.post(
        "/create_deck",
        data={"name": "Физика", "description": "Механика"},
        follow_redirects=True,
    )
    assert "Физика" in response.text
    deck = repo.list_decks()[0]

    response = client.get(f"/deck/{deck.id}")
    assert response.status_code == 200
    assert "Механика" in response.text

    edit_page = client.get(f"/deck/{deck.id}/edit")
    assert edit_page.status_code == 200
    assert "Редактирование колоды" in edit_page.text

    client.post(f"/deck/{deck.id}/edit", data={"name": "Физика 2", "description": ""})
    assert repo.get_deck(deck.id).name == "Физика 2"

    client.post(f"/deck/{deck.id}/clone")
    assert len(repo.list_decks()) == 2

    client.post(f"/deck/{deck.id}/delete")
    assert repo.get_deck(deck.id) is None


def test_empty_safe_deck_ui_exposes_data_export_without_advanced_controls(
    client, deck_id
):
    page = client.get(f'/deck/{deck_id}')

    assert page.status_code == 200
    assert f'/deck/{deck_id}/export.json' in page.text
    assert f'/deck/{deck_id}/export.csv' in page.text
    assert 'Обычная колода' in page.text
    assert 'Advanced-колода' not in page.text


def test_home_explains_disabled_advanced_mode(client):
    page = client.get('/')

    assert page.status_code == 200
    assert 'Advanced LaTeX выключен — как включить' in page.text
    assert 'DIDACTIC_CARDS_TRUSTED_LATEX_ENABLED=true' in page.text
    assert 'value="advanced" disabled' in page.text


def test_create_deck_rejects_unknown_authoring_mode(client, repo):
    response = client.post(
        '/create_deck', data={'name': 'Wrong', 'authoring_mode': 'mixed'}
    )

    assert response.status_code == 400
    assert 'Неизвестный тип колоды' in response.text
    assert repo.list_decks() == []


def test_deck_type_is_chosen_at_creation_and_not_mixed_in_ui(
    client, app, tmp_path
):
    repository = SqliteRepository(tmp_path / 'deck-types')
    app.config['REPO'] = repository
    blocked = client.post(
        '/create_deck',
        data={'name': 'Blocked advanced', 'authoring_mode': 'advanced'},
    )
    assert blocked.status_code == 503
    assert repository.list_decks() == []

    app.config['TRUSTED_LATEX_ENABLED'] = True
    app.config['TRUSTED_COMPILER'] = RecordingTrustedCompiler()
    created = client.post(
        '/create_deck',
        data={'name': 'Raw cards', 'authoring_mode': 'advanced'},
    )
    assert created.status_code == 302
    deck = repository.list_decks()[0]
    assert deck.render_settings.authoring_mode.value == 'advanced'

    page = client.get(f'/deck/{deck.id}')
    assert 'Advanced-колода' in page.text
    assert 'Оформление обычной колоды' not in page.text
    assert 'Сохранить оформление' not in page.text
    assert 'необязательные оболочки сторон' in page.text
    assert 'MathJax-script' not in page.text
    assert 'preview-header' not in page.text


def test_advanced_without_wrapper_prints_raw_only_in_sandbox(
    client, app, tmp_path
):
    compiler = RecordingTrustedCompiler()
    repository, deck = _enable_trusted(app, tmp_path, compiler=compiler)
    app.config['RENDERER'] = LatexRenderer()

    generated = client.post(f'/deck/{deck.id}/generate')

    assert generated.status_code == 200
    assert generated.data == b'%PDF trusted'
    assert compiler.sources
    assert r'\textbf{raw}' in compiler.sources[-1]
    assert r'\textbackslash{}textbf' not in compiler.sources[-1]
    assert app.config['COMPILER'].sources == []


def test_advanced_deck_rejects_builtin_render_settings_form(
    client, app, tmp_path
):
    repository, deck = _enable_trusted(app, tmp_path)

    response = client.post(
        f'/deck/{deck.id}/render_settings',
        data={'version': deck.version, 'preset': 'centered'},
    )

    assert response.status_code == 409
    assert repository.get_render_settings(deck.id).authoring_mode.value == (
        'advanced'
    )


def test_deck_page_exposes_all_user_facing_workflows(client, app, deck_id):
    app.config['PRINT_PROFILES'] = {
        'standard': PrinterProfile('standard', 'Standard')
    }
    client.post(
        f'/api/deck/{deck_id}/add_card',
        json={'front': 'Q', 'back': 'A', 'section': 'Topic'},
    )
    page = client.get(f'/deck/{deck_id}')

    expected_controls = (
        'Сохранить оформление',
        'Добавить карточку',
        'Пакетное добавление',
        'Проверить файл',
        'Импортировать',
        'Переместить карточку 1',
        'Редактировать карточку 1',
        'Удалить карточку 1',
        'Экспорт JSON',
        'Экспорт CSV',
        'Предпросмотр LaTeX',
        'Сгенерировать PDF',
        'PDF: только лица',
        'PDF: только обороты',
        'PDF-превью',
        'Проверить перед печатью',
        'Очистить всё',
        'Профиль принтера',
    )
    for control in expected_controls:
        assert control in page.text


def test_deck_json_csv_export_and_import(client, repo, deck_id):
    card = Card(front='Q; quoted', back='A')
    repo.save_cards(deck_id, CardDeck([card]))

    json_response = client.get(f'/deck/{deck_id}/export.json')
    assert json_response.status_code == 200
    assert json_response.mimetype == 'application/json'
    assert 'filename*=' in json_response.headers['Content-Disposition']
    csv_response = client.get(f'/deck/{deck_id}/export.csv')
    assert csv_response.status_code == 200
    assert csv_response.data.startswith(b'\xef\xbb\xbf')

    imported = client.post(
        '/import_deck',
        data={
            'deck_file': (io.BytesIO(json_response.data), 'deck.json'),
        },
        content_type='multipart/form-data',
    )
    assert imported.status_code == 302
    imported_deck = repo.list_decks()[0]
    assert imported_deck.id != deck_id
    assert imported_deck.parent_id == deck_id
    assert repo.load_cards(imported_deck.id).cards[0].parent_id == card.id


def test_deck_import_and_export_validation_errors(client):
    assert client.get('/deck/missing/export.json').status_code == 302
    assert client.get('/deck/missing/export.csv').status_code == 302
    assert client.post('/import_deck').status_code == 400
    broken = client.post(
        '/import_deck',
        data={'deck_file': (io.BytesIO(b'{broken'), 'deck.json')},
        content_type='multipart/form-data',
    )
    assert broken.status_code == 400
    assert 'Некорректный JSON' in broken.text


def test_card_html_workflow(client, repo, deck_id):
    client.post(f"/deck/{deck_id}/add_card", data={"front": "", "back": ""})
    assert len(repo.load_cards(deck_id)) == 0

    response = client.post(
        f"/deck/{deck_id}/add_card",
        data={
            "front": "  Question  ",
            "back": "  Answer  ",
            "section": "  Mechanics  ",
        },
    )
    assert response.status_code == 302
    assert repo.load_cards(deck_id).cards[0].front == "Question"
    assert repo.load_cards(deck_id).cards[0].section == "Mechanics"

    bulk_preview, bulk_options = _preview_bulk(
        client,
        deck_id,
        'Q2||A2',
        section='Dynamics',
    )
    client.post(
        f"/deck/{deck_id}/add_cards_bulk",
        data={
            **bulk_options,
            'preview_token': bulk_preview.json['preview_token'],
        },
    )
    second_id = repo.load_cards(deck_id).cards[1].id
    edit_page = client.get(f"/deck/{deck_id}/edit_card/{second_id}")
    assert edit_page.status_code == 200
    assert "Q2" in edit_page.text

    client.post(
        f"/deck/{deck_id}/edit_card/{second_id}",
        data={"front": "Q2+", "back": "A2+", "section": "Kinematics"},
    )
    assert repo.load_cards(deck_id).cards[1].front == "Q2+"
    assert repo.load_cards(deck_id).cards[1].section == "Kinematics"

    first_id = repo.load_cards(deck_id).cards[0].id
    response = client.post(
        f"/deck/{deck_id}/delete_card/{first_id}", follow_redirects=True
    )
    assert "Question" not in response.text

    client.post(f"/deck/{deck_id}/reset")
    assert len(repo.load_cards(deck_id)) == 0


def test_html_escapes_user_content(client, deck_id):
    client.post(
        f"/deck/{deck_id}/add_card",
        data={"front": "<script>alert(1)</script>", "back": "A"},
    )
    page = client.get(f"/deck/{deck_id}")
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text


def test_csv_import_and_encoding_error(client, repo, deck_id):
    assert client.post(f"/deck/{deck_id}/import_csv").status_code == 302
    source = b'front;back\nfront;back\n'
    preview, options = _preview_csv(client, deck_id, source)
    assert preview.status_code == 200
    response = client.post(
        f"/deck/{deck_id}/import_csv",
        data={
            **options,
            'preview_token': preview.json['preview_token'],
            'csv_file': (io.BytesIO(source), 'cards.csv'),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    assert repo.load_cards(deck_id).cards[0].back == "back"

    response, _ = _preview_csv(
        client,
        deck_id,
        b'\xff\xff',
    )
    assert response.status_code == 400
    assert 'кодиров' in response.json['error']


def test_csv_preview_is_read_only_and_reports_validation(client, repo, deck_id):
    response = client.post(
        f"/api/deck/{deck_id}/preview_csv",
        data={
                "csv_file": (
                    io.BytesIO(b"front;back\nQ;A\nbad;row;extra;column"),
                    "cards.csv",
                ),
            "delimiter": "auto",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert response.json["accepted_count"] == 1
    assert response.json["rejected_count"] == 1
    assert len(repo.load_cards(deck_id)) == 0

    imported = client.post(
        f"/deck/{deck_id}/import_csv",
        data={
            "csv_file": (
                io.BytesIO(b"Q;A\nbad;row;extra;column"),
                "cards.csv",
            ),
            "delimiter": "semicolon",
        },
        content_type="multipart/form-data",
    )
    assert imported.status_code == 400
    assert len(repo.load_cards(deck_id)) == 0


def test_csv_preview_validates_file_encoding_dialect_and_deck(client, deck_id):
    assert client.post(f"/api/deck/{deck_id}/preview_csv").status_code == 400
    invalid_encoding = client.post(
        f"/api/deck/{deck_id}/preview_csv",
        data={"csv_file": (io.BytesIO(b"\xff\xfe"), "bad.csv")},
        content_type="multipart/form-data",
    )
    assert invalid_encoding.status_code == 400
    invalid_dialect = client.post(
        f"/api/deck/{deck_id}/preview_csv",
        data={
            "csv_file": (io.BytesIO(b"Q,A"), "cards.csv"),
            "delimiter": "pipes",
        },
        content_type="multipart/form-data",
    )
    assert invalid_dialect.status_code == 400
    missing_deck = client.post(
        "/api/deck/missing/preview_csv",
        data={"csv_file": (io.BytesIO(b"Q,A"), "cards.csv")},
        content_type="multipart/form-data",
    )
    assert missing_deck.status_code == 404


def test_mode_aware_csv_templates(client, repo, deck_id):
    safe = client.get(f'/deck/{deck_id}/import-template.csv')
    advanced = repo.create_deck(
        'Advanced',
        render_settings=DeckRenderSettings(authoring_mode='advanced'),
    )
    raw = client.get(f'/deck/{advanced.id}/import-template.csv')

    assert safe.data.decode('utf-8-sig') == 'section;front;back\n'
    assert raw.data.decode('utf-8-sig') == (
        'section;front;back;upper_header;lower_header\n'
    )


def test_advanced_csv_preview_trust_and_lossless_import(client, repo):
    deck = repo.create_deck(
        'Advanced import',
        render_settings=DeckRenderSettings(authoring_mode='advanced'),
    )
    source = (
        'section;front;back;upper_header;lower_header\n'
        'Raw;"  \\vfill\nFront  ";Back;"{{ card_number }}";"Foot\\\\line"\n'
    ).encode()
    preview, options = _preview_csv(client, deck.id, source)

    assert preview.status_code == 200
    assert preview.json['accepted_count'] == 1
    assert preview.json['columns'] == [
        'section', 'front', 'back', 'upper_header', 'lower_header'
    ]
    assert preview.json['cards'][0]['front'] == '  \\vfill\nFront  '
    assert 'id' not in preview.json['cards'][0]
    token = preview.json['preview_token']

    untrusted = client.post(
        f'/deck/{deck.id}/import_csv',
        data={
            **options,
            'preview_token': token,
            'csv_file': (io.BytesIO(source), 'cards.csv'),
        },
        content_type='multipart/form-data',
    )
    assert untrusted.status_code == 400
    assert len(repo.load_cards(deck.id)) == 0

    imported = client.post(
        f'/deck/{deck.id}/import_csv',
        data={
            **options,
            'preview_token': token,
            'trust_raw_csv': 'on',
            'csv_file': (io.BytesIO(source), 'cards.csv'),
        },
        content_type='multipart/form-data',
    )
    assert imported.status_code == 302
    card = repo.load_cards(deck.id).cards[0]
    assert card.front == '  \\vfill\nFront  '
    assert card.upper_header == '{{ card_number }}'
    assert card.lower_header == r'Foot\\line'


def test_csv_import_requires_fresh_preview_for_same_file_and_deck_version(
    client, repo, deck_id
):
    source = b'front;back\nQ;A\n'
    preview, options = _preview_csv(client, deck_id, source)
    assert preview.json['preview_token']

    client.post(
        f'/api/deck/{deck_id}/add_card',
        json={'front': 'changed', 'back': 'deck'},
    )
    stale = client.post(
        f'/deck/{deck_id}/import_csv',
        data={
            **options,
            'preview_token': preview.json['preview_token'],
            'csv_file': (io.BytesIO(source), 'cards.csv'),
        },
        content_type='multipart/form-data',
    )
    assert stale.status_code == 400
    assert [card.front for card in repo.load_cards(deck_id).cards] == ['changed']


def test_bulk_v2_preview_is_required_and_imports_advanced_headers(
    client, repo
):
    deck = repo.create_deck(
        'Advanced bulk',
        render_settings=DeckRenderSettings(authoring_mode='advanced'),
    )
    source = r'"A || B"||Back\\line||Top||Bottom'
    preview, options = _preview_bulk(
        client, deck.id, source, section='Raw'
    )
    assert preview.status_code == 200
    assert preview.json['cards'][0]['front'] == 'A || B'

    changed = client.post(
        f'/deck/{deck.id}/add_cards_bulk',
        data={**options, 'bulk': source + 'x',
              'preview_token': preview.json['preview_token']},
    )
    assert changed.status_code == 400
    assert len(repo.load_cards(deck.id)) == 0

    imported = client.post(
        f'/deck/{deck.id}/add_cards_bulk',
        data={**options, 'preview_token': preview.json['preview_token']},
    )
    assert imported.status_code == 302
    card = repo.load_cards(deck.id).cards[0]
    assert (card.front, card.back, card.upper_header, card.lower_header) == (
        'A || B', r'Back\\line', 'Top', 'Bottom'
    )


def test_csv_preview_exposes_addressed_issues(client, deck_id):
    response, _ = _preview_csv(
        client,
        deck_id,
        b'front;answer\nQ;A\n',
        delimiter='semicolon',
    )
    assert response.status_code == 200
    assert response.json['preview_token'] == ''
    assert response.json['issues'][0] == {
        'row': 1,
        'code': 'unknown_column',
        'reason': "Неизвестная колонка 'answer' для режима safe",
        'column': 'answer',
        'severity': 'error',
    }


def test_import_preview_routes_validate_deck_options_and_quota(
    client, app, repo, deck_id
):
    assert client.post('/api/deck/missing/preview_bulk').status_code == 404
    assert client.post(
        '/deck/missing/add_cards_bulk', data={}
    ).status_code == 302
    assert client.post(
        '/deck/missing/import_csv',
        data={'csv_file': (io.BytesIO(b'front;back\nQ;A'), 'cards.csv')},
        content_type='multipart/form-data',
    ).status_code == 302
    app.config['MAX_CARDS'] = 0
    bulk_preview, bulk_options = _preview_bulk(client, deck_id, 'Q||A')
    bulk_import = client.post(
        f'/deck/{deck_id}/add_cards_bulk',
        data={
            **bulk_options,
            'preview_token': bulk_preview.json['preview_token'],
        },
    )
    assert bulk_import.status_code == 400

    csv_deck = repo.create_deck('CSV quota')
    source = b'front;back\nQ;A\n'
    csv_preview, csv_options = _preview_csv(client, csv_deck.id, source)
    csv_import = client.post(
        f'/deck/{csv_deck.id}/import_csv',
        data={
            **csv_options,
            'preview_token': csv_preview.json['preview_token'],
            'csv_file': (io.BytesIO(source), 'cards.csv'),
        },
        content_type='multipart/form-data',
    )
    assert csv_import.status_code == 400
    assert 'Максимум карточек' in csv_import.text
    assert len(repo.load_cards(csv_deck.id)) == 0


def test_api_card_workflow(client, deck_id):
    added = client.post(
        f"/api/deck/{deck_id}/add_card",
        json={"front": "A", "back": "1", "section": "Algebra"},
    )
    assert added.status_code == 200
    assert added.json["cards_count"] == 1
    assert added.json["card"]["section"] == "Algebra"
    first_id = added.json["card"]["id"]

    second = client.post(
        f"/api/deck/{deck_id}/add_card", json={"front": "B", "back": "2"}
    )
    second_id = second.json["card"]["id"]
    reordered = client.post(
        f"/api/deck/{deck_id}/reorder", json={"order": [second_id, first_id]}
    )
    assert reordered.status_code == 200

    edited = client.put(
        f"/api/deck/{deck_id}/edit_card/{second_id}",
        json={"front": "B+", "back": "2+", "section": "Geometry"},
    )
    assert edited.json["card"]["front"] == "B+"
    assert edited.json["card"]["section"] == "Geometry"

    deleted = client.delete(f"/api/deck/{deck_id}/delete_card/{first_id}")
    assert deleted.status_code == 200
    assert deleted.json["cards_count"] == 1


@pytest.mark.parametrize(
    ("method", "path", "payload", "status"),
    [
        ("post", "/api/deck/{deck}/add_card", {}, 415),
        ("post", "/api/deck/{deck}/add_card", {"json": {"front": "", "back": ""}}, 400),
        ("delete", "/api/deck/{deck}/delete_card/missing", {}, 404),
        ("put", "/api/deck/{deck}/edit_card/missing", {"json": {"front": "X"}}, 404),
        ("post", "/api/deck/{deck}/reorder", {"json": {"order": [99]}}, 400),
    ],
)
def test_api_validation(client, deck_id, method, path, payload, status):
    response = getattr(client, method)(path.format(deck=deck_id), **payload)
    assert response.status_code == status


def test_render_settings_form_is_versioned_and_updates_preview_contract(
    client, repo, deck_id
):
    initial = repo.get_deck(deck_id)
    page = client.get(f'/deck/{deck_id}')
    assert 'data-horizontal-alignment="center"' in page.text
    assert 'id="header-repeat"' in page.text
    assert 'id="section-break"' in page.text
    assert 'PDF-превью является точным' in page.text

    response = client.post(
        f'/deck/{deck_id}/render_settings',
        data={
            'version': initial.version,
            'preset': 'custom',
            'horizontal_alignment': 'right',
            'vertical_alignment': 'bottom',
            'header_visibility': 'both',
            'header_position': 'bottom',
            'header_alignment': 'center',
            'header_repeat': 'section-start',
            'section_break': 'new-sheet',
        },
    )

    assert response.status_code == 302
    assert repo.get_render_settings(deck_id) == DeckRenderSettings(
        preset='custom',
        horizontal_alignment='right',
        vertical_alignment='bottom',
        header_visibility='both',
        header_position='bottom',
        header_alignment='center',
        header_repeat='section-start',
        section_break='new-sheet',
    )
    assert repo.get_deck(deck_id).version == initial.version + 1
    updated_page = client.get(f'/deck/{deck_id}')
    assert 'data-horizontal-alignment="right"' in updated_page.text
    assert 'data-header-position="top"' in updated_page.text
    assert 'data-header-repeat="section-start"' in updated_page.text
    assert 'data-section-break="new-sheet"' in updated_page.text

    stale = client.post(
        f'/deck/{deck_id}/render_settings',
        data={'version': initial.version, 'preset': 'centered'},
    )
    assert stale.status_code == 409


def test_render_settings_form_rejects_unknown_values_atomically(
    client, repo, deck_id
):
    before = repo.get_deck(deck_id)
    response = client.post(
        f'/deck/{deck_id}/render_settings',
        data={
            'version': before.version,
            'preset': 'custom',
            'horizontal_alignment': 'justify',
        },
    )

    assert response.status_code == 400
    assert repo.get_render_settings(deck_id) == DeckRenderSettings.centered()
    assert repo.get_deck(deck_id).version == before.version


def test_safe_typography_and_two_headers_are_available_and_saved_from_ui(
    client, repo, deck_id
):
    deck = repo.get_deck(deck_id)
    page = client.get(f'/deck/{deck_id}')
    assert 'id="typography-profile"' in page.text
    assert 'id="typography-custom-controls"' in page.text
    assert 'id="secondary-header-visibility"' in page.text
    assert 'id="header-rule"' in page.text
    assert 'id="secondary-header-rule"' in page.text
    assert '{{ card_number }}' in page.text
    assert '{{ card_count }}' in page.text
    assert 'class="btn-small header-placeholder-button"' in page.text
    assert 'Содержимое карточки не становится LaTeX-кодом' in page.text

    response = client.post(
        f'/deck/{deck_id}/render_settings',
        data={
            'version': deck.version,
            'preset': 'centered',
            'typography_profile': 'custom',
            'body_font_family': 'sans',
            'body_font_size': 'large',
            'body_font_weight': 'bold',
            'body_font_style': 'italic',
            'line_spacing': 'relaxed',
            'paragraph_spacing': 'medium',
            'header_visibility': 'both',
            'header_source': 'custom',
            'header_text': 'Курс & группа {{ card_number }}/{{ card_count }}',
            'header_font_family': 'serif',
            'header_font_size': 'normal',
            'header_font_weight': 'bold',
            'header_font_style': 'italic',
            'header_rule': 'thin',
            'header_rule_spacing': 'compact',
            'secondary_header_visibility': 'front',
            'secondary_header_position': 'bottom',
            'secondary_header_alignment': 'right',
            'secondary_header_repeat': 'section-start',
            'secondary_header_source': 'card-number',
            'secondary_header_font_family': 'mono',
            'secondary_header_font_size': 'small',
            'secondary_header_font_weight': 'normal',
            'secondary_header_font_style': 'upright',
            'secondary_header_rule': 'medium',
            'secondary_header_rule_spacing': 'relaxed',
        },
    )

    assert response.status_code == 302
    saved = repo.get_render_settings(deck_id)
    assert saved.typography_profile.value == 'custom'
    assert saved.body_font_family.value == 'sans'
    assert saved.header_text == 'Курс & группа {{ card_number }}/{{ card_count }}'
    assert saved.header_rule.value == 'thin'
    assert saved.header_rule_spacing.value == 'compact'
    assert saved.secondary_header_visibility.value == 'front'
    assert saved.secondary_header_source.value == 'card-number'
    assert saved.secondary_header_rule.value == 'medium'
    assert saved.secondary_header_rule_spacing.value == 'relaxed'


def test_safe_header_form_rejects_unknown_placeholder_atomically(
    client, repo, deck_id
):
    deck = repo.get_deck(deck_id)

    response = client.post(
        f'/deck/{deck_id}/render_settings',
        data={
            'version': deck.version,
            'preset': 'centered',
            'header_source': 'custom',
            'header_text': '{{ arbitrary_latex }}',
        },
    )

    assert response.status_code == 400
    assert repo.get_render_settings(deck_id) == DeckRenderSettings.centered()


def test_safe_typography_form_rejects_latex_as_font_token(client, repo, deck_id):
    deck = repo.get_deck(deck_id)

    response = client.post(
        f'/deck/{deck_id}/render_settings',
        data={
            'version': deck.version,
            'preset': 'centered',
            'typography_profile': 'custom',
            'body_font_family': r'\input{/etc/passwd}',
        },
    )

    assert response.status_code == 400
    assert repo.get_render_settings(deck_id) == DeckRenderSettings.centered()


@pytest.mark.parametrize(
    ('preset', 'horizontal', 'vertical'),
    [
        ('centered', 'center', 'center'),
    ],
)
def test_render_settings_presets_are_canonical(
    client, repo, deck_id, preset, horizontal, vertical
):
    deck = repo.get_deck(deck_id)
    response = client.post(
        f'/deck/{deck_id}/render_settings',
        data={
            'version': deck.version,
            'preset': preset,
            'horizontal_alignment': 'right',
            'vertical_alignment': 'bottom',
        },
    )

    assert response.status_code == 302
    saved = repo.get_render_settings(deck_id)
    assert saved.horizontal_alignment.value == horizontal
    assert saved.vertical_alignment.value == vertical


def test_sheet_counters_include_physical_section_break_slots(
    client, app, repo, deck_id
):
    app.config['RENDERER'] = LatexRenderer(cards_per_row=2, rows_per_page=4)
    client.post(
        f'/api/deck/{deck_id}/add_card',
        json={'front': 'Q1', 'back': 'A1', 'section': 'One'},
    )
    repo.save_render_settings(
        deck_id, DeckRenderSettings(section_break='new-sheet')
    )

    added = client.post(
        f'/api/deck/{deck_id}/add_card',
        json={'front': 'Q2', 'back': 'A2', 'section': 'Two'},
    )
    page = client.get(f'/deck/{deck_id}')

    assert added.json['print_pages'] == 2
    assert added.json['empty_slots'] == 14
    assert '<span id="pages-count">2</span>' in page.text
    assert '<span id="empty-count">14</span>' in page.text


def test_api_rejects_non_string_section(client, deck_id):
    response = client.post(
        f'/api/deck/{deck_id}/add_card',
        json={'front': 'Q', 'back': 'A', 'section': 42},
    )
    assert response.status_code == 400


def test_generate_preview_success_and_failure(client, repo, deck_id, app, app_fail_compiler):
    empty = client.post(f"/deck/{deck_id}/generate")
    assert "Добавьте хотя бы одну карточку" in empty.text
    assert "Добавьте хотя бы одну карточку" in client.post(
        f"/deck/{deck_id}/preview_pdf"
    ).text
    assert "Добавьте хотя бы одну карточку" in client.post(
        f"/deck/{deck_id}/preview_latex"
    ).text

    client.post(f"/api/deck/{deck_id}/add_card", json={"front": "Q", "back": "A"})
    preview = client.post(f"/deck/{deck_id}/preview_latex")
    assert "documentclass" in preview.text

    generated = client.post(f"/deck/{deck_id}/generate")
    assert generated.status_code == 200
    assert generated.mimetype == "application/pdf"
    assert generated.data.startswith(b"%PDF")
    assert len(app.config["RENDERER"].decks[-1]) == 8

    inline_preview = client.post(f"/deck/{deck_id}/preview_pdf")
    assert inline_preview.status_code == 200
    assert inline_preview.mimetype == "application/pdf"
    assert inline_preview.headers["Content-Disposition"].startswith("inline;")

    failing_repo = app_fail_compiler.config["REPO"]
    failing_deck = failing_repo.create_deck("Fail")
    failing_client = app_fail_compiler.test_client()
    failing_client.post(
        f"/api/deck/{failing_deck.id}/add_card", json={"front": "Q", "back": "A"}
    )
    failure = failing_client.post(f"/deck/{failing_deck.id}/generate")
    assert failure.status_code == 422
    assert "Не удалось скомпилировать PDF" in failure.text
    assert "internal path" not in failure.text


def test_missing_and_invalid_html_resources_redirect(client, deck_id):
    assert client.get("/deck/missing").status_code == 302
    assert client.get("/deck/missing/edit").status_code == 302
    assert client.get("/deck/missing/edit_card/0").status_code == 302
    assert client.get(f"/deck/{deck_id}/edit_card/missing").status_code == 302


def test_api_empty_json_contracts(client, deck_id):
    headers = {"content_type": "application/json", "data": "null"}
    assert client.post(f"/api/deck/{deck_id}/add_card", **headers).status_code == 400
    assert client.post(f"/api/deck/{deck_id}/reorder", **headers).status_code == 400
    assert client.put(f"/api/deck/{deck_id}/edit_card/0", **headers).status_code == 400


def test_api_rejects_unknown_deck(client, repo):
    response = client.post(
        "/api/deck/missing/add_card", json={"front": "orphan", "back": ""}
    )
    assert response.status_code == 404
    assert repo.get_deck("missing") is None


def test_html_unknown_deck_mutation_redirects(client):
    response = client.post(
        "/deck/missing/add_card", data={"front": "orphan", "back": ""}
    )
    assert response.status_code == 302
    assert response.location.endswith("/")


def test_delete_card_requires_non_get_method(client, deck_id):
    added = client.post(
        f"/api/deck/{deck_id}/add_card", json={"front": "Q", "back": "A"}
    )
    response = client.get(
        f"/deck/{deck_id}/delete_card/{added.json['card']['id']}"
    )
    assert response.status_code == 405


def test_api_rejects_non_string_card_fields(client, deck_id):
    response = client.post(
        f"/api/deck/{deck_id}/add_card", json={"front": 123, "back": None}
    )
    assert response.status_code == 400


def test_api_rejects_non_list_reorder_payload(client, deck_id):
    response = client.post(f"/api/deck/{deck_id}/reorder", json={"order": None})
    assert response.status_code == 400


def test_api_rejects_non_object_delete_and_non_string_edit(client, deck_id):
    assert client.delete(
        f"/api/deck/{deck_id}/delete_card/missing", json=[]
    ).status_code == 400
    assert client.put(
        f"/api/deck/{deck_id}/edit_card/missing",
        json={"front": "Q", "back": 7},
    ).status_code == 400


def test_api_rejects_uuid_reorder_with_wrong_members(client, deck_id):
    response = client.post(
        f"/api/deck/{deck_id}/reorder", json={"order": ["missing"]}
    )
    assert response.status_code == 400


def test_stale_deck_version_is_rejected(client, repo, deck_id):
    initial_version = repo.get_deck(deck_id).version
    first = client.post(
        f"/api/deck/{deck_id}/add_card",
        json={"front": "first", "version": initial_version},
    )
    assert first.status_code == 200
    assert first.json["deck_version"] > initial_version

    stale = client.post(
        f"/api/deck/{deck_id}/add_card",
        json={"front": "stale", "version": initial_version},
    )
    assert stale.status_code == 409
    assert stale.json["current_version"] == first.json["deck_version"]
    assert [card.front for card in repo.load_cards(deck_id).cards] == ["first"]

    bulk_preview, bulk_options = _preview_bulk(
        client, deck_id, 'second||answer'
    )
    html_conflict = client.post(
        f"/deck/{deck_id}/add_cards_bulk",
        data={
            **bulk_options,
            'version': initial_version,
            'preview_token': bulk_preview.json['preview_token'],
        },
    )
    assert html_conflict.status_code == 409
    assert "Колода уже изменена" in html_conflict.text


@pytest.mark.parametrize("version", [True, 1.5, {}, "bad", 0, -1])
def test_api_rejects_invalid_deck_version(client, deck_id, version):
    response = client.post(
        f"/api/deck/{deck_id}/add_card",
        json={"front": "Q", "version": version},
    )
    assert response.status_code == 400


def test_state_changing_form_requires_csrf_token(client, app):
    app.config['CSRF_ENABLED'] = True
    response = client.post('/create_deck', data={"name": "CSRF"})
    assert response.status_code == 400


def test_state_changing_form_accepts_valid_csrf_token(client, app):
    app.config['CSRF_ENABLED'] = True
    client.get('/')
    with client.session_transaction() as flask_session:
        token = flask_session['_csrf_token']
    response = client.post(
        '/create_deck', data={"name": "CSRF", "_csrf_token": token}
    )
    assert response.status_code == 302


def test_card_limit_is_enforced(client, app, deck_id):
    app.config["MAX_CARDS"] = 1
    assert client.post(
        f"/api/deck/{deck_id}/add_card", json={"front": "1", "back": ""}
    ).status_code == 200
    assert client.post(
        f"/api/deck/{deck_id}/add_card", json={"front": "2", "back": ""}
    ).status_code == 409


def test_compile_failure_has_error_status(app_fail_compiler):
    repo = app_fail_compiler.config["REPO"]
    deck = repo.create_deck("Fail")
    client = app_fail_compiler.test_client()
    client.post(f"/api/deck/{deck.id}/add_card", json={"front": "Q", "back": "A"})
    assert client.post(f"/deck/{deck.id}/generate").status_code >= 400


def test_pdf_content_disposition_is_latin1_safe(client, deck_id):
    client.post(f"/api/deck/{deck_id}/add_card", json={"front": "Q", "back": "A"})
    response = client.post(f"/deck/{deck_id}/generate")
    disposition = response.headers["Content-Disposition"]
    disposition.encode("latin-1")
    assert "filename*=" in disposition


@pytest.mark.parametrize(
    ('endpoint', 'side', 'suffix'),
    [
        ('fronts', 'front', '-fronts.pdf'),
        ('backs', 'back', '-backs.pdf'),
    ],
)
def test_split_pdf_downloads_use_requested_side_and_filename(
    client, app, deck_id, endpoint, side, suffix
):
    client.post(f"/api/deck/{deck_id}/add_card", json={"front": "Q", "back": "A"})

    response = client.post(f"/deck/{deck_id}/generate/{endpoint}")

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert suffix in response.headers["Content-Disposition"]
    assert app.config["RENDERER"].sides[-1] == side
    assert len(app.config["RENDERER"].decks[-1]) == 8


@pytest.mark.parametrize('endpoint', ['fronts', 'backs'])
def test_split_pdf_rejects_empty_deck(client, deck_id, endpoint):
    response = client.post(f"/deck/{deck_id}/generate/{endpoint}")
    assert response.status_code == 200
    assert "Добавьте хотя бы одну карточку" in response.text


def test_preflight_api_is_read_only_and_returns_report(client, repo, deck_id):
    client.post(f"/api/deck/{deck_id}/add_card", json={"front": "Q", "back": ""})
    version = repo.get_deck(deck_id).version

    response = client.post(f"/api/deck/{deck_id}/preflight")

    assert response.status_code == 200
    assert response.json['ready'] is True
    assert response.json['warning_count'] == 1
    assert {issue['code'] for issue in response.json['issues']} == {
        'empty-side', 'partial-sheet'
    }
    assert repo.get_deck(deck_id).version == version


def test_preflight_api_reports_unsupported_formula(client, app, deck_id):
    app.config['RENDERER'] = LatexRenderer(cards_per_row=2, rows_per_page=4)
    client.post(
        f"/api/deck/{deck_id}/add_card",
        json={"front": r"$\input{/etc/passwd}$", "back": "A"},
    )
    response = client.post(f"/api/deck/{deck_id}/preflight")
    assert response.status_code == 200
    assert response.json['ready'] is False
    assert response.json['issues'][0]['code'] == 'unsupported-formula'
    assert app.config['COMPILER'].sources == []


def test_preflight_api_rejects_empty_deck_without_compilation(client, app, deck_id):
    response = client.post(f"/api/deck/{deck_id}/preflight")
    assert response.status_code == 200
    assert response.json['ready'] is False
    assert response.json['issues'][0]['code'] == 'empty-deck'
    assert app.config['COMPILER'].sources == []


def test_print_profile_is_applied_to_generate_preview_and_preflight(
    client, app, deck_id
):
    profile = PrinterProfile(
        'test-printer',
        'Тестовый принтер',
        back_offset_x_mm=1.5,
        registration_marks=True,
    )
    app.config['PRINT_PROFILES'] = {profile.key: profile}
    app.config['RENDERER_FACTORY'] = lambda selected: LatexRenderer(
        cards_per_row=2,
        rows_per_page=4,
        back_rotation_deg=selected.back_rotation_deg,
        back_offset_x_mm=selected.back_offset_x_mm,
        registration_marks=selected.registration_marks,
    )
    client.post(f"/api/deck/{deck_id}/add_card", json={"front": "Q", "back": "A"})

    page = client.get(f'/deck/{deck_id}')
    assert 'Тестовый принтер' in page.text
    preview = client.post(
        f'/deck/{deck_id}/preview_latex', data={'profile_id': profile.key}
    )
    assert r'\hspace*{1.5mm}' in preview.text
    generated = client.post(
        f'/deck/{deck_id}/generate', data={'profile_id': profile.key}
    )
    assert generated.status_code == 200
    assert r'\hspace*{1.5mm}' in app.config['COMPILER'].sources[-1]
    assert r'\rotatebox{180}{\backcard' in app.config['COMPILER'].sources[-1]
    preflight = client.post(
        f'/api/deck/{deck_id}/preflight', data={'profile_id': profile.key}
    )
    assert preflight.status_code == 200
    assert preflight.json['ready'] is True


@pytest.mark.parametrize(
    'path',
    [
        '/deck/{deck}/generate',
        '/deck/{deck}/generate/fronts',
        '/deck/{deck}/generate/backs',
        '/deck/{deck}/preview_pdf',
        '/deck/{deck}/preview_latex',
        '/api/deck/{deck}/preflight',
    ],
)
def test_print_routes_reject_unknown_profile(client, deck_id, path):
    client.post(f"/api/deck/{deck_id}/add_card", json={"front": "Q", "back": "A"})
    response = client.post(
        path.format(deck=deck_id), data={'profile_id': 'missing'}
    )
    assert response.status_code == 400


def test_persistent_printer_profile_web_crud_and_print_job(tmp_path):
    class RecordingCompiler:
        def __init__(self):
            self.sources = []

        def is_available(self):
            return True

        def compile(self, source):
            self.sources.append(source)
            return CompileResult(True, b'%PDF-profile', '')

    compiler = RecordingCompiler()
    profile_app = create_app(
        config=AppConfig(secret_key='profile-test', csrf_enabled=False),
        data_dir=tmp_path / 'profiles',
        compiler=compiler,
    )
    profile_app.config['TESTING'] = True
    profile_client = profile_app.test_client()

    response = profile_client.post('/printer_profiles/save', data={
        'key': 'office-printer',
        'name': 'Office <printer>',
        'duplex_mode': 'short-edge',
        'back_rotation_deg': '0',
        'front_offset_x_mm': '0,25',
        'front_offset_y_mm': '0',
        'back_offset_x_mm': '-1.5',
        'back_offset_y_mm': '0.75',
        'back_border': 'on',
        'registration_marks': 'on',
    })
    assert response.status_code == 302
    saved = profile_app.config['REPO'].list_printer_profiles()[0]
    assert saved.key == 'office-printer'
    assert saved.front_offset_x_mm == 0.25
    assert saved.duplex_mode.value == 'short-edge'
    assert saved.back_rotation_deg == 0

    page = profile_client.get('/printer_profiles')
    assert 'Office &lt;printer&gt;' in page.text
    assert '0°' in page.text
    assert f'/printer_profiles?edit={saved.key}' in page.text
    edit_page = profile_client.get(
        '/printer_profiles', query_string={'edit': saved.key}
    )
    assert edit_page.status_code == 200
    assert 'Редактировать профиль' in edit_page.text
    assert 'value="office-printer" readonly' in edit_page.text
    assert 'value="Office &lt;printer&gt;"' in edit_page.text
    assert 'name="back_offset_x_mm"' in edit_page.text
    assert 'value="-1.5"' in edit_page.text
    deck = profile_app.config['REPO'].create_deck('Profile deck')
    profile_app.config['REPO'].save_cards(
        deck.id, CardDeck([Card(front='Q', back='A')])
    )
    generated = profile_client.post(
        f'/deck/{deck.id}/generate', data={'profile_id': saved.key}
    )
    assert generated.status_code == 200
    assert r'\hspace*{-1.5mm}' in compiler.sources[-1]
    assert compiler.sources[-1].count(r'\registrationmarks') == 3

    deleted = profile_client.post(f'/printer_profiles/{saved.key}/delete')
    assert deleted.status_code == 302
    assert profile_app.config['REPO'].list_printer_profiles() == []


def test_unknown_saved_profile_edit_is_reported(client):
    response = client.get('/printer_profiles', query_string={'edit': 'missing'})

    assert response.status_code == 404
    assert 'профиль для редактирования не найден' in response.text


def test_calibration_sheet_download_uses_selected_profile(tmp_path):
    class RecordingCompiler:
        def __init__(self):
            self.sources = []

        def is_available(self):
            return True

        def compile(self, source):
            self.sources.append(source)
            return CompileResult(True, b'%PDF-calibration', '')

    profile = PrinterProfile(
        'office-printer', 'Office printer', duplex_mode='short-edge',
        back_offset_x_mm=-1.25, back_offset_y_mm=0.5,
    )
    compiler = RecordingCompiler()
    calibration_app = create_app(
        config=AppConfig(
            secret_key='calibration-test', csrf_enabled=False,
            printer_profiles=(profile,),
        ),
        data_dir=tmp_path / 'calibration',
        compiler=compiler,
    )
    calibration_app.config['TESTING'] = True

    response = calibration_app.test_client().post(
        '/printer_profiles/calibration-sheet',
        data={'profile_id': profile.key},
    )

    assert response.status_code == 200
    assert response.mimetype == 'application/pdf'
    assert 'printer-calibration-office-printer.pdf' in response.headers[
        'Content-Disposition'
    ]
    assert r'\calibrationtargets{-1.25}{0.5}{magenta,dashed}' in compiler.sources[0]
    page = calibration_app.test_client().get('/printer_profiles')
    assert 'не настройки' in page.text
    assert 'Скачать калибровочный PDF' in page.text


@pytest.mark.parametrize(
    ('profile_id', 'expected_x', 'expected_y'),
    [
        ('standard-long-edge', '1.2', '0.4'),
        ('standard-short-edge', '-1.2', '-0.4'),
    ],
)
def test_calibration_calculator_applies_profile_flip_mode_without_writing(
    client, app, profile_id, expected_x, expected_y
):
    mode = 'short-edge' if profile_id.endswith('short-edge') else 'long-edge'
    app.config['PRINT_PROFILES'] = {
        profile_id: PrinterProfile(profile_id, profile_id, duplex_mode=mode)
    }

    response = client.post(
        '/printer_profiles/calibration-calculate',
        data={
            'profile_id': profile_id,
            'measured_x_mm': '1.2',
            'measured_y_mm': '-0.4',
        },
    )

    assert response.status_code == 200
    assert f'«Оборот X» = <code>{expected_x}</code>' in response.text
    assert f'«Оборот Y» = <code>{expected_y}</code>' in response.text


def test_calibration_calculator_validates_input_and_flags_implausible_result(
    client, app,
):
    app.config['PRINT_PROFILES'] = {
        'standard-long-edge': PrinterProfile(
            'standard-long-edge', 'Standard long-edge'
        )
    }
    missing_profile = client.post(
        '/printer_profiles/calibration-calculate',
        data={'profile_id': 'missing', 'measured_x_mm': '0', 'measured_y_mm': '0'},
    )
    non_finite = client.post(
        '/printer_profiles/calibration-calculate',
        data={
            'profile_id': 'standard-long-edge',
            'measured_x_mm': 'nan',
            'measured_y_mm': '0',
        },
    )
    outside_limits = client.post(
        '/printer_profiles/calibration-calculate',
        data={
            'profile_id': 'standard-long-edge',
            'measured_x_mm': '11',
            'measured_y_mm': '0',
        },
    )

    assert missing_profile.status_code == 400
    assert non_finite.status_code == 400
    assert outside_limits.status_code == 200
    assert 'выходит за допустимые ±10 мм' in outside_limits.text


def test_printer_profile_page_contains_physical_acceptance_matrix(client):
    page = client.get('/printer_profiles')

    assert page.status_code == 200
    assert 'Протокол физической приёмки' in page.text
    assert 'Автодуплекс long-edge' in page.text
    assert 'Автодуплекс short-edge' in page.text
    assert 'Ручная подача' in page.text
    assert '99,5–100,5 мм' in page.text


@pytest.mark.parametrize(
    'form',
    [
        {'key': 'Invalid', 'name': 'Name'},
        {'key': 'valid', 'name': '', 'duplex_mode': 'long-edge'},
        {'key': 'valid', 'name': 'Name', 'duplex_mode': 'diagonal'},
        {'key': 'valid', 'name': 'Name', 'back_rotation_deg': '90'},
        {'key': 'valid', 'name': 'Name', 'back_offset_x_mm': 'not-number'},
        {'key': 'valid', 'name': 'Name', 'back_offset_x_mm': '11'},
        {'key': 'standard-long-edge', 'name': 'Override built-in'},
    ],
)
def test_persistent_printer_profile_validation_is_atomic(tmp_path, form):
    profile_app = create_app(
        config=AppConfig(secret_key='profile-test', csrf_enabled=False),
        data_dir=tmp_path / 'profiles',
    )
    profile_app.config['TESTING'] = True
    response = profile_app.test_client().post('/printer_profiles/save', data=form)
    assert response.status_code == 400
    assert profile_app.config['REPO'].list_printer_profiles() == []


def test_configured_printer_profile_cannot_be_deleted(client, app):
    profile = PrinterProfile('built-in', 'Built in')
    app.config['PRINT_PROFILES'] = {profile.key: profile}
    assert client.post('/printer_profiles/built-in/delete').status_code == 400


def test_security_headers_are_added(client):
    response = client.get('/')
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.headers['X-Frame-Options'] == 'DENY'
    assert response.headers['Referrer-Policy'] == 'no-referrer'
    assert "object-src 'none'" in response.headers['Content-Security-Policy']
    assert "frame-src 'self' blob:" in response.headers['Content-Security-Policy']


def test_local_favicon_is_served(client):
    response = client.get('/cards/static/cards/favicon.svg')
    assert response.status_code == 200
    assert response.mimetype == 'image/svg+xml'


def test_health_endpoints_report_ready_components(client):
    live = client.get('/health/live')
    ready = client.get('/health/ready')
    assert live.status_code == 200
    assert live.json == {'status': 'ok'}
    assert ready.status_code == 200
    assert ready.json == {
        'status': 'ready',
        'components': {'storage': 'ok', 'tex': 'ok'},
    }


def test_readiness_requires_sandbox_only_when_trusted_feature_is_enabled(
    client, app
):
    app.config['TRUSTED_LATEX_ENABLED'] = True
    app.config['TRUSTED_COMPILER'] = None

    unavailable = client.get('/health/ready')

    assert unavailable.status_code == 503
    assert unavailable.json['components']['trusted-tex-sandbox'] == 'unavailable'

    class AvailableSandbox:
        def is_available(self):
            return True

    app.config['TRUSTED_COMPILER'] = AvailableSandbox()
    ready = client.get('/health/ready')
    assert ready.status_code == 200
    assert ready.json['components']['trusted-tex-sandbox'] == 'ok'


def test_every_response_has_unique_request_id(client):
    first = client.get('/')
    second = client.get('/')
    first_id = first.headers['X-Request-ID']
    second_id = second.headers['X-Request-ID']
    assert str(uuid.UUID(first_id)) == first_id
    assert str(uuid.UUID(second_id)) == second_id
    assert first_id != second_id


def test_pdf_generation_logs_safe_duration_metric(client, app, deck_id, monkeypatch):
    events = []
    monkeypatch.setattr(
        app.logger,
        'info',
        lambda message, *, extra: events.append((message, extra)),
    )
    client.post(f"/api/deck/{deck_id}/add_card", json={"front": "Q", "back": "A"})
    response = client.post(f'/deck/{deck_id}/generate')
    metric = next(extra for message, extra in events if message == 'pdf_compilation')
    assert response.status_code == 200
    assert metric['deck_id'] == deck_id
    assert metric['side'] == 'duplex'
    assert metric['status'] == 'success'
    assert metric['duration_ms'] >= 0
    assert metric['request_id'] == response.headers['X-Request-ID']


def test_error_page_displays_request_id(app_fail_compiler):
    repo = app_fail_compiler.config['REPO']
    deck = repo.create_deck('Fail')
    client = app_fail_compiler.test_client()
    client.post(f"/api/deck/{deck.id}/add_card", json={"front": "Q", "back": "A"})
    response = client.post(f'/deck/{deck.id}/generate')
    assert response.headers['X-Request-ID'] in response.text


def test_readiness_reports_dependency_failure_without_internal_details(
    client, app, repo
):
    repo.readiness_check = lambda: ['private-storage-detail']
    app.config['COMPILER'].is_available = lambda: False
    response = client.get('/health/ready')
    assert response.status_code == 503
    assert response.json['status'] == 'unavailable'
    assert response.json['components'] == {
        'storage': 'unavailable', 'tex': 'unavailable'
    }
    assert 'private-storage-detail' not in response.text


def test_readiness_contains_unexpected_dependency_exceptions(client, app, repo):
    def fail():
        raise RuntimeError('/private/path')

    repo.readiness_check = fail
    app.config['COMPILER'].is_available = fail
    response = client.get('/health/ready')
    assert response.status_code == 503
    assert '/private/path' not in response.text


def test_request_size_limit_is_enforced(client, app, deck_id):
    app.config['MAX_CONTENT_LENGTH'] = 64
    response = client.post(
        f"/api/deck/{deck_id}/add_card",
        json={"front": "X" * 1000, "back": ""},
    )
    assert response.status_code == 413


@pytest.mark.parametrize(
    ('error_kind', 'status', 'message'),
    [
        ('timeout', 504, 'превысила допустимое время'),
        ('unavailable', 503, 'недоступен'),
        ('sandbox-error', 503, 'worker недоступен'),
        ('compile-error', 422, 'Не удалось скомпилировать'),
        ('validation', 422, 'не прошёл проверку'),
        ('output-limit', 422, 'превысил допустимый размер'),
    ],
)
def test_compiler_errors_are_classified_without_log_leak(
    client, app, deck_id, error_kind, status, message
):
    class FailingCompiler:
        def compile(self, _source):
            return CompileResult(False, b'', '/private/server/path', error_kind)

    app.config['COMPILER'] = FailingCompiler()
    client.post(f"/api/deck/{deck_id}/add_card", json={"front": "Q", "back": "A"})
    response = client.post(f"/deck/{deck_id}/generate")
    assert response.status_code == status
    assert message in response.text
    assert '/private/server/path' not in response.text


def test_unsafe_math_is_rejected_before_compilation(client, app, deck_id):
    app.config['RENDERER'] = LatexRenderer(cards_per_row=2, rows_per_page=4)
    client.post(
        f"/api/deck/{deck_id}/add_card",
        json={"front": r"$\input{/etc/passwd}$", "back": "A"},
    )
    response = client.post(f"/deck/{deck_id}/generate")
    assert response.status_code == 422
    assert "не поддерживается" in response.text
    assert app.config['COMPILER'].sources == []


def test_unsafe_math_preview_is_rejected(client, app, deck_id):
    app.config['RENDERER'] = LatexRenderer(cards_per_row=2, rows_per_page=4)
    client.post(
        f"/api/deck/{deck_id}/add_card",
        json={"front": r"$\write18{unsafe}$", "back": "A"},
    )
    response = client.post(f"/deck/{deck_id}/preview_latex")
    assert response.status_code == 422


def test_advanced_routes_stay_locked_but_ui_explains_deployment_flag(
    client, deck_id
):
    response = client.get(f'/deck/{deck_id}/advanced')
    deck_page = client.get(f'/deck/{deck_id}')

    assert response.status_code == 404
    assert 'Обычная колода' in deck_page.text
    assert 'Advanced-колода' not in deck_page.text
    assert f'href="/deck/{deck_id}/advanced"' not in deck_page.text


def test_safe_deck_rejects_every_direct_advanced_post(client, app, tmp_path):
    repository = SqliteRepository(tmp_path / 'safe-route-boundary')
    app.config.update(
        REPO=repository,
        TRUSTED_LATEX_ENABLED=True,
        TRUSTED_COMPILER=RecordingTrustedCompiler(),
    )
    deck = repository.create_deck('Safe')

    tested = client.post(
        f'/deck/{deck.id}/advanced/test',
        data={'front_source': '{{ content }}', 'back_source': '{{ content }}'},
    )
    staged = client.post(
        f'/deck/{deck.id}/advanced/stage',
        data={'front_source': '{{ content }}', 'back_source': '{{ content }}'},
    )
    approved = client.post(
        f'/deck/{deck.id}/advanced/missing/approve',
        data={'confirm_trusted': 'yes'},
    )
    reset = client.post(f'/deck/{deck.id}/advanced/reset')

    assert {tested.status_code, staged.status_code, approved.status_code,
            reset.status_code} == {404}
    assert repository.list_trusted_templates(deck.id) == []


def test_trusted_editor_stages_modes_without_activation(
    client, app, tmp_path
):
    repository, deck = _enable_trusted(app, tmp_path)

    page = client.get(f'/deck/{deck.id}')
    assert page.status_code == 200
    assert 'Advanced-колода' in page.text
    assert 'Оформление обычной колоды' not in page.text
    assert f'href="/deck/{deck.id}/advanced"' in page.text
    home = client.get('/')
    assert 'Можно создавать отдельные Advanced-колоды' in home.text
    assert f'href="/deck/{deck.id}/advanced"' in home.text
    editor = client.get(f'/deck/{deck.id}/advanced')
    assert 'id="trusted-front-source"' in editor.text
    assert 'id="trusted-back-source"' in editor.text
    assert '{{ card_count }}' in editor.text

    staged = client.post(
        f'/deck/{deck.id}/advanced/stage',
        data={
            'front_source': (
                r'{{ upper_header }}\vfill {{ content }}\vfill'
                r'{{ lower_header }}'
            ),
            'back_source': r'BACK {{ content }}',
        },
    )

    assert staged.status_code == 302
    history = repository.list_trusted_templates(deck.id)
    assert len(history) == 1
    assert history[0].status is TemplateStatus.QUARANTINED
    assert history[0].front_source.startswith('{{ upper_header }}')
    assert history[0].back_source == 'BACK {{ content }}'
    assert repository.get_approved_trusted_template(deck.id) is None


def test_advanced_card_ui_and_api_persist_per_card_header_values(
    client, app, tmp_path
):
    repository, deck = _enable_trusted(app, tmp_path)
    safe = repository.create_deck('Safe comparison')

    page = client.get(f'/deck/{deck.id}')
    safe_page = client.get(f'/deck/{safe.id}')
    assert 'name="upper_header"' in page.text
    assert 'name="lower_header"' in page.text
    assert 'name="upper_header"' not in safe_page.text

    added = client.post(
        f'/api/deck/{deck.id}/add_card',
        json={
            'front': r'\centering Front',
            'back': r'\centering Back',
            'upper_header': r'\small Верх {{ card_number }}',
            'lower_header': r'Низ {{ card_count }}',
        },
    )
    assert added.status_code == 200
    card_id = added.json['card']['id']
    stored = next(
        card for card in repository.load_cards(deck.id).cards
        if card.id == card_id
    )
    assert stored.upper_header == r'\small Верх {{ card_number }}'
    assert stored.lower_header == r'Низ {{ card_count }}'

    edit_page = client.get(f'/deck/{deck.id}/edit_card/{card_id}')
    assert 'id="upper-header"' in edit_page.text
    assert r'\small Верх {{ card_number }}' in edit_page.text

    edited = client.put(
        f'/api/deck/{deck.id}/edit_card/{card_id}',
        json={
            'front': stored.front,
            'back': stored.back,
            'upper_header': 'Другой верх',
            'lower_header': '',
        },
    )
    assert edited.status_code == 200
    updated = next(
        card for card in repository.load_cards(deck.id).cards
        if card.id == card_id
    )
    assert updated.upper_header == 'Другой верх'
    assert updated.lower_header == ''


def test_trusted_test_compile_is_read_only_and_validation_is_atomic(
    client, app, tmp_path
):
    repository, deck = _enable_trusted(app, tmp_path)

    invalid = client.post(
        f'/deck/{deck.id}/advanced/test',
        data={
            'front_source': '{{ content }} {{ content }}',
            'back_source': '{{ content }}',
        },
    )
    valid = client.post(
        f'/deck/{deck.id}/advanced/test',
        data={
            'front_source': r'\centering {{ content }}',
            'back_source': r'\raggedleft {{ content }}',
        },
    )

    assert invalid.status_code == 400
    assert 'exactly once' in invalid.text
    assert valid.status_code == 200
    assert valid.mimetype == 'application/pdf'
    assert repository.list_trusted_templates(deck.id) == []


def test_trusted_approval_requires_consent_compile_and_routes_print_to_sandbox(
    client, app, tmp_path
):
    compiler = RecordingTrustedCompiler()
    repository, deck = _enable_trusted(app, tmp_path, compiler=compiler)
    template = repository.quarantine_trusted_template(
        deck.id,
        r'\centering {{ content }}',
    )

    denied = client.post(
        f'/deck/{deck.id}/advanced/{template.id}/approve', data={}
    )
    approved = client.post(
        f'/deck/{deck.id}/advanced/{template.id}/approve',
        data={'confirm_trusted': 'yes'},
    )
    generated = client.post(f'/deck/{deck.id}/generate')

    assert denied.status_code == 400
    assert approved.status_code == 302
    assert generated.status_code == 200
    assert generated.data == b'%PDF trusted'
    assert repository.get_approved_trusted_template(deck.id).id == template.id
    assert len(compiler.sources) == 2
    assert app.config['COMPILER'].sources == []

    reset = client.post(f'/deck/{deck.id}/advanced/reset')
    assert reset.status_code == 302
    assert repository.get_approved_trusted_template(deck.id) is None


def test_trusted_approval_fails_closed_when_sandbox_or_compile_fails(
    client, app, tmp_path
):
    unavailable = RecordingTrustedCompiler(ready=False)
    repository, deck = _enable_trusted(
        app, tmp_path, compiler=unavailable
    )
    template = repository.quarantine_trusted_template(
        deck.id, '{{ content }}'
    )

    response = client.post(
        f'/deck/{deck.id}/advanced/{template.id}/approve',
        data={'confirm_trusted': 'yes'},
    )

    assert response.status_code == 503
    assert repository.get_approved_trusted_template(deck.id) is None

    failing = RecordingTrustedCompiler(success=False)
    app.config['TRUSTED_COMPILER'] = failing
    response = client.post(
        f'/deck/{deck.id}/advanced/{template.id}/approve',
        data={'confirm_trusted': 'yes'},
    )
    assert response.status_code == 422
    assert 'private sandbox log' not in response.text
    assert repository.get_approved_trusted_template(deck.id) is None


def test_trusted_editor_covers_empty_sample_and_fail_closed_form_paths(
    client, app, tmp_path
):
    repository, deck = _enable_trusted(app, tmp_path)
    repository.save_cards(deck.id, CardDeck())

    sample = client.post(
        f'/deck/{deck.id}/advanced/test',
        data={
            'front_source': '{{ content }}',
            'back_source': '{{ content }}',
        },
    )
    invalid_stage = client.post(
        f'/deck/{deck.id}/advanced/stage',
        data={
            'front_source': 'missing placeholder',
            'back_source': '{{ content }}',
        },
    )
    missing_version = client.post(
        f'/deck/{deck.id}/advanced/missing/approve',
        data={'confirm_trusted': 'yes'},
    )
    inactive_reset = client.post(f'/deck/{deck.id}/advanced/reset')

    assert sample.status_code == 200
    assert invalid_stage.status_code == 400
    assert missing_version.status_code == 404
    assert inactive_reset.status_code == 302

    app.config['TRUSTED_COMPILER'] = None
    page = client.get(f'/deck/{deck.id}/advanced')
    unavailable_test = client.post(
        f'/deck/{deck.id}/advanced/test',
        data={
            'front_source': '{{ content }}',
            'back_source': '{{ content }}',
        },
    )
    assert page.status_code == 200
    assert 'Sandbox:</strong> недоступен' in page.text
    assert unavailable_test.status_code == 503


def test_trusted_compile_and_print_errors_name_card_side_without_log_leak(
    client, app, tmp_path
):
    compiler = RecordingTrustedCompiler()
    repository, deck = _enable_trusted(app, tmp_path, compiler=compiler)
    template = repository.quarantine_trusted_template(
        deck.id, '{{ content }}'
    )
    client.post(
        f'/deck/{deck.id}/advanced/{template.id}/approve',
        data={'confirm_trusted': 'yes'},
    )
    compiler.success = False

    def fail_with_context(_source):
        return CompileResult(
            False,
            b'',
            'DIDACTIC-CARDS-HBOX-BEGIN:1:back:body\n/private/log',
            'compile-error',
        )

    compiler.compile = fail_with_context
    tested = client.post(
        f'/deck/{deck.id}/advanced/test',
        data={
            'front_source': '{{ content }}',
            'back_source': '{{ content }}',
        },
    )
    generated = client.post(f'/deck/{deck.id}/generate')

    assert tested.status_code == 422
    assert 'Карточка 1, оборотная сторона' in tested.text
    assert generated.status_code == 422
    assert 'карточке 1, оборотная сторона' in generated.text
    assert '/private/log' not in tested.text + generated.text


def test_approved_trusted_print_never_falls_back_when_worker_disappears(
    client, app, tmp_path
):
    repository, deck = _enable_trusted(app, tmp_path)
    template = repository.quarantine_trusted_template(
        deck.id, '{{ content }}'
    )
    repository.approve_trusted_template(deck.id, template.id)
    app.config['TRUSTED_COMPILER'] = None

    response = client.post(f'/deck/{deck.id}/generate')

    assert response.status_code == 503
    assert 'недоступен' in response.text
    assert app.config['COMPILER'].sources == []
