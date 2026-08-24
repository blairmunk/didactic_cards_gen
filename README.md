# Didactic Cards Generator

Локальное Flask-приложение для создания колод дидактических карточек и сборки A4 PDF через LaTeX. У карточки есть лицевая сторона (задание), оборотная сторона (ответ) и сохраняемая секция/тема; данные сохраняются транзакционно в SQLite. Поддерживаются пакетный ввод, CSV, формулы, сортировка, клонирование колод, сохраняемые профили принтера, двухстраничный калибровочный PDF, duplex PDF и отдельные PDF лиц/оборотов для ручной двусторонней печати.

> Статус после remediation 24.08.2026: duplex-порядок, long-edge/short-edge transforms, поворот оборота 0°/180°, calibration offsets/лист, registration marks, auto-fit и адресный preflight реализованы. SQLite schema 10 и JSON export schema 7 жёстко разделяют два типа колод. Обычная колода использует экранированный текст, профили типографики, динамические `{{ card_number }}/{{ card_count }}` и семантические верхний/нижний колонтитулы с опциональными линиями. В Advanced-колоде каждая сторона сразу является raw trusted TeX; общая versioned-оболочка и её trusted-колонтитулы необязательны, а весь print job всегда идёт только в sandbox. Тип выбирается при создании и не смешивается в UI или renderer. Гарантия точности всё ещё требует физического прогона; актуальный прогресс — в [плане ревизии](docs/REMEDIATION_PLAN.md).

Для физической приёмки используйте встроенный калькулятор компенсации на странице профилей и заполните [протокол long-edge / short-edge / ручной подачи](docs/PHYSICAL_PRINT_ACCEPTANCE.md). Этап считается завершённым только после реальных измерений.

![Редактор колоды](docs/images/deck-editor.png)

## Быстрый запуск

Требуется Python 3.11+ и `pdflatex`. Для Debian/Ubuntu TeX-зависимости можно установить так:

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

Откройте <http://127.0.0.1:5000>. Для одноразового локального запуска secret можно не задавать — он будет создан автоматически, но браузерная сессия сбросится после рестарта. База всегда находится в `app/data`, независимо от рабочего каталога. Другой абсолютный каталог можно задать переменной `DIDACTIC_CARDS_DATA_DIR`.

Для production не используйте встроенный Flask-сервер:

```bash
export DIDACTIC_CARDS_SECRET_KEY='replace-with-a-long-random-value'
export DIDACTIC_CARDS_DATA_DIR='/srv/didactic-cards/data'
.venv/bin/gunicorn --chdir app --workers 2 --bind 127.0.0.1:8000 'run:create_app()'
```

`GET /health/live` проверяет процесс, `GET /health/ready` — целостность/доступность SQLite write-транзакции и наличие TeX executable. Debug по умолчанию выключен; `DIDACTIC_CARDS_DEBUG=true` предназначен только для локальной диагностики. Ответы получают `X-Request-ID`, а HTTP и PDF timing пишутся однострочными JSON-событиями без текста карточек и внутренних путей.

Advanced/trusted LaTeX на Linux требует `bubblewrap` (`sudo apt install bubblewrap`) и явного `DIDACTIC_CARDS_TRUSTED_LATEX_ENABLED=true`. Bubblewrap (`bwrap`) запускает `pdflatex` в отдельных Linux namespaces: без сети, secrets, каталога проекта и базы, с read-only TeX runtime и одним временным writable-каталогом. Это не виртуальная машина и не нужно обычным колодам. При включённом deployment-разрешении тип выбирается в форме создания; готовую колоду нельзя незаметно переключить. Advanced-колода печатает raw TeX даже без общей оболочки; недоступная изоляция блокирует её печать, а не переводит в safe-режим. Для сетевого deployment обязательна внешняя аутентификация: встроенной модели пользоватей пока нет. Полная карта controls — в [аудите UI](docs/UI_FUNCTIONALITY_AUDIT.md).

Если существующий `venv` не запускается с `Exec format error`, не переиспользуйте его: это непереносимый Windows/WSL link-файл. Создайте `.venv` командами выше.

## Проверки

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m pytest --cov=didactic_cards --cov=run --cov=config --cov-branch
```

Все исходные regression-контракты переведены из `xfail` в обычные тесты. Текущая база — 568 тестов, включая Chromium E2E, без `xfail`; branch coverage не ниже обязательного порога 98%. Реальные PDF проходят vector geometry, матрицу выравнивания 3×3, верхнюю/нижнюю полосы с custom typography, динамическую нумерацию, линии, section sheet-break, raster golden diff, overflow, A4-калибровку и hostile-набор sandbox compiler. E2E отдельно проходит safe-оформление, direct raw Advanced, trusted-колонтитулы, карантин/approval оболочки, sandbox-routed PDF и calibration UI. GitHub Actions проверяет Python 3.11–3.13 и TeX/bubblewrap-интеграцию.

Проверить целостность хранилища без автоматического исправления:

```bash
python scripts/check_storage.py
```

Команда автоматически проверяет активный `cards.sqlite3` через SQLite integrity/foreign-key checks и возвращает код `1` при проблеме. До первой миграции она проверяет legacy JSON: missing/orphan/duplicate ID, timestamps, метаданные и версию схемы. Порядок контролируемого восстановления JSON описан в руководстве.

## Структура

```text
app/
  run.py                     # фабрика и dev-запуск Flask
  config.py                  # формат A4 и параметры LaTeX
  didactic_cards/
    domain/                  # Card, Deck, CardDeck и интерфейсы
    use_cases/               # операции над колодами и документом
    adapters/                # SQLite, legacy JSON/recovery, LaTeX, compilers
    web/                     # routes, шаблоны, CSS и JavaScript
  data/                      # cards.sqlite3 и сохранённый legacy JSON
  tests/                     # unit, web, TeX/PDF integration и browser E2E
docs/
  USER_AND_TECHNICAL_GUIDE.md
  REMEDIATION_PLAN.md
```

Пользовательская и техническая инструкция находится в [docs/USER_AND_TECHNICAL_GUIDE.md](docs/USER_AND_TECHNICAL_GUIDE.md). Проект распространяется по GPL-3.0; полный текст — в [LICENSE](LICENSE).
