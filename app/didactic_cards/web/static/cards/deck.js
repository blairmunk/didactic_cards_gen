document.addEventListener('DOMContentLoaded', function() {

    const DECK_ID = document.body.dataset.deckId;
    const IS_ADVANCED = document.body.dataset.authoringMode === 'advanced';
    let deckVersion = parseInt(document.body.dataset.deckVersion, 10);
    const CARDS_PER_PAGE = parseInt(document.body.dataset.cardsPerPage) || 8;
    const API = {
        addCard:    '/api/deck/' + DECK_ID + '/add_card',
        deleteCard: '/api/deck/' + DECK_ID + '/delete_card/',
        reorder:    '/api/deck/' + DECK_ID + '/reorder',
        previewBulk:'/api/deck/' + DECK_ID + '/preview_bulk',
        previewCsv: '/api/deck/' + DECK_ID + '/preview_csv',
        preflight:  '/api/deck/' + DECK_ID + '/preflight',
        editPage:   '/deck/' + DECK_ID + '/edit_card/',
    };

    let currentView = 'table';
    let mathjaxRendered = false;

    // ── Переключение режимов ──

    window.switchView = function(view) {
        currentView = view;
        const tableDiv = document.getElementById('view-table');
        const previewDiv = document.getElementById('view-preview');
        if (tableDiv) tableDiv.style.display = view === 'table' ? '' : 'none';
        if (previewDiv) previewDiv.style.display = view === 'preview' ? '' : 'none';
        document.getElementById('btn-view-table').classList.toggle('active', view === 'table');
        document.getElementById('btn-view-preview').classList.toggle('active', view === 'preview');
        document.getElementById('btn-view-table').setAttribute('aria-pressed', view === 'table');
        document.getElementById('btn-view-preview').setAttribute('aria-pressed', view === 'preview');

        if (!IS_ADVANCED && view === 'preview' && !mathjaxRendered) {
            mathjaxRendered = true;
            if (window.MathJax && MathJax.typesetPromise) {
                MathJax.typesetPromise([document.getElementById('preview-grid')]);
            }
        }
    };

    // ── Утилиты ──

    function showSuccess(text) {
        const el = document.getElementById('success-msg');
        el.textContent = text;
        el.style.display = 'block';
        setTimeout(() => { el.style.display = 'none'; }, 2500);
    }

    function updateCounters(count, printPages, emptySlots) {
        document.getElementById('cards-count').textContent = count;
        const pages = Number.isInteger(printPages)
            ? printPages : (count > 0 ? Math.ceil(count / CARDS_PER_PAGE) : 0);
        document.getElementById('pages-count').textContent = pages;
        document.getElementById('empty-count').textContent =
            Number.isInteger(emptySlots)
                ? emptySlots : (count > 0 ? (pages * CARDS_PER_PAGE - count) : 0);
        document.getElementById('generate-buttons').style.display =
            count > 0 ? '' : 'none';
    }

    function updateDeckVersion(version) {
        if (!Number.isInteger(version)) return;
        deckVersion = version;
        document.body.dataset.deckVersion = version;
        document.querySelectorAll('input[name="version"]').forEach(function(input) {
            input.value = version;
        });
        ['bulk', 'csv'].forEach(function(kind) {
            const token = document.getElementById(kind + '-preview-token');
            const submit = document.getElementById(kind + '-import-button');
            if (token) token.value = '';
            if (submit) submit.disabled = true;
        });
    }

    const mathStatus = document.getElementById('math-status');
    const mathScript = document.getElementById('MathJax-script');
    function reportMathReady() {
        if (window.MathJax && MathJax.startup && MathJax.startup.promise) {
            MathJax.startup.promise.then(function() {
                mathStatus.textContent = 'Формулы готовы';
            }).catch(function() {
                mathStatus.textContent = 'Не удалось отобразить формулы';
            });
        }
    }
    if (mathScript) {
        mathScript.addEventListener('load', reportMathReady);
        mathScript.addEventListener('error', function() {
            mathStatus.textContent = 'Не удалось загрузить локальный MathJax';
        });
        reportMathReady();
    }

    function renumberRows() {
        const rows = document.querySelectorAll('#cards-tbody tr');
        rows.forEach((row, i) => {
            row.querySelector('.row-number').textContent = i + 1;
        });
    }

    function renumberPreviews() {
        const cards = document.querySelectorAll('#preview-grid .preview-card');
        cards.forEach((card, i) => {
            card.querySelector('.card-number').textContent = '#' + (i + 1);
            card.querySelectorAll('.preview-header').forEach(function(header) {
                const primary = header.classList.contains('preview-header-primary');
                const dataPrefix = primary ? 'header' : 'secondaryHeader';
                header.textContent = safeHeaderValue(
                    document.body.dataset[dataPrefix + 'Source'],
                    document.body.dataset[dataPrefix + 'Text'],
                    card.dataset.section || '',
                    i + 1,
                    cards.length
                );
            });
        });
        refreshSectionHeaders();
    }

    function refreshSectionHeaders() {
        let previousSection = null;
        document.querySelectorAll('#preview-grid .preview-card').forEach(function(card) {
            const section = DidacticCardsSafeText.singleLine(
                card.dataset.section || ''
            );
            card.querySelectorAll('.preview-header').forEach(function(header) {
                const sectionStartOnly = header.dataset.repeat === 'section-start';
                const visible = !sectionStartOnly || previousSection === null || section !== previousSection;
                header.classList.toggle('section-header-suppressed', !visible);
            });
            previousSection = section;
        });
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function safeHeaderValue(source, customText, section, number, count) {
        if (source === 'section') {
            return DidacticCardsSafeText.singleLine(section || '');
        }
        if (source === 'card-number') return '№ ' + number;
        return DidacticCardsSafeText.singleLine(customText || '')
            .replaceAll('{{ card_number }}', String(number))
            .replaceAll('{{ card_count }}', String(count));
    }

    const stylePreset = document.getElementById('style-preset');
    const horizontalAlignment = document.getElementById('horizontal-alignment');
    const verticalAlignment = document.getElementById('vertical-alignment');
    function applyPresetControls() {
        if (!stylePreset) return;
        const custom = stylePreset.value === 'custom';
        horizontalAlignment.disabled = !custom;
        verticalAlignment.disabled = !custom;
        if (stylePreset.value === 'centered') {
            horizontalAlignment.value = 'center';
            verticalAlignment.value = 'center';
        }
    }
    if (stylePreset) {
        stylePreset.addEventListener('change', applyPresetControls);
        applyPresetControls();
    }

    const typographyProfile = document.getElementById('typography-profile');
    const typographyCustomControls = document.getElementById('typography-custom-controls');
    function applyTypographyProfileControls() {
        if (!typographyProfile || !typographyCustomControls) return;
        const custom = typographyProfile.value === 'custom';
        typographyCustomControls.classList.toggle('settings-inactive', !custom);
        typographyCustomControls.setAttribute('aria-disabled', String(!custom));
    }
    if (typographyProfile) {
        typographyProfile.addEventListener('change', applyTypographyProfileControls);
        applyTypographyProfileControls();
    }

    [
        ['header-source', 'header-text'],
        ['secondary-header-source', 'secondary-header-text']
    ].forEach(function(ids) {
        const source = document.getElementById(ids[0]);
        const text = document.getElementById(ids[1]);
        if (!source || !text) return;
        function refreshCustomText() {
            const enabled = source.value === 'custom';
            text.readOnly = !enabled;
            text.classList.toggle('settings-input-inactive', !enabled);
        }
        source.addEventListener('change', refreshCustomText);
        refreshCustomText();
    });

    document.querySelectorAll('.header-placeholder-button').forEach(function(button) {
        button.addEventListener('click', function() {
            const input = document.getElementById(button.dataset.target);
            if (!input) return;
            if (input.readOnly) {
                const sourceId = button.dataset.target === 'header-text'
                    ? 'header-source' : 'secondary-header-source';
                const source = document.getElementById(sourceId);
                if (source) {
                    source.value = 'custom';
                    source.dispatchEvent(new Event('change', { bubbles: true }));
                }
                input.readOnly = false;
                input.classList.remove('settings-input-inactive');
            }
            const start = input.selectionStart === null ? input.value.length : input.selectionStart;
            const end = input.selectionEnd === null ? start : input.selectionEnd;
            input.setRangeText(button.dataset.placeholder, start, end, 'end');
            input.focus();
            input.dispatchEvent(new Event('input', { bubbles: true }));
        });
    });

    const printProfile = document.getElementById('print-profile');
    document.querySelectorAll('.print-form').forEach(function(form) {
        form.addEventListener('submit', function() {
            const input = form.querySelector('input[name="profile_id"]');
            if (input) input.value = printProfile ? printProfile.value : '';
        });
    });

    // ── Удаление/добавление в превью ──

    function removeCardFromPreview(cardId) {
        const card = document.querySelector('#preview-grid .preview-card[data-card-id="' + cardId + '"]');
        if (card) card.remove();
        renumberPreviews();
    }

    function addCardToPreview(index, cardData) {
        let grid = document.getElementById('preview-grid');
        if (!grid) {
            const container = document.getElementById('view-preview');
            if (!container) return;
            grid = document.createElement('div');
            grid.className = 'preview-grid';
            grid.id = 'preview-grid';
            container.appendChild(grid);
        }

        const div = document.createElement('div');
        div.className = 'preview-card';
        div.dataset.cardId = cardData.id;
        div.dataset.section = cardData.section || '';
        function headerHtml(prefix, side, position) {
            if (IS_ADVANCED) return '';
            const dataPrefix = prefix === 'primary' ? 'header' : 'secondaryHeader';
            const visibility = document.body.dataset[dataPrefix + 'Visibility'];
            const configuredPosition = document.body.dataset[dataPrefix + 'Position'];
            if (configuredPosition !== position || (visibility !== 'both' && visibility !== side)) {
                return '';
            }
            const source = document.body.dataset[dataPrefix + 'Source'];
            const customText = document.body.dataset[dataPrefix + 'Text'];
            const repeat = document.body.dataset[dataPrefix + 'Repeat'];
            return '<div class="preview-section preview-header preview-header-' + prefix +
                '" data-repeat="' + repeat + '">' +
                escapeHtml(safeHeaderValue(
                    source,
                    customText,
                    cardData.section || '',
                    index + 1,
                    document.querySelectorAll('#preview-grid .preview-card').length + 1
                )) + '</div>';
        }
        function sideHtml(side, content, label) {
            return '<div class="preview-side preview-' + side + '">' +
                headerHtml('primary', side, 'top') +
                headerHtml('secondary', side, 'top') +
                '<div class="preview-content' +
                (IS_ADVANCED ? ' advanced-source-preview' : '') +
                '"></div>' +
                headerHtml('primary', side, 'bottom') +
                headerHtml('secondary', side, 'bottom') +
                '<span class="preview-side-label">' + label + '</span>' +
                '</div>';
        }
        div.innerHTML =
            '<span class="card-number">#' + (index + 1) + '</span>' +
            '<span class="card-actions">' +
            '    <a href="' + API.editPage + cardData.id + '" aria-label="Редактировать карточку" title="Редактировать">✏️</a>' +
            '    <a href="#" class="delete-btn" data-card-id="' + cardData.id + '" aria-label="Удалить карточку" title="Удалить">🗑️</a>' +
            '</span>' +
            '<div class="preview-card-inner">' +
            sideHtml('front', cardData.front, 'задание') +
            sideHtml('back', cardData.back, 'решение') +
            '</div>';
        const previewContents = div.querySelectorAll('.preview-content');
        if (IS_ADVANCED) {
            previewContents[0].textContent = cardData.front;
            previewContents[1].textContent = cardData.back;
        } else {
            DidacticCardsSafeText.render(previewContents[0], cardData.front);
            DidacticCardsSafeText.render(previewContents[1], cardData.back);
        }
        grid.appendChild(div);
        attachDeleteEvent(div.querySelector('.delete-btn'));
        renumberPreviews();

        if (!IS_ADVANCED && mathjaxRendered && window.MathJax && MathJax.typesetPromise) {
            MathJax.typesetPromise([div]);
        }
    }

    // ── AJAX: добавление карточки ──

    const singleForm = document.getElementById('single-card-form');
    if (singleForm) {
        singleForm.addEventListener('submit', async function(e) {
            e.preventDefault();

            const frontEl = document.getElementById('front');
            const backEl = document.getElementById('back');
            const sectionEl = document.getElementById('section');
            const upperHeaderEl = document.getElementById('upper-header');
            const lowerHeaderEl = document.getElementById('lower-header');
            const front = frontEl.value;
            const back = backEl.value;
            const section = sectionEl.value.trim();
            const upperHeader = upperHeaderEl ? upperHeaderEl.value : '';
            const lowerHeader = lowerHeaderEl ? lowerHeaderEl.value : '';

            if (!front.trim() && !back.trim() &&
                    !upperHeader.trim() && !lowerHeader.trim()) {
                alert('Заполните хотя бы одно поле');
                return;
            }

            const payload = {
                front: front,
                back: back,
                section: section,
                version: deckVersion
            };
            if (IS_ADVANCED) {
                payload.upper_header = upperHeader;
                payload.lower_header = lowerHeader;
            }

            try {
                const resp = await fetch(API.addCard, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await resp.json();

                if (!resp.ok) {
                    alert(data.error || 'Ошибка');
                    return;
                }

                addCardRow(data.index, data.card);
                addCardToPreview(data.index, data.card);
                updateCounters(data.cards_count, data.print_pages, data.empty_slots);
                updateDeckVersion(data.deck_version);
                showSuccess('Карточка добавлена!');

                frontEl.value = '';
                backEl.value = '';
                sectionEl.value = '';
                if (upperHeaderEl) upperHeaderEl.value = '';
                if (lowerHeaderEl) lowerHeaderEl.value = '';
                frontEl.focus();

                if (!document.getElementById('view-table')) {
                    location.reload();
                }

            } catch (err) {
                console.error(err);
                alert('Ошибка сети');
            }
        });
    }

    function addCardRow(index, card) {
        var tbody = document.getElementById('cards-tbody');

        if (!tbody) {
            location.reload();
            return;
        }

        const tr = document.createElement('tr');
        tr.draggable = true;
        tr.dataset.cardId = card.id;
        tr.innerHTML =
            '<td class="drag-handle" tabindex="0" role="button" aria-label="Переместить карточку; стрелки вверх и вниз меняют порядок" title="Перетащите или используйте стрелки для сортировки">⠿</td>' +
            '<td class="row-number">' + (index + 1) + '</td>' +
            '<td class="card-section">' + escapeHtml(card.section || '') + '</td>' +
            '<td class="card-text">' + escapeHtml(card.front) + '</td>' +
            '<td class="card-text">' + escapeHtml(card.back) + '</td>' +
            '<td class="actions">' +
            '    <a href="' + API.editPage + card.id + '" aria-label="Редактировать карточку" title="Редактировать">✏️</a>' +
            '    <a href="#" class="delete-btn" data-card-id="' + card.id + '" aria-label="Удалить карточку" title="Удалить">🗑️</a>' +
            '</td>';
        tbody.appendChild(tr);
        attachRowDragEvents(tr);
        attachDeleteEvent(tr.querySelector('.delete-btn'));
    }

    // ── AJAX: удаление карточки ──

    function attachDeleteEvent(btn) {
        if (!btn) return;
        btn.addEventListener('click', async function(e) {
            e.preventDefault();
            const cardId = btn.dataset.cardId;
            const row = document.querySelector('#cards-tbody tr[data-card-id="' + cardId + '"]');
            const number = row ? row.querySelector('.row-number').textContent : '';
            if (!confirm('Удалить карточку №' + number + '?')) return;

            try {
                const resp = await fetch(API.deleteCard + cardId, {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ version: deckVersion })
                });
                const data = await resp.json();

                if (!resp.ok) {
                    alert(data.error || 'Ошибка');
                    return;
                }

                if (row) row.remove();
                renumberRows();

                removeCardFromPreview(cardId);
                updateCounters(data.cards_count, data.print_pages, data.empty_slots);
                updateDeckVersion(data.deck_version);
                showSuccess('Карточка удалена');

            } catch (err) {
                console.error(err);
                alert('Ошибка сети');
            }
        });
    }

    document.querySelectorAll('.delete-btn').forEach(attachDeleteEvent);

    // ── Read-only bulk/CSV previews ──

    function renderImportPreview(result, data) {
        result.replaceChildren();
        const summary = document.createElement('p');
        const metadata = [];
        if (data.encoding) metadata.push('кодировка: ' + data.encoding);
        if (data.delimiter) metadata.push('разделитель: ' + JSON.stringify(data.delimiter));
        summary.textContent = 'Принято: ' + data.accepted_count +
            '; отклонено строк: ' + data.rejected_count +
            '; ошибок: ' + data.error_count +
            '; предупреждений: ' + data.warning_count +
            '; пропущено: ' + data.skipped_count +
            '; ' + metadata.join('; ');
        result.appendChild(summary);

        if (data.columns && data.columns.length) {
            const mapping = document.createElement('p');
            mapping.textContent = 'Колонки: ' + data.columns.join(' → ');
            result.appendChild(mapping);
        }

        const list = document.createElement('ol');
        data.cards.forEach(function(card) {
            const item = document.createElement('li');
            const title = document.createElement('strong');
            title.textContent = 'Строка ' + card.row +
                (card.section ? ' [' + card.section + ']' : '');
            item.appendChild(title);
            const values = document.createElement('pre');
            values.className = 'import-row-values';
            values.textContent = 'front: ' + card.front + '\nback: ' + card.back +
                (IS_ADVANCED
                    ? '\nupper_header: ' + card.upper_header +
                      '\nlower_header: ' + card.lower_header
                    : '');
            item.appendChild(values);
            list.appendChild(item);
        });
        result.appendChild(list);

        if (data.issues && data.issues.length) {
            const issueHeading = document.createElement('p');
            issueHeading.className = data.rejected_count ? 'error-message' : 'warning-message';
            issueHeading.textContent = data.rejected_count
                ? 'Исправьте ошибки до импорта:'
                : 'Предупреждения:';
            result.appendChild(issueHeading);
            const issues = document.createElement('ul');
            data.issues.forEach(function(issue) {
                const item = document.createElement('li');
                item.className = issue.severity === 'error'
                    ? 'import-issue-error' : 'import-issue-warning';
                item.textContent = (issue.row ? 'Строка ' + issue.row + ': ' : '') +
                    issue.reason + (issue.column ? ' [' + issue.column + ']' : '');
                issues.appendChild(item);
            });
            result.appendChild(issues);
        }
        if (data.truncated) {
            const truncated = document.createElement('p');
            truncated.className = 'warning-message';
            truncated.textContent = 'Предпросмотр усечён; итоговые счётчики относятся ко всему файлу.';
            result.appendChild(truncated);
        }
        result.focus({preventScroll: true});
        result.scrollIntoView({behavior: 'smooth', block: 'nearest'});
    }

    function installPreviewInvalidation(form, token, submit, ignoredName) {
        function invalidate(event) {
            if (ignoredName && event.target.name === ignoredName) return;
            token.value = '';
            submit.disabled = true;
        }
        form.addEventListener('input', invalidate);
        form.addEventListener('change', invalidate);
    }

    const bulkForm = document.getElementById('bulk-import-form');
    const bulkPreviewButton = document.getElementById('bulk-preview-button');
    if (bulkForm && bulkPreviewButton) {
        const bulkResult = document.getElementById('bulk-preview-result');
        const bulkToken = document.getElementById('bulk-preview-token');
        const bulkSubmit = document.getElementById('bulk-import-button');
        installPreviewInvalidation(bulkForm, bulkToken, bulkSubmit);
        bulkPreviewButton.addEventListener('click', async function() {
            bulkResult.textContent = 'Проверка…';
            bulkPreviewButton.disabled = true;
            bulkToken.value = '';
            bulkSubmit.disabled = true;
            try {
                const response = await fetch(API.previewBulk, {
                    method: 'POST',
                    body: new FormData(bulkForm)
                });
                const data = await response.json();
                if (!response.ok) {
                    bulkResult.textContent = data.error || 'Не удалось проверить пачку';
                    return;
                }
                renderImportPreview(bulkResult, data);
                if (data.preview_token && data.rejected_count === 0 && data.accepted_count > 0) {
                    bulkToken.value = data.preview_token;
                    bulkSubmit.disabled = false;
                }
            } catch (error) {
                console.error(error);
                bulkResult.textContent = 'Ошибка сети';
            } finally {
                bulkPreviewButton.disabled = false;
            }
        });
    }

    const csvForm = document.getElementById('csv-import-form');
    const csvPreviewButton = document.getElementById('csv-preview-button');
    if (csvForm && csvPreviewButton) {
        const result = document.getElementById('csv-preview-result');
        const token = document.getElementById('csv-preview-token');
        const submit = document.getElementById('csv-import-button');
        const trust = document.getElementById('trust-raw-csv');
        installPreviewInvalidation(csvForm, token, submit, 'trust_raw_csv');
        if (trust) {
            trust.addEventListener('change', function() {
                submit.disabled = !token.value || !trust.checked;
            });
        }
        csvPreviewButton.addEventListener('click', async function() {
            result.textContent = 'Проверка…';
            csvPreviewButton.disabled = true;
            token.value = '';
            submit.disabled = true;
            try {
                const response = await fetch(API.previewCsv, {
                    method: 'POST',
                    body: new FormData(csvForm)
                });
                const data = await response.json();
                if (!response.ok) {
                    result.textContent = data.error || 'Не удалось проверить CSV';
                    return;
                }
                renderImportPreview(result, data);
                if (data.preview_token && data.rejected_count === 0 && data.accepted_count > 0) {
                    token.value = data.preview_token;
                    submit.disabled = Boolean(trust && !trust.checked);
                }
            } catch (error) {
                console.error(error);
                result.textContent = 'Ошибка сети';
            } finally {
                csvPreviewButton.disabled = false;
            }
        });
    }

    // ── Exact generated-PDF preview ──

    const pdfPreviewForm = document.getElementById('pdf-preview-form');
    const pdfPreviewDialog = document.getElementById('pdf-preview-dialog');
    const pdfPreviewFrame = document.getElementById('pdf-preview-frame');
    const pdfPreviewClose = document.getElementById('pdf-preview-close');
    let pdfPreviewUrl = null;
    if (pdfPreviewForm && pdfPreviewDialog) {
        pdfPreviewForm.addEventListener('submit', async function(event) {
            event.preventDefault();
            const button = pdfPreviewForm.querySelector('button[type="submit"]');
            button.disabled = true;
            button.textContent = 'Генерация…';
            try {
                const response = await fetch(pdfPreviewForm.action, {
                    method: 'POST', body: new FormData(pdfPreviewForm)
                });
                if (!response.ok) {
                    alert('Не удалось создать PDF-превью (HTTP ' + response.status + ')');
                    return;
                }
                if (pdfPreviewUrl) URL.revokeObjectURL(pdfPreviewUrl);
                pdfPreviewUrl = URL.createObjectURL(await response.blob());
                pdfPreviewFrame.src = pdfPreviewUrl;
                pdfPreviewDialog.showModal();
            } catch (error) {
                console.error(error);
                alert('Ошибка сети при создании PDF-превью');
            } finally {
                button.disabled = false;
                button.textContent = '🔎 PDF-превью';
            }
        });
        pdfPreviewClose.addEventListener('click', function() {
            pdfPreviewDialog.close();
        });
        pdfPreviewDialog.addEventListener('close', function() {
            pdfPreviewFrame.removeAttribute('src');
            if (pdfPreviewUrl) URL.revokeObjectURL(pdfPreviewUrl);
            pdfPreviewUrl = null;
        });
    }

    // ── Проверка печатного документа ──

    const preflightButton = document.getElementById('preflight-button');
    const preflightResult = document.getElementById('preflight-result');
    if (preflightButton && preflightResult) {
        function showPreflightResult() {
            preflightResult.focus({preventScroll: true});
            preflightResult.scrollIntoView({behavior: 'smooth', block: 'start'});
        }

        preflightButton.addEventListener('click', async function() {
            preflightButton.disabled = true;
            preflightResult.className = 'preflight-result';
            preflightResult.textContent = 'Компиляция и проверка…';
            showPreflightResult();
            try {
                const body = new FormData();
                body.set('profile_id', printProfile ? printProfile.value : '');
                const response = await fetch(API.preflight, {
                    method: 'POST', body: body
                });
                const data = await response.json();
                if (!response.ok) {
                    preflightResult.classList.add('has-errors');
                    preflightResult.textContent = data.error || 'Не удалось проверить документ';
                    return;
                }

                preflightResult.replaceChildren();
                const summary = document.createElement('strong');
                summary.textContent = data.ready
                    ? 'Критических проблем не найдено.'
                    : 'Перед печатью исправьте ошибки.';
                preflightResult.appendChild(summary);
                if (data.error_count) preflightResult.classList.add('has-errors');
                else if (data.warning_count) preflightResult.classList.add('has-warnings');

                if (data.issues.length) {
                    const list = document.createElement('ul');
                    data.issues.forEach(function(issue) {
                        const item = document.createElement('li');
                        item.className = 'preflight-' + issue.severity;
                        if (issue.card_id) {
                            const link = document.createElement('a');
                            link.href = API.editPage + issue.card_id;
                            link.textContent = issue.message;
                            item.appendChild(link);
                        } else {
                            item.textContent = issue.message;
                        }
                        list.appendChild(item);
                    });
                    preflightResult.appendChild(list);
                }
            } catch (error) {
                console.error(error);
                preflightResult.classList.add('has-errors');
                preflightResult.textContent = 'Ошибка сети при проверке документа';
            } finally {
                preflightButton.disabled = false;
                showPreflightResult();
            }
        });
    }

    // ── Drag & Drop ──

    var dragSrcRow = null;

    function restoreRowOrder(previousRows) {
        const tbody = document.getElementById('cards-tbody');
        previousRows.forEach(function(previousRow) {
            tbody.appendChild(previousRow);
        });
        renumberRows();
        rebuildPreviewOrder();
    }

    async function persistRowOrder(previousRows) {
        const tbody = document.getElementById('cards-tbody');
        const newRows = Array.from(tbody.querySelectorAll('tr'));
        const order = newRows.map(function(r) { return r.dataset.cardId; });
        tbody.setAttribute('aria-busy', 'true');
        document.body.classList.add('is-saving');
        try {
            const resp = await fetch(API.reorder, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ order: order, version: deckVersion })
            });
            const data = await resp.json();
            if (!resp.ok) {
                alert(data.error || 'Ошибка сортировки');
                restoreRowOrder(previousRows);
                if (resp.status === 409) updateDeckVersion(data.current_version);
                return false;
            }
            renumberRows();
            rebuildPreviewOrder();
            updateDeckVersion(data.deck_version);
            updateCounters(
                document.querySelectorAll('#cards-tbody tr').length,
                data.print_pages,
                data.empty_slots
            );
            showSuccess('Порядок сохранён');
            return true;
        } catch (error) {
            console.error(error);
            restoreRowOrder(previousRows);
            return false;
        } finally {
            tbody.removeAttribute('aria-busy');
            document.body.classList.remove('is-saving');
        }
    }

    function attachRowDragEvents(row) {
        const handle = row.querySelector('.drag-handle');
        if (handle) {
            handle.addEventListener('keydown', async function(event) {
                if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
                event.preventDefault();
                const tbody = document.getElementById('cards-tbody');
                const previousRows = Array.from(tbody.querySelectorAll('tr'));
                const currentIndex = previousRows.indexOf(row);
                const targetIndex = event.key === 'ArrowUp'
                    ? currentIndex - 1 : currentIndex + 1;
                if (targetIndex < 0 || targetIndex >= previousRows.length) return;
                if (event.key === 'ArrowUp') {
                    tbody.insertBefore(row, previousRows[targetIndex]);
                } else {
                    tbody.insertBefore(row, previousRows[targetIndex].nextSibling);
                }
                await persistRowOrder(previousRows);
                handle.focus();
            });
        }

        row.addEventListener('dragstart', function(e) {
            dragSrcRow = row;
            row.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', row.dataset.cardId);
        });

        row.addEventListener('dragend', function() {
            row.classList.remove('dragging');
            document.querySelectorAll('#cards-tbody tr').forEach(function(r) {
                r.classList.remove('drag-over');
            });
        });

        row.addEventListener('dragover', function(e) {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            if (row !== dragSrcRow) {
                row.classList.add('drag-over');
            }
        });

        row.addEventListener('dragleave', function() {
            row.classList.remove('drag-over');
        });

        row.addEventListener('drop', async function(e) {
            e.preventDefault();
            row.classList.remove('drag-over');

            if (dragSrcRow === row) return;

            const tbody = document.getElementById('cards-tbody');
            const rows = Array.from(tbody.querySelectorAll('tr'));
            const previousRows = rows.slice();
            const fromIdx = rows.indexOf(dragSrcRow);
            const toIdx = rows.indexOf(row);

            if (fromIdx < toIdx) {
                tbody.insertBefore(dragSrcRow, row.nextSibling);
            } else {
                tbody.insertBefore(dragSrcRow, row);
            }

            await persistRowOrder(previousRows);
        });
    }

    function rebuildPreviewOrder() {
        const grid = document.getElementById('preview-grid');
        if (!grid) return;
        const tbody = document.getElementById('cards-tbody');
        if (!tbody) return;

        const rows = Array.from(tbody.querySelectorAll('tr'));
        const previews = Array.from(grid.querySelectorAll('.preview-card'));
        const previewMap = {};
        previews.forEach(function(p) { previewMap[p.dataset.cardId] = p; });

        rows.forEach(function(row) {
            const preview = previewMap[row.dataset.cardId];
            if (preview) grid.appendChild(preview);
        });

        renumberPreviews();
    }

    // Инициализация drag & drop
    document.querySelectorAll('#cards-tbody tr').forEach(attachRowDragEvents);
    refreshSectionHeaders();

});
