from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from typing import Iterable

from ..domain.entities import Card
from ..domain.rendering import AuthoringMode


CSV_DELIMITERS = {
    'comma': ',',
    'semicolon': ';',
    'tab': '\t',
}
CSV_ENCODINGS = {
    'utf-8': 'utf-8-sig',
    'utf-16': 'utf-16',
    'windows-1251': 'cp1251',
}
SAFE_COLUMNS = ('section', 'front', 'back')
ADVANCED_COLUMNS = (
    'section', 'front', 'back', 'upper_header', 'lower_header'
)
REQUIRED_COLUMNS = frozenset({'front', 'back'})
MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ROWS = 10_000
MAX_IMPORT_FIELD_CHARS = 200_000


class CsvSyntaxFailure(ValueError):
    def __init__(self, row: int, detail: str):
        self.row = row
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class CardImportRow:
    row: int
    section: str = ''
    front: str = ''
    back: str = ''
    upper_header: str = ''
    lower_header: str = ''

    def to_dict(self) -> dict:
        return {
            'row': self.row,
            'section': self.section,
            'front': self.front,
            'back': self.back,
            'upper_header': self.upper_header,
            'lower_header': self.lower_header,
        }

    def values(self) -> tuple[str, str, str, str, str]:
        return (
            self.section,
            self.front,
            self.back,
            self.upper_header,
            self.lower_header,
        )

    def to_card(self) -> Card:
        return Card(
            section=self.section,
            front=self.front,
            back=self.back,
            upper_header=self.upper_header,
            lower_header=self.lower_header,
        )


@dataclass(frozen=True)
class ImportIssue:
    row: int
    code: str
    reason: str
    column: str | None = None
    severity: str = 'error'

    def to_dict(self) -> dict:
        return {
            'row': self.row,
            'code': self.code,
            'reason': self.reason,
            'column': self.column,
            'severity': self.severity,
        }


@dataclass(frozen=True)
class CardImportPreview:
    rows: tuple[CardImportRow, ...]
    issues: tuple[ImportIssue, ...]
    source: str
    authoring_mode: str
    columns: tuple[str, ...]
    delimiter: str | None = None
    encoding: str | None = None
    skipped_count: int = 0

    @property
    def errors(self) -> tuple[ImportIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == 'error')

    @property
    def warnings(self) -> tuple[ImportIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == 'warning')

    @property
    def accepted_count(self) -> int:
        return len(self.rows)

    @property
    def rejected_count(self) -> int:
        return len({issue.row for issue in self.errors})

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    @property
    def rejected_rows(self) -> tuple[dict, ...]:
        return tuple(issue.to_dict() for issue in self.errors)

    def to_dict(self, preview_limit: int = 20) -> dict:
        shown_rows = self.rows[:preview_limit]
        shown_issues = self.issues[:preview_limit]
        return {
            'accepted_count': self.accepted_count,
            'rejected_count': self.rejected_count,
            'error_count': self.error_count,
            'warning_count': self.warning_count,
            'skipped_count': self.skipped_count,
            'source': self.source,
            'authoring_mode': self.authoring_mode,
            'columns': list(self.columns),
            'delimiter': self.delimiter,
            'encoding': self.encoding,
            'cards': [row.to_dict() for row in shown_rows],
            'issues': [issue.to_dict() for issue in shown_issues],
            'rejected_rows': [
                issue.to_dict() for issue in shown_issues
                if issue.severity == 'error'
            ],
            'truncated': (
                len(self.rows) > preview_limit
                or len(self.issues) > preview_limit
            ),
        }


CsvImportPreview = CardImportPreview
BulkImportPreview = CardImportPreview


def _mode_value(authoring_mode: AuthoringMode | str) -> str:
    return AuthoringMode(authoring_mode).value


def _decode_csv(file_bytes: bytes, encoding: str) -> tuple[str, str]:
    if len(file_bytes) > MAX_IMPORT_BYTES:
        raise ValueError(
            f'CSV превышает допустимый размер {MAX_IMPORT_BYTES} байт'
        )
    if encoding == 'auto':
        if file_bytes.startswith((b'\xff\xfe', b'\xfe\xff')):
            selected = 'utf-16'
        else:
            selected = 'utf-8'
    elif encoding in CSV_ENCODINGS:
        selected = encoding
    else:
        raise ValueError('Unsupported CSV encoding')
    return file_bytes.decode(CSV_ENCODINGS[selected]), selected


def _read_csv(text: str, delimiter: str) -> list[tuple[int, list[str]]]:
    reader = csv.reader(
        io.StringIO(text, newline=''),
        delimiter=delimiter,
        strict=True,
    )
    records: list[tuple[int, list[str]]] = []
    previous_line = 0
    while True:
        start_line = previous_line + 1
        try:
            row = next(reader)
        except StopIteration:
            break
        except csv.Error as error:
            raise CsvSyntaxFailure(
                max(start_line, reader.line_num), str(error)
            ) from error
        records.append((start_line, row))
        previous_line = reader.line_num
        if len(records) > MAX_IMPORT_ROWS:
            raise ValueError(
                f'CSV содержит более {MAX_IMPORT_ROWS} строк'
            )
    return records


