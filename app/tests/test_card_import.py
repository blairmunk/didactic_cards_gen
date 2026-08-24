from __future__ import annotations

import pytest

from didactic_cards.domain.entities import Card
from didactic_cards.domain.rendering import AuthoringMode
from didactic_cards.use_cases import card_import
from didactic_cards.use_cases.card_import import (
    parse_bulk_v2_line,
    preview_bulk_import,
    preview_csv_import,
)


def test_csv_rejects_unsupported_schema_encoding_and_ambiguous_dialect():
    with pytest.raises(ValueError, match='schema'):
        preview_csv_import(b'front;back\nQ;A', schema_mode='future')
    with pytest.raises(ValueError, match='encoding'):
        preview_csv_import(b'front;back\nQ;A', encoding='koi8-r')
    with pytest.raises(ValueError, match='однозначно'):
        preview_csv_import(
            b'front;back,section\nQ;A,S\n',
            schema_mode='header',
        )


def test_csv_auto_encoding_without_bom_and_auto_malformed_header():
    preview = preview_csv_import(
        b'front;back\nQ;A\n',
        schema_mode='header',
        encoding='auto',
    )
    assert preview.encoding == 'utf-8'

    with pytest.raises(ValueError, match='разделитель'):
        preview_csv_import(b'"front;back\n', schema_mode='header')


def test_csv_limits_are_reported(monkeypatch):
    monkeypatch.setattr(card_import, 'MAX_IMPORT_BYTES', 8)
    with pytest.raises(ValueError, match='размер'):
        preview_csv_import(b'123456789')

    monkeypatch.setattr(card_import, 'MAX_IMPORT_BYTES', 1000)
    monkeypatch.setattr(card_import, 'MAX_IMPORT_ROWS', 1)
    with pytest.raises(ValueError, match='более 1 строк'):
        preview_csv_import(
            b'front;back\nQ;A\n',
            delimiter='semicolon',
            schema_mode='header',
        )


def test_csv_field_limit_and_empty_header_variants(monkeypatch):
    monkeypatch.setattr(card_import, 'MAX_IMPORT_FIELD_CHARS', 2)
    too_long = preview_csv_import(
        b'front;back\nlong;A\n',
        delimiter='semicolon',
        schema_mode='header',
    )
    assert too_long.errors[0].code == 'field_too_large'

    whitespace = preview_csv_import(b' \n', schema_mode='header')
    missing = preview_csv_import(
        b'\n', delimiter='semicolon', schema_mode='header'
    )
    empty_name = preview_csv_import(
        b'front;;back\nQ;;A\n',
        delimiter='semicolon',
        schema_mode='header',
    )
    assert whitespace.errors[0].code == 'empty_file'
    assert missing.errors[0].code == 'missing_header'
    assert empty_name.errors[0].code == 'empty_column'


def test_csv_header_without_cards_and_blank_legacy_file_are_errors():
    header_only = preview_csv_import(
        b'front;back\n',
        delimiter='semicolon',
        schema_mode='header',
    )
    blank_legacy = preview_csv_import(
        b'\n\n',
        delimiter='semicolon',
        schema_mode='legacy',
    )
    assert header_only.errors[0].code == 'empty_file'
    assert blank_legacy.errors[0].code == 'empty_file'


def test_legacy_three_columns_and_skipped_rows_preserve_values():
    preview = preview_csv_import(
        b'\n Topic ; Q ; A \n\n',
        delimiter='semicolon',
        schema_mode='legacy',
    )
    assert preview.skipped_count == 2
    assert preview.rows[0].values() == (' Topic ', ' Q ', ' A ', '', '')


def test_advanced_header_only_card_and_existing_duplicate_warning():
    existing = Card(upper_header='Only header')
    preview = preview_csv_import(
        b'front;back;upper_header\n;;Only header\n',
        delimiter='semicolon',
        schema_mode='header',
        authoring_mode=AuthoringMode.ADVANCED,
        existing_cards=[existing],
    )
    assert preview.accepted_count == 1
    assert preview.warnings[0].code == 'duplicate_row'


def test_preview_serialization_reports_both_truncation_branches():
    rows = preview_csv_import(
        b'front;back\nQ1;A1\nQ2;A2\n',
        delimiter='semicolon',
        schema_mode='header',
    )
    issues = preview_csv_import(
        b'front;back\nQ;A\nQ;A\n',
        delimiter='semicolon',
        schema_mode='header',
    )
    assert rows.to_dict(preview_limit=1)['truncated'] is True
    assert issues.to_dict(preview_limit=1)['truncated'] is True
    assert rows.to_dict(preview_limit=20)['truncated'] is False


def test_bulk_v2_doubled_quote_and_invalid_suffix():
    assert parse_bulk_v2_line('"A ""quote"""||B') == ['A "quote"', 'B']
    with pytest.raises(ValueError, match='закрывающей'):
        parse_bulk_v2_line('"A"suffix||B')


def test_bulk_rejects_unknown_schema_and_reports_empty_skipped_input():
    with pytest.raises(ValueError, match='bulk schema'):
        preview_bulk_import('Q||A', schema_mode='future')
    preview = preview_bulk_import('\n  \n')
    assert preview.skipped_count == 2
    assert preview.errors[0].code == 'empty_input'


def test_bulk_v2_warns_for_existing_duplicate_and_legacy_mode():
    existing = Card(front='Q', back='A')
    current = preview_bulk_import(
        'Q||A',
        existing_cards=[existing],
    )
    legacy = preview_bulk_import(
        r'Q \|| literal || C:\\cards',
        schema_mode='legacy',
    )
    assert current.warnings[0].code == 'duplicate_row'
    assert legacy.rows[0].front == 'Q || literal'
    assert legacy.rows[0].back == r'C:\cards'
    assert legacy.warnings[0].code == 'legacy_bulk'
