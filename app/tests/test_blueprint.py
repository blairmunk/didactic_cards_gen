"""Contract tests for HTML routes and the JSON API."""

from __future__ import annotations

import io

import pytest

from didactic_cards.adapters.latex_renderer import LatexRenderer
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


def test_card_html_workflow(client, repo, deck_id):
    client.post(f"/deck/{deck_id}/add_card", data={"front": "", "back": ""})
    assert len(repo.load_cards(deck_id)) == 0

    response = client.post(
        f"/deck/{deck_id}/add_card",
        data={"front": "  Question  ", "back": "  Answer  "},
    )
    assert response.status_code == 302
    assert repo.load_cards(deck_id).cards[0].front == "Question"

    client.post(f"/deck/{deck_id}/add_cards_bulk", data={"bulk": "Q2 | A2"})
    edit_page = client.get(f"/deck/{deck_id}/edit_card/1")
    assert edit_page.status_code == 200
    assert "Q2" in edit_page.text

    client.post(
        f"/deck/{deck_id}/edit_card/1",
        data={"front": "Q2+", "back": "A2+"},
    )
    assert repo.load_cards(deck_id).cards[1].front == "Q2+"

    response = client.post(f"/deck/{deck_id}/delete_card/0", follow_redirects=True)
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


def test_api_card_workflow(client, deck_id):
    added = client.post(
        f"/api/deck/{deck_id}/add_card", json={"front": "A", "back": "1"}
    )
    assert added.status_code == 200
    assert added.json["cards_count"] == 1

    client.post(f"/api/deck/{deck_id}/add_card", json={"front": "B", "back": "2"})
    reordered = client.post(f"/api/deck/{deck_id}/reorder", json={"order": [1, 0]})
    assert reordered.status_code == 200

    edited = client.put(
        f"/api/deck/{deck_id}/edit_card/0", json={"front": "B+", "back": "2+"}
    )
    assert edited.json["card"]["front"] == "B+"

    deleted = client.delete(f"/api/deck/{deck_id}/delete_card/1")
    assert deleted.status_code == 200
    assert deleted.json["cards_count"] == 1


@pytest.mark.parametrize(
    ("method", "path", "payload", "status"),
    [
        ("post", "/api/deck/{deck}/add_card", {}, 415),
        ("post", "/api/deck/{deck}/add_card", {"json": {"front": "", "back": ""}}, 400),
        ("delete", "/api/deck/{deck}/delete_card/99", {}, 404),
        ("put", "/api/deck/{deck}/edit_card/99", {"json": {"front": "X"}}, 404),
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
    assert client.get(f"/deck/{deck_id}/edit_card/99").status_code == 302


def test_api_empty_json_contracts(client, deck_id):
    headers = {"content_type": "application/json", "data": "null"}
    assert client.post(f"/api/deck/{deck_id}/add_card", **headers).status_code == 400
    assert client.post(f"/api/deck/{deck_id}/reorder", **headers).status_code == 400
    assert client.put(f"/api/deck/{deck_id}/edit_card/0", **headers).status_code == 400


@pytest.mark.xfail(
    strict=True,
    reason="BUG-WEB-001: unknown deck writes create orphan JSON instead of returning 404",
)
def test_api_rejects_unknown_deck(client, repo):
    response = client.post(
        "/api/deck/missing/add_card", json={"front": "orphan", "back": ""}
    )
    assert response.status_code == 404
    assert not repo._cards_path("missing").exists()


def test_delete_card_requires_non_get_method(client, deck_id):
    client.post(f"/api/deck/{deck_id}/add_card", json={"front": "Q", "back": "A"})
    response = client.get(f"/deck/{deck_id}/delete_card/0")
    assert response.status_code == 405


@pytest.mark.xfail(
    strict=True,
    reason="BUG-WEB-002: non-string JSON values cause an unhandled AttributeError",
)
def test_api_rejects_non_string_card_fields(client, deck_id):
    response = client.post(
        f"/api/deck/{deck_id}/add_card", json={"front": 123, "back": None}
    )
    assert response.status_code == 400


@pytest.mark.xfail(
    strict=True,
    reason="BUG-WEB-003: malformed reorder types can raise TypeError instead of a validation response",
)
def test_api_rejects_non_list_reorder_payload(client, deck_id):
    response = client.post(f"/api/deck/{deck_id}/reorder", json={"order": None})
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
