document.addEventListener('DOMContentLoaded', function() {

    const DECK_ID = document.body.dataset.deckId;
    const CARDS_PER_PAGE = parseInt(document.body.dataset.cardsPerPage) || 8;
    const API = {
        addCard:    '/api/deck/' + DECK_ID + '/add_card',
        deleteCard: '/api/deck/' + DECK_ID + '/delete_card/',
        reorder:    '/api/deck/' + DECK_ID + '/reorder',
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

        if (view === 'preview' && !mathjaxRendered) {
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

    function updateCounters(count) {
        document.getElementById('cards-count').textContent = count;
        const pages = count > 0 ? Math.ceil(count / CARDS_PER_PAGE) : 0;
        document.getElementById('pages-count').textContent = pages;
        document.getElementById('empty-count').textContent =
            count > 0 ? (pages * CARDS_PER_PAGE - count) : 0;
        document.getElementById('generate-buttons').style.display =
            count > 0 ? '' : 'none';
    }

    function renumberRows() {
        const rows = document.querySelectorAll('#cards-tbody tr');
        rows.forEach((row, i) => {
            row.dataset.index = i;
            row.querySelector('.row-number').textContent = i + 1;
            const editLink = row.querySelector('a[href*="edit_card"]');
            if (editLink) editLink.href = API.editPage + i;
            const delBtn = row.querySelector('.delete-btn');
            if (delBtn) delBtn.dataset.index = i;
        });
    }

    function renumberPreviews() {
        const cards = document.querySelectorAll('#preview-grid .preview-card');
        cards.forEach((card, i) => {
            card.dataset.index = i;
            card.querySelector('.card-number').textContent = '#' + (i + 1);
            const editLink = card.querySelector('a[href*="edit_card"]');
            if (editLink) editLink.href = API.editPage + i;
            const delBtn = card.querySelector('.delete-btn');
            if (delBtn) delBtn.dataset.index = i;
        });
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // ── Удаление/добавление в превью ──

    function removeCardFromPreview(index) {
        const card = document.querySelector('#preview-grid .preview-card[data-index="' + index + '"]');
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
        div.dataset.index = index;
        div.innerHTML =
            '<span class="card-number">#' + (index + 1) + '</span>' +
            '<span class="card-actions">' +
            '    <a href="' + API.editPage + index + '" title="Редактировать">✏️</a>' +
            '    <a href="#" class="delete-btn" data-index="' + index + '" title="Удалить">🗑️</a>' +
            '</span>' +
            '<div class="preview-card-inner">' +
            '    <div class="preview-side preview-front">' +
            '        <div class="preview-content">' + escapeHtml(cardData.front) + '</div>' +
            '        <span class="preview-side-label">задание</span>' +
            '    </div>' +
            '    <div class="preview-side preview-back">' +
            '        <div class="preview-content">' + escapeHtml(cardData.back) + '</div>' +
            '        <span class="preview-side-label">решение</span>' +
            '    </div>' +
            '</div>';
        grid.appendChild(div);
        attachDeleteEvent(div.querySelector('.delete-btn'));

        if (mathjaxRendered && window.MathJax && MathJax.typesetPromise) {
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
            const front = frontEl.value.trim();
            const back = backEl.value.trim();

            if (!front && !back) {
                alert('Заполните хотя бы одно поле');
                return;
            }

            try {
                const resp = await fetch(API.addCard, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ front: front, back: back })
                });
                const data = await resp.json();

                if (!resp.ok) {
                    alert(data.error || 'Ошибка');
                    return;
                }

                addCardRow(data.index, data.card);
                addCardToPreview(data.index, data.card);
                updateCounters(data.cards_count);
                showSuccess('Карточка добавлена!');

                frontEl.value = '';
                backEl.value = '';
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
        tr.dataset.index = index;
        tr.innerHTML =
            '<td class="drag-handle" title="Перетащите для сортировки">⠿</td>' +
            '<td class="row-number">' + (index + 1) + '</td>' +
            '<td class="card-text">' + escapeHtml(card.front) + '</td>' +
            '<td class="card-text">' + escapeHtml(card.back) + '</td>' +
            '<td class="actions">' +
            '    <a href="' + API.editPage + index + '" title="Редактировать">✏️</a>' +
            '    <a href="#" class="delete-btn" data-index="' + index + '" title="Удалить">🗑️</a>' +
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
            const idx = parseInt(btn.dataset.index);
            if (!confirm('Удалить карточку №' + (idx + 1) + '?')) return;

            try {
                const resp = await fetch(API.deleteCard + idx, {
                    method: 'DELETE'
                });
                const data = await resp.json();

                if (!resp.ok) {
                    alert(data.error || 'Ошибка');
                    return;
                }

                const row = document.querySelector('#cards-tbody tr[data-index="' + idx + '"]');
                if (row) row.remove();
                renumberRows();

                removeCardFromPreview(idx);
                updateCounters(data.cards_count);
                showSuccess('Карточка удалена');

            } catch (err) {
                console.error(err);
                alert('Ошибка сети');
            }
        });
    }

    document.querySelectorAll('.delete-btn').forEach(attachDeleteEvent);

    // ── Drag & Drop ──

    var dragSrcRow = null;

    function attachRowDragEvents(row) {
        row.addEventListener('dragstart', function(e) {
            dragSrcRow = row;
            row.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', row.dataset.index);
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
            const fromIdx = rows.indexOf(dragSrcRow);
            const toIdx = rows.indexOf(row);

            if (fromIdx < toIdx) {
                tbody.insertBefore(dragSrcRow, row.nextSibling);
            } else {
                tbody.insertBefore(dragSrcRow, row);
            }

            renumberRows();

            const newRows = Array.from(tbody.querySelectorAll('tr'));
            const order = newRows.map(function(r) { return parseInt(r.dataset.index); });

            try {
                const resp = await fetch(API.reorder, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ order: order })
                });
                const data = await resp.json();

                if (resp.ok) {
                    newRows.forEach(function(r, i) { r.dataset.index = i; });
                    renumberRows();
                    rebuildPreviewOrder();
                    showSuccess('Порядок сохранён');
                } else {
                    alert(data.error || 'Ошибка сортировки');
                    location.reload();
                }
            } catch (err) {
                console.error(err);
                location.reload();
            }
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
        previews.forEach(function(p) { previewMap[p.dataset.index] = p; });

        rows.forEach(function(row) {
            const oldIdx = row.dataset.index;
            const preview = previewMap[oldIdx];
            if (preview) grid.appendChild(preview);
        });

        renumberPreviews();
    }

    // Инициализация drag & drop
    document.querySelectorAll('#cards-tbody tr').forEach(attachRowDragEvents);

});
