from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys

from didactic_cards.adapters.sqlite_repository import SqliteRepository
from didactic_cards.domain.entities import Card, CardDeck
from scripts import storage as storage_script
from scripts.storage import main


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STORAGE_SCRIPT = PROJECT_ROOT / 'scripts' / 'storage.py'


def _run_storage_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(STORAGE_SCRIPT), *arguments],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_inspect_cli_missing_path_does_not_create_database(tmp_path, capsys):
    data_dir = tmp_path / 'missing'

    exit_code = main(['inspect', '--data-dir', str(data_dir)])

    assert exit_code == 2
    assert not data_dir.exists()
    error = json.loads(capsys.readouterr().err)
    assert error['status'] == 'error'
    assert 'does not exist' in error['error']


def test_backup_cli_writes_machine_readable_result(tmp_path, capsys):
    data_dir = tmp_path / 'data'
    repository = SqliteRepository(data_dir)
    deck = repository.create_deck('CLI')
    repository.save_cards(deck.id, CardDeck([Card(front='Q')]))
    output = tmp_path / 'backup.sqlite3'

    exit_code = main([
        'backup', '--data-dir', str(data_dir), '--output', str(output)
    ])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['status'] == 'ok'
    assert payload['backup'] == str(output.resolve())
    assert output.exists()


def test_restore_cli_requires_explicit_confirmation(tmp_path, capsys):
    data_dir = tmp_path / 'data'
    repository = SqliteRepository(data_dir)
    source = tmp_path / 'backup.sqlite3'
    main(['backup', '--data-dir', str(data_dir), '--output', str(source)])
    capsys.readouterr()

    exit_code = main([
        'restore', str(source), '--data-dir', str(data_dir)
    ])

    assert exit_code == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload['status'] == 'refused'
    assert '--yes' in payload['error']


def test_restore_cli_reports_active_application_lease(tmp_path, capsys):
    data_dir = tmp_path / 'data'
    repository = SqliteRepository(data_dir)
    source = tmp_path / 'backup.sqlite3'
    assert main([
        'backup', '--data-dir', str(data_dir), '--output', str(source)
    ]) == 0
    capsys.readouterr()

    exit_code = main([
        'restore', str(source), '--data-dir', str(data_dir), '--yes'
    ])

    assert exit_code == 4
    payload = json.loads(capsys.readouterr().err)
    assert 'application is running' in payload['error']


def test_inspect_cli_subprocess_returns_zero_and_json_for_healthy_database(
    tmp_path
):
    data_dir = tmp_path / 'data'
    repository = SqliteRepository(data_dir)
    repository.create_deck('Subprocess')
    repository.close()

    completed = _run_storage_cli('inspect', '--data-dir', str(data_dir))

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload['healthy'] is True
    assert payload['counts']['decks'] == 1
    assert completed.stderr == ''


def test_inspect_cli_subprocess_returns_one_for_unhealthy_database(tmp_path):
    database = tmp_path / 'damaged.sqlite3'
    database.write_bytes(b'not sqlite')

    completed = _run_storage_cli('inspect', '--database', str(database))

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload['healthy'] is False
    assert any(issue.startswith('sqlite-error:') for issue in payload['issues'])
    assert completed.stderr == ''


def test_inspect_cli_subprocess_returns_two_and_does_not_create_missing_path(
    tmp_path
):
    data_dir = tmp_path / 'missing'

    completed = _run_storage_cli('inspect', '--data-dir', str(data_dir))

    assert completed.returncode == 2
    assert completed.stdout == ''
    assert json.loads(completed.stderr)['status'] == 'error'
    assert not data_dir.exists()


def test_backup_cli_subprocess_returns_two_on_destination_collision(tmp_path):
    data_dir = tmp_path / 'data'
    repository = SqliteRepository(data_dir)
    repository.close()
    output = tmp_path / 'existing.sqlite3'
    output.write_bytes(b'keep')

    completed = _run_storage_cli(
        'backup', '--data-dir', str(data_dir), '--output', str(output)
    )

    assert completed.returncode == 2
    assert json.loads(completed.stderr)['status'] == 'error'
    assert output.read_bytes() == b'keep'


def test_restore_cli_subprocess_success_returns_zero_and_json(tmp_path):
    data_dir = tmp_path / 'data'
    repository = SqliteRepository(data_dir)
    deck = repository.create_deck('Backup state')
    source = tmp_path / 'snapshot.sqlite3'
    assert main([
        'backup', '--data-dir', str(data_dir), '--output', str(source)
    ]) == 0
    repository.update_deck(deck.id, 'Live state')
    repository.close()

    completed = _run_storage_cli(
        'restore', str(source), '--data-dir', str(data_dir), '--yes'
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload['status'] == 'ok'
    assert payload['source'] == str(source.resolve())
    assert Path(payload['safety_backup']).exists()
    reopened = SqliteRepository(data_dir)
    assert reopened.get_deck(deck.id).name == 'Backup state'
    reopened.close()


def test_restore_cli_allows_unhealthy_live_and_reports_forensic_bundle(
    tmp_path, capsys
):
    data_dir = tmp_path / 'data'
    repository = SqliteRepository(data_dir)
    deck = repository.create_deck('Backup state')
    source = tmp_path / 'snapshot.sqlite3'
    assert main([
        'backup', '--data-dir', str(data_dir), '--output', str(source)
    ]) == 0
    capsys.readouterr()
    repository.update_deck(deck.id, 'Corrupted live state')
    repository.close()
    with closing(sqlite3.connect(repository.database_file)) as connection:
        connection.execute(
            'DELETE FROM deck_render_settings WHERE deck_id = ?', (deck.id,)
        )
        connection.commit()

    exit_code = main([
        'restore', str(source), '--data-dir', str(data_dir), '--yes',
        '--allow-unhealthy-live',
    ])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['status'] == 'ok'
    assert payload['safety_backup'] is None
    forensic_bundle = Path(payload['forensic_bundle'])
    assert forensic_bundle.is_dir()
    assert (forensic_bundle / 'cards.sqlite3').is_file()
    assert (forensic_bundle / 'forensic-manifest.json').is_file()
    reopened = SqliteRepository(data_dir)
    assert reopened.get_deck(deck.id).name == 'Backup state'
    reopened.close()


def test_backup_cli_subprocess_returns_one_for_invalid_source(tmp_path):
    data_dir = tmp_path / 'data'
    repository = SqliteRepository(data_dir)
    deck = repository.create_deck('Invalid')
    repository.close()

    with closing(sqlite3.connect(repository.database_file)) as connection:
        connection.execute(
            'DELETE FROM deck_render_settings WHERE deck_id = ?', (deck.id,)
        )
        connection.commit()

    completed = _run_storage_cli('backup', '--data-dir', str(data_dir))

    assert completed.returncode == 1
    payload = json.loads(completed.stderr)
    assert payload['status'] == 'error'
    assert 'missing-render-settings' in payload['error']


def test_cli_reports_generic_storage_io_error_as_exit_five(
    tmp_path, capsys, monkeypatch
):
    data_dir = tmp_path / 'data'
    repository = SqliteRepository(data_dir)

    def fail_backup(*args, **kwargs):
        raise OSError('injected filesystem failure')

    monkeypatch.setattr(storage_script, 'backup_database', fail_backup)

    exit_code = main(['backup', '--data-dir', str(data_dir)])

    assert exit_code == 5
    payload = json.loads(capsys.readouterr().err)
    assert payload == {
        'status': 'error',
        'error': 'OSError: storage operation failed',
    }
