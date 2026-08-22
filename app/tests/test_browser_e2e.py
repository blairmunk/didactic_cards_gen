from __future__ import annotations

import asyncio
import shutil
import threading

import pytest
from werkzeug.serving import make_server

from config import AppConfig
from didactic_cards.adapters.latex_renderer import LatexRenderer
from didactic_cards.domain.interfaces import CompileResult
from run import create_app


CHROMIUM = shutil.which('chromium') or shutil.which('chromium-browser')


class BrowserCompiler:
    def compile(self, _source):
        return CompileResult(True, b'%PDF-1.7 browser-e2e', '')


async def _exercise_browser(base_url: str, csv_path: str) -> None:
    from pyppeteer import launch

    browser = await launch(
        executablePath=CHROMIUM,
        headless=True,
        args=['--no-sandbox', '--disable-dev-shm-usage'],
    )
    try:
        page = await browser.newPage()
        await page.goto(base_url, {'waitUntil': 'networkidle2'})
        await page.type('#name', 'Browser E2E')
        await page.type('#description', 'isolated')
        await asyncio.gather(
            page.waitForNavigation({'waitUntil': 'networkidle2'}),
            page.click('form[action$="create_deck"] button[type="submit"]'),
        )
        await asyncio.gather(
            page.waitForNavigation({'waitUntil': 'networkidle2'}),
            page.click('.deck-name-link'),
        )

        assert await page.Jeval(
            'body', 'element => element.dataset.horizontalAlignment'
        ) == 'center'
        await page.select('#style-preset', 'custom')
        await page.select('#horizontal-alignment', 'right')
        await page.select('#vertical-alignment', 'bottom')
        await page.select('#header-visibility', 'both')
        await page.select('#header-alignment', 'center')
        await page.select('#header-repeat', 'section-start')
        await page.select('#section-break', 'new-row')
        await asyncio.gather(
            page.waitForNavigation({'waitUntil': 'networkidle2'}),
            page.click('#render-settings-form button[type="submit"]'),
        )
        assert await page.Jeval(
            'body', 'element => element.dataset.verticalAlignment'
        ) == 'bottom'
        assert await page.Jeval(
            'body', 'element => element.dataset.sectionBreak'
        ) == 'new-row'

        async def add_card(
            front: str, back: str, expected_count: int, section: str = ''
        ) -> None:
            await page.type('#section', section)
            await page.type('#front', front)
            await page.type('#back', back)
            table = await page.querySelector('#cards-tbody')
            if table is None:
                await asyncio.gather(
                    page.waitForNavigation({'waitUntil': 'networkidle2'}),
                    page.click('#single-card-form button[type="submit"]'),
                )
            else:
                await page.click('#single-card-form button[type="submit"]')
            await page.waitForFunction(
                f"document.getElementById('cards-count').textContent === '{expected_count}'"
            )

        await add_card('$x^2$', 'first', 1, 'Алгебра')
        await add_card('second', 'answer', 2, 'Алгебра')
        await page.waitForFunction(
            "document.getElementById('math-status').textContent === 'Формулы готовы'"
        )
        await page.click('#btn-view-preview')
        assert await page.Jeval(
            '.preview-card:first-child .preview-section',
            'element => element.textContent',
        ) == 'Алгебра'
        assert await page.Jeval(
            '.preview-card:nth-child(2) .preview-section',
            'element => getComputedStyle(element).display',
        ) == 'none'
        await page.click('#btn-view-table')

        handles = await page.querySelectorAll('.drag-handle')
        await handles[0].focus()
        await page.keyboard.press('ArrowDown')
        await page.waitForFunction(
            "document.getElementById('success-msg').textContent === 'Порядок сохранён'"
        )
        await page.reload({'waitUntil': 'networkidle2'})
        first_front = await page.Jeval(
            '#cards-tbody tr:first-child td.card-text', 'element => element.textContent'
        )
        assert first_front == 'second'

        await asyncio.gather(
            page.waitForNavigation({'waitUntil': 'networkidle2'}),
            page.click('#cards-tbody tr:first-child a[aria-label^="Редактировать"]'),
        )
        await page.evaluate("document.getElementById('front').value = 'second edited'")
        await asyncio.gather(
            page.waitForNavigation({'waitUntil': 'networkidle2'}),
            page.click('form button[type="submit"]'),
        )
        edited = await page.Jeval(
            '#cards-tbody tr:first-child td.card-text', 'element => element.textContent'
        )
        assert edited == 'second edited'

        upload = await page.querySelector('#csv_file')
        await upload.uploadFile(csv_path)
        await page.select('#delimiter', 'semicolon')
        await page.click('#csv-preview-button')
        await page.waitForFunction(
            "document.getElementById('csv-preview-result').textContent.includes('Принято: 1')"
        )
        await asyncio.gather(
            page.waitForNavigation({'waitUntil': 'networkidle2'}),
            page.click('#csv-import-form button[type="submit"]'),
        )
        assert await page.Jeval('#cards-count', 'element => element.textContent') == '3'

        pdf_result = await page.evaluate(
            '''async () => {
                const form = document.querySelector('form[action$="generate"]');
                const response = await fetch(form.action, {
                    method: 'POST', body: new FormData(form)
                });
                const bytes = new Uint8Array(await response.arrayBuffer());
                return {
                    status: response.status,
                    contentType: response.headers.get('content-type'),
                    prefix: String.fromCharCode(...bytes.slice(0, 4))
                };
            }'''
        )
        assert pdf_result == {
            'status': 200,
            'contentType': 'application/pdf',
            'prefix': '%PDF',
        }

        await page.evaluate(
            '''() => {
                window.__preflightScrolls = 0;
                Element.prototype.scrollIntoView = function() {
                    window.__preflightScrolls += 1;
                };
            }'''
        )
        await page.evaluate("document.getElementById('preflight-button').click()")
        await page.waitForFunction(
            "document.getElementById('preflight-result').textContent.includes('Критических проблем')"
        )
        preflight_state = await page.evaluate(
            '''() => ({
                scrolls: window.__preflightScrolls,
                focused: document.activeElement.id
            })'''
        )
        assert preflight_state['scrolls'] >= 2
        assert preflight_state['focused'] == 'preflight-result'

        resource_urls = await page.evaluate(
            "() => performance.getEntriesByType('resource').map(entry => entry.name)"
        )
        assert all(url.startswith(base_url) for url in resource_urls)
    finally:
        await browser.close()


@pytest.mark.browser
@pytest.mark.skipif(CHROMIUM is None, reason='system Chromium is not installed')
@pytest.mark.filterwarnings('ignore:remove loop argument:DeprecationWarning')
def test_complete_browser_workflow_is_offline_and_persistent(tmp_path):
    csv_path = tmp_path / 'cards.csv'
    csv_path.write_text('csv question;csv answer\n', encoding='utf-8')
    app = create_app(
        config=AppConfig(secret_key='browser-secret'),
        data_dir=tmp_path / 'data',
        renderer=LatexRenderer(),
        compiler=BrowserCompiler(),
    )
    app.config.update(TESTING=False, CSRF_ENABLED=True)
    server = make_server('127.0.0.1', 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        asyncio.run(
            _exercise_browser(
                f'http://127.0.0.1:{server.server_port}', str(csv_path)
            )
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
