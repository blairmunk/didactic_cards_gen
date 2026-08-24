# План багфиксов, стабилизации и развития

Дата исходной ревизии: 22 августа 2026 года. Повторная ревизия выполнена на `main` после коммита `eeb7ead`: перепроверены актуальная SQLite-модель, web UX, TeX-рендеринг, реальные PDF и история первой версии приложения.

## Статус выполнения

- [x] Baseline аудита зафиксирован коммитом `39359a0`.
- [x] Этап 0: удалены неиспользуемые сломанные JSON/session adapters и их устаревшие тесты.
- [x] Этап 0: нормализованы executable-биты исходников, данных и документации.
- [x] Этап 0: добавлена CI-матрица Python 3.11–3.13 и отдельный TeX/coverage job.
- [ ] Этап 1: модель физического листа и корректная duplex-раскладка.
  - [x] Введены `Sheet`, `DuplexMode` и независимые long-edge/short-edge transforms.
  - [x] Страницы чередуются по физическим листам: `F1,B1,F2,B2…`.
  - [x] Перестановка ячеек отделена от поворота содержимого: профиль выбирает 0°/180°, legacy long-edge по умолчанию восстановлен на 180°.
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
  - [x] Активное хранилище мигрировано в SQLite schema 11 с FK, WAL, транзакциями, одноразовым backup/import legacy JSON, профилями принтера, секциями, authoring mode, presentation/typography settings, колонтитулами карточек и quarantine пар Advanced-оболочек.
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
  - [x] Web calibration workflow сохраняет валидированные пользовательские профили в SQLite schema 3.
  - [x] Страница профилей генерирует двухстраничный A4 calibration PDF с мишенями, контрольным отрезком 100 мм и инструкцией знаков X/Y для обоих duplex-режимов.
  - [x] TeX auto-fit уменьшает 12pt → small → footnotesize → scriptsize и оставляет адресные preflight markers.
  - [ ] Расширенная layout-конфигурация; controlled clipping намеренно не включён, остаточный overflow блокирует готовность preflight.
- [ ] Этап 6: секции, оформление карточек и trusted LaTeX.
  - [x] Повторная ревизия и сравнение с первой версией зафиксированы в разделе 7.
  - [x] Исправлены обычные регистрационные метки; независимый raster oracle проверяет четыре креста/восемь штрихов на обеих страницах при одном проходе TeX.
  - [x] Восстановлен legacy long-edge 180° и добавлена независимая настройка поворота оборота 0°/180° в профилях и калибровочном листе.
  - [x] Printable-area warning учитывает полный bounding box на физическом A4 и оба знака offsets.
  - [x] Физический профиль принтера отделён от versioned `DeckRenderSettings` колоды на уровне модели и schema 4.
  - [x] Добавлена сохраняемая секция/тема карточки с clone/import/export/migration-контрактами.
  - [x] Добавлен управляемый колонтитул из секции: front/back/both, top/bottom, собственное выравнивание и отдельный overflow.
  - [x] Добавлены безопасные presets и вертикальное/горизонтальное выравнивание 3×3 с единым HTML/PDF-контрактом.
  - [x] Добавлены повтор колонтитула на каждой карточке/только при смене секции и физические section breaks: continuous/new row/new sheet до duplex permutation.
  - [x] Добавлен закрытый по умолчанию trusted foundation: feature flag, schema 6 quarantine/provenance, строгий job protocol и изолированный bubblewrap compiler worker.
  - [x] Этап 6.5: advanced UI, escaped/raw для каждой стороны, test compile, история/approval/reset, schema 7, JSON export schema 4 и immutable print snapshot подключены к sandbox-печати.
  - [x] Этап 6.8: колоды жёстко разделены на safe/advanced; Advanced всегда печатает raw в sandbox, а versioned-оболочки front/back необязательны. Safe UI содержит только верхний/нижний колонтитулы и allowlisted-оформление; schema 11/export schema 8 сохраняют тип, оболочки и колонтитулы карточек.
  - [x] Программная подготовка 6.6: UI-калькулятор учитывает знаки long-edge/short-edge, опубликованы измеримые критерии и протокол automatic/manual прогонов.
  - [ ] Физическая часть 6.6: заполнить `PHYSICAL_PRINT_ACCEPTANCE.md` по реальным отпечаткам и записать модель принтера/остаточные отклонения.
- [x] Production runtime.
  - [x] Debug выключен по умолчанию и включается только строгой env-переменной.
  - [x] Добавлены `/health/live` и sanitised `/health/ready` для SQLite/TeX.
  - [x] Gunicorn добавлен в runtime dependencies; WSGI-команда и probe semantics документированы.
  - [x] Добавлены однострочные JSON-логи, request/error IDs и duration событий HTTP/PDF compilation без контента/путей.

## 1. Итог аудита

