import pytest

from didactic_cards.domain.rendering import (
    DeckRenderSettings,
    FontFamily,
    FontSize,
    FontStyle,
    FontWeight,
    LineSpacing,
    ParagraphSpacing,
    render_safe_header_template,
)


def test_render_settings_centered_preset_is_explicit():
    centered = DeckRenderSettings.centered()

    assert centered.preset.value == 'centered'
    assert centered.horizontal_alignment.value == 'center'
    assert centered.vertical_alignment.value == 'center'
    assert centered.authoring_mode.value == 'safe'


def test_header_slots_are_always_top_and_bottom():
    settings = DeckRenderSettings(
        header_position='bottom',
        secondary_header_position='top',
    )

    assert settings.header_position.value == 'top'
    assert settings.secondary_header_position.value == 'bottom'


def test_render_settings_round_trip_preserves_safe_header_options():
    settings = DeckRenderSettings(
        preset='custom',
        horizontal_alignment='right',
        vertical_alignment='bottom',
        header_visibility='both',
        header_position='bottom',
        header_alignment='center',
        header_repeat='section-start',
        section_break='new-row',
    )

    assert DeckRenderSettings.from_dict(settings.to_dict()) == settings


def test_typography_profiles_resolve_without_accepting_latex_fragments():
    book = DeckRenderSettings(typography_profile='book').typography
    custom = DeckRenderSettings(
        typography_profile='custom',
        body_font_family='mono',
        body_font_size='large',
        body_font_weight='bold',
        body_font_style='italic',
        line_spacing='relaxed',
        paragraph_spacing='medium',
    ).typography

    assert DeckRenderSettings().typography is None
    assert book.body.family is FontFamily.SERIF
    assert custom.body.family is FontFamily.MONO
    assert custom.body.size is FontSize.LARGE
    assert custom.body.weight is FontWeight.BOLD
    assert custom.body.style is FontStyle.ITALIC
    assert custom.line_spacing is LineSpacing.RELAXED
    assert custom.paragraph_spacing is ParagraphSpacing.MEDIUM


def test_two_headers_and_typography_round_trip_as_allowlisted_values():
    settings = DeckRenderSettings(
        typography_profile='custom',
        header_source='custom',
        header_text='Курс & группа',
        header_font_family='serif',
        header_font_size='normal',
        header_font_weight='bold',
        header_font_style='italic',
        secondary_header_visibility='both',
        secondary_header_position='bottom',
        secondary_header_alignment='right',
        secondary_header_repeat='section-start',
        secondary_header_source='card-number',
        secondary_header_font_family='mono',
    )

    assert DeckRenderSettings.from_dict(settings.to_dict()) == settings
    assert settings.typography_dict()['header_text'] == 'Курс & группа'


@pytest.mark.parametrize(
    'kwargs',
    [
        {'preset': 'unknown'},
        {'authoring_mode': 'mixed'},
        {'horizontal_alignment': 'justify'},
        {'vertical_alignment': 'middle-ish'},
        {'header_visibility': 'sometimes'},
        {'header_position': 'side'},
        {'header_alignment': 'justify'},
        {'header_repeat': 'sometimes'},
        {'section_break': 'new-column'},
        {'typography_profile': r'\input{/etc/passwd}'},
        {'body_font_family': r'\rmfamily'},
        {'body_font_size': 'huge'},
        {'line_spacing': 'double'},
        {'header_source': 'latex'},
        {'secondary_header_visibility': 'sometimes'},
    ],
)
def test_render_settings_reject_unknown_values(kwargs):
    with pytest.raises(ValueError):
        DeckRenderSettings(**kwargs)


def test_render_settings_from_dict_rejects_non_object():
    with pytest.raises(ValueError, match='object'):
        DeckRenderSettings.from_dict([])


def test_render_settings_from_dict_rejects_unknown_fields():
    with pytest.raises(ValueError, match='unknown'):
        DeckRenderSettings.from_dict({'latex_source': r'\input{/etc/passwd}'})


@pytest.mark.parametrize('field', ['header_text', 'secondary_header_text'])
def test_header_custom_text_has_a_bounded_string_contract(field):
    with pytest.raises(ValueError, match='string'):
        DeckRenderSettings(**{field: 42})
    with pytest.raises(ValueError, match='200'):
        DeckRenderSettings(**{field: 'x' * 201})


def test_safe_header_template_supports_only_number_and_total():
    assert render_safe_header_template(
        'Явления · Карточка {{ card_number }}/{{ card_count }}',
        card_number=7,
        card_count=21,
    ) == 'Явления · Карточка 7/21'

    with pytest.raises(ValueError, match='numbering context'):
        render_safe_header_template('', card_number=True, card_count=1)

    with pytest.raises(ValueError, match='unsupported'):
        DeckRenderSettings(header_text='{{ section }}')
    with pytest.raises(ValueError, match='malformed'):
        DeckRenderSettings(header_text='{{ card_number }')


def test_header_rules_are_allowlisted_and_round_trip():
    settings = DeckRenderSettings(
        header_rule='thin',
        header_rule_spacing='compact',
        secondary_header_rule='medium',
        secondary_header_rule_spacing='relaxed',
    )

    assert DeckRenderSettings.from_dict(settings.to_dict()) == settings
    with pytest.raises(ValueError, match='unsupported'):
        DeckRenderSettings(header_rule=r'\hrulefill')
