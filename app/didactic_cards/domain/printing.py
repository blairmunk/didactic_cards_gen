from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from .entities import Card


class DuplexMode(str, Enum):
    """How a portrait sheet is flipped by a duplex printer."""

    LONG_EDGE = "long-edge"
    SHORT_EDGE = "short-edge"


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
