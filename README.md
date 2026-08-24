# Didactic Cards Generator

Локальное Flask-приложение для создания колод дидактических карточек и сборки A4 PDF через LaTeX. У карточки есть лицевая сторона (задание), оборотная сторона (ответ) и сохраняемая секция/тема; данные сохраняются транзакционно в SQLite. Поддерживаются проверяемый пакетный ввод, строгий mode-aware CSV, формулы, сортировка, клонирование колод, сохраняемые профили принтера, двухстраничный калибровочный PDF, duplex PDF и отдельные PDF лиц/оборотов для ручной двусторонней печати.

> Статус после remediation 24.08.2026: duplex-порядок, long-edge/short-edge transforms, поворот оборота 0°/180°, calibration offsets/лист, registration marks, auto-fit и адресный preflight реализованы. SQLite schema 14 и JSON export schema 8 жёстко разделяют два неизменяемых типа колод. Legacy backend, старые JSON-схемы, positional CSV/bulk и preset прежней вёрстки удалены; SQLite поддерживает только явную цепочку 12→13→14 с проверенным pre-migration backup. Обычная колода использует экранированный текст, явные строки/абзацы с одинаковой HTML/PDF-семантикой, профили типографики, динамические `{{ card_number }}/{{ card_count }}` и семантические верхний/нижний колонтитулы с опциональными линиями. В Advanced-колоде каждая сторона является raw trusted TeX; для лица и оборота можно независимо версионировать необязательные оболочки. Только у Advanced-карточки доступны raw-поля `upper_header/lower_header`: их расположение, линии и оформление полностью определяет raw-оболочка. Safe API/JSON/persistence отклоняют даже whitespace-only значение этих полей, поэтому невидимые данные не создаются и Safe CSV ничего не теряет молча. Strict CSV переносит все пять полей Advanced-карточки без изменения raw-значений, требует подписанный preview и подтверждение доверия. Весь Advanced print job всегда идёт только в sandbox. Тип выбирается при создании и не смешивается в UI, use cases, storage или renderer. Гарантия точности всё ещё требует физического прогона; актуальный прогресс — в [плане ревизии](docs/REMEDIATION_PLAN.md).

Для физической приёмки используйте встроенный калькулятор компенсации на странице профилей и заполните [протокол long-edge / short-edge / ручной подачи](docs/PHYSICAL_PRINT_ACCEPTANCE.md). Этап считается завершённым только после реальных измерений.

![Редактор колоды](docs/images/deck-editor.png)

## Быстрый запуск

Требуется Linux, Python 3.11+ и `pdflatex`. Для Debian/Ubuntu TeX-зависимости можно установить так:

```bash
sudo apt update
sudo apt install texlive-latex-base texlive-latex-extra texlive-lang-cyrillic
```

Из корня репозитория:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export DIDACTIC_CARDS_SECRET_KEY='replace-with-a-long-random-value'
python app/run.py
```

Для локального запуска переменную можно не задавать: приложение один раз создаст
общий для всех workers файл `app/data/.secret_key` с правами `0600`. В production
задавайте стабильный `DIDACTIC_CARDS_SECRET_KEY` через окружение или secret manager.
Новый data directory создаётся с правами `0700`; SQLite main/WAL/SHM и
локальный secret имеют `0600`.

Откройте <http://127.0.0.1:5000>. Локальный secret сохраняется между рестартами в каталоге данных. База всегда находится в `app/data`, независимо от рабочего каталога. Другой абсолютный каталог можно задать переменной `DIDACTIC_CARDS_DATA_DIR`.

Каталог данных должен быть на локальной Linux/POSIX-файловой системе одного
хоста. Persistence и maintenance CLI опираются на `fcntl/flock`, hard links,
directory `fsync` и SQLite WAL; Windows, NFS/SMB и multi-host shared volume не
поддерживаются. В WSL храните данные в Linux-файловой системе, а не на
смонтированном Windows-диске.

Для production не используйте встроенный Flask-сервер:

```bash
export DIDACTIC_CARDS_SECRET_KEY='replace-with-a-long-random-value'
export DIDACTIC_CARDS_DATA_DIR='/srv/didactic-cards/data'
.venv/bin/gunicorn --chdir app --workers 2 --bind 127.0.0.1:8000 'run:create_app()'
```

`GET /health/live` проверяет процесс, `GET /health/ready` — целостность/доступность SQLite write-транзакции и наличие TeX executable. Debug по умолчанию выключен; `DIDACTIC_CARDS_DEBUG=true` предназначен только для локальной диагностики. Ответы получают `X-Request-ID`, а HTTP и PDF timing пишутся однострочными JSON-событиями без текста карточек и внутренних путей.

Advanced/trusted LaTeX на Linux требует `bubblewrap` (`sudo apt install bubblewrap`) и явного `DIDACTIC_CARDS_TRUSTED_LATEX_ENABLED=true`. Bubblewrap (`bwrap`) запускает `pdflatex` в отдельных Linux namespaces: без сети, secrets, каталога проекта и базы, с read-only TeX runtime и одним временным writable-каталогом. Это не виртуальная машина и не нужно обычным колодам. При включённом deployment-разрешении тип выбирается в форме создания; готовую колоду нельзя незаметно переключить. Advanced-колода печатает raw TeX даже без оболочек сторон; недоступная изоляция блокирует её печать, а не переводит в safe-режим. Для сетевого deployment обязательна внешняя аутентификация: встроенной модели пользователей пока нет. Полная карта controls — в [аудите UI](docs/UI_FUNCTIONALITY_AUDIT.md).

Если существующий `venv` не запускается с `Exec format error`, не переиспользуйте его: это непереносимый Windows/WSL link-файл. Создайте `.venv` командами выше.

## Проверки

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m pytest --cov=didactic_cards --cov=run --cov=config --cov-branch
```

