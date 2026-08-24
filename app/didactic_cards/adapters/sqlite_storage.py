from __future__ import annotations

import fcntl
import hashlib
import json
import os
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import stat
import tempfile
from typing import Iterator
import uuid

from .sqlite_schema import (
    MINIMUM_MIGRATABLE_SCHEMA_VERSION,
    SQLITE_SCHEMA_VERSION,
    migrate_to_current,
    schema_structure_issues,
)


DATABASE_NAME = 'cards.sqlite3'


class StorageError(RuntimeError):
    """Base class for explicit storage maintenance failures."""


class StoragePathError(StorageError):
    pass


class StorageValidationError(StorageError):
    pass


class StorageDestinationExistsError(StorageError):
    pass


class StorageBusyError(StorageError):
    pass


@dataclass(frozen=True)
class StorageInspection:
    path: Path
    schema_version: int | None
    expected_schema_version: int
    healthy: bool
    issues: tuple[str, ...]
    counts: dict[str, int]
    sha256: str
    logical_sha256: str | None
    size_bytes: int
    has_wal: bool

    def to_dict(self) -> dict:
        return {
            'path': str(self.path),
            'schema_version': self.schema_version,
            'expected_schema_version': self.expected_schema_version,
            'healthy': self.healthy,
            'issues': list(self.issues),
            'counts': dict(self.counts),
            'sha256': self.sha256,
            'sha256_scope': 'main-file-only',
            'logical_sha256': self.logical_sha256,
            'size_bytes': self.size_bytes,
            'has_wal': self.has_wal,
        }


@dataclass(frozen=True)
class BackupResult:
    path: Path
    manifest_path: Path
    sha256: str
    schema_version: int
    size_bytes: int


@dataclass(frozen=True)
class RestoreResult:
    source: Path
    database: Path
    safety_backup: Path | None
    forensic_bundle: Path | None = None


class RuntimeStorageLease:
    """Shared process-lifetime lease that makes offline restore fail closed."""

    def __init__(self, database: Path):
        self.path = database
        self._descriptor = _open_lock_target(database, directory=False)
        fcntl.flock(self._descriptor, fcntl.LOCK_SH)

    def close(self) -> None:
        if self._descriptor is not None:
            # Do not issue LOCK_UN here. After a preloaded Gunicorn process is
            # forked, parent and children share one open-file description;
            # an explicit unlock in one child would drop the lease for all of
            # them. Closing releases the lock once the final duplicate closes.
            os.close(self._descriptor)
            self._descriptor = None


def _open_lock_target(path: Path, *, directory: bool) -> int:
    flags = os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0)
    flags |= getattr(os, 'O_NOFOLLOW', 0)
    if directory:
        flags |= getattr(os, 'O_DIRECTORY', 0)
    descriptor = os.open(path, flags)
    mode = os.fstat(descriptor).st_mode
    expected = stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)
    if not expected:
        os.close(descriptor)
        raise StoragePathError('storage lock target has an invalid file type')
    return descriptor


@contextmanager
def exclusive_schema_lock(data_dir: Path) -> Iterator[None]:
    descriptor = _open_lock_target(data_dir, directory=True)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


@contextmanager
def exclusive_runtime_lock(database: Path) -> Iterator[None]:
    descriptor = _open_lock_target(database, directory=False)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise StorageBusyError(
                'application is running; stop every worker before restore'
            ) from error
        yield
    finally:
        os.close(descriptor)


def _checked_database_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise StoragePathError('database path must not be a symlink')
    if not candidate.exists():
        raise StoragePathError(f'database does not exist: {candidate}')
    if not candidate.is_file():
        raise StoragePathError('database path must be a regular file')
    return candidate.resolve()


