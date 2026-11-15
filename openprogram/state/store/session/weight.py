"""A text's weight: a fixed, additive estimate of the tokens it takes.

Every code point counts on its own: a CJK ideograph, kana, hangul or a
full-width form counts 1, anything else 0.25 (about four characters of
Latin text to a token). The estimate is not a tokenizer's count; it is a
pure function of the text, the same on every installation, and additive:
``weight(a + b) == weight(a) + weight(b)``. That makes it usable for
deciding where to cut a text, which a sampled or tokenizer-dependent
count is not (``runtime.context.tokens`` gives a different answer with and
without ``tiktoken``, and classifies a whole text by a sample).

Measured in quarters internally, so sums are exact integers.
"""
from __future__ import annotations

# Code point ranges counted as one token each.
_DENSE_RANGES: tuple[tuple[int, int], ...] = (
    (0x1100, 0x11FF),    # Hangul Jamo
    (0x2E80, 0x2FDF),    # CJK radicals, Kangxi radicals
    (0x2FF0, 0x303F),    # ideographic description, CJK symbols and punctuation
    (0x3040, 0x30FF),    # Hiragana, Katakana
    (0x3100, 0x31FF),    # Bopomofo, Hangul compatibility jamo, Kanbun, Katakana ext.
    (0x3200, 0x4DBF),    # enclosed CJK, CJK compatibility, CJK ext. A
    (0x4E00, 0x9FFF),    # CJK unified ideographs
    (0xA960, 0xA97F),    # Hangul Jamo ext. A
    (0xAC00, 0xD7FF),    # Hangul syllables, Jamo ext. B
    (0xF900, 0xFAFF),    # CJK compatibility ideographs
    (0xFE30, 0xFE4F),    # CJK compatibility forms
    (0xFF00, 0xFFEF),    # half-width and full-width forms
    (0x20000, 0x3FFFF),  # CJK ext. B and later, supplementary ideographs
)

DENSE_QUARTERS = 4   # a dense code point weighs 1
OTHER_QUARTERS = 1   # anything else weighs 0.25


def _dense(code_point: int) -> bool:
    for low, high in _DENSE_RANGES:
        if code_point < low:
            return False
        if code_point <= high:
            return True
    return False


def quarters(text: str) -> int:
    """``text``'s weight in quarter tokens: an exact, additive integer."""
    total = 0
    for char in text:
        code_point = ord(char)
        total += DENSE_QUARTERS if code_point >= 0x1100 and _dense(code_point) else OTHER_QUARTERS
    return total


def weight(text: str) -> float:
    """``text``'s weight in tokens (``quarters(text) / 4``)."""
    return quarters(text) / 4


__all__ = ["quarters", "weight", "DENSE_QUARTERS", "OTHER_QUARTERS"]