def _first_record(text: str, delimiter: str) -> list[str] | None:
    reader = csv.reader(
        io.StringIO(text, newline=''),
        delimiter=delimiter,
        strict=True,
    )
    try:
        return next(reader)
    except (StopIteration, csv.Error):
        return None


def _select_delimiter(text: str, delimiter: str, mode: str) -> str:
    if delimiter != 'auto':
        if delimiter not in CSV_DELIMITERS:
            raise ValueError('Unsupported CSV delimiter')
        return CSV_DELIMITERS[delimiter]
    allowed = set(
        ADVANCED_COLUMNS
        if mode == AuthoringMode.ADVANCED.value
        else SAFE_COLUMNS
    )
    header_candidates = []
    structural_candidates = []
    for candidate in CSV_DELIMITERS.values():
        first = _first_record(text, candidate)
        if first is not None and len(first) >= 2:
            structural_candidates.append(candidate)
            if REQUIRED_COLUMNS <= set(first) <= allowed:
                header_candidates.append(candidate)
    candidates = header_candidates or structural_candidates
    if len(candidates) != 1:
        raise ValueError(
            'Не удалось однозначно определить разделитель CSV; выберите его вручную'
        )
    return candidates[0]


def _control_issue(row: int, column: str, value: str) -> ImportIssue | None:
    for char in value:
        if ord(char) < 32 and char not in {'\t', '\n', '\r'}:
            return ImportIssue(
                row,
                'control_character',
                'Поле содержит недопустимый управляющий символ',
                column,
            )
    if len(value) > MAX_IMPORT_FIELD_CHARS:
        return ImportIssue(
            row,
            'field_too_large',
            f'Поле длиннее {MAX_IMPORT_FIELD_CHARS} символов',
            column,
        )
    return None


def _row_is_empty(row: list[str]) -> bool:
    return not row or not any(value.strip() for value in row)


def _card_is_empty(row: CardImportRow, mode: str) -> bool:
    values = [row.front, row.back]
    if mode == AuthoringMode.ADVANCED.value:
        values.extend((row.upper_header, row.lower_header))
    return not any(value.strip() for value in values)


def _duplicate_warnings(
    rows: list[CardImportRow],
    existing_cards: Iterable[Card],
) -> list[ImportIssue]:
    seen = {
        (
            card.section, card.front, card.back,
            card.upper_header, card.lower_header,
        )
        for card in existing_cards
    }
    issues = []
    for row in rows:
        key = row.values()
        if key in seen:
            issues.append(ImportIssue(
                row.row,
                'duplicate_row',
                'Карточка дублирует другую строку или существующую карточку',
                severity='warning',
            ))
        seen.add(key)
    return issues


def preview_csv_import(
    file_bytes: bytes,
    delimiter: str = 'auto',
    *,
    authoring_mode: AuthoringMode | str = AuthoringMode.SAFE,
    encoding: str = 'utf-8',
    existing_cards: Iterable[Card] = (),
) -> CsvImportPreview:
    mode = _mode_value(authoring_mode)
    text, selected_encoding = _decode_csv(file_bytes, encoding)
    if not text:
        return CardImportPreview(
            (),
            (ImportIssue(1, 'empty_file', 'CSV-файл пуст'),),
            'csv', mode, (),
            encoding=selected_encoding,
        )
    try:
        selected_delimiter = _select_delimiter(text, delimiter, mode)
    except ValueError:
        if not text.strip():
            return CardImportPreview(
                (),
                (ImportIssue(1, 'empty_file', 'CSV-файл пуст'),),
                'csv', mode, (),
                encoding=selected_encoding,
            )
        raise
    try:
        records = _read_csv(text, selected_delimiter)
    except CsvSyntaxFailure as error:
        return CardImportPreview(
            (),
            (ImportIssue(
                error.row,
                'malformed_csv',
                f'Некорректное quoting CSV: {error.detail}',
            ),),
            'csv', mode, (), selected_delimiter, selected_encoding,
        )

    issues: list[ImportIssue] = []
    rows: list[CardImportRow] = []
    skipped = 0
    columns: tuple[str, ...]
    data_records = records
    if not records or _row_is_empty(records[0][1]):
        issues.append(ImportIssue(
            1, 'missing_header', 'CSV не содержит строку заголовка'
        ))
        columns = ()
        data_records = ()
    else:
        header_row, header = records[0]
        columns = tuple(header)
        data_records = records[1:]
        allowed = set(
            ADVANCED_COLUMNS
            if mode == AuthoringMode.ADVANCED.value
            else SAFE_COLUMNS
        )
        duplicates = {
            name for name in columns if columns.count(name) > 1
        }
        for name in sorted(duplicates):
            issues.append(ImportIssue(
                header_row,
                'duplicate_column',
                f'Колонка {name!r} указана несколько раз',
                name,
            ))
        for name in columns:
            if not name:
                issues.append(ImportIssue(
                    header_row,
                    'empty_column',
                    'Имя колонки не может быть пустым',
                ))
            elif name not in allowed:
                issues.append(ImportIssue(
                    header_row,
                    'unknown_column',
                    f'Неизвестная колонка {name!r} для режима {mode}',
                    name,
                ))
        for name in sorted(REQUIRED_COLUMNS - set(columns)):
            issues.append(ImportIssue(
                header_row,
                'missing_column',
                f'Отсутствует обязательная колонка {name!r}',
                name,
            ))
        if issues:
            data_records = ()

    for row_number, values in data_records:
        if _row_is_empty(values):
            skipped += 1
            continue
        if len(values) != len(columns):
            issues.append(ImportIssue(
                row_number,
                'column_count',
                f'Ожидалось колонок: {len(columns)}, получено: {len(values)}',
            ))
            continue
        mapped = dict(zip(columns, values))
        row = CardImportRow(
            row=row_number,
            section=mapped.get('section', ''),
            front=mapped.get('front', ''),
            back=mapped.get('back', ''),
            upper_header=mapped.get('upper_header', ''),
            lower_header=mapped.get('lower_header', ''),
        )
        row_issues = [
            issue
            for column, value in zip(
                ADVANCED_COLUMNS,
                row.values(),
            )
            if (issue := _control_issue(row_number, column, value)) is not None
        ]
        if _card_is_empty(row, mode):
            row_issues.append(ImportIssue(
                row_number,
                'empty_card',
                'Строка не содержит данных карточки',
            ))
        if row_issues:
            issues.extend(row_issues)
        else:
            rows.append(row)

    if not rows and not issues:
        issues.append(ImportIssue(
            1, 'empty_file', 'CSV-файл не содержит карточек'
        ))
    issues.extend(_duplicate_warnings(rows, existing_cards))
    return CardImportPreview(
        tuple(rows), tuple(issues), 'csv', mode, columns,
        selected_delimiter, selected_encoding, skipped,
    )


