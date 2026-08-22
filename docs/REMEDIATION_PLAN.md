# План багфиксов, стабилизации и развития

Дата ревизии: 22 августа 2026 года. Объект аудита — текущее незакоммиченное состояние ветки `phase5-persistence`, включая multi-deck JSON persistence.

## Статус выполнения

- [x] Baseline аудита зафиксирован коммитом `39359a0`.
- [x] Этап 0: удалены неиспользуемые сломанные JSON/session adapters и их устаревшие тесты.
- [x] Этап 0: нормализованы executable-биты исходников, данных и документации.
- [x] Этап 0: добавлена CI-матрица Python 3.11–3.13 и отдельный TeX/coverage job.
- [ ] Этап 1: модель физического листа и корректная duplex-раскладка.
  - [x] Введены `Sheet`, `DuplexMode` и независимые long-edge/short-edge transforms.
  - [x] Страницы чередуются по физическим листам: `F1,B1,F2,B2…`.
  - [x] Убран безусловный поворот оборота на 180°.
  - [x] Настраиваемый размер стал внешним cut size; layout валидируется до компиляции.
  - [x] Добавлен реальный четырёхстраничный `pdflatex`/`pdftotext` integration test.
  - [x] Добавлены независимые offsets сторон (±10 мм) и опциональные registration marks.
  - [x] Реальный PDF geometry test измеряет векторную рамку через `mutool` с допуском 0.1 мм.
  - [ ] Добавить overflow preflight.
  - [ ] Выполнить raster golden tests и физический прогон.
- [x] Этап 2: безопасная граница TeX/HTTP на уровне приложения.
  - [x] Математические команды ограничены allowlist; malicious/malformed fixtures отклоняются до компиляции.
  - [x] `pdflatex`/`xelatex` используют `-no-shell-escape -halt-on-error -file-line-error`.
  - [x] Non-zero return code не считается успешным даже при наличии partial PDF.
  - [x] Unicode download names отдаются через RFC 5987; проверены реальным Werkzeug/curl запросом.
  - [x] Validation/compile errors больше не маскируются HTTP 200.
  - [x] Добавлены CSRF, quotas, safe external logs и security headers.
  - [ ] Для production deployment изолировать TeX отдельным непривилегированным worker/container.
- [ ] Этап 3: транзакционное persistence.
- [ ] Этап 4: импорт и web UX.
- [ ] Этап 5: функциональное развитие.

## 1. Итог аудита

Вердикт: приложение имеет понятную слоистую основу и работоспособный CRUD, но пока не выполняет главное обещание — гарантированное совмещение лицевой и оборотной стороны при произвольной двусторонней печати. До устранения P0-блокеров PDF нельзя выдавать как готовый печатный результат.

Самые опасные проблемы исходного baseline (выполненные отмечены):

1. ✅ Для двух и более листов все лицевые страницы выводились раньше всех оборотных (`BUG-PRINT-001`). Исправлено через модель физических листов и interleaved page sequence.
2. ✅ Ячейки оборота зеркалились по столбцам и одновременно безусловно поворачивались на 180° (`BUG-PRINT-002`). Теперь transform зависит от long/short edge и не переворачивает текст.
3. ✅ Кириллица в названии колоды попадала в `Content-Disposition` без RFC 5987 и роняла реальный Werkzeug WSGI (`BUG-HTTP-003`). Исправлено через `send_file/download_name`.
4. ✅ TeX внутри математических delimiters проходил без allowlist, а компилятор не получал явный `-no-shell-escape` (`BUG-SEC-001/002`). Оба слоя закрыты тестируемым контрактом.
5. JSON обновляется неатомарно и без lock. Сбой посередине записи способен обнулить рабочий файл; повреждённый JSON затем молча трактуется как пустой (`BUG-DATA-002/004`).

## 2. Как проводилась проверка

- прочитаны domain, use cases, adapters, Flask routes, Jinja, CSS и JavaScript;
- проверены git-история и незакоммиченные изменения; пользовательские изменения не откатывались;
- исходный `venv` признан непереносимым (`IntxLNK`, `Exec format error`), создан отдельный `.venv` на Python 3.13.5;
- выполнены unit/contract/integration-тесты с реальным `pdflatex` и `pdfinfo`;
- Flask поднят с отдельной базой в `/tmp`, UI пройден Chromium через pyppeteer;
- сгенерирован настоящий A4 PDF, обе страницы растеризованы и визуально проверены;
- проверены зависимости через `pip check` и исходники через `git diff --check`.

Текущая автоматизированная база: 159 проходящих тестов и 16 строгих `xfail`-контрактов для подтверждённых дефектов. Общий branch coverage составляет 98.71% при обязательном CI-пороге 98%. Физический прогон на нескольких моделях принтеров ещё обязателен: PDF-проверка не моделирует driver margins, feed skew и аппаратный duplex offset.

## 3. Реестр дефектов

### P0 — блокируют основное назначение или создают риск безопасности/потери данных

