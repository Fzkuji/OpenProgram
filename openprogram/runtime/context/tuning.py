"""The settings that would have cost the user's own calls the fewest tokens.

Two settings trade tokens against each other in ways that depend on how one
person works: the share of the window automatic compaction runs at
(``context.auto_compact_percent``), and how long an Anthropic prompt cache
lives (``models.prompt_cache_ttl``). Compacting sooner reads less on every
call but pays for more summaries and for reading back what they left out;
an hour's cache costs more to write and pays off only for someone who comes
back to a conversation after more than five minutes.

:func:`tune` replays each setting on the user's own calls of the period
from the usage ledger: the same conversations, growing by the same amounts
at the same times, compacted or read from the cache as the setting would
have had them. What it answers holds for this user only.

Tokens are weighed by what the model's vendor charges for them against a
plain input token (the catalog's prices, else the vendor's usual ratios),
which is what both a subscription's quota and an API bill count. A setting
is advised only when it saves at least :data:`SAME_COST` of the period.

After magpie's tune (yetone/magpie, MIT).
"""
from __future__ import annotations

import statistics
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from openprogram.models.usage.event import (
    CALL_KIND_CHAT,
    CALL_KIND_EXEC,
    CALL_KIND_SUBAGENT,
)
from openprogram.runtime.context.budget import output_reserve_for

# The calls a conversation is made of; compactions, titles and the like
# are the replay's own business.
TURN_KINDS = (CALL_KIND_CHAT, CALL_KIND_EXEC, CALL_KIND_SUBAGENT)
# Fewer calls than this say too little about a habit.
MIN_CALLS = 50
# A prompt that drops to under this share of the one before, from at least
# DROP_FROM tokens, was compacted.
DROP_SHARE = 0.6
DROP_FROM = 20_000
# The calls after a compaction its rework (what is read back) is counted over.
REWORK_CALLS = 10
# Settings within this share of each other cost the same; a change is
# advised only when it saves at least this much.
SAME_COST = 0.03
# The compaction shares tried, in percent of the window.
PERCENTS = tuple(range(50, 96, 5))
FIVE_MINUTES_S = 300
HOUR_S = 3600
# The cache lifetime applies to these APIs (explicit cache markers).
CACHE_LIFETIME_APIS = frozenset({"anthropic-messages", "bedrock-converse-stream"})


@dataclass(frozen=True)
class Call:
    """One model call of a conversation, as the replay sees it."""

    ts: float
    stream: str  # the conversation it is in
    model: str  # "provider/model_id"
    api: str
    prompt: int  # all of it: input, cache read and cache write
    out: int


def calls_from(rows: Iterable[dict]) -> list[Call]:
    """The ledger's rows as calls, leaving out those with no prompt."""
    out: list[Call] = []
    for r in rows:
        prompt = (r.get("input_tokens") or 0) + (r.get("cache_read_tokens") or 0) + (
            r.get("cache_write_tokens") or 0
        )
        if prompt <= 0 or not r.get("session_id"):
            continue
        out.append(Call(
            ts=float(r["ts"]),
            stream=str(r["session_id"]),
            model=f"{r.get('provider') or ''}/{r.get('model_id') or ''}",
            api=str(r.get("api") or ""),
            prompt=int(prompt),
            out=int(r.get("output_tokens") or 0),
        ))
    return out


@dataclass(frozen=True)
class Weights:
    """What a vendor charges for each kind of token against one of plain input."""

    read: float
    write: float
    write_hour: float
    out: float


ANTHROPIC_WEIGHTS = Weights(read=0.1, write=1.25, write_hour=2.0, out=5.0)
OTHER_WEIGHTS = Weights(read=0.1, write=1.0, write_hour=1.0, out=8.0)

Weigh = Callable[[str, str], Weights]  # (model, api) -> weights


def weights_for(model: str, api: str) -> Weights:
    """The catalog's prices as ratios to input, else the vendor's usual ones.
    Only Anthropic's API charges more for an hour's cache write (twice input)."""
    usual = ANTHROPIC_WEIGHTS if api in CACHE_LIFETIME_APIS else OTHER_WEIGHTS
    try:
        from openprogram.models.providers.catalog.models import get_model

        provider, _, model_id = model.partition("/")
        found = get_model(provider, model_id)
    except Exception:
        found = None
    cost = found.cost if found is not None else None
    if cost is None or not cost.input:
        return usual

    def ratio(price: float | None, fallback: float) -> float:
        return price / cost.input if price is not None else fallback

    write = ratio(cost.cache_write, usual.write)
    return Weights(
        read=ratio(cost.cache_read, usual.read),
        write=write,
        write_hour=usual.write_hour if api in CACHE_LIFETIME_APIS else write,
        out=ratio(cost.output, usual.out),
    )


