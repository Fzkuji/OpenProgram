"""What impact_forecast and tech_transfer share: words of a text, TF-IDF cosine and
quote matching. Plain code, no model calls."""

import math
import re
from collections import Counter

STOP = set("""a an and are as at be been being but by can could did do does for from had has have how in into is it
its itself more most no not of on or our over such than that the their them then there these they this those through
to under up upon use used using via was we were what when where which while who will with within without you your
also both each few other some only same so too very just show shows shown paper propose proposed approach method
methods result results new based study work task tasks""".split())
WORD = re.compile(r"[a-z][a-z0-9-]{2,}")


def words(text: str) -> list[str]:
    """Lower-case content words of three or more letters, stop words removed."""
    return [w.strip("-") for w in WORD.findall((text or "").lower()) if w.strip("-") not in STOP]


def tfidf(docs: list[str]) -> list[dict[str, float]]:
    """A unit-length TF-IDF vector per document (smoothed idf over these documents)."""
    bags = [Counter(words(d)) for d in docs]
    df = Counter(w for b in bags for w in b)
    n = len(docs)
    out = []
    for b in bags:
        v = {w: (1 + math.log(c)) * (math.log((1 + n) / (1 + df[w])) + 1) for w, c in b.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        out.append({w: x / norm for w, x in v.items()})
    return out


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


def squash(text: str) -> str:
    """Text for verbatim matching: LaTeX ties and runs of whitespace collapsed, case folded."""
    return " ".join((text or "").replace("~", " ").split()).casefold()


def found_in(quote: str, text: str, least_words: int = 4) -> bool:
    q = squash(quote).strip(" .\"'“”")
    return len(q.split()) >= least_words and q in squash(text)


__all__ = ["cosine", "found_in", "squash", "tfidf", "words"]