| ID | Дефект и доказательство | Исправление | Критерий приёмки |
|---|---|---|---|
| ~~BUG-PRINT-001~~ ✅ | `LatexRenderer` формировал все fronts, затем все backs. | Выполнено: `Sheet` отделяет физическую модель, PDF идёт `F1,B1,F2,B2…`; unit и реальный четырёхстраничный TeX test проходят. | Автоматизированная часть выполнена; physical matrix остаётся в этапе 1. |
| ~~BUG-PRINT-002~~ ✅ | Оборот сочетал horizontal mirror с `rotatebox{180}`. | Выполнено: явные long-edge/short-edge permutations без безусловного поворота текста. | Unit-transform tests проходят; физическая проверка обоих режимов остаётся. |
| ~~BUG-PRINT-003~~ ✅ | `9.3 × 6.3` были размером inner minipage, а не cut box. | Выполнено: content box вычисляется вычитанием frame inset, `fboxrule` фиксирован явно. | Векторная рамка реального PDF измеряется через `mutool` с допуском 0.1 мм. |
| ~~BUG-HTTP-003~~ ✅ | Русское имя PDF вызывало `UnicodeEncodeError` на реальном dev-сервере. | Выполнено через Werkzeug `send_file(..., download_name=...)`, который формирует ASCII fallback и RFC 5987 `filename*`. | Реальный Werkzeug/curl запрос вернул 200, PDF и Latin-1-safe headers. |
| ~~BUG-SEC-001~~ ✅ | Команды внутри `$...$` не фильтровались. | Выполнено: allowlist учебной математики, balance validation, запрет опасных команд/символов до compiler call. | Malicious и malformed fixtures возвращают 422; compiler mock не вызывается. |
| ~~BUG-SEC-002~~ ✅ | Не было явного `-no-shell-escape`. | Выполнено для pdfLaTeX и XeLaTeX: `-no-shell-escape -halt-on-error -file-line-error`. | Аргументы и реальные TeX builds проверяются тестами; отдельный OS/container sandbox ещё нужен для production. |
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
| ~~BUG-PDF-001~~ ✅ | Partial PDF считался успехом даже при non-zero exit code. | Выполнено: обязательный return code 0, наличие PDF, safe failure flags и fallback stdout/stderr log. |
| ~~BUG-LIMIT-001~~ ✅ | `max_cards=200` не использовался. | Выполнено: единый quota в use cases и web/API; bulk/CSV проверяют будущую ёмкость до сохранения. |
| BUG-CONF-001 | База зависит от process CWD. | Абсолютный `DATA_DIR` из env/Flask instance path; миграционная диагностика найденных `data/`. |
| ~~BUG-CONF-002~~ ✅ / BUG-VAL-001 | Layout теперь проверяет positive/finite dimensions, frame inset и попадание сетки в printable A4. Прямой вызов use case с `cards_per_page=0` ещё требует отдельной защиты. | Config/renderer validation выполнена; добавить invariant в `CardDeck.padded`/use case. |
| BUG-CONF-003 | `create_app()` не принимает config/dependencies. | `create_app(config=None, repo=None, renderer=None, compiler=None)`; env mapping; production/test profiles. |
| ~~BUG-HTTP-001~~ ✅ | Удаление карточки было доступно через GET. | Выполнено: HTML fallback принимает только POST + CSRF, AJAX использует DELETE JSON API. |
| ~~BUG-HTTP-002~~ ✅ | Ошибка компиляции возвращала 200. | Validation и compile failure теперь возвращают 422. Разделение tool failure на 503/504 и sanitization log остаются в этапе 2. |
| BUG-WEB-002/003 | Число вместо string и `order=None` дают необработанные исключения. | Schema validation (dataclass/Pydantic/ручная) до use case; единый JSON error handler. |
| ~~BUG-SEC-003~~ ✅ | HTML-формы не имели CSRF. | Выполнено: per-session token, constant-time comparison, 400 без token; JSON API отделён по Content-Type/method contract. |

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
- `MAX_CONTENT_LENGTH=2 MiB` и `max_cards=200` реализованы; отдельные ограничения длины стороны/имени ещё нужны.
- Нет structured logging, healthcheck (`pdflatex`/write access), error IDs и метрик времени компиляции.
- Нет backup/restore/export всей колоды и schema version/migrations.
- JSON read-modify-write не масштабируется и теряет обновления при параллельных запросах. После стабилизации перейти на SQLite с transactions/WAL.
- Dev entrypoint всегда запускается с `debug=True`; production должен использовать WSGI server и env-controlled debug.
- Secret берётся из `DIDACTIC_CARDS_SECRET_KEY`, без env генерируется случайный для локального запуска; production должен задавать стабильное значение.
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

1. [x] Определить поддерживаемый язык карточки: plain text + allowlisted math, а не произвольный TeX.
2. [x] Разбирать формулы; выдавать validation errors, не компилировать опасный input.
3. [x] Запускать TeX с `-no-shell-escape -halt-on-error`; отдельный непривилегированный worker/container остаётся deployment-задачей.
4. [x] Исправить Unicode filename через framework API и RFC 5987.
5. [x] Ввести CSRF, request size/card quotas, safe error pages, security headers.
6. [x] Не возвращать полный log наружу; debug-лог остаётся только при явно включённом Flask debug.

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
