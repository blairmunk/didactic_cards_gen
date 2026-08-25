from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import re
from typing import Sequence

from .entities import Card
from .safe_text import safe_single_line
from .rendering import SectionBreak


class DuplexMode(str, Enum):
    """How a portrait sheet is flipped by a duplex printer."""

    LONG_EDGE = "long-edge"
    SHORT_EDGE = "short-edge"


@dataclass(frozen=True)
class DuplexTransform:
    """Affine front-slot to printed-back-slot transform in grid coordinates."""

    rows: int
    columns: int
    duplex_mode: DuplexMode = DuplexMode.LONG_EDGE

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in (self.rows, self.columns)
        ):
            raise ValueError('duplex transform grid must be positive')
        object.__setattr__(self, 'duplex_mode', DuplexMode(self.duplex_mode))

    @property
    def matrix(self) -> tuple[int, int, int, int, int, int]:
        """CSS-style ``matrix(a,b,c,d,e,f)`` over (column, row)."""
        if self.duplex_mode is DuplexMode.LONG_EDGE:
            return (-1, 0, 0, 1, self.columns - 1, 0)
        return (1, 0, 0, -1, 0, self.rows - 1)

    @property
    def mirror_axis(self) -> str:
        return (
            'horizontal'
            if self.duplex_mode is DuplexMode.LONG_EDGE
            else 'vertical'
        )

    def target_coordinates(self, row: int, column: int) -> tuple[int, int]:
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (row, column)
        ) or not (0 <= row < self.rows and 0 <= column < self.columns):
            raise ValueError('slot coordinates are outside duplex grid')
        a, b, c, d, e, f = self.matrix
        target_column = a * column + c * row + e
        target_row = b * column + d * row + f
        return target_row, target_column

    def target_index(self, source_index: int) -> int:
        capacity = self.rows * self.columns
        if isinstance(source_index, bool) or not 0 <= source_index < capacity:
            raise ValueError('slot index is outside duplex grid')
        row, column = divmod(source_index, self.columns)
        target_row, target_column = self.target_coordinates(row, column)
        return target_row * self.columns + target_column

    def to_dict(self) -> dict:
        return {
            'duplex_mode': self.duplex_mode.value,
            'matrix': list(self.matrix),
            'mirror_axis': self.mirror_axis,
        }


@dataclass(frozen=True)
class PrintGeometry:
    """Renderer-owned physical geometry shared by PDF and HTML overlay."""

    profile_id: str
    profile_name: str
    rows: int
    columns: int
    page_width_mm: float
    page_height_mm: float
    grid_origin_x_mm: float
    grid_origin_y_mm: float
    card_width_mm: float
    card_height_mm: float
    row_gap_mm: float
    front_offset_x_mm: float
    front_offset_y_mm: float
    back_offset_x_mm: float
    back_offset_y_mm: float
    back_rotation_deg: int
    duplex_transform: DuplexTransform

    def __post_init__(self) -> None:
        if not isinstance(self.profile_id, str) or not self.profile_id or (
            not isinstance(self.profile_name, str) or not self.profile_name
        ):
            raise ValueError('print geometry profile identity is required')
        positive_dimensions = (
            self.page_width_mm,
            self.page_height_mm,
            self.card_width_mm,
            self.card_height_mm,
        )
        offsets_and_origins = (
            self.grid_origin_x_mm,
            self.grid_origin_y_mm,
            self.front_offset_x_mm,
            self.front_offset_y_mm,
            self.back_offset_x_mm,
            self.back_offset_y_mm,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0
            for value in positive_dimensions
        ) or any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in offsets_and_origins
        ):
            raise ValueError('print geometry dimensions must be finite')
        if (
            isinstance(self.row_gap_mm, bool)
            or not isinstance(self.row_gap_mm, (int, float))
            or not math.isfinite(self.row_gap_mm)
            or self.row_gap_mm < 0
        ):
            raise ValueError('print geometry row gap must be non-negative')
        if isinstance(self.back_rotation_deg, bool) or (
            self.back_rotation_deg not in {0, 180}
        ):
            raise ValueError('print geometry back rotation must be 0 or 180')
        if self.duplex_transform.rows != self.rows or (
            self.duplex_transform.columns != self.columns
        ):
            raise ValueError('print geometry and duplex grid do not match')

    def to_dict(self) -> dict:
        return {
            'profile_id': self.profile_id,
            'profile_name': self.profile_name,
            'rows': self.rows,
            'columns': self.columns,
            'page_width_mm': self.page_width_mm,
            'page_height_mm': self.page_height_mm,
            'grid_origin_x_mm': self.grid_origin_x_mm,
            'grid_origin_y_mm': self.grid_origin_y_mm,
            'card_width_mm': self.card_width_mm,
            'card_height_mm': self.card_height_mm,
            'row_gap_mm': self.row_gap_mm,
            'front_offset_x_mm': self.front_offset_x_mm,
            'front_offset_y_mm': self.front_offset_y_mm,
            'back_offset_x_mm': self.back_offset_x_mm,
            'back_offset_y_mm': self.back_offset_y_mm,
            'back_rotation_deg': self.back_rotation_deg,
            'transform': self.duplex_transform.to_dict(),
        }


