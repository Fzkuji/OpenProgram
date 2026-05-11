"""Recall logic — translate a query into a markdown snippet for prefetch."""
from __future__ import annotations

from typing import Iterable

from .. import index, store


def recall_for_prompt(
    query: str,
    *,
    wiki_k: int = 4,
    short_k: int = 4,
    short_days: int = 7,
    record_signals: bool = True,
) -> str:
    """Search wiki + recent short-term, return a single markdown block.

    Returns an empty string if nothing matches. Caller is responsible
    for fencing the result before feeding it to the model.

    Side effect: when ``record_signals`` is True (the default), this
    bumps ``recall-counts.json`` for every text that came back, which
    feeds the sleep deep-phase promotion threshold. Pass False from
    sleep itself to avoid self-amplification.
    """
    index.init()
    wiki_hits = index.search_wiki(query, limit=wiki_k)
    short_hits = index.search_short(query, limit=short_k, days=short_days)
    if record_signals and short_hits:
        # Only short-term hits feed the promotion signal — wiki entries
        # are already promoted, no point reinforcing them again.
        try:
            from .. import recall_counts
            recall_counts.record_hit(texts=[h.text for h in short_hits], query=query)
        except Exception:
            pass
    return _format(wiki_hits, short_hits)


def _format(
    wiki_hits: Iterable["index.WikiHit"],
    short_hits: Iterable["index.ShortHit"],
) -> str:
    parts: list[str] = []
    wiki_list = list(wiki_hits)
    short_list = list(short_hits)
    if wiki_list:
        parts.append("**Wiki**")
        for h in wiki_list:
            type_label = f" `type:{h.type}`" if getattr(h, "type", "") else ""
            parts.append(f"- [[{h.title}]] (`{h.path}`{type_label}): {h.snippet}")
    if short_list:
        if parts:
            parts.append("")
        parts.append("**Recent notes**")
        for h in short_list:
            parts.append(f"- {h.date} {h.kind}: {h.text}")
    return "\n".join(parts).strip()


def cheap_extract(user_content: str, assistant_content: str) -> list[str]:
    """Detect simple high-signal facts in a turn for sync_turn.

    Pure pattern matching, no LLM. Captures things like:

        "I prefer X" / "I like X" / "I use X"
        "我喜欢 / 我用 / 我们用 ..."
        "from now on, X"
        "always X" / "never X"

    Returns short facts to append as short-term entries. Caller decides
    whether to write them; this layer only suggests.
    """
    import re
    out: list[str] = []
    text = user_content.strip()
    patterns = [
        r"\b[Ii] (?:prefer|like|use|run|am using) ([^.!?\n]{2,80})",
        r"\b[Ww]e (?:use|run|prefer) ([^.!?\n]{2,80})",
        r"[Ff]rom now on,?\s*([^.!?\n]{2,120})",
        r"\balways\s+([^.!?\n]{2,80})",
        r"\bnever\s+([^.!?\n]{2,80})",
        r"我(?:喜欢|想|用|使用|偏好)([^。！？\n]{2,80})",
        r"我们(?:用|使用)([^。！？\n]{2,80})",
        r"以后(?:都|一直|始终)([^。！？\n]{2,80})",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text):
            phrase = m.group(0).strip()
            if 5 < len(phrase) < 200 and phrase not in out:
                out.append(phrase)
    return _drop_substrings(out)[:3]  # cap per turn


def _drop_substrings(phrases: list[str]) -> list[str]:
    """Drop entries that are substrings of a longer entry in the list.

    Pattern matches often produce overlapping captures (``"always use X"``
    and ``"from now on always use X"``) — keep only the most informative.
    """
    sorted_by_len = sorted(phrases, key=len, reverse=True)
    kept: list[str] = []
    for p in sorted_by_len:
        lp = p.lower().strip()
        if any(lp in k.lower() for k in kept):
            continue
        kept.append(p)
    # Restore original input order.
    order = {p: i for i, p in enumerate(phrases)}
    kept.sort(key=lambda x: order.get(x, 9999))
    return kept
