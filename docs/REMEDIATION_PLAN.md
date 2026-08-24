# Актуальный план стабилизации и развития

Актуализировано 24 августа 2026 года после повторной ревизии хранения,
импорта, safe/Advanced UI, TeX-контура и двусторонней печати. Это единый
текущий backlog; прежний пошаговый аудит сохранён только как
[исторический архив](REMEDIATION_HISTORY.md).

## 1. Актуальный контракт

- Единственное хранилище — SQLite schema 13. В ней есть
  `schema_migrations`, фиксированный `application_id`, foreign keys, WAL и
  транзакционные read/write snapshots.
- Пустая база инициализируется сразу как schema 13. Единственный
  автоматический upgrade — явная миграция 12→13; перед ней создаётся
  проверенный stable backup, повторно используемый при retry. Upgrade только
  offline: все workers старой schema-12 версии нужно остановить до запуска нового
  кода. Чужая schema 0, версии ниже 12 и будущие версии отклоняются без попытки
  «починить» их.
- Полный обмен колодой использует только JSON schema 8. Прежние JSON-схемы
  и legacy backend удалены; JSON export не заменяет backup всей базы.
- CSV — строгий формат с обязательным canonical header, стандартным quoting,
  проверяемым preview и атомарным import. Safe-колода принимает
  `section/front/back`, Advanced —
  `section/front/back/upper_header/lower_header`; raw-значения не
  подрезаются и не переписываются.
- Safe и Advanced — неизменяемые типы колод. Safe экранирует текст и
  даёт allowlisted-типографику; Advanced передаёт raw TeX только в
  fail-closed `bubblewrap` sandbox.
- Программные geometry/raster/preflight-проверки не являются заменой
  физической приёмки на конкретном принтере.
- Persistence-контур рассчитан на Linux и один host с локальной POSIX-файловой
  системой: контракт зависит от `fcntl/flock`, hard links, directory `fsync` и WAL.
  Windows, NFS/SMB и multi-host shared volume не поддерживаются.

## 2. Статус последней ревизии

| ID | Статус | Результат и критерий приёмки |
|---|---|---|
| `DATA-BACKUP-001` | ✅ Выполнено | `scripts/storage.py backup` снимает целостный online snapshot вместе с committed WAL-данными; hard-link publication атомарно отказывает при уже существующей цели. Копия и manifest имеют `0600`; manifest содержит байтовый и логический SHA-256, размер и schema version. |
| `DATA-MIGRATION-001` | ✅ Выполнено | Реестр миграций ведёт schema 12→13 в одной write-транзакции. `pre-migration-v12-stable.sqlite3` создаётся один раз, проверяется и повторно используется при failed-start retry; неизвестная/неполная схема отклоняется. |
| `DATA-RESTORE-001` | ✅ Выполнено | Offline restore требует `--yes`, matching manifest и exclusive inode lease. Healthy live сначала превращается в pre-restore backup; unhealthy live по отдельному opt-in `--allow-unhealthy-live` сначала копируется без изменения в forensic bundle вместе с WAL/SHM/journal. После этого проверенная staged-копия публикуется атомарно; schema 12 мигрируется только в staging. |
| `LIMIT-002` | ✅ Выполнено | `CardDeck.padded()` отклоняет bool, нецелые и `cards_per_page <= 0` до арифметики; прямые domain/use-case regression-тесты не допускают частичного действия. |
| `IMPORT-CSV-V2` | ✅ Выполнено | Canonical header, comma/semicolon/tab, BOM/кодировки, quoted delimiter/multiline/doubled quote, пять Advanced-полей, preview binding и atomic reject покрыты regression/E2E. |
| `MODE-SPLIT-001` | ✅ Выполнено | Safe/Advanced разделены в domain, persistence, import/export, UI и renderer; built-in-оформление не влияет на Advanced. |
| `TEXT-NEWLINE-001` | ✅ Выполнено | Safe front/back получили единые CRLF/LF/CR, line/paragraph/display-math semantics в HTML и PDF; SQLite/CSV/JSON остаются character-preserving, no-op browser edit не переписывает импортированный EOL, Advanced остаётся raw. Реальные Chromium, `pdflatex` и bbox regression проверяют layout и TeX-boundary. |
| `PRINT-GEOMETRY-001` | ✅ Программная часть | PDF идёт `front-1/back-1/…`, long-edge/short-edge permutation отделена от поворота 0°/180°; cut size, offsets, мишени, calibration PDF и overflow покрыты PDF/raster/vector-тестами. |
| `PRINT-PHYSICAL-001` | 🟡 Заблокировано вне кода | Нужен принтер и реальные измерения long-edge, short-edge и ручной подачи по [протоколу](PHYSICAL_PRINT_ACCEPTANCE.md). До этого нельзя обещать точность на любом принтере. |

Статус storage-пунктов подтверждён обычными regression-тестами: проверены
forensic API/CLI restore, отказ при publication `replace/fsync`, committed WAL,
конкурентный no-clobber backup и повторное использование stable pre-migration backup.
Последний полный локальный gate 24.08.2026: **727 passed**, statement coverage
100%, общий branch coverage **99,44%**; в прогон вошли реальные Chromium,
`pdflatex` и `bubblewrap` integration-тесты.

## 3. Ближайший backlog

Пункт переводится в «выполнено» только после падающего контракта, исправления,
позитивных/негативных regression-тестов и обновления этого плана.

### P1 — надёжность и основной UX

1. **`MODE-HIDDEN-FIELDS-001` — закрыть скрытые raw-поля в Safe.**
   Сейчас API/JSON могут сохранить у Safe-карточки `upper_header/lower_header`, хотя
   UI, renderer и Safe CSV их не показывают. Определить миграцию тестовых значений и
   fail-closed отклонять новые non-empty raw headers во всех Safe ingress. Критерий:
   API/JSON/import не создают скрытых данных, export не теряет их молча, Advanced
   round-trip всех пяти полей остаётся посимвольным.
2. **`DATA-TRASH-001` — обратимое удаление колоды.**
   Заменить мгновенный hard delete на trash/restore с явным сроком хранения и
   отдельным безвозвратным purge. Критерий: cards/settings/trusted history восстанавливаются
   атомарно; stale-version и quota semantics определены тестами.
3. **`PREVIEW-OVERLAY-001` — точная сверка сторон.**
   Добавить в preview управляемое наложение front/back с зеркалированием,
   прозрачностью, cut bounds, номерами slots и выбранным printer profile.
   Критерий: overlay и print PDF получают один immutable job и одинаковую
   transform-матрицу; E2E проходит оба duplex mode.

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

1. Закрыть `MODE-HIDDEN-FIELDS-001` без изменения raw-контракта Advanced.
2. Реализовать `DATA-TRASH-001` как отдельную migration + UI-фичу с полным
   restore/purge-контуром.
3. Сделать `PREVIEW-OVERLAY-001`, не меняя print transform и не дублируя renderer.
4. После доступа к принтеру выполнить `PRINT-PHYSICAL-001`, внести измерения
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
