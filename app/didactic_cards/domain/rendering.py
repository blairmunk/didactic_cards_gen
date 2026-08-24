from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class StylePreset(str, Enum):
    CENTERED = 'centered'
    CUSTOM = 'custom'


class AuthoringMode(str, Enum):
    SAFE = 'safe'
    ADVANCED = 'advanced'


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


class HeaderSource(str, Enum):
    SECTION = 'section'
    CARD_NUMBER = 'card-number'
    CUSTOM = 'custom'


class HeaderRule(str, Enum):
    NONE = 'none'
    THIN = 'thin'
    MEDIUM = 'medium'


class HeaderRuleSpacing(str, Enum):
    COMPACT = 'compact'
    NORMAL = 'normal'
    RELAXED = 'relaxed'


class SectionBreak(str, Enum):
    CONTINUOUS = 'continuous'
    NEW_ROW = 'new-row'
    NEW_SHEET = 'new-sheet'


class TypographyProfile(str, Enum):
    OFF = 'off'
    BOOK = 'book'
    SANS_LARGE = 'sans-large'
    COMPACT = 'compact'
    CUSTOM = 'custom'


class FontFamily(str, Enum):
    SERIF = 'serif'
    SANS = 'sans'
    MONO = 'mono'


class FontSize(str, Enum):
    SMALL = 'small'
    NORMAL = 'normal'
    LARGE = 'large'


class FontWeight(str, Enum):
    NORMAL = 'normal'
    BOLD = 'bold'


class FontStyle(str, Enum):
    UPRIGHT = 'upright'
    ITALIC = 'italic'


class LineSpacing(str, Enum):
    COMPACT = 'compact'
    NORMAL = 'normal'
    RELAXED = 'relaxed'


class ParagraphSpacing(str, Enum):
    NONE = 'none'
    SMALL = 'small'
    MEDIUM = 'medium'


@dataclass(frozen=True)
class TextStyle:
    family: FontFamily
    size: FontSize
    weight: FontWeight
    style: FontStyle


@dataclass(frozen=True)
class ResolvedTypography:
    body: TextStyle
    primary_header: TextStyle
    secondary_header: TextStyle
    line_spacing: LineSpacing
    paragraph_spacing: ParagraphSpacing


_PROFILE_TYPOGRAPHY = {
    TypographyProfile.BOOK: ResolvedTypography(
        body=TextStyle(FontFamily.SERIF, FontSize.NORMAL, FontWeight.NORMAL, FontStyle.UPRIGHT),
        primary_header=TextStyle(
            FontFamily.SERIF, FontSize.SMALL,
            FontWeight.NORMAL, FontStyle.ITALIC,
        ),
        secondary_header=TextStyle(
            FontFamily.SERIF, FontSize.SMALL,
            FontWeight.NORMAL, FontStyle.UPRIGHT,
        ),
        line_spacing=LineSpacing.NORMAL,
        paragraph_spacing=ParagraphSpacing.SMALL,
    ),
    TypographyProfile.SANS_LARGE: ResolvedTypography(
        body=TextStyle(FontFamily.SANS, FontSize.LARGE, FontWeight.NORMAL, FontStyle.UPRIGHT),
        primary_header=TextStyle(
            FontFamily.SANS, FontSize.SMALL,
            FontWeight.BOLD, FontStyle.UPRIGHT,
        ),
        secondary_header=TextStyle(
            FontFamily.SANS, FontSize.SMALL,
            FontWeight.NORMAL, FontStyle.UPRIGHT,
        ),
        line_spacing=LineSpacing.RELAXED,
        paragraph_spacing=ParagraphSpacing.SMALL,
    ),
    TypographyProfile.COMPACT: ResolvedTypography(
        body=TextStyle(FontFamily.SERIF, FontSize.SMALL, FontWeight.NORMAL, FontStyle.UPRIGHT),
        primary_header=TextStyle(
            FontFamily.SANS, FontSize.SMALL,
            FontWeight.BOLD, FontStyle.UPRIGHT,
        ),
        secondary_header=TextStyle(
            FontFamily.SANS, FontSize.SMALL,
            FontWeight.NORMAL, FontStyle.UPRIGHT,
        ),
        line_spacing=LineSpacing.COMPACT,
        paragraph_spacing=ParagraphSpacing.NONE,
    ),
}