Вердикт после выполненной программной remediation: исходные P0-дефекты page pairing, duplex transform, cut size, TeX boundary, HTTP download и persistence закрыты обычными regression/integration-тестами. Программная раскладка теперь воспроизводима, но точность на конкретном принтере всё ещё нельзя гарантировать без пробного листа и физической калибровки: драйвер, подача бумаги и механический skew находятся вне приложения.

Самые опасные проблемы исходного baseline (выполненные отмечены):

1. ✅ Для двух и более листов все лицевые страницы выводились раньше всех оборотных (`BUG-PRINT-001`). Исправлено через модель физических листов и interleaved page sequence.
2. ✅ Исходный renderer смешивал перестановку ячеек и поворот содержимого (`BUG-PRINT-002`). Теперь transform зависит от long/short edge, а профиль независимо задаёт 0°/180°; после подтверждения владельца legacy long-edge снова использует 180°.
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

Текущая автоматизированная база: 569 проходящих тестов, 0 `xfail`, включая browser E2E; branch coverage не ниже обязательного CI-порога 98%. Chromium-сценарий проверяет раздельные safe/Advanced-колоды, динамические safe-колонтитулы и линии, direct raw-печать, разные Advanced-оболочки front/back, колонтитулы карточки, focus/scroll preflight и calibration UI. Физический прогон на реальном принтере ещё обязателен: PDF/raster-проверка и калькулятор не моделируют driver margins, feed skew и аппаратный duplex offset.

## 3. Реестр дефектов

### P0 — блокируют основное назначение или создают риск безопасности/потери данных

| ID | Дефект и доказательство | Исправление | Критерий приёмки |
|---|---|---|---|
| ~~BUG-PRINT-001~~ ✅ | `LatexRenderer` формировал все fronts, затем все backs. | Выполнено: `Sheet` отделяет физическую модель, PDF идёт `F1,B1,F2,B2…`; unit и реальный четырёхстраничный TeX test проходят. | Автоматизированная часть выполнена; physical matrix остаётся в этапе 1. |
| ~~BUG-PRINT-002~~ ✅ | Оборот сочетал horizontal mirror с неявным `rotatebox{180}`, затем remediation ошибочно полностью удалила поворот. | Выполнено: явные long-edge/short-edge permutations и независимый `back_rotation_deg` 0°/180°; legacy long-edge default — 180°. | Domain/config/SQLite/web/renderer tests проходят; физическая проверка комбинаций остаётся. |
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
- ✅ `BUG-UI-004`: Advanced больше не исчезает без объяснения при disabled deployment flag; главная и колода показывают состояние/команду запуска, а enabled-режим имеет прямые ссылки из списка колод.
- ✅ `BUG-UI-005`: JSON/CSV export вынесен из скрываемой пустой генерации и доступен даже для колоды без карточек, сохраняя настройки и trusted history.
- ✅ `BUG-UI-006`: сохранённый printer profile получил явное действие «Редактировать» и prefilled форму вместо ручного повторного ввода ключа/offsets.
- ✅ Инвентаризация routes → visible controls зафиксирована в `UI_FUNCTIONALITY_AUDIT.md` и обычном regression-контракте; deployment/API-only boundaries перечислены отдельно.
- ✅ Добавлен inline preview реально скомпилированного PDF; визуальный front/back overlay остаётся дальнейшим улучшением.
- ✅ Устранена двусмысленность между профилем приложения и настройкой драйвера; добавлен скачиваемый двухстраничный A4 calibration PDF с пятью парами мишеней, линейкой 100 мм и формулами коррекции X/Y. Автоматическое распознавание скана остаётся в roadmap.
- ✅ `BUG-CAL-001`: мишени первой версии calibration PDF использовали TikZ `remember picture` и при единственном проходе TeX смещались относительно текущей строки либо уходили за MediaBox. Лист переведён на фиксированный блок 186×225 мм без повторного прохода; raster regression проверяет все пять ожидаемых координат на обеих страницах A4.
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
2. [x] Базовый CSV wizard: dialect/UTF-8/header skip, preview counts и atomic rollback;
   [ ] mode-aware strict v2 и lossless Advanced round-trip — по плану
   [аудита bulk/CSV](BULK_CSV_IMPORT_AUDIT.md).
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
4. [x] Полный импорт/экспорт колоды обновлён до versioned JSON schema 8; schema 1–7 обратно совместимы, trusted history всегда импортируется как Advanced + `imported/quarantined`, без approval. Текущий UTF-8-BOM CSV сохраняет только `section/front/back` и пока не является полным Advanced round-trip: исправление вынесено в `IMPORT-CSV-V2`. Полный backup/restore всей базы остаётся эксплуатационным пунктом.
5. 🟡 Шаблоны оформления: ✅ безопасные профили шрифта/размера/начертания/интервалов и два колонтитула выполнены; фон, изображения и QR остаются после отдельной security-модели.
6. [x] Auto-fit и preflight: 12pt → small → footnotesize → scriptsize, адресный vertical/horizontal overflow, missing glyphs, unsupported formulas и printable-area warning. Результат получает focus и прокручивается в видимую область; layout-only `Overfull hbox` игнорируется. Silent clipping отклонён как риск потери текста.
7. Поиск, теги, массовое редактирование, undo/trash вместо немедленного удаления.
8. PDF metadata, deterministic builds и сохранение print job с конфигурацией для повторной печати.
9. [x] Advanced-контракт исправлен: `{{ upper_header }}` и `{{ lower_header }}` получают значения конкретной карточки, а UI версии хранит две общие raw-оболочки — отдельно для front и back.
10. [x] Верхний и нижний safe-колонтитулы опционально отделяются горизонтальной линией allowlisted-толщины и отступа.
11. [x] Встроенные `{{ card_number }}` и `{{ card_count }}` реализованы для динамических колонтитулов карточки; универсальные пользовательские поля убраны из активного roadmap и помещены в icebox до подтверждения спроса.
12. [ ] `IMPORT-CSV-V2`: строгий mode-aware CSV, пять полей Advanced-карточки, lossless raw round-trip, подробный обязательный preview и симметричный export. Полная спецификация и тестовая матрица — в [отдельном аудите](BULK_CSV_IMPORT_AUDIT.md).

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

