# Актуальный план стабилизации и развития

Актуализировано 24 августа 2026 года после повторной ревизии хранения,
импорта, safe/Advanced UI, TeX-контура и двусторонней печати. Это единый
текущий backlog; прежний пошаговый аудит сохранён только как
[исторический архив](REMEDIATION_HISTORY.md).

## 1. Актуальный контракт

- Единственное хранилище — SQLite schema 15. В ней есть
  `schema_migrations`, фиксированный `application_id`, foreign keys, WAL и
  транзакционные read/write snapshots.
- Пустая база инициализируется сразу как schema 15. Автоматически принимаются
  только точные schema 12/13/14: выполняется миграция до 15,
  перед ней создаётся проверенный stable backup, повторно используемый при retry.
  Upgrade только offline: все workers старой schema-12/13 версии нужно остановить
  до запуска нового кода. Чужая schema 0, версии ниже 12 и будущие версии
  отклоняются без попытки «починить» их.
- Полный обмен колодой использует только JSON schema 8. Прежние JSON-схемы
  и legacy backend удалены; JSON export не заменяет backup всей базы. Safe payload
  не может содержать trusted history и никогда автоматически не становится Advanced.
- CSV — строгий формат с обязательным canonical header, стандартным quoting,
  проверяемым preview и атомарным import. Safe-колода принимает
  `section/front/back`, Advanced —
  `section/front/back/upper_header/lower_header`; raw-значения не
  подрезаются и не переписываются.
- Safe и Advanced — неизменяемые типы колод. Safe экранирует текст и
  даёт allowlisted-типографику; Advanced передаёт raw TeX только в
  fail-closed `bubblewrap` sandbox. Raw-поля карточки
  `upper_header/lower_header` разрешены только Advanced; Safe требует точную
  пустую строку во всех ingress/storage/export boundaries.
- Программные geometry/raster/preflight-проверки не являются заменой
  физической приёмки на конкретном принтере.
- Persistence-контур рассчитан на Linux и один host с локальной POSIX-файловой
  системой: контракт зависит от `fcntl/flock`, hard links, directory `fsync` и WAL.
  Windows, NFS/SMB и multi-host shared volume не поддерживаются.

## 2. Статус последней ревизии

| ID | Статус | Результат и критерий приёмки |
|---|---|---|
| `DATA-BACKUP-001` | ✅ Выполнено | `scripts/storage.py backup` снимает целостный online snapshot вместе с committed WAL-данными; hard-link publication атомарно отказывает при уже существующей цели. Копия и manifest имеют `0600`; manifest содержит байтовый и логический SHA-256, размер и schema version. |
| `DATA-MIGRATION-001` | ✅ Выполнено | Реестр миграций ведёт цепочку schema 12→13→14→15 в одной write-транзакции. Stable backup исходной schema создаётся один раз, проверяется и повторно используется при failed-start retry; неизвестная/неполная схема отклоняется. Шаг 13→14 очищает только legacy Safe raw-колонтитулы, а 14→15 добавляет trash-state без переписывания агрегатов. |
| `DATA-TRASH-001` | ✅ Выполнено | Schema 15 хранит парные UTC `trashed_at/purge_after`; активные queries fail closed для удалённой колоды. UI даёт отдельные version-locked trash/restore/purge POST. Карточки, настройки и trusted history восстанавливаются без re-import; purge удаляет агрегат каскадно. 30-дневный retention настраивается в deployment, просроченная запись сохраняется до явного purge, а `MAX_CARDS` при restore не применяется повторно. |
| `DATA-RESTORE-001` | ✅ Выполнено | Offline restore требует `--yes`, matching manifest и exclusive inode lease. Healthy live сначала превращается в pre-restore backup; unhealthy live по отдельному opt-in `--allow-unhealthy-live` сначала копируется без изменения в forensic bundle вместе с WAL/SHM/journal. После этого проверенная staged-копия публикуется атомарно; schema 12/13 мигрируется только в staging. |
| `LIMIT-002` | ✅ Выполнено | `CardDeck.padded()` отклоняет bool, нецелые и `cards_per_page <= 0` до арифметики; прямые domain/use-case regression-тесты не допускают частичного действия. |
| `IMPORT-CSV-V2` | ✅ Выполнено | Canonical header, comma/semicolon/tab, BOM/кодировки, quoted delimiter/multiline/doubled quote, пять Advanced-полей, preview binding и atomic reject покрыты regression/E2E. |
| `MODE-SPLIT-001` | ✅ Выполнено | Safe/Advanced разделены в domain, persistence, import/export, UI и renderer; built-in-оформление не влияет на Advanced. |
| `MODE-HIDDEN-FIELDS-001` | ✅ Выполнено | Safe add/edit/API/JSON/repository отклоняют любое непустое raw-поле карточки без частичной записи; type transition запрещён. Schema 14 исправляет schema-13 данные с recoverable backup, integrity ловит ручную порчу, export не теряет скрытые значения, Advanced round-trip пяти полей остаётся character-preserving. |
| `TEXT-NEWLINE-001` | ✅ Выполнено | Safe front/back получили единые CRLF/LF/CR, line/paragraph/display-math semantics в HTML и PDF; SQLite/CSV/JSON остаются character-preserving, no-op browser edit не переписывает импортированный EOL, Advanced остаётся raw. Реальные Chromium, `pdflatex` и bbox regression проверяют layout и TeX-boundary. |
| `PRINT-GEOMETRY-001` | ✅ Программная часть | PDF идёт `front-1/back-1/…`, long-edge/short-edge permutation отделена от поворота 0°/180°; cut size, offsets, мишени, calibration PDF и overflow покрыты PDF/raster/vector-тестами. |
| `PREVIEW-OVERLAY-001` | ✅ Выполнено | UI накладывает front/back выбранного физического листа с opacity, cut bounds, slot/source numbers и профилем. Overlay и PDF используют один snapshot, renderer-owned geometry, transform matrix и детерминированный job ID. Оверлей выявил и закрыл два реальных print-дефекта: сетка теперь симметрична A4, а 180° вращается вокруг центра карточки. Chromium проходит long-edge/short-edge и сверяет PDF header. |
| `PRINT-PHYSICAL-001` | 🟡 Заблокировано вне кода | Нужен принтер и реальные измерения long-edge, short-edge и ручной подачи по [протоколу](PHYSICAL_PRINT_ACCEPTANCE.md). До этого нельзя обещать точность на любом принтере. |

