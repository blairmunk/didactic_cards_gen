"""Read-only inspection, online backup and validated offline restore."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / 'app'))

from didactic_cards.adapters.sqlite_storage import (  # noqa: E402
    DATABASE_NAME,
    StorageBusyError,
    StorageDestinationExistsError,
    StoragePathError,
    StorageValidationError,
    backup_database,
    inspect_database,
    restore_database,
)


def _default_data_dir() -> Path:
    configured = os.environ.get('DIDACTIC_CARDS_DATA_DIR')
    return Path(configured).expanduser() if configured else PROJECT_ROOT / 'app' / 'data'


def _database_path(args) -> Path:
    if getattr(args, 'database', None) is not None:
        return args.database.expanduser()
    return (args.data_dir or _default_data_dir()).expanduser() / DATABASE_NAME


def _print(payload: dict, *, stream=None) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, indent=2),
        file=stream or sys.stdout,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Maintain didactic-cards SQLite storage.'
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    inspect_parser = subparsers.add_parser(
        'inspect', help='Inspect an existing database without modifying it.'
    )
    inspect_paths = inspect_parser.add_mutually_exclusive_group()
    inspect_paths.add_argument('--data-dir', type=Path)
    inspect_paths.add_argument('--database', type=Path)

    backup_parser = subparsers.add_parser(
        'backup', help='Create a verified online SQLite backup.'
    )
    backup_parser.add_argument('--data-dir', type=Path)
    backup_parser.add_argument('--output', type=Path)

    restore_parser = subparsers.add_parser(
        'restore', help='Restore a verified backup while the app is stopped.'
    )
    restore_parser.add_argument('source', type=Path)
    restore_parser.add_argument('--data-dir', type=Path)
    restore_parser.add_argument(
        '--yes', action='store_true', help='Confirm destructive restore.'
    )
    restore_parser.add_argument(
        '--allow-unhealthy-live',
        action='store_true',
        help=(
            'Allow disaster recovery after preserving the current SQLite '
            'family in a forensic bundle.'
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database = _database_path(args)
    try:
        if args.command == 'inspect':
            report = inspect_database(database)
            _print(report.to_dict())
            return 0 if report.healthy else 1
        if args.command == 'backup':
            result = backup_database(database, args.output)
            _print({
                'status': 'ok',
                'database': str(database.expanduser().absolute()),
                'backup': str(result.path),
                'manifest': str(result.manifest_path),
                'schema_version': result.schema_version,
                'sha256': result.sha256,
                'size_bytes': result.size_bytes,
            })
            return 0
        if not args.yes:
            _print({
                'status': 'refused',
                'error': 'restore requires explicit --yes confirmation',
            }, stream=sys.stderr)
            return 2
        result = restore_database(
            database,
            args.source,
            allow_unhealthy_live=args.allow_unhealthy_live,
        )
        _print({
            'status': 'ok',
            'database': str(result.database),
            'source': str(result.source),
            'safety_backup': (
                str(result.safety_backup) if result.safety_backup else None
            ),
            'forensic_bundle': (
                str(result.forensic_bundle) if result.forensic_bundle else None
            ),
        })
        return 0
    except StorageBusyError as error:
        _print({'status': 'error', 'error': str(error)}, stream=sys.stderr)
        return 4
    except StorageValidationError as error:
        _print({'status': 'error', 'error': str(error)}, stream=sys.stderr)
        return 1
    except (
        StoragePathError,
        StorageDestinationExistsError,
    ) as error:
        _print({'status': 'error', 'error': str(error)}, stream=sys.stderr)
        return 2
    except (OSError, sqlite3.Error) as error:
        _print({
            'status': 'error',
            'error': f'{type(error).__name__}: storage operation failed',
        }, stream=sys.stderr)
        return 5


if __name__ == '__main__':
    raise SystemExit(main())