## 7. Повторная ревизия: секции, оформление и trusted LaTeX

### 7.1. Baseline и проверенные факты

Ревизия выполнена на `main` после `eeb7ead`. Рабочее дерево до документирования было чистым, `pip check` не обнаружил конфликтов зависимостей. Основной набор — 349 тестов, один browser E2E запускается отдельно; branch coverage — 98.33% при пороге 98%. Это хороший regression baseline, но процент покрытия не заменяет независимые проверки физической геометрии PDF.

История подтверждает описанное пользователем поведение первой версии. В `4c0a39d` и последующих ранних коммитах приложение создавало внешний `minipage`, а `front`/`back` карточки вставляло внутрь почти как готовый TeX, экранируя только `#`. Поэтому в содержимом работали `minipage`, списки, отступы, `\vfill`, `\hfill` и другие команды. Явного advanced-переключателя не было: любой текст фактически считался доверенным TeX. Современная версия намеренно экранирует обычный текст и разрешает только ограниченную учебную математику; это безопаснее, но несовместимо с прежним авторским сценарием.

Подтверждённое текущее расхождение интерфейсов: декоративный HTML-preview центрирует содержимое через flexbox и `text-align: center`, а PDF использует `minipage` с параметрами `[t][height][t]`, поэтому реальный текст начинается сверху и слева. До исправления HTML-карточка не должна восприниматься как точный предпечатный просмотр; таким источником истины остаётся встроенный PDF-preview.

### 7.2. Новые дефекты и технический долг

