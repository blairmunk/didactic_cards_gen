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


class BrowserTrustedCompiler(BrowserCompiler):
    def readiness_check(self):
        return True


async def _exercise_browser(
    base_url: str,
    csv_path: str,
    advanced_csv_path: str,
) -> None:
    from pyppeteer import launch

    browser = await launch(
        executablePath=CHROMIUM,
        headless=True,
        autoClose=False,
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
        assert 'Можно создавать отдельные Advanced-колоды' in await page.Jeval(
            '.app-navigation', 'element => element.textContent'
        )
        await asyncio.gather(
            page.waitForNavigation({'waitUntil': 'networkidle2'}),
            page.click('.deck-name-link'),
        )

        assert await page.Jeval(
            'body', 'element => element.dataset.horizontalAlignment'
        ) == 'center'
        assert not await page.querySelector('[name="upper_header"]')
        assert not await page.querySelector('[name="lower_header"]')
        forged_safe_header = await page.evaluate(
            '''async () => {
                const response = await fetch(`/api${location.pathname}/add_card`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        front: 'forged', back: 'value',
                        upper_header: 'hidden raw'
                    })
                });
                return {status: response.status, payload: await response.json()};
            }'''
        )
        assert forged_safe_header['status'] == 400
        assert 'Advanced' in forged_safe_header['payload']['error']
        assert await page.Jeval(
            '#cards-count', 'element => element.textContent'
        ) == '0'
        await page.select('#style-preset', 'custom')
        await page.select('#horizontal-alignment', 'right')
        await page.select('#vertical-alignment', 'bottom')
        await page.select('#header-visibility', 'both')
        await page.select('#header-alignment', 'center')
        await page.select('#header-repeat', 'section-start')
        await page.evaluate(
            "() => { document.querySelectorAll('details.typography-settings')"
            ".forEach(details => { details.open = true; }); }"
        )
        await page.select('#typography-profile', 'custom')
        await page.select('#paragraph-spacing', 'medium')
        await page.select('#secondary-header-visibility', 'both')
        await page.select('#secondary-header-source', 'custom')
        await page.evaluate(
            "() => { const input = document.getElementById('secondary-header-text'); "
            "input.value = 'Карточка '; input.setSelectionRange(input.value.length, input.value.length); }"
        )
        await page.click(
            '#secondary-header-text + .placeholder-actions button:first-child'
        )
        await page.type('#secondary-header-text', '/')
        await page.click(
            '#secondary-header-text + .placeholder-actions button:last-child'
        )
        assert await page.Jeval(
            '#secondary-header-text',
            'element => ({value: element.value, readOnly: element.readOnly})',
        ) == {
            'value': 'Карточка {{ card_number }}/{{ card_count }}',
            'readOnly': False,
        }
        await page.select('#secondary-header-rule', 'thin')
        await page.select('#secondary-header-rule-spacing', 'compact')
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
            await page.evaluate(
                '''values => {
                    document.getElementById('section').value = values.section;
                    document.getElementById('front').value = values.front;
                    document.getElementById('back').value = values.back;
                }''',
                {'section': section, 'front': front, 'back': back},
            )
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

        await add_card(
            '$x^2$\nВторая строка\n\nНовый абзац\n',
            'first <img src=x onerror="window.__unsafePreview = true">',
            1,
            'Алгебра',
        )
        await add_card('second', 'answer', 2, 'Алгебра')
        await page.waitForFunction(
            "document.getElementById('math-status').textContent === 'Формулы готовы'"
        )
        await page.click('#btn-view-preview')
        safe_layout = await page.Jeval(
            '.preview-card:first-child .preview-front .safe-text-flow',
            '''element => {
                const paragraphs = [...element.querySelectorAll('.safe-text-paragraph')];
                const lines = [...element.querySelectorAll('.safe-text-line')];
                const first = paragraphs[0].getBoundingClientRect();
                const second = paragraphs[1].getBoundingClientRect();
                return {
                    paragraphs: paragraphs.length,
                    lines: lines.length,
                    paragraphGap: second.top - first.bottom,
                };
            }''',
        )
        assert safe_layout['paragraphs'] == 2
        assert safe_layout['lines'] == 3
        assert safe_layout['paragraphGap'] > 4
        assert not await page.querySelector('.preview-card:first-child img')
        assert not await page.evaluate('Boolean(window.__unsafePreview)')
        assert await page.Jeval(
            '.preview-card:first-child .preview-section',
            'element => element.textContent',
        ) == 'Алгебра'
        assert await page.Jeval(
            '.preview-card:nth-child(2) .preview-section',
            'element => getComputedStyle(element).display',
        ) == 'none'
        assert await page.Jeval(
            '.preview-card:nth-child(2) .preview-header-secondary',
            'element => element.textContent',
        ) == 'Карточка 2/2'
        assert await page.Jeval(
            '.preview-card:nth-child(2) .preview-header-secondary',
            'element => getComputedStyle(element).borderTopStyle',
        ) == 'solid'
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
        await page.evaluate(
            '''value => {
                const input = document.getElementById('front');
                input.value = value;
                input.dispatchEvent(new Event('input', {bubbles: true}));
            }''',
            'second edited\nline 2\n\nparagraph',
        )
        await page.waitForFunction(
            "document.querySelectorAll('#previewFront .safe-text-paragraph').length === 2"
        )
        await asyncio.gather(
            page.waitForNavigation({'waitUntil': 'networkidle2'}),
            page.click('form button[type="submit"]'),
        )
        edited = await page.Jeval(
            '#cards-tbody tr:first-child td.card-text', 'element => element.textContent'
        )
        assert edited == 'second edited\nline 2\n\nparagraph'

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
        await asyncio.gather(
            page.waitForNavigation({'waitUntil': 'networkidle2'}),
            page.click('#cards-tbody tr:nth-child(3) a[aria-label^="Редактировать"]'),
        )
        assert not await page.querySelector('[name="upper_header"]')
        assert not await page.querySelector('[name="lower_header"]')
        await asyncio.gather(
            page.waitForNavigation({'waitUntil': 'networkidle2'}),
            page.click('#edit-card-form button[type="submit"]'),
        )
        assert not await page.querySelector('#advanced-mode')
        assert await page.querySelector('#render-settings-form')

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

        await page.goto(
            f'{base_url}/printer_profiles', {'waitUntil': 'networkidle2'}
        )
        await page.select('#calculation-profile', 'standard-short-edge')
        await page.type('#measured-x', '1.2')
        await page.type('#measured-y', '-0.4')
        await asyncio.gather(
            page.waitForNavigation({'waitUntil': 'networkidle2'}),
            page.click('.calibration-calculator button[type="submit"]'),
        )
        calculation = await page.Jeval(
            '.calibration-result', 'element => element.textContent'
        )
        assert '«Оборот X» = -1.2 мм' in calculation
        assert '«Оборот Y» = -0.4 мм' in calculation

        # Advanced is a separate deck type: raw TeX works without a wrapper,
        # while the safe typography form is absent altogether.
        await page.goto(base_url, {'waitUntil': 'networkidle2'})
        await page.type('#name', 'Browser Advanced')
        await page.click('input[name="authoring_mode"][value="advanced"]')
        await asyncio.gather(
            page.waitForNavigation({'waitUntil': 'networkidle2'}),
            page.click('form[action$="create_deck"] button[type="submit"]'),
        )
        await page.evaluate(
            '''() => {
                const link = [...document.querySelectorAll('.deck-name-link')]
                    .find(item => item.textContent === 'Browser Advanced');
                link.click();
            }'''
        )
        await page.waitForNavigation({'waitUntil': 'networkidle2'})
        assert await page.Jeval('body', 'element => element.dataset.authoringMode') == 'advanced'
        assert not await page.querySelector('#render-settings-form')
        assert await page.querySelector('#advanced-mode')

        await page.type('#front', r'\centering Сырой \TeX')
        await page.type('#back', r'\vfill Ответ \vfill')
        await page.type('#upper-header', r'\small Раздел {{ section }}')
        await page.type(
            '#lower-header', 'Карточка {{ card_number }}/{{ card_count }}'
        )
        await asyncio.gather(
            page.waitForNavigation({'waitUntil': 'networkidle2'}),
            page.click('#single-card-form button[type="submit"]'),
        )

        advanced_upload = await page.querySelector('#csv_file')
        await advanced_upload.uploadFile(advanced_csv_path)
        await page.select('#delimiter', 'semicolon')
        await page.click('#csv-preview-button')
        await page.waitForFunction(
            "document.getElementById('csv-preview-result').textContent.includes('Принято: 1')"
        )
        advanced_preview = await page.Jeval(
            '#csv-preview-result', 'element => element.textContent'
        )
        assert 'upper_header: CSV top' in advanced_preview
        await page.click('#trust-raw-csv')
        await asyncio.gather(
            page.waitForNavigation({'waitUntil': 'networkidle2'}),
            page.click('#csv-import-button'),
        )
        assert await page.Jeval(
            '#cards-count', 'element => element.textContent'
        ) == '2'

        direct_pdf = await page.evaluate(
            '''async () => {
                const form = document.querySelector('form[action$="generate"]');
                const response = await fetch(form.action, {
                    method: 'POST', body: new FormData(form)
                });
                return response.status;
            }'''
        )
        assert direct_pdf == 200

        await asyncio.gather(
            page.waitForNavigation({'waitUntil': 'networkidle2'}),
            page.click('.advanced-entry a.btn-warning'),
        )
        await page.evaluate(
            "value => document.getElementById('trusted-front-source').value = value",
            (
                r'{{ upper_header }}\begin{center}{{ content }}\end{center}'
                r'{{ lower_header }}'
            ),
        )
        await page.evaluate(
            "value => document.getElementById('trusted-back-source').value = value",
            r'\raggedleft {{ upper_header }} {{ content }} {{ lower_header }}',
        )
        await asyncio.gather(
            page.waitForNavigation({'waitUntil': 'networkidle2'}),
            page.click('button[formaction$="advanced/stage"]'),
        )
        await page.click('input[name="confirm_trusted"]')
        await asyncio.gather(
            page.waitForNavigation({'waitUntil': 'networkidle2'}),
            page.click('.trusted-version form button[type="submit"]'),
        )
        assert 'Активная версия' in await page.Jeval(
            '.trusted-version', 'element => element.textContent'
        )
        assert 'Оборотная сторона' in await page.Jeval(
            '.trusted-version', 'element => element.textContent'
        )

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
    csv_path.write_bytes(
        b'front;back\r\n"csv line 1\r\ncsv line 2";csv answer\r\n'
    )
    advanced_csv_path = tmp_path / 'advanced-cards.csv'
    advanced_csv_path.write_text(
        'section;front;back;upper_header;lower_header\n'
        'CSV raw;"\\vfill CSV front \\vfill";CSV back;CSV top;CSV bottom\n',
        encoding='utf-8',
    )
    app = create_app(
        config=AppConfig(
            secret_key='browser-secret', trusted_latex_enabled=True
        ),
        data_dir=tmp_path / 'data',
        renderer=LatexRenderer(),
        compiler=BrowserCompiler(),
    )
    app.config.update(
        TESTING=False,
        CSRF_ENABLED=True,
        TRUSTED_COMPILER=BrowserTrustedCompiler(),
    )
    server = make_server('127.0.0.1', 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        asyncio.run(
            _exercise_browser(
                f'http://127.0.0.1:{server.server_port}',
                str(csv_path),
                str(advanced_csv_path),
            )
        )
        csv_card = next(
            card for card in app.config['REPO'].list_decks()
            if card.name == 'Browser E2E'
        )
        imported = app.config['REPO'].load_cards(csv_card.id).cards[-1]
        assert imported.front == 'csv line 1\r\ncsv line 2'
    finally:
        server.shutdown()
        thread.join(timeout=5)
