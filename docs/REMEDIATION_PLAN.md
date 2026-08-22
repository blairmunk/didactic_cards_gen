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
  - [x] Добавлен read-only preflight: TeX-измерение vertical/horizontal overflow с card/side mapping, missing glyphs, printable-area и empty/partial-sheet warnings; служебные `Overfull hbox` сетки не создают ложных ошибок.
  - [x] Добавлен двухстраничный PBM raster golden diff реального PDF с pixel tolerance 0.2% и явным regeneration script.
  - [ ] Выполнить физический прогон на целевых принтерах.
- [x] Этап 2: безопасная граница TeX/HTTP на уровне приложения.
  - [x] Математические команды ограничены allowlist; malicious/malformed fixtures отклоняются до компиляции.
  - [x] `pdflatex`/`xelatex` используют `-no-shell-escape -halt-on-error -file-line-error`.
  - [x] Non-zero return code не считается успешным даже при наличии partial PDF.
  - [x] Unicode download names отдаются через RFC 5987; проверены реальным Werkzeug/curl запросом.
  - [x] Validation/compile errors больше не маскируются HTTP 200.
  - [x] Добавлены CSRF, quotas, safe external logs и security headers.
  - [ ] Для production deployment изолировать TeX отдельным непривилегированным worker/container.
- [x] Этап 3: транзакционное persistence.
  - [x] Путь базы абсолютный, поддерживает `DIDACTIC_CARDS_DATA_DIR` и не зависит от CWD.
  - [x] Запись JSON использует temp + fsync + atomic replace и сохраняет последнюю `.bak`.
  - [x] Read–modify–write сериализован между потоками и процессами; use cases используют единый mutation-контракт.
  - [x] Corrupt/missing/invalid JSON останавливает запись и даёт безопасную HTTP-ошибку.
  - [x] Устранены stale `card_ids`, orphan writes и потеря ancestry при clone.
  - [x] Добавлены schema version 1, read-only startup integrity report и управляемое CLI-восстановление backup с сохранением `.broken-*`.
  - [x] Активное хранилище мигрировано в SQLite schema 2 с FK, WAL, транзакциями, одноразовым backup/import legacy JSON и профилями принтера.
  - [x] Карточные HTML/API операции переведены с индексов на UUID; deck version даёт HTTP 409 при stale mutation.
- [ ] Этап 4: импорт и web UX.
  - [x] Bulk использует exact `||` и документированное escaping.
  - [x] CSV поддерживает auto/explicit dialect, UTF-8 BOM, header toggle, read-only preview и atomic reject при bad rows.
  - [x] DnD/keyboard reorder использует UUID, loading state и DOM rollback.
  - [x] MathJax 3.2.2 и fonts vendored локально; добавлен status/fallback.
  - [x] Декоративный preview дополнен inline preview того же сгенерированного PDF.
  - [x] Устранён stray HTML, добавлены favicon, focus styles и aria-label для icon actions.
  - [x] Chromium E2E после исправления offline resource assertion проходит полностью на временной SQLite-базе.
  - [ ] Расширить PDF overlay и полный accessibility review.
- [ ] Этап 5: функциональное развитие.
  - [x] Versioned JSON/CSV export и транзакционный import-копия с lineage.
  - [x] Раздельные front-only/back-only PDF сохраняют sheet mapping, duplex transform и offsets для ручной подачи.
  - [x] Компилируемый preflight проверяет overflow, missing glyphs, unsupported formulas и printable area до печати.
  - [x] Именованные config-профили принтера выбираются на print job и изолированы между запросами.
  - [x] Web calibration workflow сохраняет валидированные пользовательские профили в SQLite schema 2.
  - [x] Страница профилей генерирует двухстраничный A4 calibration PDF с мишенями, контрольным отрезком 100 мм и инструкцией знаков X/Y для обоих duplex-режимов.
  - [x] TeX auto-fit уменьшает 12pt → small → footnotesize → scriptsize и оставляет адресные preflight markers.
  - [ ] Расширенная layout-конфигурация; controlled clipping намеренно не включён, остаточный overflow блокирует готовность preflight.
- [x] Production runtime.
  - [x] Debug выключен по умолчанию и включается только строгой env-переменной.
  - [x] Добавлены `/health/live` и sanitised `/health/ready` для SQLite/TeX.
  - [x] Gunicorn добавлен в runtime dependencies; WSGI-команда и probe semantics документированы.
  - [x] Добавлены однострочные JSON-логи, request/error IDs и duration событий HTTP/PDF compilation без контента/путей.

## 1. Итог аудита