| Приоритет / ID | Доказательство | Исправление | Критерий приёмки |
|---|---|---|---|
| ~~**P1 `BUG-PRINT-010`**~~ ✅ | Обычные регистрационные метки использовали TikZ `remember picture` и `current page`, тогда как `PdfLatexCompiler` запускает TeX один раз. Реальная сборка сообщала, что labels могли измениться, а raster показывал один крест около верхнего центра вместо четырёх крестов по краям. Существующий golden был создан тем же ошибочным кодом и закреплял дефект. | Выполнено: `eso-pic` ставит восемь штрихов четырёх крестов в абсолютных A4 shipout-координатах 5 мм от краёв без второго прохода; golden обновлён только после независимого oracle. | Один проход TeX без rerun warning; front/back raster 596×842 содержит четыре креста в ожидаемых координатах; cut size regression проходит. |
| ~~**P2 `BUG-PRINT-011`**~~ ✅ | `printable_area_warnings()` считал любое отрицательное X/Y-смещение выходом за область, даже если сетка оставалась на листе. Положительное смещение той же величины могло не дать предупреждения. | Выполнено: полный bounding box после offset сравнивается со всеми четырьмя границами физического A4 с учётом базового поля 5 мм. Несимметричные аппаратные поля остаются будущим расширением printer profile. | Boundary-тесты покрывают оба знака, обе оси и обе стороны; −5 мм допустим, фактическое пересечение каждого края предупреждается. |
| ~~**P2 `BUG-PREVIEW-004`**~~ ✅ | HTML-preview показывал центрированный текст, PDF — top-left. Пользователь принимал декоративный preview за результат печати. | Выполнено: HTML и TeX используют один `DeckRenderSettings`; UI явно называет HTML приблизительным, а PDF точным. | Chromium E2E сохраняет custom alignment/headers; реальный PDF-тест численно различает все девять координатных комбинаций. |
| ~~**P1 `BUG-PRINT-012`**~~ ✅ | Первая реализация header band помещала header- и body-minipage в одну горизонтальную строку; body первой карточки мог визуально вытечь в соседний slot, хотя TeX успешно компилировался. Кроме того, auto-fit сравнивал body с полной высотой карточки, не вычитая header band. | Выполнено: блоки разделены явной вертикальной границей `\par\nointerlineskip`; header/body остаются внутри app-owned outer minipage, а fit получает фактически доступную высоту body. | Независимый `pdftotext -bbox` regression проверяет, что `LEFTBODY` и `RIGHTBODY` находятся в соответствующих половинах реального A4 PDF; unit regression фиксирует передачу уменьшенной высоты в fit. |
| ~~**P1 `FEAT-SECTION-001`**~~ ✅ | Одного поля `Card.section` было недостаточно для реального секционирования печатной колоды: название всегда повторялось, а новая тема не могла начаться с физической строки или листа. | Выполнено: `header_repeat=every-card/section-start` и `section_break=continuous/new-row/new-sheet` сохранены в schema 5/export schema 3. Пустые slots добавляются в доменной физической раскладке до duplex permutation. | Unit-матрица проверяет row/sheet padding и оба flip modes; реальный четырёхстраничный PDF сохраняет пары `front-1/back-1/front-2/back-2`; E2E проверяет сохранение настроек и скрытие повторного header. |
| **P2 `BUG-OBS-002`** | Калибровочный route вызывает compiler напрямую и не пишет унифицированное структурированное событие `pdf_compilation` с duration/result, используемое для обычной печати. | Провести calibration job через общий сервис/observability wrapper без логирования содержимого или внутренних путей. | Success, validation, timeout и compiler failure дают одинаково коррелируемые безопасные события. |
| ~~**P2 `BUG-DATA-006`**~~ ✅ | У доменной модели и SQLite schema 3 не было секции карточки и versioned render settings колоды. JSON schema 1 и CSV сохраняли только прежнее представление. | Выполнено: schema 4 хранит `Card.section` и отдельные safe `DeckRenderSettings`; JSON schema 2 и CSV с `section;front;back` сохраняют их, а schema 1/две колонки остаются совместимыми. Каждая запись настроек увеличивает `deck.version`. | Migration 3→4 назначает существующим колодам `legacy-top-left`; новые получают `centered`; SQLite/JSON clone, JSON round-trip, CSV и stale-version tests проходят. |
| ~~**P1 `BUG-SEC-004` при наивной реализации**~~ ✅ | Простое отключение escaping для advanced возвращает возможность `\input`, `\openout`, закрытия app-owned environments, бесконечных макросов и resource exhaustion. | Выполнено: raw доступен только в отдельной feature-gated Advanced-колоде, которая всегда компилируется sandbox worker и fail closed. Bubblewrap исключает сеть, secrets и host/project mounts; действуют resource/output limits. Общая оболочка дополнительно требует quarantine/test/approval. | Hostile fixtures, direct raw и raw с оболочкой проходят в sandbox; при недоступном namespace/readiness Advanced-печать блокируется. |
| ~~**P1 `FEAT-TYPOGRAPHY-001`**~~ ✅ | Обычный режим не позволял выбрать шрифт, размер, начертание и интервалы; был только один колонтитул из секции. Наивное оборачивание содержимого пользовательской TeX-директивой могло бы смешать safe и trusted semantics. | Выполнено: allowlisted-профили `off/book/sans-large/compact/custom`, renderer-owned font-команды и две фиксированные семантические полосы: верхняя и нижняя. У каждой есть side/alignment/repeat/source/style; custom text экранируется. Любая Advanced-колода обходит built-in слой независимо от наличия оболочки. SQLite schema 9/JSON schema 6 сохраняют тип и настройки. | Unit/domain/web/storage/export tests проверяют allowlist, fixed positions, жёсткую границу режимов и round-trip; реальный `pdflatex` собирает custom body + обе полосы без overflow. |
| ~~**P1 `BUG-UX-ADV-001`**~~ ✅ | Safe/Advanced были переключателем одной колоды; built-in controls оставались рядом, а raw зависел от approved-шаблона. | Выполнено: immutable `authoring_mode` выбирается при создании; Advanced raw-содержимое не требует оболочки, а safe-слой не попадает в UI/HTML/PDF. Advanced всегда маршрутизируется в sandbox и fail closed. | Миграция 8→9, export/import/clone, route boundary, renderer source, immutable snapshot и Chromium E2E покрывают оба типа и direct raw-печать. |

Дополнительный долг, который следует закрыть вместе с этапом:

- задать доменные ограничения длины имени, описания, секции, стороны карточки и шаблона; лимит HTTP 2 MiB и максимум 200 карточек не защищают один дорогой TeX-фрагмент;
- сделать print job неизменяемым snapshot колоды, стиля, шаблона, printer profile и их версий/хэшей, чтобы параллельное редактирование не смешивало настройки в одном PDF;
- не хранить `back_border` и `registration_marks` как свойства принтера: это параметры диагностического overlay конкретной печати;
- golden raster оставить для обнаружения общих изменений, но критические размеры и координаты проверять независимыми числовыми/vector/raster assertions.