Все regression-контракты являются обычными тестами без `xfail`; обязательный порог branch coverage — 98%. Набор включает SQLite migration/backup/restore и WAL-concurrency, Chromium E2E, strict safe/Advanced CSV, hostile sandbox fixtures, реальную TeX-сборку, vector geometry, матрицу выравнивания 3×3, динамическую нумерацию, section breaks, raster golden diff, overflow и A4-калибровку. GitHub Actions проверяет Python 3.11–3.13 и TeX/bubblewrap-интеграцию; фактическое число тестов и coverage фиксируются результатом текущего CI-прогона.

В обычной колоде один Enter внутри стороны карточки создаёт видимый перенос строки,
а пустая строка — новый абзац; выбранный межабзацный интервал применяется и в
HTML-preview, и в PDF. Внешние пустые строки не добавляют искусственный отступ.
Исходная строка остаётся неизменной в SQLite/CSV/JSON, а CRLF/CR приводятся к LF
только на границе Safe-отображения. Advanced/raw TeX этим правилом не затрагивается.

Проверить активную базу без создания отсутствующего файла и без изменения данных:

```bash
python scripts/storage.py inspect
```

`sha256` в отчёте — fingerprint только основного SQLite-файла.
`logical_sha256` вычисляется по схеме и строкам одного read-snapshot, поэтому
учитывает committed-состояние из активного WAL. Для переносимого автономного
snapshot всё равно используйте `backup`.

Создать проверенный online backup можно при работающем приложении:

```bash
python scripts/storage.py backup
# или в явное место, которое ещё не существует:
python scripts/storage.py backup --output /safe/place/cards.sqlite3
```

Рядом с копией создаётся `*.manifest.json` с schema version, размером,
байтовым `sha256` и логическим `logical_sha256`. Hard-link publication атомарно
отказывает при уже существующем файле или
manifest, не перезаписывая их. Для restore остановите
все workers, затем явно подтвердите операцию:

```bash
python scripts/storage.py restore /safe/place/cards.sqlite3 --yes
```

Runtime lock отклонит restore, если хотя бы один worker ещё работает. До
замены live-базы команда потребует соседний manifest, проверит оба файла и
автоматически создаст pre-restore backup. При старте schema 12 база автоматически
бэкапится в один stable-файл перед цепочкой 12→13→14; при старте schema 13 — перед
миграцией 13→14. Последняя очищает только ошибочно сохранённые raw-колонтитулы
Safe-карточек, оставляя исходные значения в backup, и не меняет Advanced-данные.
Перед upgrade остановите **все** workers schema 12/13: rolling upgrade со
смешанными версиями небезопасен, потому что старый процесс не знает текущего
инварианта и миграционного протокола.

По умолчанию restore fail-closed откажется заменять unhealthy live DB. Для явного
disaster recovery после остановки всех workers и проверки backup используйте:

```bash
python scripts/storage.py restore /safe/place/cards.sqlite3 \
  --yes --allow-unhealthy-live
```

До замены CLI побайтово скопирует main/WAL/SHM/journal в private
`app/data/backups/forensic-*` и вернёт путь в JSON-поле `forensic_bundle`.
Для другого каталога данных добавьте
`--data-dir /absolute/path` к любой команде.

## Структура

```text
app/
  run.py                     # фабрика и dev-запуск Flask
  config.py                  # формат A4 и параметры LaTeX
  didactic_cards/
    domain/                  # Card, Deck, CardDeck и интерфейсы
    use_cases/               # операции над колодами и документом
    adapters/                # SQLite, LaTeX и compilers
    web/                     # routes, шаблоны, CSS и JavaScript
  data/                      # cards.sqlite3
  tests/                     # unit, web, TeX/PDF integration и browser E2E
docs/
  USER_AND_TECHNICAL_GUIDE.md
  REMEDIATION_PLAN.md
  REMEDIATION_HISTORY.md
  BULK_CSV_IMPORT_AUDIT.md
scripts/
  storage.py                 # inspect, online backup, offline restore
```

Пользовательская и техническая инструкция находится в [docs/USER_AND_TECHNICAL_GUIDE.md](docs/USER_AND_TECHNICAL_GUIDE.md). План строгого импорта готовых Advanced-карточек из другой программы или нейросети — в [docs/BULK_CSV_IMPORT_AUDIT.md](docs/BULK_CSV_IMPORT_AUDIT.md). Проект распространяется по GPL-3.0; полный текст — в [LICENSE](LICENSE).
