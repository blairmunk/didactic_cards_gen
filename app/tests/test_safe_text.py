import pytest

from didactic_cards.domain.safe_text import safe_single_line, safe_text_paragraphs


@pytest.mark.parametrize('newline', ('\n', '\r\n', '\r'))
def test_safe_text_normalises_platform_newlines_for_layout_only(newline):
    assert safe_text_paragraphs(
        f'Первая{newline}Вторая{newline}{newline}Третья'
    ) == (
        ('Первая', 'Вторая'),
        ('Третья',),
    )


def test_safe_text_collapses_blank_line_runs_and_honours_whitespace_only_lines():
    assert safe_text_paragraphs('A\n\n\nB\n \n \t\nC') == (
        ('A',),
        ('B',),
        ('C',),
    )


def test_safe_text_ignores_all_outer_layout_breaks():
    assert safe_text_paragraphs('\nA\n\nB\n') == (
        ('A',),
        ('B',),
    )


def test_safe_text_ignores_outer_paragraph_separators():
    assert safe_text_paragraphs('\n\nA\n\n') == (('A',),)
    assert safe_text_paragraphs('\n\n') == (('',),)


def test_safe_text_treats_newlines_inside_display_math_as_whitespace():
    assert safe_text_paragraphs(
        'До $a = b$\nПосле $$x =\n\ny$$'
    ) == (
        ('До $a = b$', 'После'),
        ('$$x =  y$$',),
    )


def test_safe_text_display_math_is_a_block_without_adjacent_forced_lines():
    assert safe_text_paragraphs('A\n$$x$$\nB') == (
        ('A',),
        ('$$x$$',),
        ('B',),
    )


def test_safe_text_does_not_swallow_line_break_with_unclosed_inline_math():
    assert safe_text_paragraphs('Цена $5\nСледующая $x$') == (
        ('Цена $5', 'Следующая $x$'),
    )


def test_safe_text_rejects_non_string_values():
    with pytest.raises(TypeError, match='text must be a string'):
        safe_text_paragraphs(None)


def test_safe_single_line_collapses_all_line_endings_and_adjacent_whitespace():
    assert safe_single_line('  Раздел \r\n  один\rдва\n\tтри  ') == (
        'Раздел один два три'
    )


def test_safe_single_line_rejects_non_string_values():
    with pytest.raises(TypeError, match='text must be a string'):
        safe_single_line(None)
