"""Тесты web-слоя: routes и API endpoints."""
import io


class TestIndexPage:

    def test_index_empty(self, client):
        resp = client.get('/')
        assert resp.status_code == 200

    def test_index_with_cards(self, client):
        client.post('/add_card', data={'front': 'Q1', 'back': 'A1'})
        resp = client.get('/')
        assert resp.status_code == 200
        assert 'Q1' in resp.data.decode()


class TestAddCard:

    def test_add_card_redirect(self, client):
        resp = client.post('/add_card', data={'front': 'Q', 'back': 'A'})
        assert resp.status_code == 302

    def test_add_card_appears(self, client):
        client.post('/add_card', data={'front': 'Hello', 'back': 'World'})
        resp = client.get('/')
        assert 'Hello' in resp.data.decode()

    def test_add_empty_ignored(self, client):
        client.post('/add_card', data={'front': '', 'back': ''})
        resp = client.get('/')
        assert resp.status_code == 200


class TestAddCardsBulk:

    def test_bulk_add(self, client):
        bulk = "front1||back1\nfront2||back2\nfront3||back3"
        client.post('/add_cards_bulk', data={'bulk': bulk})
        resp = client.get('/')
        html = resp.data.decode()
        assert 'front1' in html
        assert 'front3' in html


class TestDeleteCard:

    def test_delete_card(self, client):
        client.post('/add_card', data={'front': 'XREMOVE', 'back': 'XGONE'})
        resp = client.get('/delete_card/0', follow_redirects=True)
        assert 'XREMOVE' not in resp.data.decode()

    def test_delete_invalid_index(self, client):
        resp = client.get('/delete_card/999')
        assert resp.status_code == 302


class TestEditCard:

    def test_edit_get(self, client):
        client.post('/add_card', data={'front': 'Old', 'back': 'Data'})
        resp = client.get('/edit_card/0')
        assert resp.status_code == 200
        assert 'Old' in resp.data.decode()

    def test_edit_post(self, client):
        client.post('/add_card', data={'front': 'Old', 'back': 'Data'})
        client.post('/edit_card/0', data={'front': 'New', 'back': 'Info'})
        resp = client.get('/')
        assert 'New' in resp.data.decode()

    def test_edit_invalid_index(self, client):
        resp = client.get('/edit_card/999')
        assert resp.status_code == 302


class TestReset:

    def test_reset_clears_all(self, client):
        client.post('/add_card', data={'front': 'Q', 'back': 'A'})
        client.post('/reset')
        resp = client.get('/')
        assert resp.status_code == 200


class TestGenerate:

    def test_generate_empty_deck(self, client):
        resp = client.post('/generate')
        assert resp.status_code == 200
        assert 'Добавьте' in resp.data.decode()

    def test_generate_success(self, client):
        client.post('/add_card', data={'front': 'Q', 'back': 'A'})
        resp = client.post('/generate')
        assert resp.status_code == 200
        assert resp.content_type == 'application/pdf'
        assert resp.data.startswith(b'%PDF')

    def test_generate_compile_failure(self, app_fail_compiler):
        c = app_fail_compiler.test_client()
        c.post('/add_card', data={'front': 'Q', 'back': 'A'})
        resp = c.post('/generate')
        assert resp.status_code == 200
        assert 'pdflatex error' in resp.data.decode()


class TestPreviewLatex:

    def test_preview_empty(self, client):
        resp = client.post('/preview_latex')
        assert resp.status_code == 200
        assert 'Добавьте' in resp.data.decode()

    def test_preview_success(self, client):
        client.post('/add_card', data={'front': 'Q', 'back': 'A'})
        resp = client.post('/preview_latex')
        assert resp.status_code == 200
        assert 'documentclass' in resp.data.decode()


class TestImportCsv:

    def test_import_csv(self, client):
        csv_data = "front1;back1\nfront2;back2"
        data = {
            'csv_file': (io.BytesIO(csv_data.encode('utf-8')), 'cards.csv')
        }
        client.post('/import_csv', data=data, content_type='multipart/form-data')
        resp = client.get('/')
        assert 'front1' in resp.data.decode()

    def test_import_no_file(self, client):
        resp = client.post('/import_csv')
        assert resp.status_code == 302


# ─── API endpoints ──────────────────────────────────────────────────

class TestApiAddCard:

    def test_api_add_card(self, client):
        resp = client.post('/api/add_card',
                           json={'front': 'Q', 'back': 'A'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['ok'] is True
        assert data['card']['front'] == 'Q'

    def test_api_add_empty(self, client):
        resp = client.post('/api/add_card',
                           json={'front': '', 'back': ''})
        assert resp.status_code == 400

    def test_api_add_no_json(self, client):
        resp = client.post('/api/add_card',
                           data='not json',
                           content_type='application/json')
        assert resp.status_code == 400


class TestApiDeleteCard:

    def test_api_delete(self, client):
        client.post('/api/add_card', json={'front': 'Q', 'back': 'A'})
        resp = client.delete('/api/delete_card/0')
        assert resp.status_code == 200
        assert resp.get_json()['ok'] is True

    def test_api_delete_invalid(self, client):
        resp = client.delete('/api/delete_card/999')
        assert resp.status_code == 404


class TestApiEditCard:

    def test_api_edit(self, client):
        client.post('/api/add_card', json={'front': 'Old', 'back': 'Data'})
        resp = client.put('/api/edit_card/0',
                          json={'front': 'New', 'back': 'Info'})
        assert resp.status_code == 200
        assert resp.get_json()['card']['front'] == 'New'

    def test_api_edit_invalid(self, client):
        resp = client.put('/api/edit_card/999',
                          json={'front': 'X', 'back': 'Y'})
        assert resp.status_code == 404


class TestApiReorder:

    def test_api_reorder(self, client):
        client.post('/api/add_card', json={'front': 'A', 'back': '1'})
        client.post('/api/add_card', json={'front': 'B', 'back': '2'})
        resp = client.post('/api/reorder', json={'order': [1, 0]})
        assert resp.status_code == 200
        assert resp.get_json()['ok'] is True

    def test_api_reorder_invalid(self, client):
        client.post('/api/add_card', json={'front': 'A', 'back': '1'})
        resp = client.post('/api/reorder', json={'order': [5, 0]})
        assert resp.status_code == 400