### 7.3. Целевая модель настроек

Настройки разделяются на три независимых слоя:

| Слой | Содержимое | Время жизни |
|---|---|---|
| `PrinterProfile` | duplex mode, offsets front/back, доступная область и позднее skew/scale конкретного принтера | переиспользуется разными колодами |
| `DeckRenderSettings` | preset, шрифт/размер, внутренние отступы, border/background, horizontal/vertical alignment, правила колонтитула | принадлежит колоде и входит в экспорт |
| `PrintJobOptions` | registration/crop marks, split front/back, debug overlay, выбранные profile/style versions | snapshot одной генерации |

Реализованная в этапе 6.1 schema 4 (schema 3 уже занята независимым поворотом оборота в printer profile):

- `cards.section TEXT NOT NULL DEFAULT ''` — семантическая тема карточки, общая для front/back;
- `deck_render_settings` — одна versioned запись на колоду: `mode`, preset и безопасные presentation-поля;
- trusted template source/version/hash хранится отдельно от безопасных полей, чтобы его нельзя было случайно активировать обычным импортом;
- существующие колоды получают явный preset `legacy-top-left`, чтобы миграция не изменила уже отлаженную печать; для новых колод рекомендуемый default — `centered`.

JSON export schema 8 сохраняет `authoring_mode`, section, колонтитулы карточек, safe render settings и историю пар trusted-оболочек без approval; schema 1–7 импортируются с совместимыми defaults. SQLite schema 11 хранит значения колонтитулов рядом с карточкой, а две оболочки сторон — в одной неизменяемой trusted-версии. Импортированный trusted-код делает колоду Advanced, всегда получает `imported/quarantined` и требует локального approval оболочек. CSV переносит `section;front;back`, но не тип, колонтитулы или оболочки.

### 7.4. Фича: колонтитулы и секционирование

✅ Реализованы верхний и нижний колонтитулы. Каждый строится из семантического `Card.section`, логического номера или экранированного постоянного текста, но не из произвольного TeX. Положение не выбирается: верхний всегда сверху, нижний всегда снизу. Остальные настройки:

- показывать: нигде / только front / только back / с обеих сторон;
- независимые выравнивание, семейство/размер/начертание шрифта;
- повторять значение на каждой карточке либо показывать только при смене секции;
- ✅ `section_break`: без разрыва / с новой строки сетки / с нового листа.

Внутри карточки нужно выделить фиксированную header/footer band, а основное содержимое центрировать в оставшейся области. Иначе колонтитул будет сдвигать визуальный центр каждой карточки по-разному. Длинный колонтитул имеет собственные auto-fit/overflow markers и никогда не должен молча перекрывать body.

Разрывы секций реализуются в физической модели листа, а не в TeX-renderer. Пустые slots обязаны участвовать в front/back pairing, иначе переход «с нового листа» нарушит совмещение оборота.

### 7.5. Фича: вертикальное и горизонтальное выравнивание

Вместо буквального пользовательского `\hfill`/`\vfill` безопасный renderer должен предлагать явные значения:

- horizontal: `left`, `center`, `right`;
- vertical: `top`, `center`, `bottom`;
- presets: `legacy-top-left`, `centered`, `header-centered`.

Горизонталь задаётся paragraph alignment (`\raggedright`, `\centering`, `\raggedleft`), вертикаль — фиксированной body box с top/center/bottom alignment или контролируемыми `\vfil`. Такой контракт работает для многострочного текста и display math предсказуемее одиночных glue-команд. Overflow и auto-fit измеряют header и body раздельно; уменьшение body не должно бесконечно маскировать слишком длинный header.

### 7.6. Фича: advanced / trusted LaTeX

Реализованы два явно разных и неизменяемых типа колоды, а не одна настройка «экранировать / не экранировать»:

1. `built_in` — обычный безопасный текст, allowlisted math, семантические колонтитулы и настройки оформления. Это default и единственный режим для неавторизованного/network deployment.
2. `advanced` — для локального доверенного автора. Front/back всегда вставляются как raw TeX в app-owned область карточки. Приложение владеет документом, A4-страницей, cut box и duplex geometry.

Чтобы не копировать одинаковое оформление в каждую карточку, Advanced дополнительно предлагает необязательные оболочки лица и оборота:

- два шаблона внутренней области колоды (front/back) с точными placeholders `{{ content }}`, `{{ section }}`, `{{ card_number }}`, `{{ card_count }}`, `{{ side }}`, `{{ upper_header }}`, `{{ lower_header }}`;
- `upper_header/lower_header` являются raw TeX-значениями конкретной карточки; приложение не добавляет им геометрию, линию или стиль;
- raw-интерпретацию `{{ content }}` без escaped/raw selector;
- test compile на примерной карточке, PDF-preview, понятную привязку TeX error к card/side;
- историю версий, отключение оболочки с возвратом к direct raw и показ hash активной версии.

