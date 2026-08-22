"""Inspect JSON persistence and optionally restore one file from its .bak."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / 'app'))

from config import AppConfig  # noqa: E402
from didactic_cards.adapters.json_repository import JsonRepository  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Check didactic-cards JSON storage without automatic repair.'
    )
    parser.add_argument('--data-dir', type=Path, default=None)
    parser.add_argument(
        '--recover',
        metavar='RELATIVE_JSON_PATH',
        help='restore one repository JSON file from its adjacent .bak',
    )
    parser.add_argument(
        '--yes',
        action='store_true',
        help='confirm recovery; the current file is retained as .broken-*',
    )
    args = parser.parse_args()

    repository = JsonRepository(args.data_dir or AppConfig().data_dir)
    if args.recover:
        if not args.yes:
            parser.error('--recover requires --yes')
        broken_path = repository.recover_from_backup(args.recover)
        print(f'Recovery completed; previous file retained at {broken_path}')

    report = repository.scan_integrity()
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.healthy else 1


if __name__ == '__main__':
    raise SystemExit(main())
