"""Contract tests for HTML routes and the JSON API."""

from __future__ import annotations

import io

import pytest

from didactic_cards.adapters.json_repository import RepositoryCorruptionError
from didactic_cards.adapters.latex_renderer import LatexRenderer
from didactic_cards.domain.entities import Card, CardDeck
from didactic_cards.domain.interfaces import CompileResult


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
        data={"front": "  Question  ", "back": "  Answer  "},
    )
    assert response.status_code == 302
    assert repo.load_cards(deck_id).cards[0].front == "Question"

    client.post(f"/deck/{deck_id}/add_cards_bulk", data={"bulk": "Q2 || A2"})
    second_id = repo.load_cards(deck_id).cards[1].id
    edit_page = client.get(f"/deck/{deck_id}/edit_card/{second_id}")
    assert edit_page.status_code == 200
    assert "Q2" in edit_page.text

    client.post(
        f"/deck/{deck_id}/edit_card/{second_id}",
        data={"front": "Q2+", "back": "A2+"},
    )
    assert repo.load_cards(deck_id).cards[1].front == "Q2+"

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
    response = client.post(
        f"/deck/{deck_id}/import_csv",
        data={"csv_file": (io.BytesIO("front,back".encode()), "cards.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    assert repo.load_cards(deck_id).cards[0].back == "back"

    response = client.post(
        f"/deck/{deck_id}/import_csv",
        data={"csv_file": (io.BytesIO(b"\xff\xfe"), "broken.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert "Ошибка кодировки" in response.text


def test_csv_preview_is_read_only_and_reports_validation(client, repo, deck_id):
    response = client.post(
        f"/api/deck/{deck_id}/preview_csv",
        data={
            "csv_file": (io.BytesIO(b"front;back\nQ;A\nbad;row;extra"), "cards.csv"),
            "has_header": "on",
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
            "csv_file": (io.BytesIO(b"Q;A\nbad;row;extra"), "cards.csv"),
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


def test_api_card_workflow(client, deck_id):
    added = client.post(
        f"/api/deck/{deck_id}/add_card", json={"front": "A", "back": "1"}
    )
    assert added.status_code == 200
    assert added.json["cards_count"] == 1
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
        json={"front": "B+", "back": "2+"},
    )
    assert edited.json["card"]["front"] == "B+"

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
    assert not repo._cards_path("missing").exists()


def test_html_unknown_deck_mutation_redirects(client):
    response = client.post(
        "/deck/missing/add_card", data={"front": "orphan", "back": ""}
    )
    assert response.status_code == 302
    assert response.location.endswith("/")


def test_repository_corruption_has_safe_html_and_api_errors(client, repo, monkeypatch):
    error = RepositoryCorruptionError(repo.decks_file, "test failure")

    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(repo, "list_decks", fail)
    html_response = client.get("/")
    assert html_response.status_code == 500
    assert "Хранилище данных повреждено" in html_response.text
    assert str(repo.decks_file) not in html_response.text

    monkeypatch.setattr(repo, "mutate_cards", fail)
    api_response = client.post(
        "/api/deck/anything/add_card", json={"front": "Q", "back": "A"}
    )
    assert api_response.status_code == 500
    assert api_response.json == {"error": "Хранилище данных повреждено"}


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

    html_conflict = client.post(
        f"/deck/{deck_id}/add_cards_bulk",
        data={"bulk": "second || answer", "version": initial_version},
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
        ('compile-error', 422, 'Не удалось скомпилировать'),
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
