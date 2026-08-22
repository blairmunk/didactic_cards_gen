from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import secrets

from didactic_cards.domain.printing import DuplexMode, PrinterProfile


def _environment_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {'1', 'true', 'yes', 'on'}:
        return True
    if normalized in {'0', 'false', 'no', 'off'}:
        return False
    raise ValueError(f'{name} must be a boolean value')


@dataclass
class CardLayoutConfig:
    """Параметры раскладки карточек на странице."""
    card_width_cm: float = 9.3
    card_height_cm: float = 6.3
    cards_per_row: int = 2
    rows_per_page: int = 4
    fbox_sep_pt: int = 8
    fbox_rule_pt: float = 0.4
    back_border: bool = False  # True = рамка на обороте (для отладки центровки)
    duplex_mode: DuplexMode = DuplexMode.LONG_EDGE
    front_offset_x_mm: float = 0.0
    front_offset_y_mm: float = 0.0
    back_offset_x_mm: float = 0.0
    back_offset_y_mm: float = 0.0
    registration_marks: bool = False
    auto_fit: bool = True

    def __post_init__(self) -> None:
        if self.cards_per_row <= 0 or self.rows_per_page <= 0:
            raise ValueError('cards_per_row and rows_per_page must be positive')
        numeric_values = (
            self.card_width_cm,
            self.card_height_cm,
            self.fbox_sep_pt,
            self.fbox_rule_pt,
            self.front_offset_x_mm,
            self.front_offset_y_mm,
            self.back_offset_x_mm,
            self.back_offset_y_mm,
        )
        if not all(math.isfinite(value) for value in numeric_values):
            raise ValueError('layout dimensions must be finite')
        if self.card_width_cm <= 0 or self.card_height_cm <= 0:
            raise ValueError('card dimensions must be positive')
        if self.fbox_sep_pt < 0 or self.fbox_rule_pt < 0:
            raise ValueError('frame spacing and rule must not be negative')
        if not isinstance(self.auto_fit, bool):
            raise ValueError('auto_fit must be boolean')
        offsets = (
            self.front_offset_x_mm,
            self.front_offset_y_mm,
            self.back_offset_x_mm,
            self.back_offset_y_mm,
        )
        if any(abs(offset) > 10 for offset in offsets):
            raise ValueError('calibration offsets must be within +/- 10 mm')

        frame_inset_cm = 2 * (self.fbox_sep_pt + self.fbox_rule_pt) * 2.54 / 72.27
        if self.card_width_cm <= frame_inset_cm or self.card_height_cm <= frame_inset_cm:
            raise ValueError('card dimensions are too small for frame spacing')
        if self.cards_per_row * self.card_width_cm > 20.0:
            raise ValueError('card grid does not fit A4 printable width')
        row_advance_cm = self.card_height_cm + 2 * 2.54 / 72.27
        if self.rows_per_page * row_advance_cm > 28.7:
            raise ValueError('card grid does not fit A4 printable height')

        self.duplex_mode = DuplexMode(self.duplex_mode)

    @property
    def cards_per_page(self):
        return self.cards_per_row * self.rows_per_page


def _default_printer_profiles() -> tuple[PrinterProfile, ...]:
    return (
        PrinterProfile(
            key='standard-long-edge',
            name='Стандартный long-edge',
        ),
        PrinterProfile(
            key='calibration-long-edge',
            name='Калибровочный long-edge',
            back_border=True,
            registration_marks=True,
        ),
        PrinterProfile(
            key='standard-short-edge',
            name='Стандартный short-edge',
            duplex_mode=DuplexMode.SHORT_EDGE,
        ),
    )


@dataclass
class AppConfig:
    """Конфигурация приложения."""
    data_dir: Path = field(default_factory=lambda: Path(
        os.environ.get(
            'DIDACTIC_CARDS_DATA_DIR',
            Path(__file__).resolve().parent / 'data',
        )
    ).expanduser().resolve())
    secret_key: str = field(default_factory=lambda: os.environ.get(
        'DIDACTIC_CARDS_SECRET_KEY', secrets.token_urlsafe(32)
    ))
    pdflatex_path: str = 'pdflatex'
    pdflatex_timeout: int = 30
    max_cards: int = 200
    max_request_bytes: int = 2 * 1024 * 1024
    csrf_enabled: bool = True
    debug: bool = field(
        default_factory=lambda: _environment_bool('DIDACTIC_CARDS_DEBUG')
    )
    layout: CardLayoutConfig = field(default_factory=CardLayoutConfig)
    printer_profiles: tuple[PrinterProfile, ...] = field(
        default_factory=_default_printer_profiles
    )

    def __post_init__(self) -> None:
        if not isinstance(self.debug, bool):
            raise ValueError('debug must be boolean')
        keys = [profile.key for profile in self.printer_profiles]
        if len(keys) != len(set(keys)):
            raise ValueError('printer profile keys must be unique')
