from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import secrets
import stat

import fcntl

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


def _environment_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f'{name} must be an integer') from error


def load_or_create_local_secret(data_dir: Path) -> str:
    """Return one process-safe local secret for every app worker.

    Production deployments should provide ``DIDACTIC_CARDS_SECRET_KEY``. The
    file fallback keeps the zero-configuration local launch usable with more
    than one WSGI worker without weakening CSRF/session consistency.
    """
    data_dir_existed = data_dir.exists()
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not data_dir_existed:
        os.chmod(data_dir, 0o700)
    secret_file = data_dir / '.secret_key'
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    file_descriptor = os.open(secret_file, flags, 0o600)
    with os.fdopen(file_descriptor, 'r+', encoding='utf-8') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        file_mode = os.fstat(handle.fileno()).st_mode
        if not stat.S_ISREG(file_mode):
            raise RuntimeError('local secret path must be a regular file')
        os.fchmod(handle.fileno(), 0o600)
        handle.seek(0)
        secret = handle.read().strip()
        if not secret:
            secret = secrets.token_urlsafe(32)
            handle.seek(0)
            handle.truncate()
            handle.write(secret)
            handle.flush()
            os.fsync(handle.fileno())
        return secret


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
    back_rotation_deg: int = 180
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
        if isinstance(self.back_rotation_deg, bool) or self.back_rotation_deg not in {
            0, 180
        }:
            raise ValueError('back rotation must be 0 or 180 degrees')

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
            back_rotation_deg=0,
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
    secret_key: str | None = field(default_factory=lambda: os.environ.get(
        'DIDACTIC_CARDS_SECRET_KEY'
    ))
    pdflatex_path: str = 'pdflatex'
    pdflatex_timeout: int = 30
    max_cards: int = 200
    trash_retention_days: int = field(default_factory=lambda: _environment_int(
        'DIDACTIC_CARDS_TRASH_RETENTION_DAYS', 30
    ))
    max_request_bytes: int = 2 * 1024 * 1024
    csrf_enabled: bool = True
    trusted_latex_enabled: bool = field(
        default_factory=lambda: _environment_bool(
            'DIDACTIC_CARDS_TRUSTED_LATEX_ENABLED'
        )
    )
    bwrap_path: str = '/usr/bin/bwrap'
    trusted_pdflatex_timeout: int = 30
    debug: bool = field(
        default_factory=lambda: _environment_bool('DIDACTIC_CARDS_DEBUG')
    )
    layout: CardLayoutConfig = field(default_factory=CardLayoutConfig)
    printer_profiles: tuple[PrinterProfile, ...] = field(
        default_factory=_default_printer_profiles
    )

    def __post_init__(self) -> None:
        if self.secret_key is not None and not self.secret_key:
            raise ValueError('DIDACTIC_CARDS_SECRET_KEY must not be empty')
        if not isinstance(self.debug, bool):
            raise ValueError('debug must be boolean')
        if not isinstance(self.trusted_latex_enabled, bool):
            raise ValueError('trusted LaTeX feature flag must be boolean')
        if (
            isinstance(self.trash_retention_days, bool)
            or not isinstance(self.trash_retention_days, int)
            or not 1 <= self.trash_retention_days <= 3650
        ):
            raise ValueError(
                'trash retention must be an integer from 1 to 3650 days'
            )
        if (
            isinstance(self.trusted_pdflatex_timeout, bool)
            or not isinstance(self.trusted_pdflatex_timeout, int)
            or self.trusted_pdflatex_timeout <= 0
        ):
            raise ValueError('trusted pdflatex timeout must be positive')
        keys = [profile.key for profile in self.printer_profiles]
        if len(keys) != len(set(keys)):
            raise ValueError('printer profile keys must be unique')
