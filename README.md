# Didactic Cards Generator

Локальное Flask-приложение для создания колод дидактических карточек и сборки A4 PDF через LaTeX. У карточки есть лицевая сторона (задание) и оборотная сторона (ответ); данные сохраняются в JSON, поддерживаются пакетный ввод, CSV, формулы, сортировка, клонирование колод и PDF на 8 карточек на лист.

> Статус после аудита 22.08.2026: редактор и одностраничная PDF-генерация работают, но приложение пока нельзя считать готовым к гарантированно точной двусторонней печати. Для колод больше 8 карточек страницы расположены в неверном duplex-порядке, обороты всегда повёрнуты на 180°, а скачивание PDF с кириллическим названием колоды ломает реальный dev-сервер. Подробности и последовательность исправлений — в [плане ревизии](docs/REMEDIATION_PLAN.md).

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
cd app
../.venv/bin/python run.py
```

Откройте <http://127.0.0.1:5000>. Запуск именно из каталога `app` важен для текущей версии: путь к `data/` вычисляется относительно рабочего каталога процесса.

Если существующий `venv` не запускается с `Exec format error`, не переиспользуйте его: это непереносимый Windows/WSL link-файл. Создайте `.venv` командами выше.

## Проверки

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m pytest --cov=didactic_cards --cov=run --cov=config --cov-branch
```

На момент ревизии: 96+ обычных сценариев проходят, подтверждённые дефекты зафиксированы строгими `xfail`-тестами. Реальная интеграционная проверка вызывает локальные `pdflatex` и `pdfinfo`.

## Структура

```text
app/
  run.py                     # фабрика и dev-запуск Flask
  config.py                  # формат A4 и параметры LaTeX
  didactic_cards/
    domain/                  # Card, Deck, CardDeck и интерфейсы
    use_cases/               # операции над колодами и документом
    adapters/                # JSON, LaTeX, pdflatex/xelatex
    web/                     # routes, шаблоны, CSS и JavaScript
  data/                      # JSON-данные при запуске из app/
  tests/                     # unit, web, integration и xfail-контракты
docs/
  USER_AND_TECHNICAL_GUIDE.md
  REMEDIATION_PLAN.md
```

Пользовательская и техническая инструкция находится в [docs/USER_AND_TECHNICAL_GUIDE.md](docs/USER_AND_TECHNICAL_GUIDE.md). Проект распространяется по GPL-3.0; полный текст — в [LICENSE](LICENSE).