Placeholder substitution выполняется собственным строгим заменителем, а не полноценным Jinja внутри TeX. На первом этапе запрещены custom document class, preamble и произвольное подключение пакетов. Raw preview в HTML всегда экранирует исходник; MathJax не выдаётся за preview произвольного TeX — эталоном служит только PDF из sandbox.

Sandbox-контракт: отдельный непривилегированный процесс/контейнер; пустой одноразовый job directory; read-only минимальный TeX runtime; без mount проекта, базы, env secrets и сети; `-no-shell-escape`; timeout; CPU/RAM/PID/file-size limits; ограниченный размер source/output; гарантированная очистка job directory. Без этого контракта UI advanced mode не выпускается.

**Выполнено в 6.4.** Deployment flag закрыт по умолчанию и не создаёт UI/routes. SQLite schema 6 хранит source/hash/provenance/status отдельно от безопасного JSON export; каждое новое значение сначала `quarantined`, approval явный, предыдущая активная версия отзывается, а clone никогда не наследует trust. Placeholder-язык допускает только точные `{{ content }}`, `{{ section }}`, `{{ card_number }}`, `{{ side }}` и требует ровно один `content`. Immutable job protocol фиксирует schema, UUID, source и SHA-256. Dedicated compiler запускает TeX через `bubblewrap --unshare-all --clearenv` с единственным writable job-dir, tmpfs `/tmp`, read-only runtime, `-no-shell-escape` и resource limits; readiness выполняет реальную namespace-пробу и закрывается при отказе. Hostile tests проверяют чтение `/etc`/проекта, host-write, `\write18`, рекурсию/timeout, output cap и очистку. CI устанавливает `bubblewrap` для обязательного интеграционного прогона.

**Выполнено в 6.5.** Feature-gated UI редактирует внутренний fragment с точными placeholders и независимыми escaped/raw front/back. Test compile не пишет данные; сохранение создаёт quarantined version; approval требует checkbox, повторную успешную sandbox-компиляцию и readiness. История показывает status/provenance/hash, reset отзывает active. Print/preflight/LaTeX preview используют один SQLite snapshot карточек, настроек, deck version и approved template; ошибка привязана к card/side без log leak. JSON schema 4 переносит source/provenance только в карантин. Chromium E2E проходит полный UI-путь.

**Исправлено в 6.8.** Модель 6.5 с escaped/raw selector внутри одной колоды признана смешанной и заменена на immutable `safe|advanced`. Advanced печатает raw и без approved-оболочки; оболочка осталась необязательным versioned-удобством. UI, renderer, compiler routing и snapshot invariant не допускают смешивания. Schema 8→9 бережно классифицирует существующие колоды.

### 7.7. Обязательная тестовая матрица

**Регистрационные метки и printable area**

- ровно четыре регистрационных креста (восемь штрихов) в ожидаемых координатах front/back при одном проходе TeX;
- отсутствие rerun warning и попадание всех меток в A4 MediaBox;
- offsets `0`, безопасные и гранично-опасные `±X/±Y` для обеих сторон;
- printer profile с несимметричными left/right/top/bottom margins.

**Колонтитулы**

- пустая/непустая секция, кириллица, allowlisted math, длинная строка и multiline;
- front/back/both, header/footer, смена секции внутри полного и неполного листа;
- `section_break` не нарушает slot permutation ни для long-edge, ни для short-edge;
- add/edit/bulk/CSV/JSON/clone/migration сохраняют section без потерь;
- отдельные header/body overflow markers указывают card UUID и side.

**Выравнивание**

- матрица 3×3 vertical/horizontal для пустого, короткого, multiline и display-math содержимого;
- auto-fit на каждом пороге шрифта и остаточный overflow;
- явные raster/vector bounds для front/back, а не только self-generated golden;
- HTML E2E проверяет настройки и предупреждает, что PDF-preview является точным.

**Trusted LaTeX**

- каждый placeholder, отсутствующий/повторный placeholder, Unicode и raw fragment из сценария первой версии;
- попытки `\end{minipage}`, `\input`, `\openin`, `\openout`, `\write18`, traversal и чтения известных host-файлов;
- бесконечная рекурсия, CPU/RAM/output exhaustion и timeout;
- отсутствие сети, секретов и project/database mounts в worker;
- импорт template всегда disabled до явного approval; clone/export сохраняет source и provenance, но не повышает trust;
- ошибку можно связать с card UUID/side, а провал job не оставляет временные файлы.

### 7.8. Очерёдность реализации и критерии завершения