Вердикт после выполненной программной remediation: исходные P0-дефекты page pairing, duplex transform, cut size, TeX boundary, HTTP download и persistence закрыты обычными regression/integration-тестами. Программная раскладка теперь воспроизводима, но точность на конкретном принтере всё ещё нельзя гарантировать без пробного листа и физической калибровки: драйвер, подача бумаги и механический skew находятся вне приложения.

Самые опасные проблемы исходного baseline (выполненные отмечены):

1. ✅ Для двух и более листов все лицевые страницы выводились раньше всех оборотных (`BUG-PRINT-001`). Исправлено через модель физических листов и interleaved page sequence.
2. ✅ Ячейки оборота зеркалились по столбцам и одновременно безусловно поворачивались на 180° (`BUG-PRINT-002`). Теперь transform зависит от long/short edge и не переворачивает текст.
3. ✅ Кириллица в названии колоды попадала в `Content-Disposition` без RFC 5987 и роняла реальный Werkzeug WSGI (`BUG-HTTP-003`). Исправлено через `send_file/download_name`.
4. ✅ TeX внутри математических delimiters проходил без allowlist, а компилятор не получал явный `-no-shell-escape` (`BUG-SEC-001/002`). Оба слоя закрыты тестируемым контрактом.
5. ✅ JSON persistence защищён атомарной заменой, fsync, backup и блокировкой полного read–modify–write; повреждение больше не маскируется пустыми данными (`BUG-DATA-002/004`).

## 2. Как проводилась проверка

- прочитаны domain, use cases, adapters, Flask routes, Jinja, CSS и JavaScript;
- проверены git-история и незакоммиченные изменения; пользовательские изменения не откатывались;
- исходный `venv` признан непереносимым (`IntxLNK`, `Exec format error`), создан отдельный `.venv` на Python 3.13.5;
- выполнены unit/contract/integration-тесты с реальным `pdflatex` и `pdfinfo`;
- Flask поднят с отдельной базой в `/tmp`, UI пройден Chromium через pyppeteer;
- сгенерирован настоящий A4 PDF, обе страницы растеризованы и визуально проверены;
- проверены зависимости через `pip check` и исходники через `git diff --check`.

Текущая автоматизированная база: 349 проходящих основных тестов, 0 `xfail`, один отдельно успешно пройденный browser E2E; общий branch coverage составляет 98.33% при обязательном CI-пороге 98%. Chromium-сценарий проверяет в том числе focus/scroll результата preflight и подтверждает только локальные resource URLs. Физический прогон на нескольких моделях принтеров ещё обязателен: PDF/raster-проверка не моделирует driver margins, feed skew и аппаратный duplex offset.

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
| ~~BUG-DATA-002~~ ✅ | Invalid JSON превращался в `[]`, скрывая аварию и открывая путь к перезаписи. | Выполнено: missing/corrupt/schema errors останавливают операцию, HTTP не раскрывает путь; CLI восстанавливает выбранный файл из `.bak`, сохраняя `.broken-*`. | Byte/schema/missing/recovery fault tests подтверждают отказ без потери повреждённого оригинала. |
| ~~BUG-DATA-004~~ ✅ | `_write_json` писал прямо в live-файл. | Выполнено: `NamedTemporaryFile` в том же каталоге → flush/fsync → backup → `os.replace`; lock охватывает полный read-modify-write. | Fault injection сохраняет прежний live JSON; concurrent stress не теряет добавления. |

### P1 — нарушение данных, API и ключевых пользовательских сценариев

