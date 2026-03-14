from dataclasses import dataclass, field


@dataclass
class CardLayoutConfig:
    """Параметры раскладки карточек на странице."""
    card_width_cm: float = 9.3
    card_height_cm: float = 6.3
    cards_per_row: int = 2
    rows_per_page: int = 4
    fbox_sep_pt: int = 8

    @property
    def cards_per_page(self):
        return self.cards_per_row * self.rows_per_page


@dataclass
class AppConfig:
    """Конфигурация приложения."""
    secret_key: str = 'change-me-in-production-use-env-variable'
    pdflatex_path: str = 'pdflatex'
    pdflatex_timeout: int = 30
    max_cards: int = 200
    layout: CardLayoutConfig = field(default_factory=CardLayoutConfig)