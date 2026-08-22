import pytest

from didactic_cards.domain.rendering import DeckRenderSettings


def test_render_settings_centered_and_legacy_presets_are_explicit():
    centered = DeckRenderSettings.centered()
    legacy = DeckRenderSettings.legacy()

    assert centered.preset.value == 'centered'
    assert centered.horizontal_alignment.value == 'center'
    assert centered.vertical_alignment.value == 'center'
    assert legacy.preset.value == 'legacy-top-left'
    assert legacy.horizontal_alignment.value == 'left'
    assert legacy.vertical_alignment.value == 'top'


def test_render_settings_round_trip_preserves_safe_header_options():
    settings = DeckRenderSettings(
        preset='custom',
        horizontal_alignment='right',
        vertical_alignment='bottom',
        header_visibility='both',
        header_position='bottom',
        header_alignment='center',
    )

    assert DeckRenderSettings.from_dict(settings.to_dict()) == settings


@pytest.mark.parametrize(
    'kwargs',
    [
        {'preset': 'unknown'},
        {'horizontal_alignment': 'justify'},
        {'vertical_alignment': 'middle-ish'},
        {'header_visibility': 'sometimes'},
        {'header_position': 'side'},
        {'header_alignment': 'justify'},
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
