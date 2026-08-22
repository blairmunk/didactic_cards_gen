# План багфиксов, стабилизации и развития

Дата ревизии: 22 августа 2026 года. Объект аудита — текущее незакоммиченное состояние ветки `phase5-persistence`, включая multi-deck JSON persistence.

## Статус выполнения

- [x] Baseline аудита зафиксирован коммитом `39359a0`.
- [x] Этап 0: удалены неиспользуемые сломанные JSON/session adapters и их устаревшие тесты.
- [x] Этап 0: нормализованы executable-биты исходников, данных и документации.
- [x] Этап 0: добавлена CI-матрица Python 3.11–3.13 и отдельный TeX/coverage job.
- [ ] Этап 1: модель физического листа и корректная duplex-раскладка.
- [ ] Этап 2: безопасная граница TeX/HTTP.
- [ ] Этап 3: транзакционное persistence.
- [ ] Этап 4: импорт и web UX.
- [ ] Этап 5: функциональное развитие.

## 1. Итог аудита

Вердикт: приложение имеет понятную слоистую основу и работоспособный CRUD, но пока не выполняет главное обещание — гарантированное совмещение лицевой и оборотной стороны при произвольной двусторонней печати. До устранения P0-блокеров PDF нельзя выдавать как готовый печатный результат.

Самые опасные проблемы:

1. Для двух и более листов все лицевые страницы выводятся раньше всех оборотных. Duplex-принтер совмещает `front-1` с `front-2`, а не с `back-1` (`BUG-PRINT-001`).
2. Ячейки оборота зеркалятся по столбцам и одновременно безусловно поворачиваются на 180°. Для portrait/long-edge текст в результате перевёрнут (`BUG-PRINT-002`).
3. Кириллица в названии колоды попадает в `Content-Disposition` без RFC 5987 и роняет реальный Werkzeug WSGI при отдаче уже собранного PDF (`BUG-HTTP-003`).
4. TeX внутри математических delimiters проходит без allowlist; компилятор не получает явный `-no-shell-escape` (`BUG-SEC-001/002`).
5. JSON обновляется неатомарно и без lock. Сбой посередине записи способен обнулить рабочий файл; повреждённый JSON затем молча трактуется как пустой (`BUG-DATA-002/004`).

## 2. Как проводилась проверка

- прочитаны domain, use cases, adapters, Flask routes, Jinja, CSS и JavaScript;
- проверены git-история и незакоммиченные изменения; пользовательские изменения не откатывались;
- исходный `venv` признан непереносимым (`IntxLNK`, `Exec format error`), создан отдельный `.venv` на Python 3.13.5;
- выполнены unit/contract/integration-тесты с реальным `pdflatex` и `pdfinfo`;
- Flask поднят с отдельной базой в `/tmp`, UI пройден Chromium через pyppeteer;
- сгенерирован настоящий A4 PDF, обе страницы растеризованы и визуально проверены;
- проверены зависимости через `pip check` и исходники через `git diff --check`.

Текущая автоматизированная база: 91 проходящий тест и 28 строгих `xfail`-контрактов для подтверждённых дефектов. После удаления dead adapters общий branch coverage составляет 99.51% при обязательном CI-пороге 98%. Физический прогон на нескольких моделях принтеров ещё обязателен: PDF-проверка не моделирует driver margins, feed skew и аппаратный duplex offset.

## 3. Реестр дефектов

### P0 — блокируют основное назначение или создают риск безопасности/потери данных