| ID | Дефект | Исправление и тест |
|---|---|---|
| ~~BUG-DATA-001~~ ✅ | `card_ids` синхронизировались только при различии длины. | Выполнено: сравнивается полный упорядоченный список; equal-length stale IDs покрыты регрессией. |
| ~~BUG-DATA-003 / BUG-WEB-001~~ ✅ | Запись в несуществующую колоду создавала orphan JSON; API отвечал success. | Выполнено: repository под lock проверяет существование до записи; API возвращает 404, HTML безопасно перенаправляет. |
| ~~BUG-DATA-005~~ ✅ | Deep-clone создавал новые UUID, но терял `parent_id` карточек. | Выполнено через `Card.clone()`; lineage contract стал обычным тестом. |
| ~~BUG-ARCH-001~~ ✅ | Старый `JsonFileStorage` импортировал удалённый `StorageBackend`; весь старый test collection раньше падал. | Выполнено на этапе 0: неиспользуемые `JsonFileStorage` и `FlaskSessionRepository` удалены вместе с устаревшими тестами; активным остаётся один `JsonRepository`. |
| ~~BUG-UI-001~~ ✅ | Drag-and-drop вызывал `renumberRows()` до построения permutation и отправлял identity order. | Выполнено: DOM и API используют card UUID, payload строится до renumber, failure восстанавливает прежний DOM без reload, stale version даёт 409; browser E2E проходит. |
| ~~BUG-IMP-001~~ ✅ | UI обещал `||`, parser делил по первому одиночному `|`. | Выполнено: exact `||`, `\||` и `\\`; parser/UI/docs и regression tests синхронизированы. |
| ~~BUG-IMP-002~~ ✅ | UI обещал `;`, `csv.reader` использовал `,`. | Выполнено: auto/explicit comma-semicolon-tab, UTF-8/BOM, header toggle, read-only preview, rejected counts и atomic reject. |
| ~~BUG-PDF-001~~ ✅ | Partial PDF считался успехом даже при non-zero exit code. | Выполнено: обязательный return code 0, наличие PDF, safe failure flags и fallback stdout/stderr log. |
| ~~BUG-LIMIT-001~~ ✅ | `max_cards=200` не использовался. | Выполнено: единый quota в use cases и web/API; bulk/CSV проверяют будущую ёмкость до сохранения. |
| ~~BUG-CONF-001~~ ✅ | База зависела от process CWD. | Выполнено: стабильный абсолютный `app/data`, override через `DIDACTIC_CARDS_DATA_DIR`; оба CWD дают один путь. Диагностика legacy-каталогов остаётся частью миграции. |
| ~~BUG-CONF-002~~ ✅ / BUG-VAL-001 | Layout теперь проверяет positive/finite dimensions, frame inset и попадание сетки в printable A4. Прямой вызов use case с `cards_per_page=0` ещё требует отдельной защиты. | Config/renderer validation выполнена; добавить invariant в `CardDeck.padded`/use case. |
| ~~BUG-CONF-003~~ ✅ | `create_app()` не принимал config/dependencies. | Выполнено: фабрика принимает config, data_dir, repository, renderer и compiler; профиль production остаётся этапом deployment. |
| ~~BUG-HTTP-001~~ ✅ | Удаление карточки было доступно через GET. | Выполнено: HTML fallback принимает только POST + CSRF, AJAX использует DELETE JSON API. |
| ~~BUG-HTTP-002~~ ✅ | Ошибка компиляции возвращала 200. | Validation и compile failure теперь возвращают 422. Разделение tool failure на 503/504 и sanitization log остаются в этапе 2. |
| ~~BUG-WEB-002/003~~ ✅ | Число вместо string и `order=None` давали необработанные исключения. | Выполнено: JSON shape/type/version validation возвращает 400; UUID membership и stale version дают 400/409. |
| ~~BUG-SEC-003~~ ✅ | HTML-формы не имели CSRF. | Выполнено: per-session token, constant-time comparison, 400 без token; JSON API отделён по Content-Type/method contract. |

### P2 — надёжность, эксплуатация и качество UX

- ✅ `BUG-UI-002`: видимый хвост `HTML` после `</html>` удалён и покрыт проверкой всех templates.
- ✅ `BUG-UI-003`: MathJax 3.2.2 + fonts vendored локально, CDN удалён из templates/CSP, есть status/fallback.
- ✅ Добавлен inline preview реально скомпилированного PDF; визуальный front/back overlay остаётся дальнейшим улучшением.
- ✅ Устранена двусмысленность между профилем приложения и настройкой драйвера; добавлен скачиваемый двухстраничный A4 calibration PDF с пятью парами мишеней, линейкой 100 мм и формулами коррекции X/Y. Автоматическое распознавание скана остаётся в roadmap.
- ✅ TeX auto-fit последовательно уменьшает шрифт до `scriptsize`; preflight показывает уменьшение warning-ом и точным измерением помечает остаточный vertical overflow как error. Controlled clipping намеренно не применяется, чтобы не скрывать потерю текста.
- Single newline в исходном тексте не равен видимому переносу LaTeX; нужен определённый markdown/rich-text contract.
- ✅ Routes адресуют карточки UUID; deck `version` защищает параллельные add/edit/delete/reorder/reset от lost update.
- Времена сохраняются UTC, но UI форматирует без зоны и без явной конвертации в локальную.
- Полный LaTeX log показывается пользователю и может раскрыть пути/служебные детали.
- `MAX_CONTENT_LENGTH=2 MiB` и `max_cards=200` реализованы; отдельные ограничения длины стороны/имени ещё нужны.
- ✅ Liveness/readiness, JSON logging, request/error IDs и HTTP/PDF duration events реализованы без записи контента карточек или внутренних путей.
- Нет backup/restore/export всей колоды и schema version/migrations.
- ✅ Активный backend переведён с JSON read-modify-write на SQLite transactions/WAL; thread/process stress tests не теряют обновления. Legacy JSON оставлен read-only источником миграции и recovery.
- ✅ Debug по умолчанию выключен и управляется строгой env-переменной; Gunicorn runtime/команда добавлены. Reverse proxy/TLS и deployment hardening зависят от окружения.
- Secret берётся из `DIDACTIC_CARDS_SECRET_KEY`, без env генерируется случайный для локального запуска; production должен задавать стабильное значение.
- UI: emoji-only actions без accessible names, слабая keyboard DnD, таблица не имеет mobile overflow, focus/disabled/loading states неполны.
- ✅ Добавлены локальный favicon, CSP и стандартные security headers; offline browser E2E проходит без внешних resource URL.
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
5. [x] Добавить independent horizontal/vertical calibration offsets для оборота, printer profile и двухстраничную тестовую страницу с измеряемыми мишенями.
6. Включить crop/registration marks; border — отдельная опция, не влияющая на размеры.
7. Добавить overflow policy: reject/warn, auto-fit до минимального font size, либо controlled clipping.
8. [x] Проверять PDF MediaBox/реальные vector bounds и raster visual diff: geometry измеряется `mutool`, front/back PBM хранятся как golden artifacts и сравниваются с допуском 0.2%.

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