1. **6.0 — геометрические багфиксы:** ✅ `BUG-PRINT-010`, `BUG-PRINT-011` и восстановление независимого поворота оборота выполнены через падающие контракты → исправление → координатный oracle → обновление golden. Физическая проверка профилей остаётся в 6.6.
2. **6.1 — данные и совместимость:** ✅ schema 4, `DeckRenderSettings`, `Card.section`, JSON schema 2, расширенный CSV и clone/version/migration tests выполнены. UI и TeX semantics намеренно пока не включены; старый PDF остаётся неизменным.
3. **6.2 — built-in presentation:** ✅ безопасные presets, 3×3 alignment, header/footer band, раздельный overflow и единый HTML/PDF semantic contract выполнены.
4. **6.3 — секционирование:** ✅ bulk section, повтор колонтитула на каждой карточке/только при смене и физические row/sheet breaks выполнены; preflight показывает добавленные пустые slots, а schema 4→5 сохраняет прежнее поведение.
5. **6.4 — trusted infrastructure:** ✅ feature flag, schema 6 quarantine/provenance, job protocol, namespace readiness и sandbox worker с hostile test suite выполнены без выпуска UI.
6. **6.5 — advanced UX (исторический этап):** ✅ редактор, test compile/PDF, history/approval/reset, immutable print snapshot и карантин выполнены; смешанный raw/escaped selector затем удалён в 6.8.
7. **6.6 — физическая приёмка:** 🟡 калькулятор компенсации, критерии и воспроизводимый акт подготовлены; остаётся минимум один реальный duplex-принтер в обоих flip modes и ручная подача с измерением пяти мишеней после калибровки.
8. **6.7 — safe typography:** ✅ профили `off/book/sans-large/compact/custom`, ручные font/size/weight/style/line/paragraph controls и две фиксированные полосы колонтитулов выполнены. Настройки allowlisted, custom text экранируется; hard split итогово сохраняется в schema 9/JSON schema 6.
9. **6.8 — hard mode split:** ✅ `authoring_mode`, direct raw Advanced, optional shared wrapper, fixed upper/lower headers, schema 9/JSON schema 6, safe/advanced UI separation, migration и sandbox-only compiler routing выполнены.

Программные критерии этапов 6.0–6.5, 6.7 и 6.8 выполнены. Следующий обязательный печатный инкремент — 6.6: физическая приёмка на реальном duplex-принтере в обоих flip modes и при ручной подаче; это нельзя достоверно заменить программным тестом.

### 7.9. Расширение колонтитулов

#### ✅ FEAT-HEADER-ADV-001 — колонтитулы карточек в Advanced-режиме

- Хранить две независимые общие raw-оболочки: для front и back; обе обязаны содержать `{{ content }}` ровно один раз.
- Добавить в форму каждой Advanced-карточки необязательные поля «Верхний колонтитул» и «Нижний колонтитул». Не показывать их как настройки safe-оформления.
- Подставлять значения карточки вместо `{{ upper_header }}` и `{{ lower_header }}` в выбранной оболочке стороны. Считать их доверенными LaTeX-фрагментами и пропускать через тот же sandbox/bubblewrap-контур.
- Пустой плейсхолдер должен разворачиваться в пустую строку. Приложение не должно автоматически резервировать место или навязывать оформление: геометрию определяет пользовательский Advanced-шаблон.
- Версионировать и подтверждать обе оболочки как единое состояние. Значения колонтитулов версионируются вместе с самой карточкой и входят в атомарный print snapshot.
- Добавить `{{ card_count }}` в общий набор контекстных Advanced-плейсхолдеров; существующие `{{ card_number }}`, `{{ section }}` и `{{ side }}` сохранить.

#### ✅ FEAT-HEADER-RULE-001 — горизонтальная линия

- Для безопасного режима добавить независимые переключатели разделительной линии под верхним и над нижним колонтитулом.
- Начать с ограниченного набора параметров: линия включена/выключена, толщина из allowlist и отступ из allowlist. Не принимать произвольный LaTeX или CSS.
- Учитывать линию и отступ в расчёте доступной высоты содержимого и в предпечатной проверке переполнения.
- Обеспечить одинаковую геометрию линии на лицевой и оборотной сторонах и после применения профиля оформления.
- В Advanced-режиме не добавлять линию автоматически: пользователь при необходимости описывает её в своём шаблоне.

#### ✅ FEAT-HEADER-DYNAMIC-001 — номер карточки и общее количество

Реализовать первым этапом без универсальной системы метаданных. Это закрывает основной сценарий вида `Явления · Карточка 7/21` с существенно меньшей сложностью.

- Разрешить в текстовых колонтитулах безопасного режима плейсхолдеры `{{ card_number }}` и `{{ card_count }}`.
- `card_number` вычислять после окончательной сортировки карточек; при изменении порядка нумерация должна обновляться.
- `card_count` считать по реальным карточкам колоды. Технические пустые ячейки и листы, добавленные для выравнивания двусторонней печати, в число карточек не включать.
- На лицевой и оборотной стороне одной карточки должны использоваться одинаковые номер и общее количество.
- Неизвестные плейсхолдеры считать ошибкой валидации и показывать до запуска LaTeX-компиляции.
- Обычный текст экранировать как и раньше; приложение подставляет только собственные числовые значения, не превращая безопасный колонтитул в канал произвольного LaTeX.
- Добавить в UI короткую справку и кнопку/список вставки доступных плейсхолдеров вместо требования помнить синтаксис.