| ID | Дефект и доказательство | Исправление | Критерий приёмки |
|---|---|---|---|
| BUG-PRINT-001 | `LatexRenderer` формирует все fronts, затем все backs. `test_duplex_pages_are_interleaved_per_physical_sheet` — strict xfail. | Генерировать пары страниц физического листа: `F1,B1,F2,B2…`; отделить построение sheet model от LaTeX. | Для 1–33 карточек каждый ID на обороте находится на странице сразу после своей лицевой страницы и в том же физическом слоте после выбранного flip transform. |
| BUG-PRINT-002 | Оборот: horizontal mirror плюс `rotatebox{180}`. Реальная page-2 на скриншоте перевёрнута. | Ввести явный `duplex_mode`: long-edge/short-edge/manual; хранить slot transform отдельно от orientation; убрать безусловный rotate. | Эталонная сетка с координатами и стрелкой «верх» совпадает после физического переворота обоими поддерживаемыми способами. |
| BUG-PRINT-003 | `9.3 × 6.3` — размер inner minipage, а cut box около `9.89 × 6.89`. | Переопределить config как внешний cut size; вычислять content box вычитанием padding/border. | Измеренный MediaBox/rect в PDF отличается от настройки не более чем на 0.1 мм. |
| BUG-HTTP-003 | Русское имя PDF вызывает `UnicodeEncodeError` на реальном dev-сервере. | Использовать Werkzeug `send_file(..., download_name=...)` или ASCII fallback + `filename*=UTF-8''...`; нормализовать CR/LF/quotes. | Скачиваются имена на русском, emoji и кавычки; каждый header кодируется/отдаётся WSGI без ошибки. |
| BUG-SEC-001 | Команды внутри `$...$` не экранируются. | Перейти от regex к ограниченному parser/allowlist математических команд; запретить `\input`, `\include`, `\write`, `\openin`, `\csname`, macro definitions и закрытие окружений. | Набор malicious fixtures не читает файлы, не меняет документ и даёт понятную validation error. |
| BUG-SEC-002 | Нет явного `-no-shell-escape`. | Добавить `-no-shell-escape`, `-halt-on-error`, изолированный env/TEXMF; в deployment компилировать непривилегированным worker/container. | Команда компилятора проверена тестом; файловый и процессный sandbox подтверждён integration-тестом. |
| BUG-DATA-002 | Invalid JSON превращается в `[]`, скрывая аварию и открывая путь к перезаписи. | Различать missing/empty/corrupt; на corrupt прекращать запись, показывать recovery UI, сохранять `.broken-<timestamp>`. | Повреждение одного байта не уничтожает исходник и даёт диагностируемую ошибку. |
| BUG-DATA-004 | `_write_json` пишет прямо в live-файл. | `NamedTemporaryFile` в том же каталоге → flush/fsync → `os.replace`; file lock на read-modify-write. | Fault-injection на каждом шаге сохраняет либо старую, либо полностью новую валидную версию. |

### P1 — нарушение данных, API и ключевых пользовательских сценариев

| ID | Дефект | Исправление и тест |
|---|---|---|
| BUG-DATA-001 | `card_ids` синхронизируются только при различии длины; равная длина с другими ID остаётся stale. | Сравнивать полный упорядоченный список либо удалить денормализацию. Strict xfail уже воспроизводит. |
| BUG-DATA-003 / BUG-WEB-001 | Запись в несуществующую колоду создаёт orphan JSON; API отвечает success. | Проверять deck existence в use case/repository, отдавать 404, удалять/мигрировать orphan files. |
| BUG-DATA-005 | Deep-clone создаёт новые UUID, но теряет `parent_id` карточек. | Вызывать `Card.clone()`; сохранить lineage contract обычным тестом. |
| ~~BUG-ARCH-001~~ ✅ | Старый `JsonFileStorage` импортировал удалённый `StorageBackend`; весь старый test collection раньше падал. | Выполнено на этапе 0: неиспользуемые `JsonFileStorage` и `FlaskSessionRepository` удалены вместе с устаревшими тестами; активным остаётся один `JsonRepository`. |
| BUG-UI-001 | Drag-and-drop вызывает `renumberRows()` до построения permutation и всегда отправляет `[0,1,…]`. | Хранить stable card IDs; собрать old indices до mutation; optimistic UI откатывать без `location.reload`. Добавить browser E2E reorder + reload. |
| BUG-IMP-001 | UI обещает `||`, parser делит по первому одиночному `|`. | Единый parser с exact delimiter `||`, escaping/quoting и preview результата до commit. |
| BUG-IMP-002 | UI обещает `;`, `csv.reader` использует `,`. | `csv.Sniffer` с явным выбором delimiter; UTF-8/UTF-8-BOM; header toggle; preview и отчёт rejected rows. |
| BUG-PDF-001 | Наличие partial PDF считается успехом даже при non-zero exit code. | Проверять return code, `%PDF`/EOF, `-halt-on-error`; лог объединять с stderr. |
| BUG-LIMIT-001 | `max_cards=200` не используется. | Централизованный quota в use case для single/bulk/CSV/API; транзакционно отклонять превышение. |
| BUG-CONF-001 | База зависит от process CWD. | Абсолютный `DATA_DIR` из env/Flask instance path; миграционная диагностика найденных `data/`. |
| BUG-CONF-002 / BUG-VAL-001 | Нулевые capacity и не помещающиеся на A4 размеры принимаются. | Валидировать positive ints, finite dimensions, printable bounds и minimum safe area при старте. |
| BUG-CONF-003 | `create_app()` не принимает config/dependencies. | `create_app(config=None, repo=None, renderer=None, compiler=None)`; env mapping; production/test profiles. |
| BUG-HTTP-001 | Удаление карточки доступно через GET. | Только `DELETE`/POST + CSRF; ссылки заменить form/button. |
| BUG-HTTP-002 | Ошибка компиляции возвращает 200. | 422 для invalid TeX, 503/504 для tool failure/timeout; request ID без полного internal log пользователю. |
| BUG-WEB-002/003 | Число вместо string и `order=None` дают необработанные исключения. | Schema validation (dataclass/Pydantic/ручная) до use case; единый JSON error handler. |
| BUG-SEC-003 | HTML-формы не имеют CSRF. | CSRF token, SameSite cookie, POST/DELETE only; contract-тест без token получает 400/403. |

