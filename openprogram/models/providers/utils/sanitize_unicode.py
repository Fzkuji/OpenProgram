"""
Removes unpaired Unicode surrogate characters from strings.

Unpaired surrogates (high surrogates 0xD800-0xDBFF without matching low surrogates
0xDC00-0xDFFF, or vice versa) cause JSON serialization errors in many API providers.

Mirrors sanitize-unicode.ts
"""

import re

# Matches unpaired high surrogate (not followed by low surrogate)
# or unpaired low surrogate (not preceded by high surrogate)
_SURROGATE_RE = re.compile(r"[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]")


def sanitize_surrogates(text: str) -> str:
    """Remove unpaired Unicode surrogate characters from a string.

    Valid emoji and other characters outside the Basic Multilingual Plane use
    properly paired surrogates and will NOT be affected by this function.
    """
    return _SURROGATE_RE.sub("", text)
