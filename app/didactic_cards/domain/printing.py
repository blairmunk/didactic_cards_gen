from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import re
from typing import Sequence

from .entities import Card


class DuplexMode(str, Enum):
    """How a portrait sheet is flipped by a duplex printer."""

    LONG_EDGE = "long-edge"
    SHORT_EDGE = "short-edge"


@dataclass(frozen=True)
class PrinterProfile:
    """Named printer-specific calibration layered over the base card layout."""

    key: str
    name: str
    duplex_mode: DuplexMode = DuplexMode.LONG_EDGE
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
        object.__setattr__(self, 'duplex_mode', DuplexMode(self.duplex_mode))


@dataclass(frozen=True)
class Sheet:
    """A physical sheet represented in PDF page reading order."""

    front_slots: tuple[Card, ...]
    back_slots: tuple[Card, ...]


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
        mode = DuplexMode(duplex_mode)
    except ValueError as error:
        raise ValueError(f"unsupported duplex mode: {duplex_mode}") from error

    capacity = rows * columns
    sheets: list[Sheet] = []
    for offset in range(0, len(cards), capacity):
        front = list(cards[offset:offset + capacity])
        front.extend(Card() for _ in range(capacity - len(front)))
        back: list[Card | None] = [None] * capacity

        for source_index, card in enumerate(front):
            row, column = divmod(source_index, columns)
            if mode is DuplexMode.LONG_EDGE:
                target_row, target_column = row, columns - 1 - column
            else:
                target_row, target_column = rows - 1 - row, column
            back[target_row * columns + target_column] = card

        sheets.append(Sheet(tuple(front), tuple(card for card in back if card is not None)))

    return sheets