**Выполнено 24.08.2026.** Safe-шаблон принимает только два точных числовых плейсхолдера и после подстановки проходит обычное экранирование. HTML-превью пересчитывает значения после add/delete/reorder, PDF считает логические карточки до sheet padding. Advanced-контекст получил тот же `card_count`. После уточнения модели Advanced имеет две versioned оболочки сторон, а значения колонтитулов принадлежат карточке. SQLite 10→11 и JSON 7→8 сохраняют новый контракт; старый общий шаблон мигрирует в обе стороны, а его общие trusted-фрагменты встраиваются в шаблон.

#### 🧊 FEAT-CARD-FIELDS-001 — пользовательские поля (icebox)

**Решение 24.08.2026:** исключено из активного плана. Основной сценарий динамического
колонтитула закрывают встроенные `card_number/card_count`, а универсальная схема полей
несоразмерно усложняет хранение, UI, шаблоны и импорт. К пункту следует возвращаться
только после подтверждённого пользовательского спроса; CSV v2 от него не зависит.

- Дать колоде возможность объявлять именованные поля, а каждой карточке — хранить значения этих полей.
- Разрешить использовать значения в колонтитулах через отдельное пространство имён плейсхолдеров; точный синтаксис утвердить до реализации, например `{{ fields.topic }}`.
- Предусмотреть тип поля, стабильный внутренний идентификатор и отображаемое название: переименование подписи не должно ломать уже созданные шаблоны.
- Заранее определить поведение для отсутствующего значения, удаления поля, дублирующихся имён и клонирования колоды.
- Включить схему полей и значения в экспорт/импорт, резервное копирование и CSV-поток, если он поддерживает редактирование карточек.
- Ограничить первый релиз строковыми значениями и безопасной текстовой подстановкой. Формулы, вычисляемые поля и произвольный LaTeX оставить вне первого этапа.
- Не делать эту систему зависимостью для `card_number/card_count`: встроенная нумерация должна появиться раньше и оставаться простой.

#### ✅ Выполненные тесты расширения колонтитулов

- unit: разбор разрешённых плейсхолдеров, отклонение неизвестных имён, экранирование значений и пустые значения;
- unit: нумерация после перестановки, удаления и добавления карточек, а также исключение технических пустых карточек из `card_count`;
- integration: одинаковый контекст лицевой и оборотной стороны, применение профиля и расчёт высоты при включённой горизонтальной линии;
- integration: Advanced-шаблон с обоими колонтитулами, пустыми значениями и `card_count`, включая проверку версии/подтверждения доверенного шаблона;
- icebox: round-trip пользовательских полей через клонирование и экспорт/импорт выполняется только если сама фича вернётся в активный roadmap;
- browser/E2E: вставка плейсхолдера из UI, понятная ошибка неизвестного плейсхолдера и PDF-пример `Явления · Карточка 7/21`;
- regression: переполнение длинного колонтитула и линии не должно менять совмещение лицевой и оборотной сторон.

### 7.10. Повторная ревизия bulk и CSV

Ревизия 24.08.2026 показала, что прежний выполненный пункт про CSV покрывал базовый
safe-сценарий, но перестал быть достаточным после жёсткого разделения типов колод и
появления колонтитулов карточки. Для Advanced CSV имеет отдельное назначение: другая
программа или нейросеть готовит значения `section/front/back/upper_header/lower_header`,
а приложение валидирует их и компонует в уже настроенной raw-колоде.

Критические подтверждённые дефекты: bulk схлопывает `\\`, CSV делает `strip()` raw-
ячеек, пятиколоночные строки отклоняются, Advanced CSV export теряет оба колонтитула,
а non-strict parsing допускает повреждённые кавычки. Пользовательские произвольные поля
не включаются в исправление.

Активный порядок работ:

1. `IMPORT-CONTRACT-001` — failing fixtures и единый mode-aware import DTO/parser.
2. `IMPORT-CSV-V2` — strict header schema, детерминированные delimiter/encoding,
   подробный preview и атомарный import.
3. `IMPORT-ADV-001` — пять Advanced-полей, character-preserving import/export,
   свежий preview и явное доверие raw-файлу.
4. `IMPORT-BULK-V2` — parser без backslash escaping, четыре Advanced-поля,
   quote-aware строки и preview с адресными ошибками.
5. `IMPORT-UX-001` — шаблоны файлов, focus/scroll, причины и warnings, документация,
   screenshots и browser E2E.

Полный перечень подводных камней, принятые решения, этапы, security boundary,
legacy-совместимость и тестовая матрица находятся в
[BULK_CSV_IMPORT_AUDIT.md](BULK_CSV_IMPORT_AUDIT.md). Этот документ является источником
истины для реализации серии `IMPORT-*`.