### P2 — надёжность, эксплуатация и качество UX

- `BUG-UI-002`: удалить видимый хвост `HTML` после `</html>`.
- `BUG-UI-003`: MathJax полностью зависит от CDN; vendor assets локально или предусмотреть понятный fallback/status.
- Preview не соответствует PDF по шрифту, размеру, top alignment, padding, pagination и orientation. Нужен PDF/image preview, а не декоративная HTML-карточка.
- `back_border=False` скрывает совмещение. Нужен режим calibration/debug с крестами, crop marks и идентификаторами slot/page.
- Длинный текст не получает overflow warning, auto-fit или clip policy; возможен выход за границы соседней карточки.
- Single newline в исходном тексте не равен видимому переносу LaTeX; нужен определённый markdown/rich-text contract.
- Routes адресуют карточки индексом. При параллельной сортировке/редактировании индекс указывает уже на другой объект; перейти на UUID.
- Времена сохраняются UTC, но UI форматирует без зоны и без явной конвертации в локальную.
- Полный LaTeX log показывается пользователю и может раскрыть пути/служебные детали.
- Нет `MAX_CONTENT_LENGTH`, ограничения CSV, количества строк, длины card side и имени колоды.
- Нет structured logging, healthcheck (`pdflatex`/write access), error IDs и метрик времени компиляции.
- Нет backup/restore/export всей колоды и schema version/migrations.
- JSON read-modify-write не масштабируется и теряет обновления при параллельных запросах. После стабилизации перейти на SQLite с transactions/WAL.
- Dev entrypoint всегда запускается с `debug=True`; production должен использовать WSGI server и env-controlled debug.
- Hardcoded secret должен идти из env, даже если текущий JSON flow почти не использует session.
- UI: emoji-only actions без accessible names, слабая keyboard DnD, таблица не имеет mobile overflow, focus/disabled/loading states неполны.
- Нет favicon (реальный browser audit получил 404), CSP и стандартных security headers.
- Массовая случайная смена executable-битов у исходников загрязняет diff; нормализовать modes отдельным механическим коммитом после согласования.

## 4. Пошаговый план внедрения

### Этап 0. Зафиксировать воспроизводимую базу

1. [x] Принять `requirements.txt`, `requirements-dev.txt`, `pytest.ini` и новый набор тестов.
2. [x] Удалить/мигрировать dead adapters; добиться 100% collection без import errors.
3. [x] Нормализовать file modes, не смешивая это с логическими изменениями.
4. [x] Добавить CI на Python 3.11, 3.12, 3.13: unit всегда, TeX integration в image с фиксированной TeX Live.
5. [x] В CI считать strict xfail техническим долгом: strict XPASS уже завершает pytest ошибкой, полный список виден в test report.

Выход: чистое окружение устанавливается одной командой; test result не зависит от старого `venv` или CWD.

### Этап 1. Перепроектировать печатное ядро

1. Ввести модель `Sheet(front_slots, back_slots, duplex_mode)`; renderer только сериализует готовую модель.
2. Формировать page sequence по физическим листам, не по сторонам документа.
3. Описать coordinate system: origin, row/column, top arrow, slot ID; для каждого flip mode иметь явную permutation matrix.
4. Сделать внешний cut size первичным. Из него вычислять content width/height с учётом `2 × (padding + border)`.
5. Добавить independent horizontal/vertical calibration offsets для оборота, printer profile и тестовую страницу.
6. Включить crop/registration marks; border — отдельная опция, не влияющая на размеры.
7. Добавить overflow policy: reject/warn, auto-fit до минимального font size, либо controlled clipping.
8. Проверять PDF MediaBox=A4 и реальные bounding boxes с PyMuPDF/pypdf; raster visual diff хранить как golden artifacts.

Минимальная матрица тестов:

- количество карточек: 0, 1, 2, 7, 8, 9, 15, 16, 17, 199, 200;
- rows/cols: 1×1, 2×4, 3×3 и invalid;
- partial last sheet, пустая front/back, Unicode, multiline, длинная формула;
- long-edge и short-edge;
- на каждом slot печатать уникальные ID, стрелку вверх и координату; после transform пары совпадают;
- физическая линейка/скан: отклонение рамок ≤ допуск продукта (предлагается 0.5 мм после калибровки, 1.0 мм без неё).

### Этап 2. Закрыть TeX/HTTP security boundary

