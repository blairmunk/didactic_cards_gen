# Аудит доступности функций в web UI

Дата исходной проверки: 23 августа 2026 года. Повторная проверка импорта: 24 августа
2026 года. Проверялись Flask routes, Jinja templates,
JavaScript controls и фактически отрисованный Chromium UI.

## Итог

Все уже реализованные прикладные сценарии имеют видимую точку входа. Колоды жёстко делятся на
«Обычные» и «Advanced» при создании. Обычная колода показывает только safe-оформление;
Advanced — raw TeX, необязательные отдельные оболочки лица/оборота и значения верхнего/нижнего колонтитула у каждой карточки, без встроенных safe-шрифтов, линий и оформления.
Deployment-флаг лишь разрешает создание/исполнение Advanced-колод и не меняет тип уже созданных.

Повторная ревизия выявила и закрыла существенное исключение из полноты UI: формы
bulk/CSV были видимы в обоих режимах, но имели несколько несовместимых контрактов.
Теперь UI показывает единственный строгий формат, отдельные safe/Advanced-наборы полей,
downloadable template, подробный обязательный preview и подтверждение доверия raw-файлу.

Повторная проверка границы режимов закрыла ещё один невидимый UI-разрыв: forged
HTML/JSON-запрос и JSON import могли записать `upper_header/lower_header` в обычную
карточку, хотя пользователь не имел ни поля просмотра, ни способа исправления. Теперь
Safe допускает только точную пустую строку на HTTP/use-case/storage boundaries,
schema 14 очищает прежние скрытые значения с pre-migration backup, а Advanced UI и
character-preserving round-trip пяти полей не изменены.

В ходе аудита исправлены пять разрывов:

1. Advanced перенесён из смешанной панели оформления в явный неизменяемый тип колоды.
2. Export JSON/CSV находился внутри скрытого блока генерации и поэтому исчезал у пустой
   колоды, хотя export хранит также настройки и trusted history.
3. Два произвольно перемещаемых numbered-колонтитула заменены на семантические верхний и нижний.
4. Backend поддерживал обновление printer profile по ключу, но в таблице был только
   Delete: для изменения приходилось вручную заново вводить ключ и все значения.
5. Raw-поля Advanced-карточки можно было подделать в Safe-запросе и сохранить без
   видимого control; теперь UI не отправляет их, а backend отклоняет fail closed.

## Матрица пользовательских функций

| Область | Функции | Видимый вход |
|---|---|---|
| Главная | Создание обычной/Advanced-колоды и versioned JSON import | Явный выбор типа в форме создания; import сохраняет тип |
| Главная | Открытие, переименование, clone, delete колоды | Текстовые действия в каждой строке |
| Главная | Printer profiles и calibration | Верхняя навигация |
| Главная | Состояние Advanced | Статус «включён/выключен»; при выключении раскрывается команда запуска |
| Колода | Название/описание и printer profiles | Верхняя навигация колоды |
| Обычная колода | Preset, выравнивание 3×3, верхний/нижний колонтитулы, `card_number/card_count`, линии, профили шрифтов/интервалов и section breaks | «Оформление обычной колоды»; дополнительные секции свёрнуты |
| Advanced-колода | Raw TeX каждой стороны; необязательная versioned-оболочка и trusted-поля верхнего/нижнего колонтитула | Отдельная Advanced-панель; safe-оформление отсутствует |
| Колода | Single add; strict bulk; mode-aware CSV с обязательным заголовком и read-only preview | Раздельные подсказки, шаблоны и поля обычной/Advanced-колоды |
| Колода | Table/visual preview, edit/delete, drag и keyboard reorder | Таблица/превью карточек |
| Колода | JSON/CSV export, включая пустую колоду | Всегда видимый раздел «Данные колоды» |
| Колода | LaTeX preview, PDF, front-only, back-only, inline PDF | «Генерация» при наличии карточек |
| Колода | Preflight с переходом к проблемной карточке | «Проверить перед печатью» |
| Printer profiles | Calibration PDF и calculator X/Y | Верхние формы страницы |
| Printer profiles | Create, edit/update и delete сохранённого профиля | Текстовые действия таблицы и prefilled editor |
| Advanced | Direct raw print; поля колонтитулов карточки; test compile, quarantine, history, approval и reset пары front/back-оболочек | Карточка и versioned Advanced editor |

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

## ✅ Закрытый UI-разрыв: импорт

Advanced CSV загружает готовые поля из другой программы или нейросети. UI показывает
пять canonical колонок, даёт скачать шаблон, отображает все поля и адресные ошибки,
требует свежий preview и подтверждение доверия raw-файлу. Обычная колода показывает
только `section/front/back`. Контракт и выполненная browser-test matrix:
[аудит bulk/CSV](BULK_CSV_IMPORT_AUDIT.md).

## Проверяемый контракт

Regression-тесты отдельно открывают safe и Advanced-колоды и запрещают смешанные controls. Также покрыты
базовые single/bulk/CSV, edit/delete/reorder, оба export, все PDF-варианты,
preview, preflight, reset и printer profile. Отдельные тесты проверяют disabled/enabled
Advanced navigation, export пустой колоды и prefilled edit printer profile. Chromium E2E
проходит реальную навигацию в Advanced editor и calibration calculator, отсутствие
raw-полей в Safe add/edit и атомарный HTTP 400 для поддельного Safe raw-поля.

Дополнительное покрытие доказывает пятиколоночный Advanced round-trip, strict malformed
input, сохранение raw whitespace/backslashes, свежесть preview и mode-aware UI. Chromium
E2E импортирует внешний Advanced CSV, подтверждает raw trust и затем создаёт PDF через
sandbox compiler.

Полный accessibility review PDF overlay остаётся отдельным пунктом roadmap: этот аудит
проверяет достижимость функций, названия controls, keyboard reorder и основные focus
transitions, но не заявляет завершённую WCAG-оценку каждой страницы.
