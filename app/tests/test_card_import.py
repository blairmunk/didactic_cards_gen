from __future__ import annotations

import csv
import io

import pytest

from didactic_cards.domain.entities import Card
from didactic_cards.domain.rendering import AuthoringMode
from didactic_cards.use_cases import card_import
from didactic_cards.use_cases.card_import import (
    parse_bulk_line,
    preview_bulk_import,
    preview_csv_import,
)


def test_csv_rejects_unsupported_encoding_delimiter_and_ambiguous_dialect():
    with pytest.raises(ValueError, match='encoding'):
        preview_csv_import(b'front;back\nQ;A', encoding='koi8-r')
    with pytest.raises(ValueError, match='delimiter'):
        preview_csv_import(b'front;back\nQ;A', delimiter='pipes')
    with pytest.raises(ValueError, match='однозначно'):
        preview_csv_import(b'front;back,section\nQ;A,S\n')


def test_csv_auto_encoding_and_malformed_header():
    preview = preview_csv_import(
        b'front;back\nQ;A\n',
        encoding='auto',
    )
    assert preview.encoding == 'utf-8'

    with pytest.raises(ValueError, match='разделитель'):
        preview_csv_import(b'"front;back\n')


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
        )


def test_csv_field_limit_and_empty_header_variants(monkeypatch):
    monkeypatch.setattr(card_import, 'MAX_IMPORT_FIELD_CHARS', 2)
    too_long = preview_csv_import(
        b'front;back\nlong;A\n',
        delimiter='semicolon',
    )
    assert too_long.errors[0].code == 'field_too_large'

    whitespace = preview_csv_import(b' \n')
    missing = preview_csv_import(b'\n', delimiter='semicolon')
    empty_name = preview_csv_import(
        b'front;;back\nQ;;A\n',
        delimiter='semicolon',
    )
    assert whitespace.errors[0].code == 'empty_file'
    assert missing.errors[0].code == 'missing_header'
    assert empty_name.errors[0].code == 'empty_column'


def test_csv_header_without_cards_is_an_error():
    preview = preview_csv_import(
        b'front;back\n',
        delimiter='semicolon',
    )
    assert preview.errors[0].code == 'empty_file'


def test_advanced_header_only_card_and_existing_duplicate_warning():
    existing = Card(upper_header='Only header')
    preview = preview_csv_import(
        b'front;back;upper_header\n;;Only header\n',
        delimiter='semicolon',
        authoring_mode=AuthoringMode.ADVANCED,
        existing_cards=[existing],
    )
    assert preview.accepted_count == 1
    assert preview.warnings[0].code == 'duplicate_row'


def test_preview_serialization_reports_both_truncation_branches():
    rows = preview_csv_import(
        b'front;back\nQ1;A1\nQ2;A2\n',
        delimiter='semicolon',
    )
    issues = preview_csv_import(
        b'front;back\nQ;A\nQ;A\n',
        delimiter='semicolon',
    )
    assert rows.to_dict(preview_limit=1)['truncated'] is True
    assert issues.to_dict(preview_limit=1)['truncated'] is True
    assert rows.to_dict(preview_limit=20)['truncated'] is False


def test_bulk_doubled_quote_and_invalid_suffix():
    assert parse_bulk_line('"A ""quote"""||B') == ['A "quote"', 'B']
    with pytest.raises(ValueError, match='закрывающей'):
        parse_bulk_line('"A"suffix||B')


def test_bulk_reports_empty_input_and_duplicate_warning():
    empty = preview_bulk_import('\n  \n')
    assert empty.skipped_count == 2
    assert empty.errors[0].code == 'empty_input'

    duplicate = preview_bulk_import(
        'Q||A',
        existing_cards=[Card(front='Q', back='A')],
    )
    assert duplicate.warnings[0].code == 'duplicate_row'


def test_semicolon_csv_preserves_unquoted_commas_in_raw_content():
    source = (
        'section;front;back;upper_header;lower_header\n'
        'Math;\\text{a, b};Back, with comma;Top, 1;Bottom, 2\n'
    ).encode()
    preview = preview_csv_import(
        source,
        authoring_mode=AuthoringMode.ADVANCED,
    )
    row = preview.rows[0]
    assert preview.delimiter == ';'
    assert row.front == r'\text{a, b}'
    assert row.back == 'Back, with comma'
    assert row.upper_header == 'Top, 1'
    assert row.lower_header == 'Bottom, 2'


def test_comma_csv_requires_delimiter_inside_field_to_be_quoted():
    good = (
        'section,front,back,upper_header,lower_header\r\n'
        'Math,"\\text{a, b}","Back, quoted",Top,Bottom\r\n'
    ).encode()
    preview = preview_csv_import(
        good,
        authoring_mode=AuthoringMode.ADVANCED,
    )
    assert preview.delimiter == ','
    assert preview.rows[0].front == r'\text{a, b}'
    assert preview.rows[0].back == 'Back, quoted'

    bad = (
        'section,front,back,upper_header,lower_header\n'
        'Math,\\text{a, b},Back,Top,Bottom\n'
    ).encode()
    rejected = preview_csv_import(
        bad,
        delimiter='comma',
        authoring_mode=AuthoringMode.ADVANCED,
    )
    assert rejected.accepted_count == 0
    assert rejected.errors[0].code == 'column_count'


def test_raw_csv_round_trip_preserves_quotes_whitespace_and_multiline_crlf():
    expected = [
        ' Raw ',
        '  \\begin{minipage}{2cm}\r\nA, B; C "quoted"\r\n\\end{minipage}  ',
        'Back\r\nline',
        ' Head "A" ',
        ' Foot; value ',
    ]
    output = io.StringIO(newline='')
    writer = csv.writer(output, delimiter=',', lineterminator='\r\n')
    writer.writerow(['section', 'front', 'back', 'upper_header', 'lower_header'])
    writer.writerow(expected)

    preview = preview_csv_import(
        output.getvalue().encode('utf-8-sig'),
        authoring_mode=AuthoringMode.ADVANCED,
    )

    assert preview.errors == ()
    assert list(preview.rows[0].values()) == expected


def test_unclosed_multiline_quote_is_rejected_atomically():
    preview = preview_csv_import(
        b'front,back\n"line one\nline two,A\n',
        delimiter='comma',
    )
    assert preview.accepted_count == 0
    assert preview.errors[0].code == 'malformed_csv'


def test_header_contract_rejects_unknown_duplicate_and_advanced_columns():
    duplicate = preview_csv_import(
        b'front;front;back\nA;B;C\n',
        delimiter='semicolon',
    )
    unknown = preview_csv_import(
        b'front;answer\nA;B\n',
        delimiter='semicolon',
    )
    advanced_only = preview_csv_import(
        b'front;back;upper_header\nQ;A;raw\n',
        delimiter='semicolon',
    )
    assert duplicate.errors[0].code == 'duplicate_column'
    assert {issue.code for issue in unknown.errors} == {
        'unknown_column', 'missing_column'
    }
    assert advanced_only.errors[0].code == 'unknown_column'
