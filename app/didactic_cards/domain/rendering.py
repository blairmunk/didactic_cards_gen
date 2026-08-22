from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StylePreset(str, Enum):
    LEGACY_TOP_LEFT = 'legacy-top-left'
    CENTERED = 'centered'
    CUSTOM = 'custom'


class HorizontalAlignment(str, Enum):
    LEFT = 'left'
    CENTER = 'center'
    RIGHT = 'right'


class VerticalAlignment(str, Enum):
    TOP = 'top'
    CENTER = 'center'
    BOTTOM = 'bottom'


class HeaderVisibility(str, Enum):
    NONE = 'none'
    FRONT = 'front'
    BACK = 'back'
    BOTH = 'both'


class HeaderPosition(str, Enum):
    TOP = 'top'
    BOTTOM = 'bottom'


class HeaderRepeat(str, Enum):
    EVERY_CARD = 'every-card'
    SECTION_START = 'section-start'


class SectionBreak(str, Enum):
    CONTINUOUS = 'continuous'
    NEW_ROW = 'new-row'
    NEW_SHEET = 'new-sheet'


@dataclass(frozen=True)
class DeckRenderSettings:
    """Safe, serializable presentation choices owned by one deck."""

    preset: StylePreset | str = StylePreset.CENTERED
    horizontal_alignment: HorizontalAlignment | str = HorizontalAlignment.CENTER
    vertical_alignment: VerticalAlignment | str = VerticalAlignment.CENTER
    header_visibility: HeaderVisibility | str = HeaderVisibility.NONE
    header_position: HeaderPosition | str = HeaderPosition.TOP
    header_alignment: HorizontalAlignment | str = HorizontalAlignment.LEFT
    header_repeat: HeaderRepeat | str = HeaderRepeat.EVERY_CARD
    section_break: SectionBreak | str = SectionBreak.CONTINUOUS

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, 'preset', StylePreset(self.preset))
            object.__setattr__(
                self,
                'horizontal_alignment',
                HorizontalAlignment(self.horizontal_alignment),
            )
            object.__setattr__(
                self,
                'vertical_alignment',
                VerticalAlignment(self.vertical_alignment),
            )
            object.__setattr__(
                self,
                'header_visibility',
                HeaderVisibility(self.header_visibility),
            )
            object.__setattr__(
                self,
                'header_position',
                HeaderPosition(self.header_position),
            )
            object.__setattr__(
                self,
                'header_alignment',
                HorizontalAlignment(self.header_alignment),
            )
            object.__setattr__(
                self, 'header_repeat', HeaderRepeat(self.header_repeat)
            )
            object.__setattr__(
                self, 'section_break', SectionBreak(self.section_break)
            )
        except ValueError as error:
            raise ValueError(f'unsupported deck render setting: {error}') from error

    @classmethod
    def centered(cls) -> DeckRenderSettings:
        return cls()

    @classmethod
    def legacy(cls) -> DeckRenderSettings:
        return cls(
            preset=StylePreset.LEGACY_TOP_LEFT,
            horizontal_alignment=HorizontalAlignment.LEFT,
            vertical_alignment=VerticalAlignment.TOP,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            'preset': self.preset.value,
            'horizontal_alignment': self.horizontal_alignment.value,
            'vertical_alignment': self.vertical_alignment.value,
            'header_visibility': self.header_visibility.value,
            'header_position': self.header_position.value,
            'header_alignment': self.header_alignment.value,
            'header_repeat': self.header_repeat.value,
            'section_break': self.section_break.value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DeckRenderSettings:
        if not isinstance(data, dict):
            raise ValueError('deck render settings must be an object')
        known_fields = {
            'preset',
            'horizontal_alignment',
            'vertical_alignment',
            'header_visibility',
            'header_position',
            'header_alignment',
            'header_repeat',
            'section_break',
        }
        unknown_fields = set(data) - known_fields
        if unknown_fields:
            raise ValueError(
                'unknown deck render settings: '
                + ', '.join(sorted(unknown_fields))
            )
        return cls(**data)
