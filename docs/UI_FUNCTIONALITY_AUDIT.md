# Аудит доступности функций в web UI

Дата проверки: 23 августа 2026 года. Проверялись Flask routes, Jinja templates,
JavaScript controls и фактически отрисованный Chromium UI.

## Итог

Все прикладные пользовательские сценарии имеют видимую точку входа. Advanced LaTeX
остаётся закрыт deployment-флагом, но больше не исчезает бесследно: UI показывает его
состояние, причину ограничения и точную команду включения. Сам флаг намеренно нельзя
изменить из браузера, поскольку режим разрешает доверенный исполняемый TeX-код.

В ходе аудита исправлены три разрыва:

1. При выключенном Advanced UI раньше вообще не упоминал функцию; при включённом ссылка
   находилась только в форме оформления конкретной колоды.
2. Export JSON/CSV находился внутри скрытого блока генерации и поэтому исчезал у пустой
   колоды, хотя export хранит также настройки и trusted history.
3. Backend поддерживал обновление printer profile по ключу, но в таблице был только
   Delete: для изменения приходилось вручную заново вводить ключ и все значения.

## Матрица пользовательских функций

| Область | Функции | Видимый вход |
|---|---|---|
| Главная | Создание и versioned JSON import | Отдельные формы |
| Главная | Открытие, переименование, clone, delete колоды | Текстовые действия в каждой строке |
| Главная | Printer profiles и calibration | Верхняя навигация |
| Главная | Состояние Advanced | Статус «включён/выключен»; при выключении раскрывается команда запуска |
| Колода | Название/описание и printer profiles | Верхняя навигация колоды |
| Колода | Preset, выравнивание 3×3, колонтитулы и section breaks | «Оформление карточек» |
| Колода | Advanced template | Всегда видимый раздел: link при enabled, инструкция при disabled |
| Колода | Single/bulk/CSV import и read-only CSV preview | Формы добавления |
| Колода | Table/visual preview, edit/delete, drag и keyboard reorder | Таблица/превью карточек |
| Колода | JSON/CSV export, включая пустую колоду | Всегда видимый раздел «Данные колоды» |
| Колода | LaTeX preview, PDF, front-only, back-only, inline PDF | «Генерация» при наличии карточек |
| Колода | Preflight с переходом к проблемной карточке | «Проверить перед печатью» |
| Printer profiles | Calibration PDF и calculator X/Y | Верхние формы страницы |
| Printer profiles | Create, edit/update и delete сохранённого профиля | Текстовые действия таблицы и prefilled editor |
| Advanced | Test compile, quarantine, history, approval и reset | Versioned Advanced editor |

## Маршруты без отдельной страницы

Следующие routes намеренно не являются самостоятельными пользовательскими экранами:

- `/health/live` и `/health/ready` — probes для процесса/deployment;
- `/api/deck/...` — JSON endpoints кнопок, CSV preview, preflight и reorder;
- server-side add/delete routes — совместимые HTML boundaries, основной интерфейс вызывает
  эквивалентные API-действия и показывает результат без полной перезагрузки.

## Deployment-настройки, которые не должны быть browser toggles

- `DIDACTIC_CARDS_TRUSTED_LATEX_ENABLED`;
- пути/timeout TeX, data directory, secret key, debug и resource limits;
- глобальная физическая сетка, card dimensions и default auto-fit policy;
- выбор compiler adapter.

Они задают границу безопасности либо свойства процесса/всех документов. Для них UI
может показывать состояние и инструкцию, но не должен менять работающий сервер. Настройки
конкретной колоды и принтера, напротив, сохраняются через UI.

## Проверяемый контракт

Regression-тест открывает непустую колоду и требует присутствия controls для оформления,
Advanced discovery, single/bulk/CSV, edit/delete/reorder, обоих exports, всех PDF-вариантов,
preview, preflight, reset и printer profile. Отдельные тесты проверяют disabled/enabled
Advanced navigation, export пустой колоды и prefilled edit printer profile. Chromium E2E
проходит реальную навигацию в Advanced editor и calibration calculator.

Полный accessibility review PDF overlay остаётся отдельным пунктом roadmap: этот аудит
проверяет достижимость функций, названия controls, keyboard reorder и основные focus
transitions, но не заявляет завершённую WCAG-оценку каждой страницы.
