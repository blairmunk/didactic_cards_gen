from __future__ import annotations

import re


_SAFE_MATH = re.compile(
    r'(\$\$.+?\$\$|\$[^\n$]+?\$)',
    flags=re.DOTALL,
)
_PARAGRAPH_BREAK = re.compile(r'\n(?:[^\S\n]*\n)+')


def _normalise_layout_text(text: str) -> str:
    """Normalise Safe presentation without changing the stored value."""
    normalised = text.replace('\r\n', '\n').replace('\r', '\n')
    parts = _SAFE_MATH.split(normalised)
    for index in range(1, len(parts), 2):
        # TeX and MathJax already treat physical newlines inside math as
        # whitespace.  They must not become card layout instructions.
        math = parts[index].replace('\n', ' ')
        if math.startswith('$$'):
            # Display math already owns vertical layout in TeX and MathJax.
            # Make it a paragraph instead of adding forced line breaks around
            # it and accidentally doubling the display spacing.
            parts[index - 1] = parts[index - 1].rstrip(' \t\n') + '\n\n'
            parts[index + 1] = '\n\n' + parts[index + 1].lstrip(' \t\n')
        parts[index] = math
    result = ''.join(parts)
    result = re.sub(r'^(?:[^\S\n]*\n)+', '', result)
    return re.sub(r'(?:\n[^\S\n]*)+$', '', result)


def safe_text_paragraphs(text: str) -> tuple[tuple[str, ...], ...]:
    """Split a Safe card body into paragraphs containing explicit lines.

    A single newline is a forced line break.  One or more empty (including
    whitespace-only) lines form one paragraph boundary.  Outer blank lines are
    ignored.  CRLF and lone CR have the same presentation as LF.  Display math
    is kept as its own paragraph.

    The function is presentation-only: persistence and export keep the input
    string unchanged, which is especially important for Advanced/raw cards.
    """
    if not isinstance(text, str):
        raise TypeError('text must be a string')

    paragraphs = _PARAGRAPH_BREAK.split(_normalise_layout_text(text))
    return tuple(tuple(paragraph.split('\n')) for paragraph in paragraphs)


def safe_single_line(text: str) -> str:
    """Return one visible Safe label line without mutating stored data."""
    if not isinstance(text, str):
        raise TypeError('text must be a string')
    return re.sub(r'[\t ]*(?:\r\n|\r|\n)[\t ]*', ' ', text).strip()
