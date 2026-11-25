"""Find passages of the paper that repeat a source, by word 8-gram shingles.
Plain code, no model calls.

The paper's compared words run as one stream across sentences. A word is covered by a
source when it lies in an 8-gram of the stream that the source also contains. A
sentence matches a source when at least half its words are covered; adjacent matching
sentences merge into a passage, and a passage is a match when it has at least
MIN_WORDS words and at least MIN_OVERLAP of the 8-grams inside it are in the source.
"""

N = 8
SENTENCE_COVER = 0.5
MIN_WORDS = 25
MIN_OVERLAP = 0.6


def grams(words: list[str]) -> list[tuple]:
    return [tuple(words[i:i + N]) for i in range(len(words) - N + 1)]


def find(sentences: list[dict], sources: list[dict]) -> tuple[list[dict], int, int]:
    """(matches, matched words, total words). A match: source id, the sentence span
    [first, last], words, overlap (0..1), quoted and cited."""
    stream = [w for s in sentences for w in s["norm"]]
    starts, pos = [], 0
    for s in sentences:
        starts.append(pos)
        pos += len(s["norm"])
    paper_grams = grams(stream)
    index: dict[tuple, list[int]] = {}
    for i, g in enumerate(paper_grams):
        index.setdefault(g, []).append(i)

    matches, matched = [], set()
    for src in sources:
        source_grams = set(grams(src["words"]))
        hits = [i for g in source_grams.intersection(index) for i in index[g]]
        if not hits:
            continue
        covered = bytearray(len(stream))
        for i in hits:
            covered[i:i + N] = b"\x01" * N
        hit_sentences = [sum(covered[starts[k]:starts[k] + len(s["norm"])]) >= SENTENCE_COVER * len(s["norm"])
                         for k, s in enumerate(sentences)]
        for first, last in runs(hit_sentences):
            words = stream[starts[first]:starts[last] + len(sentences[last]["norm"])]
            inner = grams(words)
            if len(words) < MIN_WORDS or not inner:
                continue
            overlap = sum(g in source_grams for g in inner) / len(inner)
            if overlap < MIN_OVERLAP:
                continue
            span = range(starts[first], starts[last] + len(sentences[last]["norm"]))
            quoted = [q for s in sentences[first:last + 1] for q in s["quoted"]]
            matches.append({"source": src["id"], "first": first, "last": last, "words": len(words),
                            "overlap": overlap, "quoted": sum(quoted) * 2 >= len(quoted),
                            "cited": any(s["cited"] for s in sentences[first:last + 1])})
            matched.update(p for p in span if covered[p])
    return matches, len(matched), len(stream)


def runs(flags: list[bool]) -> list[tuple[int, int]]:
    out, start = [], None
    for i, f in enumerate(flags + [False]):
        if f and start is None:
            start = i
        elif not f and start is not None:
            out.append((start, i - 1))
            start = None
    return out


__all__ = ["find", "grams"]