Статус storage-пунктов подтверждён обычными regression-тестами: проверены
forensic API/CLI restore, отказ при publication `replace/fsync`, committed WAL,
конкурентный no-clobber backup и повторное использование stable pre-migration backup.
Последний полный локальный gate 25.08.2026: **815 passed**, statement coverage
100%, branch coverage **99,49%** (общий coverage 99,89%); в прогон вошли реальные Chromium,
`pdflatex` и `bubblewrap` integration-тесты.

## 3. Ближайший backlog

Пункт переводится в «выполнено» только после падающего контракта, исправления,
позитивных/негативных regression-тестов и обновления этого плана.

### P2 — эксплуатация и полировка

1. **`BUG-OBS-002` — единое логирование calibration PDF.**
   Провести calibration job через тот же sanitised observability wrapper, что обычную
   печать. Критерий: success/validation/timeout/compiler failure дают одинаково
   коррелируемые события без контента и путей.
2. **`SEC-AUTH-001` — deployment-граница.**
   Описать и проверить reference deployment с TLS и внешней аутентификацией;
   до того не публиковать редактор напрямую в Интернет.
3. **`DOC-OPS-001` — операционная проверка backup.**
   В CI оставить автоматический round-trip; для production регулярно выносить backup
   за пределы host и периодически проводить учебное восстановление в отдельный
   каталог. Критерий: зафиксированы RPO/RTO, retention и результат последней drill-проверки.
4. **`IMPORT-ERROR-FOCUS-001` — единый error focus для preview импорта.**
   HTTP/network ошибки bulk/CSV должны получать тот же focus/scroll, что успешный
   preview и preflight; проверить keyboard/aria-live сценарием Chromium.

## 4. Порядок работ

1. Сделать `BUG-OBS-002`, затем унифицировать error focus импорта.
2. После доступа к принтеру выполнить `PRINT-PHYSICAL-001`, внести измерения
   в протокол и только после этого закрыть печатный этап.

## 5. Обязательные quality gates

Для каждого инкремента:

- сначала фиксируется падающий контракт, после исправления он остаётся
  обычным regression-тестом без `xfail`;
- проходят unit, repository/use-case, HTTP и релевантные integration/E2E-тесты;
- для storage-изменений обязательны missing/corrupt/future schema, migration
  rollback/retry, concurrent startup, WAL snapshot, concurrent no-clobber, manifest mismatch,
  inode lease, active-worker refusal, healthy/forensic restore и publish-failure rollback;
- для печати обязательны unit permutation, реальный TeX, vector geometry,
  raster oracle/golden, overflow/preflight и browser E2E;
- branch coverage не ниже 98%, `git diff --check` без ошибок;
- меняющийся UI доступен клавиатурой, имеет focus/error state и покрыт Chromium E2E;
- README, руководство и статус этого плана обновлены в том же инкременте.

## 6. Icebox

- `FEAT-CARD-FIELDS-001`: произвольные поля карточки/кастомные placeholders. Основной
  сценарий нумерации уже закрыт `card_number/card_count`; возвращать фичу в
  backlog только после подтверждённого спроса.
- Свободные TeX-директивы в safe-типографике не планируются: произвольная
  вёрстка остаётся исключительно в Advanced sandbox.