1. Определить поддерживаемый язык карточки: plain text + allowlisted math, а не произвольный TeX.
2. Разбирать формулы; выдавать validation errors с позицией, не компилировать опасный input.
3. Запускать TeX с `-no-shell-escape -halt-on-error`, очищенным env, лимитами CPU/RAM/file size и отдельным непривилегированным пользователем.
4. Исправить Unicode filename через framework API и RFC 5987.
5. Ввести CSRF, request size/card quotas, safe error pages, security headers.
6. Не возвращать полный log наружу; хранить sanitized excerpt и internal request ID.

Выход: malicious fixture suite проходит, timeout и compile failure имеют корректные статусы, русский PDF скачивается во всех целевых браузерах.

### Этап 3. Сделать persistence транзакционным

1. Сначала исправить текущий JSON: absolute path, atomic replace, lock, backup, corruption recovery и schema version.
2. Убрать двойной источник истины `card_ids` либо проверять точное равенство и целостность на каждой транзакции.
3. Добавить startup integrity scan: missing/orphan/duplicate IDs, invalid timestamps, recovery report без автоматической потери данных.
4. Перейти на SQLite: `decks`, `cards`, `deck_cards(position)`, foreign keys, transactions, migrations.
5. Все UI/API операции адресовать card UUID + optimistic version, а не индексом.

Выход: concurrent add/edit/reorder stress test без lost updates; kill/fault injection не портит последнюю подтверждённую версию; старые JSON мигрируются один раз с backup.

### Этап 4. Исправить импорт и web UX

1. Единая спецификация bulk delimiter; исправить UI, parser и docs одновременно.
2. CSV wizard: dialect/encoding/header preview, validation counts, atomic import или rollback.
3. Исправить DnD на stable IDs; keyboard reorder; loading/error/rollback states.
4. Локальный MathJax bundle и явный статус typesetting.
5. Заменить HTML preview на raster/PDF preview конкретного листа front/back с overlay mode.
6. Accessibility: semantic buttons, `aria-label`, focus, confirmation dialogs, responsive table.

Выход: Playwright/pyppeteer E2E проходит create → import → reorder → edit → generate → reload; порядок и формулы сохраняются.

### Этап 5. Функции после стабилизации

Приоритетный roadmap:

1. Профили принтеров и calibration wizard с измеряемыми X/Y offsets.
2. Выбор формата A4/Letter, ориентации, сетки, внешнего размера карточки, margins/gaps/bleed/safe area.
3. Двусторонний PDF и два отдельных файла front/back для принтеров без duplex.
4. Импорт/экспорт колоды в versioned JSON/CSV; backup/restore.
5. Шаблоны оформления: шрифт, выравнивание, размер, фон, изображения/QR после отдельной security-модели.
6. Auto-fit и preflight: overflow, missing glyphs, unsupported formulas, printable-area warning.
7. Поиск, теги, массовое редактирование, undo/trash вместо немедленного удаления.
8. PDF metadata, deterministic builds и сохранение print job с конфигурацией для повторной печати.

## 5. Правила реализации каждого фикса

Для каждого ID:

1. Оставить существующий failing contract и воспроизвести локально.
2. Сделать минимальный fix без изменения несвязанных semantics.
3. Удалить `xfail`, сохранив тест обычным.
4. Добавить negative/boundary test и, для print/UI, integration или E2E.
5. Обновить руководство и migration note.
6. Прогнать весь pytest, реальный TeX integration, browser smoke и `git diff --check`.

Definition of Done для релиза двусторонней печати:

- нет P0/P1 strict xfail;
- page pairing и slot mapping доказаны property/integration-тестами;
- golden PDF/raster snapshots стабильны на фиксированном TeX image;
- минимум два принтера или один duplex-принтер + ручной feed прошли calibration sheet;
- русский filename, offline UI и data recovery проверены;
- backup и rollback документированы;
- UI явно показывает выбранный flip mode, масштаб 100% и измеренный допуск.

## 6. Рекомендуемый порядок PR

1. `test-baseline-and-packaging` — текущие тесты, зависимости, CI, нормализация legacy.
2. `print-sheet-model` — только page pairing/slot transforms и размеры.
3. `print-calibration-and-preflight` — offsets, marks, bounding-box/visual tests.
4. `safe-tex-and-download` — allowlist, compiler flags, Unicode headers, status codes.
5. `atomic-persistence` — JSON safety, integrity scan, migration foundation.
6. `stable-card-api-and-dnd` — UUID endpoints, validation, reorder E2E.
7. `imports-and-offline-preview` — CSV/bulk, local MathJax, PDF preview.
8. `sqlite-and-production-runtime` — transactions, migrations, WSGI/deployment docs.

Такой порядок сначала восстанавливает истинность основного обещания продукта, затем защищает данные и только после этого расширяет функции.
