"""Pure text normalization shared by low-level runtime modules."""

from __future__ import annotations


IDENTITY_HEADER_PART_MAX_CHARS = 64


def normalize_identity_header_part(
    value: str,
    *,
    max_chars: int = IDENTITY_HEADER_PART_MAX_CHARS,
) -> str:
    """Return one safe identity field for a runtime-controlled header.

    Identity values may come from an external platform. Whitespace and
    controls cannot create another physical record, and header delimiters
    cannot terminate the field early.
    """
    normalized = " ".join(value.split())
    normalized = "".join(
        character for character in normalized if character.isprintable()
    )
    normalized = (
        normalized.replace("[", "(").replace("]", ")").replace(":", "：")
    )
    if len(normalized) > max_chars:
        return normalized[:max_chars] + "…"
    return normalized