def _readonly_connection(path: Path) -> sqlite3.Connection:
    wal = path.with_name(f'{path.name}-wal')
    shm = path.with_name(f'{path.name}-shm')
    has_wal_data = wal.exists() and wal.stat().st_size > 0
    if has_wal_data and not shm.exists():
        raise StoragePathError(
            'database has a non-empty WAL but no SHM; refusing to create '
            'sidecar files during read-only inspection'
        )
    # A database whose last WAL connection has closed is fully checkpointed.
    # ``immutable=1`` is the only SQLite mode which also guarantees that a
    # read will not create zero-byte -wal/-shm files for such a database.
    immutable = '' if has_wal_data else '&immutable=1'
    connection = sqlite3.connect(
        f'{path.as_uri()}?mode=ro{immutable}', uri=True, timeout=30
    )
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only = ON')
    connection.execute('PRAGMA foreign_keys = ON')
    return connection


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _logical_sha256(connection: sqlite3.Connection) -> str:
    """Hash schema and rows from the connection's single read snapshot."""
    digest = hashlib.sha256()
    digest.update(b'didactic-cards-logical-snapshot-v1\0')
    objects = connection.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    for object_type, name, source in objects:
        digest.update(object_type.encode('utf-8') + b'\0')
        digest.update(name.encode('utf-8') + b'\0')
        digest.update((source or '').encode('utf-8') + b'\0')
    tables = [
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    for table in tables:
        quoted_table = table.replace('"', '""')
        columns = [
            row[1] for row in connection.execute(
                f'PRAGMA table_info("{quoted_table}")'
            )
        ]
        quoted_columns = [
            f'"{column.replace(chr(34), chr(34) * 2)}"'
            for column in columns
        ]
        order = ', '.join(quoted_columns)
        query = f'SELECT {order} FROM "{quoted_table}"'
        if order:
            query += f' ORDER BY {order}'
        digest.update(table.encode('utf-8') + b'\0')
        digest.update('\0'.join(columns).encode('utf-8') + b'\0')
        for row in connection.execute(query):
            serialized = json.dumps(
                list(row),
                ensure_ascii=False,
                separators=(',', ':'),
                default=lambda value: {
                    '$bytes': bytes(value).hex()
                },
            )
            digest.update(serialized.encode('utf-8') + b'\n')
    return digest.hexdigest()


def _semantic_issues(connection: sqlite3.Connection) -> list[str]:
    issues: list[str] = []
    missing_settings = connection.execute(
        '''
        SELECT decks.id FROM decks
        LEFT JOIN deck_render_settings
            ON deck_render_settings.deck_id = decks.id
        WHERE deck_render_settings.deck_id IS NULL
        ORDER BY decks.id
        '''
    ).fetchall()
    issues.extend(
        f"missing-render-settings: {row['id']}" for row in missing_settings
    )
    orphan_cards = connection.execute(
        '''
        SELECT cards.id FROM cards
        LEFT JOIN deck_cards ON deck_cards.card_id = cards.id
        WHERE deck_cards.card_id IS NULL ORDER BY cards.id
        '''
    ).fetchall()
    issues.extend(f"orphan-card: {row['id']}" for row in orphan_cards)
    shared_cards = connection.execute(
        '''
        SELECT card_id FROM deck_cards GROUP BY card_id HAVING COUNT(*) != 1
        '''
    ).fetchall()
    issues.extend(f"shared-card: {row['card_id']}" for row in shared_cards)
    position_rows = connection.execute(
        '''
        SELECT deck_id, COUNT(*) AS count_rows, MIN(position) AS first_position,
               MAX(position) AS last_position, COUNT(DISTINCT position) AS unique_rows
        FROM deck_cards GROUP BY deck_id
        '''
    ).fetchall()
    for row in position_rows:
        count = row['count_rows']
        if (
            row['first_position'] != 0
            or row['last_position'] != count - 1
            or row['unique_rows'] != count
        ):
            issues.append(f"non-contiguous-positions: {row['deck_id']}")

    from ..domain.printing import PrinterProfile
    from ..domain.rendering import DeckRenderSettings
    from ..domain.trusted import TrustedTemplateVersion

    allowed_typography = set(DeckRenderSettings().typography_dict())
    for row in connection.execute(
        'SELECT * FROM deck_render_settings ORDER BY deck_id'
    ):
        try:
            typography = json.loads(row['typography_json'])
            if not isinstance(typography, dict):
                raise ValueError
            if set(typography) - allowed_typography:
                raise ValueError
            DeckRenderSettings.from_dict({
                'preset': row['preset'],
                'horizontal_alignment': row['horizontal_alignment'],
                'vertical_alignment': row['vertical_alignment'],
                'header_visibility': row['header_visibility'],
                'header_position': row['header_position'],
                'header_alignment': row['header_alignment'],
                'header_repeat': row['header_repeat'],
                'section_break': row['section_break'],
                **typography,
            })
        except (TypeError, ValueError, json.JSONDecodeError):
            issues.append(f"invalid-render-settings: {row['deck_id']}")

    for row in connection.execute('SELECT * FROM trusted_templates ORDER BY id'):
        try:
            if not row['source_hash']:
                raise ValueError
            TrustedTemplateVersion(
                id=row['id'],
                deck_id=row['deck_id'],
                version=row['version'],
                source_hash=row['source_hash'],
                front_source=row['front_source'],
                back_source=row['back_source'],
                provenance=row['provenance'],
                status=row['status'],
                origin_template_id=row['origin_template_id'],
                created_at=datetime.fromisoformat(row['created_at']),
                approved_at=(
                    datetime.fromisoformat(row['approved_at'])
                    if row['approved_at'] else None
                ),
            )
        except (TypeError, ValueError):
            issues.append(f"invalid-trusted-template: {row['id']}")

    for row in connection.execute('SELECT * FROM printer_profiles ORDER BY key'):
        try:
            PrinterProfile(
                key=row['key'],
                name=row['name'],
                duplex_mode=row['duplex_mode'],
                back_rotation_deg=row['back_rotation_deg'],
                front_offset_x_mm=row['front_offset_x_mm'],
                front_offset_y_mm=row['front_offset_y_mm'],
                back_offset_x_mm=row['back_offset_x_mm'],
                back_offset_y_mm=row['back_offset_y_mm'],
                back_border=bool(row['back_border']),
                registration_marks=bool(row['registration_marks']),
            )
        except (TypeError, ValueError):
            issues.append(f"invalid-printer-profile: {row['key']}")

    for table in ('decks', 'cards'):
        for row in connection.execute(
            f'SELECT id, created_at, updated_at, version FROM "{table}" '
            'ORDER BY id'
        ):
            try:
                created_at = datetime.fromisoformat(row['created_at'])
                updated_at = datetime.fromisoformat(row['updated_at'])
                if created_at.tzinfo is None or updated_at.tzinfo is None:
                    raise ValueError
                if isinstance(row['version'], bool) or row['version'] <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                issues.append(f"invalid-{table[:-1]}-metadata: {row['id']}")

    if 'schema_migrations' in {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }:
        migration_rows = connection.execute(
            'SELECT version, name, applied_at FROM schema_migrations '
            'ORDER BY version'
        ).fetchall()
        if (
            not migration_rows
            or migration_rows[-1]['version'] != SQLITE_SCHEMA_VERSION
        ):
            issues.append('invalid-migration-history: current version is missing')
        for row in migration_rows:
            try:
                applied_at = datetime.fromisoformat(row['applied_at'])
                if (
                    applied_at.tzinfo is None
                    or not row['name']
                    or row['version'] > SQLITE_SCHEMA_VERSION
                ):
                    raise ValueError
            except (TypeError, ValueError):
                issues.append(f"invalid-migration-history: {row['version']}")
    return issues


def connection_validation_issues(
    connection: sqlite3.Connection,
    *,
    expected_schema_version: int = SQLITE_SCHEMA_VERSION,
    quick: bool = False,
) -> list[str]:
    """Validate one already-open snapshot using the canonical contract."""
    structural = schema_structure_issues(
        connection, expected_version=expected_schema_version
    )
    issues = list(structural)
    pragma = 'PRAGMA quick_check(1)' if quick else 'PRAGMA integrity_check'
    integrity = [row[0] for row in connection.execute(pragma)]
    if integrity != ['ok']:
        issues.extend(f'integrity: {item}' for item in integrity)
    issues.extend(
        f'foreign-key: {tuple(row)}'
        for row in connection.execute('PRAGMA foreign_key_check')
    )
    if not structural:
        issues.extend(_semantic_issues(connection))
    return issues


def inspect_database(
    database: str | Path,
    *,
    expected_schema_version: int = SQLITE_SCHEMA_VERSION,
) -> StorageInspection:
    path = _checked_database_path(database)
    issues: list[str] = []
    counts: dict[str, int] = {}
    schema_version: int | None = None
    logical_sha256: str | None = None
    try:
        with closing(_readonly_connection(path)) as connection:
            connection.execute('BEGIN')
            schema_version = connection.execute(
                'PRAGMA user_version'
            ).fetchone()[0]
            issues.extend(connection_validation_issues(
                connection,
                expected_schema_version=expected_schema_version,
            ))
            if not any(
                issue.startswith(('missing-table:', 'unexpected-table:'))
                for issue in issues
            ):
                for table in (
                    'decks', 'cards', 'printer_profiles', 'trusted_templates'
                ):
                    counts[table] = connection.execute(
                        f'SELECT COUNT(*) FROM "{table}"'
                    ).fetchone()[0]
            logical_sha256 = _logical_sha256(connection)
            connection.rollback()
    except sqlite3.Error as error:
        issues.append(f'sqlite-error: {type(error).__name__}')

    return StorageInspection(
        path=path,
        schema_version=schema_version,
        expected_schema_version=expected_schema_version,
        healthy=not issues,
        issues=tuple(issues),
        counts=counts,
        sha256=_file_sha256(path),
        logical_sha256=logical_sha256,
        size_bytes=path.stat().st_size,
        has_wal=path.with_name(f'{path.name}-wal').exists(),
    )


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _copy_sqlite_snapshot(source: Path, destination: Path) -> None:
    with closing(_readonly_connection(source)) as source_connection:
        with closing(sqlite3.connect(destination)) as destination_connection:
            source_connection.backup(destination_connection, pages=256, sleep=0.01)
            destination_connection.execute('PRAGMA journal_mode = DELETE')
            destination_connection.commit()


def _default_backup_path(
    database: Path,
    prefix: str = 'cards',
    schema_version: int = SQLITE_SCHEMA_VERSION,
) -> Path:
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    return database.parent / 'backups' / (
        f'{prefix}-v{schema_version}-{timestamp}.sqlite3'
    )


def backup_database(
    database: str | Path,
    output: str | Path | None = None,
    *,
    expected_schema_version: int = SQLITE_SCHEMA_VERSION,
    prefix: str = 'cards',
) -> BackupResult:
    source = _checked_database_path(database)
    source_report = inspect_database(
        source, expected_schema_version=expected_schema_version
    )
    if not source_report.healthy:
        raise StorageValidationError('; '.join(source_report.issues))
    destination = Path(output).expanduser() if output else _default_backup_path(
        source, prefix, expected_schema_version
    )
    if destination.is_symlink():
        raise StoragePathError('backup destination must not be a symlink')
    destination = destination.absolute()
    manifest = destination.with_suffix(destination.suffix + '.manifest.json')
    if destination == source:
        raise StoragePathError('backup destination must differ from live database')
    if destination.exists() or manifest.exists():
        raise StorageDestinationExistsError(
            f'backup destination already exists: {destination}'
        )
    parent_existed = destination.parent.exists()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not parent_existed:
        os.chmod(destination.parent, 0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f'.{destination.name}.', suffix='.tmp', dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary_manifest = temporary.with_suffix('.manifest.tmp')
    try:
        os.chmod(temporary, 0o600)
        _copy_sqlite_snapshot(source, temporary)
        report = inspect_database(
            temporary, expected_schema_version=expected_schema_version
        )
        if not report.healthy:
            raise StorageValidationError('; '.join(report.issues))
        with temporary.open('rb') as handle:
            os.fsync(handle.fileno())
        payload = {
            'format': 'didactic-cards-sqlite-backup-v1',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'schema_version': report.schema_version,
            'sha256': report.sha256,
            'logical_sha256': report.logical_sha256,
            'size_bytes': report.size_bytes,
        }
        temporary_manifest.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8',
        )
        os.chmod(temporary_manifest, 0o600)
        with temporary_manifest.open('rb') as handle:
            os.fsync(handle.fileno())
        published_database = False
        published_manifest = False
        try:
            os.link(temporary, destination)
            published_database = True
            os.link(temporary_manifest, manifest)
            published_manifest = True
            _fsync_directory(destination.parent)
        except FileExistsError as error:
            if published_manifest:
                manifest.unlink(missing_ok=True)
            if published_database:
                destination.unlink(missing_ok=True)
            raise StorageDestinationExistsError(
                f'backup destination already exists: {destination}'
            ) from error
        except Exception:
            if published_manifest:
                manifest.unlink(missing_ok=True)
            if published_database:
                destination.unlink(missing_ok=True)
            try:
                _fsync_directory(destination.parent)
            except OSError:
                pass
            raise
        return BackupResult(
            path=destination.resolve(),
            manifest_path=manifest.resolve(),
            sha256=report.sha256,
            schema_version=report.schema_version or expected_schema_version,
            size_bytes=report.size_bytes,
        )
    finally:
        temporary.unlink(missing_ok=True)
        temporary_manifest.unlink(missing_ok=True)


def _manifest_matches_report(
    payload: dict,
    report: StorageInspection,
) -> bool:
    return (
        payload.get('format') == 'didactic-cards-sqlite-backup-v1'
        and payload.get('schema_version') == report.schema_version
        and payload.get('size_bytes') == report.size_bytes
        and payload.get('sha256') == report.sha256
        and payload.get('logical_sha256') == report.logical_sha256
    )


def _verify_manifest(
    source: Path,
    report: StorageInspection,
    *,
    required: bool = False,
) -> dict | None:
    manifest = source.with_suffix(source.suffix + '.manifest.json')
    if manifest.is_symlink():
        raise StorageValidationError('backup manifest must not be a symlink')
    if not manifest.exists():
        if required:
            raise StorageValidationError('backup manifest is missing')
        return None
    try:
        payload = json.loads(manifest.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise StorageValidationError('backup manifest is invalid') from error
    if not isinstance(payload, dict) or not _manifest_matches_report(
        payload, report
    ):
        raise StorageValidationError('backup manifest does not match database')
    return payload


def ensure_pre_migration_backup(
    database: str | Path,
    version: int,
) -> BackupResult:
    """Create once, then validate and reuse across failed startup retries."""
    source = _checked_database_path(database)
    destination = (
        source.parent / 'backups'
        / f'pre-migration-v{version}-stable.sqlite3'
    )
    if not destination.exists():
        return backup_database(
            source,
            destination,
            expected_schema_version=version,
            prefix=f'pre-migration-v{version}',
        )
    report = inspect_database(
        destination, expected_schema_version=version
    )
    if not report.healthy:
        raise StorageValidationError(
            'existing pre-migration backup is invalid: '
            + '; '.join(report.issues)
        )
    _verify_manifest(destination, report, required=True)
    source_report = inspect_database(
        source, expected_schema_version=version
    )
    if (
        not source_report.healthy
        or source_report.logical_sha256 != report.logical_sha256
    ):
        raise StorageValidationError(
            'existing pre-migration backup does not match the live database'
        )
    return BackupResult(
        path=destination.resolve(),
        manifest_path=destination.with_suffix(
            destination.suffix + '.manifest.json'
        ).resolve(),
        sha256=report.sha256,
        schema_version=report.schema_version or version,
        size_bytes=report.size_bytes,
    )


def _restore_source_report(candidate: Path) -> StorageInspection:
    wal = candidate.with_name(f'{candidate.name}-wal')
    if wal.exists() and wal.stat().st_size:
        raise StorageValidationError(
            'restore source must be a standalone backup without a WAL'
        )
    report = inspect_database(candidate)
    if report.healthy:
        return report
    version = report.schema_version
    if (
        isinstance(version, int)
        and MINIMUM_MIGRATABLE_SCHEMA_VERSION
        <= version
        < SQLITE_SCHEMA_VERSION
    ):
        migratable_report = inspect_database(
            candidate, expected_schema_version=version
        )
        if migratable_report.healthy:
            return migratable_report
        report = migratable_report
    raise StorageValidationError('; '.join(report.issues))


def _migrate_staged_database(
    staged: Path,
    version: int,
) -> None:
    if version == SQLITE_SCHEMA_VERSION:
        return
    with closing(sqlite3.connect(staged)) as connection:
        try:
            connection.execute('BEGIN IMMEDIATE')
            migrate_to_current(connection, version)
            issues = schema_structure_issues(connection)
            if issues:
                raise StorageValidationError(
                    'staged migration failed validation: ' + '; '.join(issues)
                )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise


def _publish_staged_database(staged: Path, live: Path) -> None:
    token = uuid.uuid4().hex
    rollback_main = live.with_name(f'.{live.name}.rollback-{token}')
    sidecars: list[tuple[Path, Path]] = []
    swapped = False
    os.link(live, rollback_main)
    try:
        for suffix in ('-wal', '-shm', '-journal'):
            current = live.with_name(f'{live.name}{suffix}')
            if current.exists():
                rollback_sidecar = live.with_name(
                    f'.{live.name}{suffix}.rollback-{token}'
                )
                os.replace(current, rollback_sidecar)
                sidecars.append((current, rollback_sidecar))
        os.replace(staged, live)
        swapped = True
        _fsync_directory(live.parent)
    except Exception:
        try:
            if swapped:
                os.replace(rollback_main, live)
            else:
                rollback_main.unlink(missing_ok=True)
            for current, rollback_sidecar in sidecars:
                os.replace(rollback_sidecar, current)
            _fsync_directory(live.parent)
        except Exception:
            # Preserve the original publication exception. Any remaining
            # rollback artifact is deliberately left for manual recovery.
            pass
        raise
    rollback_main.unlink(missing_ok=True)
    for _, rollback_sidecar in sidecars:
        rollback_sidecar.unlink(missing_ok=True)


def _checkpoint_live_database(live: Path) -> None:
    with closing(sqlite3.connect(live, timeout=30)) as connection:
        checkpoint = connection.execute(
            'PRAGMA wal_checkpoint(TRUNCATE)'
        ).fetchone()
        if checkpoint and checkpoint[0] != 0:
            raise StorageBusyError(
                'live database WAL is busy; stop every database client'
            )
        mode = connection.execute('PRAGMA journal_mode = DELETE').fetchone()[0]
        if mode.lower() != 'delete':
            raise StorageBusyError(
                'could not detach the live WAL before restore'
            )


def _copy_regular_file(source: Path, destination: Path) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(destination, flags, 0o600)
    try:
        with source.open('rb') as input_file, os.fdopen(
            descriptor, 'wb'
        ) as output_file:
            descriptor = -1
            for chunk in iter(lambda: input_file.read(1024 * 1024), b''):
                output_file.write(chunk)
            output_file.flush()
            os.fsync(output_file.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _quarantine_database_family(live: Path) -> Path:
    backups = live.parent / 'backups'
    backups_existed = backups.exists()
    backups.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not backups_existed:
        os.chmod(backups, 0o700)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    bundle = backups / f'forensic-{timestamp}-{uuid.uuid4().hex[:8]}'
    bundle.mkdir(mode=0o700)
    members: dict[str, dict[str, str | int]] = {}
    try:
        for suffix in ('', '-wal', '-shm', '-journal'):
            source = live.with_name(f'{live.name}{suffix}')
            if not source.exists():
                continue
            destination = bundle / source.name
            _copy_regular_file(source, destination)
            members[source.name] = {
                'sha256': _file_sha256(destination),
                'size_bytes': destination.stat().st_size,
            }
        manifest = bundle / 'forensic-manifest.json'
        payload = {
            'format': 'didactic-cards-forensic-bundle-v1',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'source_database': live.name,
            'members': members,
        }
        descriptor = os.open(
            manifest,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, 'O_NOFOLLOW', 0),
            0o600,
        )
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(payload, output, ensure_ascii=False, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        _fsync_directory(bundle)
        _fsync_directory(backups)
        return bundle.resolve()
    except Exception:
        # Keep any completed members: even a partial forensic capture can be
        # valuable, and the live family has not been touched yet.
        raise


def _restore_forensic_bundle(bundle: Path, live: Path) -> None:
    saved_main = bundle / live.name
    staged_main = live.with_name(f'.forensic-rollback-{uuid.uuid4().hex}')
    _copy_regular_file(saved_main, staged_main)
    try:
        _publish_staged_database(staged_main, live)
        for suffix in ('-wal', '-shm', '-journal'):
            saved = bundle / f'{live.name}{suffix}'
            if saved.exists():
                _copy_regular_file(
                    saved, live.with_name(f'{live.name}{suffix}')
                )
        _fsync_directory(live.parent)
    finally:
        staged_main.unlink(missing_ok=True)


def restore_database(
    database: str | Path,
    source: str | Path,
    *,
    allow_unhealthy_live: bool = False,
) -> RestoreResult:
    live = _checked_database_path(database)
    candidate = _checked_database_path(source)
    if live == candidate or os.path.samefile(live, candidate):
        raise StoragePathError('restore source must differ from live database')
    candidate_report = _restore_source_report(candidate)
    manifest_payload = _verify_manifest(
        candidate, candidate_report, required=True
    )

    data_dir = live.parent
    with exclusive_schema_lock(data_dir), exclusive_runtime_lock(live):
        live_report = inspect_database(live)
        safety: BackupResult | None = None
        forensic_bundle: Path | None = None
        if not live_report.healthy:
            if not allow_unhealthy_live:
                raise StorageValidationError(
                    'live database is unhealthy; refusing automatic restore'
                )
            forensic_bundle = _quarantine_database_family(live)
        else:
            safety = backup_database(live, prefix='pre-restore')
            _checkpoint_live_database(live)
        descriptor, staged_name = tempfile.mkstemp(
            prefix='.restore-', suffix='.sqlite3', dir=data_dir
        )
        os.close(descriptor)
        staged = Path(staged_name)
        rollback = staged.with_name(f'{staged.name}.rollback')
        try:
            os.chmod(staged, 0o600)
            _copy_sqlite_snapshot(candidate, staged)
            staged_report = inspect_database(
                staged,
                expected_schema_version=(
                    candidate_report.schema_version or SQLITE_SCHEMA_VERSION
                ),
            )
            if not staged_report.healthy:
                raise StorageValidationError('; '.join(staged_report.issues))
            candidate_after_copy = _restore_source_report(candidate)
            if (
                candidate_after_copy.schema_version
                != candidate_report.schema_version
                or _verify_manifest(
                    candidate,
                    candidate_after_copy,
                    required=True,
                ) != manifest_payload
            ):
                raise StorageValidationError(
                    'backup changed while restore was staging it'
                )
            _migrate_staged_database(
                staged,
                candidate_report.schema_version or SQLITE_SCHEMA_VERSION,
            )
            staged_report = inspect_database(staged)
            if not staged_report.healthy:
                raise StorageValidationError('; '.join(staged_report.issues))
            _publish_staged_database(staged, live)
            final_report = inspect_database(live)
            if not final_report.healthy:
                if safety is not None:
                    _copy_sqlite_snapshot(safety.path, rollback)
                    _publish_staged_database(rollback, live)
                elif forensic_bundle is not None:
                    _restore_forensic_bundle(forensic_bundle, live)
                raise StorageValidationError(
                    'restored database failed post-restore validation'
                )
            with closing(sqlite3.connect(live)) as connection:
                connection.execute('PRAGMA journal_mode = WAL')
            return RestoreResult(
                candidate,
                live,
                safety.path if safety is not None else None,
                forensic_bundle,
            )
        finally:
            staged.unlink(missing_ok=True)
            rollback.unlink(missing_ok=True)
