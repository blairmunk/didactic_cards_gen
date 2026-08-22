# Didactic Cards Generator

Локальное Flask-приложение для создания колод дидактических карточек и сборки A4 PDF через LaTeX. У карточки есть лицевая сторона (задание) и оборотная сторона (ответ); данные сохраняются транзакционно в SQLite, поддерживаются пакетный ввод, CSV, формулы, сортировка, клонирование колод, сохраняемые профили калибровки принтера, duplex PDF и отдельные PDF лиц/оборотов для ручной двусторонней печати.

> Статус после начала remediation 22.08.2026: программный порядок duplex-страниц исправлен (`front-1, back-1, front-2, back-2`), добавлены long-edge/short-edge transforms, X/Y calibration offsets, registration marks, TeX auto-fit и preflight содержимого, а заданный размер проверяется по векторной геометрии PDF. Формулы ограничены безопасным учебным подмножеством TeX, русский filename отдаётся по RFC 5987. Гарантия точности всё ещё требует физического прогона; актуальный прогресс — в [плане ревизии](docs/REMEDIATION_PLAN.md).

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

Если существующий `venv` не запускается с `Exec format error`, не переиспользуйте его: это непереносимый Windows/WSL link-файл. Создайте `.venv` командами выше.

## Проверки

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m pytest --cov=didactic_cards --cov=run --cov=config --cov-branch
```

Все исходные regression-контракты переведены из `xfail` в обычные тесты. Текущая база — 343 проходящих теста без `xfail`, branch coverage 98.59%; отдельный Chromium E2E успешно проходит полный offline workflow. Реальные PDF также проходят vector geometry и двухстраничный raster golden diff. GitHub Actions проверяет Python 3.11–3.13 и отдельно запускает TeX-интеграцию с обязательным порогом 98%.

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