@dataclass
class Facts:
    """What the replay found of the user's habits, said beside the advice."""

    calls: int = 0
    conversations: int = 0
    compactions: int = 0
    peak: int = 0  # the median prompt a compaction was made at
    after: int = 0  # the median prompt right after one
    growth: int = 0  # the median a prompt grows by from one call to the next
    rework: int = 0  # what a compaction costs to read back, in the calls after it
    quick: float = 0.0  # of the gaps between calls, the share under 5 minutes
    pause: float = 0.0  # between 5 minutes and an hour
    away: float = 0.0  # over an hour


@dataclass(frozen=True)
class Point:
    """One setting tried, and what the period would have cost with it."""

    percent: int
    cost: float
    compactions: int


@dataclass
class CompactionAdvice:
    """The compaction share advised for one model."""

    model: str
    window: int
    current: int
    best: int
    cost: float  # the period's weighted tokens with ``current``
    low: float  # with ``best``
    curve: list[Point] = field(default_factory=list)
    facts: Facts = field(default_factory=Facts)


@dataclass
class CacheAdvice:
    """The prompt cache lifetime advised for the calls it applies to."""

    current: str
    best: str
    cost: float
    low: float
    five_minutes: float
    one_hour: float
    facts: Facts = field(default_factory=Facts)


@dataclass
class Tuning:
    days: float
    compaction: list[CompactionAdvice] = field(default_factory=list)
    cache: CacheAdvice | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _median(xs: Sequence[int]) -> int:
    return int(statistics.median_low(xs)) if xs else 0


def _compacted(prev: int, cur: int) -> bool:
    return prev >= DROP_FROM and cur < DROP_SHARE * prev


def streams(calls: Iterable[Call]) -> list[list[Call]]:
    """Calls grouped by conversation, each oldest first."""
    by: dict[str, list[Call]] = defaultdict(list)
    for c in calls:
        by[c.stream].append(c)
    return [sorted(by[k], key=lambda c: c.ts) for k in sorted(by)]


def observe(ss: list[list[Call]]) -> Facts:
    """How the conversations grow, what their compactions came to, and how
    long the user leaves between calls."""
    f = Facts()
    peaks: list[int] = []
    afters: list[int] = []
    growths: list[int] = []
    reworks: list[int] = []
    gaps = quick = pause = 0
    for s in ss:
        f.conversations += 1
        f.calls += len(s)
        grew: list[int] = []
        cuts: list[int] = []
        for i in range(1, len(s)):
            gaps += 1
            gap = s[i].ts - s[i - 1].ts
            quick += gap <= FIVE_MINUTES_S
            pause += FIVE_MINUTES_S < gap <= HOUR_S
            if _compacted(s[i - 1].prompt, s[i].prompt):
                peaks.append(s[i - 1].prompt)
                afters.append(s[i].prompt)
                cuts.append(i)
            elif s[i].prompt >= s[i - 1].prompt:
                grew.append(s[i].prompt - s[i - 1].prompt)
        growths.extend(grew)
        usual = _median(grew)
        # what the calls after a compaction read back beyond the usual
        # growth: the files and results the summary left out
        for at in cuts:
            end = at + REWORK_CALLS
            if end >= len(s) or any(
                _compacted(s[j - 1].prompt, s[j].prompt) for j in range(at + 1, end + 1)
            ):
                continue
            reworks.append(max(0, s[end].prompt - s[at].prompt - REWORK_CALLS * usual))
    f.compactions = len(peaks)
    f.peak, f.after = _median(peaks), _median(afters)
    f.growth, f.rework = _median(growths), _median(reworks)
    if gaps:
        f.quick, f.pause = quick / gaps, pause / gaps
        f.away = 1 - f.quick - f.pause
    return f


def replay_compaction(
    ss: list[list[Call]], threshold: int, after: int, base: int, rework: int,
    w: Weights, write: float,
) -> tuple[float, int]:
    """What the conversations would have cost compacting past ``threshold``,
    and how many compactions that makes. Each prompt grows by what it grew
    by, the recorded compactions left out; a compaction reads the whole
    prompt and writes a summary (``after`` less ``base``, as output), the
    next call writes the summary to the cache and the ones after read back
    ``rework``."""
    cost, made = 0.0, 0
    summary = max(0, after - base)
    for s in ss:
        ctx = s[0].prompt
        cost += ctx * write + s[0].out * w.out
        for i in range(1, len(s)):
            grew = s[i].prompt - s[i - 1].prompt
            if grew < 0 or _compacted(s[i - 1].prompt, s[i].prompt):
                grew = 0
            if ctx + grew > threshold and ctx > after:
                cost += ctx * w.read + summary * w.out + (summary + rework) * write
                ctx = after + rework
                made += 1
            cost += ctx * w.read + grew * write + s[i].out * w.out
            ctx += grew
    return cost, made


def _threshold(percent: int, window: int) -> int:
    """The prompt automatic compaction runs at for ``percent`` of ``window``
    (the engine counts the reply's reserve in the share)."""
    return int(percent / 100 * window) - output_reserve_for(window)


def _cheaper_enough(cost: float, low: float) -> bool:
    return cost > 0 and (cost - low) / cost >= SAME_COST