@dataclass(frozen=True)
class PrinterProfile:
    """Named printer-specific calibration layered over the base card layout."""

    key: str
    name: str
    duplex_mode: DuplexMode = DuplexMode.LONG_EDGE
    back_rotation_deg: int = 180
    front_offset_x_mm: float = 0.0
    front_offset_y_mm: float = 0.0
    back_offset_x_mm: float = 0.0
    back_offset_y_mm: float = 0.0
    back_border: bool = False
    registration_marks: bool = False

    def __post_init__(self) -> None:
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', self.key):
            raise ValueError('printer profile key must be a lowercase slug')
        if not self.name.strip() or len(self.name) > 100:
            raise ValueError('printer profile name must contain 1..100 characters')
        offsets = (
            self.front_offset_x_mm,
            self.front_offset_y_mm,
            self.back_offset_x_mm,
            self.back_offset_y_mm,
        )
        if not all(math.isfinite(offset) for offset in offsets):
            raise ValueError('printer profile offsets must be finite')
        if any(abs(offset) > 10 for offset in offsets):
            raise ValueError('printer profile offsets must be within +/- 10 mm')
        if not isinstance(self.back_border, bool) or not isinstance(
            self.registration_marks, bool
        ):
            raise ValueError('printer profile flags must be boolean')
        if isinstance(self.back_rotation_deg, bool) or self.back_rotation_deg not in {
            0, 180
        }:
            raise ValueError('back rotation must be 0 or 180 degrees')
        object.__setattr__(self, 'duplex_mode', DuplexMode(self.duplex_mode))


def recommend_back_offsets(
    profile: PrinterProfile,
    measured_x_mm: float,
    measured_y_mm: float,
) -> tuple[float, float]:
    """Return corrected back offsets from a face-up transmitted-light reading.

    ``measured_x_mm`` and ``measured_y_mm`` describe where the printed back
    target appears relative to the front target: right/down are positive.
    The duplex transform changes the correction signs between flip modes.
    """
    if not isinstance(profile, PrinterProfile):
        raise TypeError('profile must be PrinterProfile')
    if not all(math.isfinite(value) for value in (measured_x_mm, measured_y_mm)):
        raise ValueError('calibration measurements must be finite')
    if profile.duplex_mode is DuplexMode.LONG_EDGE:
        corrected_x = profile.back_offset_x_mm + measured_x_mm
        corrected_y = profile.back_offset_y_mm - measured_y_mm
    else:
        corrected_x = profile.back_offset_x_mm - measured_x_mm
        corrected_y = profile.back_offset_y_mm + measured_y_mm
    return round(corrected_x, 3), round(corrected_y, 3)


@dataclass(frozen=True)
class Sheet:
    """A physical sheet represented in PDF page reading order."""

    front_slots: tuple[Card, ...]
    back_slots: tuple[Card, ...]


@dataclass
class PrintPaddingCard(Card):
    """Ephemeral blank slot which is never persisted as a user card."""


@dataclass(frozen=True)
class PrintLayout:
    """Physical front slots before the duplex permutation is applied."""

    cards: tuple[Card, ...]
    section_padding: int
    trailing_padding: int


def build_print_layout(
    cards: Sequence[Card],
    *,
    rows: int,
    columns: int,
    section_break: SectionBreak | str = SectionBreak.CONTINUOUS,
) -> PrintLayout:
    """Insert section gaps and final padding in physical front-slot order."""
    if rows <= 0 or columns <= 0:
        raise ValueError("rows and columns must be positive")
    try:
        break_mode = SectionBreak(section_break)
    except ValueError as error:
        raise ValueError(f"unsupported section break: {section_break}") from error

    capacity = rows * columns
    laid_out: list[Card] = []
    section_padding = 0
    previous_section: str | None = None
    for card in cards:
        section = safe_single_line(card.section)
        section_changed = (
            previous_section is not None and section != previous_section
        )
        if section_changed and break_mode is not SectionBreak.CONTINUOUS:
            boundary = (
                columns
                if break_mode is SectionBreak.NEW_ROW
                else capacity
            )
            gap = (-len(laid_out)) % boundary
            laid_out.extend(PrintPaddingCard() for _ in range(gap))
            section_padding += gap
        laid_out.append(card)
        previous_section = section

    trailing_padding = (-len(laid_out)) % capacity if laid_out else 0
    laid_out.extend(PrintPaddingCard() for _ in range(trailing_padding))
    return PrintLayout(tuple(laid_out), section_padding, trailing_padding)


def build_sheets(
    cards: Sequence[Card],
    *,
    rows: int,
    columns: int,
    duplex_mode: DuplexMode | str = DuplexMode.LONG_EDGE,
) -> list[Sheet]:
    """Build front/back page pairs with one stable transform per physical sheet."""
    if rows <= 0 or columns <= 0:
        raise ValueError("rows and columns must be positive")

    try:
        transform = DuplexTransform(rows, columns, DuplexMode(duplex_mode))
    except ValueError as error:
        raise ValueError(f"unsupported duplex mode: {duplex_mode}") from error

    capacity = rows * columns
    sheets: list[Sheet] = []
    for offset in range(0, len(cards), capacity):
        front = list(cards[offset:offset + capacity])
        front.extend(PrintPaddingCard() for _ in range(capacity - len(front)))
        back: list[Card | None] = [None] * capacity

        for source_index, card in enumerate(front):
            back[transform.target_index(source_index)] = card

        sheets.append(Sheet(tuple(front), tuple(card for card in back if card is not None)))

    return sheets