def parse_bulk_line(line: str) -> list[str]:
    fields: list[str] = []
    current: list[str] = []
    in_quotes = False
    quoted = False
    closed_quote = False
    index = 0
    while index < len(line):
        char = line[index]
        if in_quotes:
            if char == '"':
                if index + 1 < len(line) and line[index + 1] == '"':
                    current.append('"')
                    index += 2
                    continue
                in_quotes = False
                closed_quote = True
                index += 1
                continue
            current.append(char)
            index += 1
            continue
        if closed_quote:
            if line.startswith('||', index):
                fields.append(''.join(current))
                current = []
                quoted = False
                closed_quote = False
                index += 2
                continue
            raise ValueError('После закрывающей кавычки ожидался разделитель ||')
        if not current and not quoted and char == '"':
            quoted = True
            in_quotes = True
            index += 1
            continue
        if line.startswith('||', index):
            fields.append(''.join(current))
            current = []
            quoted = False
            index += 2
            continue
        current.append(char)
        index += 1
    if in_quotes:
        raise ValueError('Не закрыта двойная кавычка')
    fields.append(''.join(current))
    return fields


def preview_bulk_import(
    bulk_text: str,
    authoring_mode: AuthoringMode | str = AuthoringMode.SAFE,
    *,
    section: str = '',
    existing_cards: Iterable[Card] = (),
) -> BulkImportPreview:
    mode = _mode_value(authoring_mode)
    columns = (
        ('front', 'back', 'upper_header', 'lower_header')
        if mode == AuthoringMode.ADVANCED.value
        else ('front', 'back')
    )
    rows: list[CardImportRow] = []
    issues: list[ImportIssue] = []
    skipped = 0
    physical_lines = (
        re.split(r'\r\n|\r|\n', bulk_text) if bulk_text else []
    )
    if physical_lines and physical_lines[-1] == '' and bulk_text.endswith(
        ('\r', '\n')
    ):
        physical_lines.pop()
    for row_number, line in enumerate(physical_lines, start=1):
        if not line.strip():
            skipped += 1
            continue
        try:
            values = parse_bulk_line(line)
        except ValueError as error:
            issues.append(ImportIssue(
                row_number, 'malformed_bulk', str(error)
            ))
            continue
        if len(values) != len(columns):
            issues.append(ImportIssue(
                row_number,
                'column_count',
                f'Ожидалось полей: {len(columns)}, получено: {len(values)}',
            ))
            continue
        mapped = dict(zip(columns, values))
        row = CardImportRow(
            row=row_number,
            section=section,
            front=mapped.get('front', ''),
            back=mapped.get('back', ''),
            upper_header=mapped.get('upper_header', ''),
            lower_header=mapped.get('lower_header', ''),
        )
        if _card_is_empty(row, mode):
            issues.append(ImportIssue(
                row_number, 'empty_card', 'Строка не содержит данных карточки'
            ))
            continue
        rows.append(row)
    if not rows and not issues:
        issues.append(ImportIssue(
            1, 'empty_input', 'Пакетный ввод не содержит карточек'
        ))
    issues.extend(_duplicate_warnings(rows, existing_cards))
    return CardImportPreview(
        tuple(rows), tuple(issues), 'bulk', mode, columns,
        delimiter='||', skipped_count=skipped,
    )