def advise_compaction(
    calls: list[Call], *, window: int, current: int, weigh: Weigh = weights_for,
) -> CompactionAdvice | None:
    """The compaction share for one model's calls, or None with too little to go on."""
    ss = streams(calls)
    f = observe(ss)
    if f.calls < MIN_CALLS or window <= 0:
        return None
    w = weigh(calls[0].model, calls[0].api)
    base = _median([s[0].prompt for s in ss])
    # with no compaction seen, a summary comes to some 10K over the start
    after = f.after or base + 10_000
    rework = f.rework if f.compactions >= 5 else max(f.rework, 10_000)
    # a share that leaves little room over the summary compacts over and
    # over: keep half the summary's size, or twenty calls' growth, free
    floor = after + max(after // 2, 20 * f.growth)

    def run(percent: int) -> Point:
        cost, made = replay_compaction(
            ss, max(after + 1, _threshold(percent, window)), after, base, rework, w, w.write
        )
        return Point(percent=percent, cost=cost, compactions=made)

    now = run(current)
    curve = [run(p) for p in PERCENTS if _threshold(p, window) >= floor]
    if not curve:
        return None
    low = min(p.cost for p in curve)
    # of the shares within 1% of the cheapest, the largest: fewer
    # compactions lose less of what the conversation knew
    best = max((p for p in curve if p.cost <= low * 1.01), key=lambda p: p.percent)
    advice = CompactionAdvice(
        model=calls[0].model, window=window, current=current, best=current,
        cost=now.cost, low=now.cost, curve=curve, facts=f,
    )
    if _cheaper_enough(now.cost, best.cost):
        advice.best, advice.low = best.percent, best.cost
    return advice


def replay_cache(ss: list[list[Call]], ttl_s: float, hour: bool, weigh: Weigh) -> float:
    """What the conversations would have cost with the cache kept ``ttl_s``:
    a call within it of the one before reads what that one sent and writes
    what it adds; one after it, or after a compaction, writes it all again."""
    cost = 0.0
    for s in ss:
        w = weigh(s[0].model, s[0].api)
        write = w.write_hour if hour else w.write
        cost += s[0].prompt * write
        for i in range(1, len(s)):
            p, c = s[i - 1].prompt, s[i].prompt
            if s[i].ts - s[i - 1].ts > ttl_s or _compacted(p, c):
                cost += c * write
            elif c >= p:
                cost += p * w.read + (c - p) * write
            else:
                cost += c * w.read
    return cost


def advise_cache(calls: list[Call], *, current: str, weigh: Weigh = weights_for) -> CacheAdvice | None:
    """The prompt cache lifetime for the calls it applies to, or None with too little."""
    applies = [c for c in calls if c.api in CACHE_LIFETIME_APIS]
    ss = streams(applies)
    f = observe(ss)
    if f.calls < MIN_CALLS:
        return None
    short = replay_cache(ss, FIVE_MINUTES_S, False, weigh)
    hour = replay_cache(ss, HOUR_S, True, weigh)
    cost = hour if current == "1h" else short
    best, low = ("1h", hour) if hour < short else ("5m", short)
    if not _cheaper_enough(cost, low):
        best, low = current, cost
    return CacheAdvice(
        current=current, best=best, cost=cost, low=low,
        five_minutes=short, one_hour=hour, facts=f,
    )


def _window_of(model: str) -> int:
    try:
        from openprogram.models.providers.catalog.models import get_model

        provider, _, model_id = model.partition("/")
        found = get_model(provider, model_id)
    except Exception:
        return 0
    return int(found.context_window) if found is not None else 0


def tune(
    *, days: float = 30.0, ledger: Any = None, now: float | None = None,
    window_of: Callable[[str], int] = _window_of, weigh: Weigh = weights_for,
) -> Tuning:
    """Replay the settings on the user's calls of the last ``days``."""
    from openprogram.state.settings import setting_value

    if ledger is None:
        from openprogram.models.usage.ledger import default_ledger as ledger
    since = (time.time() if now is None else now) - days * 86400
    calls = calls_from(ledger.calls(since=since, call_kinds=TURN_KINDS))
    result = Tuning(days=days)
    current = int(setting_value("context.auto_compact_percent"))
    by_model: dict[str, list[Call]] = defaultdict(list)
    for c in calls:
        by_model[c.model].append(c)
    for model, own in sorted(by_model.items()):
        advice = advise_compaction(own, window=window_of(model), current=current, weigh=weigh)
        if advice is not None:
            result.compaction.append(advice)
    result.cache = advise_cache(
        calls, current=str(setting_value("models.prompt_cache_ttl")), weigh=weigh
    )
    return result


__all__ = [
    "CacheAdvice",
    "Call",
    "CompactionAdvice",
    "Facts",
    "Point",
    "Tuning",
    "Weights",
    "advise_cache",
    "advise_compaction",
    "calls_from",
    "observe",
    "replay_cache",
    "replay_compaction",
    "streams",
    "tune",
    "weights_for",
]