@dataclass(frozen=True)
class DeckRenderSettings:
    """Safe, serializable presentation choices owned by one deck.

    Every value is an allow-listed token. No field accepts a LaTeX command.
    Typography is a separate layer: ``off`` preserves the historic renderer.
    ``advanced`` keeps the physical deck geometry but bypasses every built-in
    presentation choice, with or without an approved shared wrapper.
    """

    authoring_mode: AuthoringMode | str = AuthoringMode.SAFE
    preset: StylePreset | str = StylePreset.CENTERED
    horizontal_alignment: HorizontalAlignment | str = HorizontalAlignment.CENTER
    vertical_alignment: VerticalAlignment | str = VerticalAlignment.CENTER
    header_visibility: HeaderVisibility | str = HeaderVisibility.NONE
    header_position: HeaderPosition | str = HeaderPosition.TOP
    header_alignment: HorizontalAlignment | str = HorizontalAlignment.LEFT
    header_repeat: HeaderRepeat | str = HeaderRepeat.EVERY_CARD
    section_break: SectionBreak | str = SectionBreak.CONTINUOUS
    typography_profile: TypographyProfile | str = TypographyProfile.OFF
    body_font_family: FontFamily | str = FontFamily.SERIF
    body_font_size: FontSize | str = FontSize.NORMAL
    body_font_weight: FontWeight | str = FontWeight.NORMAL
    body_font_style: FontStyle | str = FontStyle.UPRIGHT
    line_spacing: LineSpacing | str = LineSpacing.NORMAL
    paragraph_spacing: ParagraphSpacing | str = ParagraphSpacing.NONE
    header_source: HeaderSource | str = HeaderSource.SECTION
    header_text: str = ''
    header_font_family: FontFamily | str = FontFamily.SANS
    header_font_size: FontSize | str = FontSize.SMALL
    header_font_weight: FontWeight | str = FontWeight.NORMAL
    header_font_style: FontStyle | str = FontStyle.UPRIGHT
    header_rule: HeaderRule | str = HeaderRule.NONE
    header_rule_spacing: HeaderRuleSpacing | str = HeaderRuleSpacing.NORMAL
    secondary_header_visibility: HeaderVisibility | str = HeaderVisibility.NONE
    secondary_header_position: HeaderPosition | str = HeaderPosition.BOTTOM
    secondary_header_alignment: HorizontalAlignment | str = HorizontalAlignment.RIGHT
    secondary_header_repeat: HeaderRepeat | str = HeaderRepeat.EVERY_CARD
    secondary_header_source: HeaderSource | str = HeaderSource.CARD_NUMBER
    secondary_header_text: str = ''
    secondary_header_font_family: FontFamily | str = FontFamily.SANS
    secondary_header_font_size: FontSize | str = FontSize.SMALL
    secondary_header_font_weight: FontWeight | str = FontWeight.NORMAL
    secondary_header_font_style: FontStyle | str = FontStyle.UPRIGHT
    secondary_header_rule: HeaderRule | str = HeaderRule.NONE
    secondary_header_rule_spacing: HeaderRuleSpacing | str = HeaderRuleSpacing.NORMAL

    def __post_init__(self) -> None:
        enum_fields = {
            'authoring_mode': AuthoringMode,
            'preset': StylePreset,
            'horizontal_alignment': HorizontalAlignment,
            'vertical_alignment': VerticalAlignment,
            'header_visibility': HeaderVisibility,
            'header_position': HeaderPosition,
            'header_alignment': HorizontalAlignment,
            'header_repeat': HeaderRepeat,
            'section_break': SectionBreak,
            'typography_profile': TypographyProfile,
            'body_font_family': FontFamily,
            'body_font_size': FontSize,
            'body_font_weight': FontWeight,
            'body_font_style': FontStyle,
            'line_spacing': LineSpacing,
            'paragraph_spacing': ParagraphSpacing,
            'header_source': HeaderSource,
            'header_font_family': FontFamily,
            'header_font_size': FontSize,
            'header_font_weight': FontWeight,
            'header_font_style': FontStyle,
            'header_rule': HeaderRule,
            'header_rule_spacing': HeaderRuleSpacing,
            'secondary_header_visibility': HeaderVisibility,
            'secondary_header_position': HeaderPosition,
            'secondary_header_alignment': HorizontalAlignment,
            'secondary_header_repeat': HeaderRepeat,
            'secondary_header_source': HeaderSource,
            'secondary_header_font_family': FontFamily,
            'secondary_header_font_size': FontSize,
            'secondary_header_font_weight': FontWeight,
            'secondary_header_font_style': FontStyle,
            'secondary_header_rule': HeaderRule,
            'secondary_header_rule_spacing': HeaderRuleSpacing,
        }
        try:
            for field_name, enum_type in enum_fields.items():
                object.__setattr__(
                    self, field_name, enum_type(getattr(self, field_name))
                )
        except ValueError as error:
            raise ValueError(f'unsupported deck render setting: {error}') from error
        # These are semantic slots, not freely movable numbered bands.
        object.__setattr__(self, 'header_position', HeaderPosition.TOP)
        object.__setattr__(
            self, 'secondary_header_position', HeaderPosition.BOTTOM
        )
        for field_name in ('header_text', 'secondary_header_text'):
            value = getattr(self, field_name)
            if not isinstance(value, str):
                raise ValueError(f'{field_name} must be a string')
            if len(value) > 200:
                raise ValueError(f'{field_name} must not exceed 200 characters')
            validate_safe_header_template(value)

    @classmethod
    def centered(cls) -> DeckRenderSettings:
        return cls()

    @property
    def typography(self) -> ResolvedTypography | None:
        if self.typography_profile is TypographyProfile.OFF:
            return None
        if self.typography_profile is not TypographyProfile.CUSTOM:
            return _PROFILE_TYPOGRAPHY[self.typography_profile]
        return ResolvedTypography(
            body=TextStyle(
                self.body_font_family,
                self.body_font_size,
                self.body_font_weight,
                self.body_font_style,
            ),
            primary_header=TextStyle(
                self.header_font_family,
                self.header_font_size,
                self.header_font_weight,
                self.header_font_style,
            ),
            secondary_header=TextStyle(
                self.secondary_header_font_family,
                self.secondary_header_font_size,
                self.secondary_header_font_weight,
                self.secondary_header_font_style,
            ),
            line_spacing=self.line_spacing,
            paragraph_spacing=self.paragraph_spacing,
        )

    def typography_dict(self) -> dict[str, str]:
        structured_fields = {
            'preset', 'horizontal_alignment', 'vertical_alignment',
            'header_visibility', 'header_position', 'header_alignment',
            'header_repeat', 'section_break',
        }
        return {
            key: value for key, value in self.to_dict().items()
            if key not in structured_fields
        }

    def to_dict(self) -> dict[str, str]:
        data = {}
        for field_name in self.__dataclass_fields__:
            value = getattr(self, field_name)
            data[field_name] = value.value if isinstance(value, Enum) else value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> DeckRenderSettings:
        if not isinstance(data, dict):
            raise ValueError('deck render settings must be an object')
        known_fields = set(cls.__dataclass_fields__)
        unknown_fields = set(data) - known_fields
        if unknown_fields:
            raise ValueError(
                'unknown deck render settings: '
                + ', '.join(sorted(unknown_fields))
            )
        return cls(**data)


SAFE_HEADER_PLACEHOLDERS = (
    '{{ card_number }}',
    '{{ card_count }}',
)


def validate_safe_header_template(source: str) -> None:
    """Validate placeholders without interpreting ordinary safe text as TeX."""
    tokens = re.findall(r'{{.*?}}', source, flags=re.DOTALL)
    unknown = [token for token in tokens if token not in SAFE_HEADER_PLACEHOLDERS]
    if unknown:
        raise ValueError(f'unsupported header placeholder: {unknown[0]}')
    without_tokens = source
    for token in SAFE_HEADER_PLACEHOLDERS:
        without_tokens = without_tokens.replace(token, '')
    if '{{' in without_tokens or '}}' in without_tokens:
        raise ValueError('malformed header placeholder')


def render_safe_header_template(
    source: str, *, card_number: int, card_count: int
) -> str:
    validate_safe_header_template(source)
    if (
        isinstance(card_number, bool)
        or isinstance(card_count, bool)
        or card_number < 1
        or card_count < card_number
    ):
        raise ValueError('invalid card numbering context')
    return (
        source.replace('{{ card_number }}', str(card_number))
        .replace('{{ card_count }}', str(card_count))
    )