1. [x] Исправить текущий JSON: absolute path, atomic replace, lock, backup, безопасный отказ при corruption, управляемое recovery и schema version.
2. [x] Проверять точное равенство `card_ids` и целостность при каждой транзакции.
3. [x] Добавить startup integrity scan: missing/orphan/duplicate IDs, invalid timestamps, recovery report без автоматической потери данных.
4. [x] Перейти на SQLite: `decks`, `cards`, `deck_cards(position)`, foreign keys, transactions, schema version и одноразовая миграция JSON с backup. Реальная рабочая база сверена 4/4 колоды и 8/8 карточек; legacy `card-id-mismatch` перенесён как warning без изменения источника.
5. [x] Все UI/API карточные операции адресовать card UUID + optimistic deck version, а не индексом.

Выход: concurrent add/edit/reorder stress test без lost updates; kill/fault injection не портит последнюю подтверждённую версию; старые JSON мигрируются один раз с backup.

### Этап 4. Исправить импорт и web UX

1. [x] Единая спецификация bulk delimiter; UI, parser, escaping и docs синхронизированы.
2. [x] CSV wizard: dialect/encoding/header preview, validation counts и atomic rollback.
3. [x] DnD на stable IDs; keyboard reorder; loading/error/rollback states.
4. [x] Локальный MathJax bundle и явный status/fallback typesetting.
5. [ ] Preview: [x] inline PDF конкретного print job; [ ] front/back raster overlay с совмещением.
6. [ ] Accessibility: [x] aria-label, keyboard reorder, focus, responsive table; [ ] полный screen-reader/contrast/dialog audit.

Выход: Playwright/pyppeteer E2E проходит create → import → reorder → edit → generate → reload; порядок и формулы сохраняются.

### Этап 5. Функции после стабилизации

Приоритетный roadmap:

1. [x] Config-defined и сохраняемые SQLite-профили, выбор на print job, двухстраничный калибровочный PDF и web workflow с X/Y offsets. Фактические значения пользователь получает по пяти парам мишеней и контрольному отрезку 100 мм.
2. Выбор формата A4/Letter, ориентации, сетки, внешнего размера карточки, margins/gaps/bleed/safe area.
3. [x] Двусторонний PDF и два отдельных файла front/back для принтеров без duplex. Раздельные документы сохраняют одинаковую нумерацию физических листов, back permutation и калибровочные offsets; unit, HTTP и реальный `pdflatex` page-count test проходят.
4. [x] Импорт/экспорт колоды в versioned JSON schema 1 и UTF-8-BOM CSV; импорт создаёт транзакционную копию с lineage, validation/quota и без перезаписи существующих данных. Полный backup/restore всей базы остаётся эксплуатационным пунктом.
5. Шаблоны оформления: шрифт, выравнивание, размер, фон, изображения/QR после отдельной security-модели.
6. [x] Auto-fit и preflight: 12pt → small → footnotesize → scriptsize, адресный vertical/horizontal overflow, missing glyphs, unsupported formulas и printable-area warning. Результат получает focus и прокручивается в видимую область; layout-only `Overfull hbox` игнорируется. Silent clipping отклонён как риск потери текста.
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
