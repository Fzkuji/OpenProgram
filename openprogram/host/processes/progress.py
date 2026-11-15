"""What a managed process is doing, read from its output.

Progress comes from, most trusted first:

- ``##progress 45/100 label`` (or ``##progress 45% label``) lines a program
  prints on purpose;
- the OSC 9;4 terminal progress sequence (``ESC ] 9 ; 4 ; state ; percent``);
- tqdm and pip/rich progress bars;
- counters such as ``Epoch 3/10``, ``step 120 of 1000`` or ``[45/100]``.

Once a more trusted source has spoken, less trusted ones are ignored, so an
inner per-batch bar does not overwrite an explicit sample count.

Output is also stored the way a terminal shows it: a line redrawn with
carriage returns keeps only its last frame, so a progress bar takes one line
instead of one line per refresh.
"""
from __future__ import annotations

import re
import time

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
OSC_PROGRESS = re.compile(r"\x1b\]9;4;(\d)(?:;(\d{0,3}))?(?:\x07|\x1b\\)")
MARKER = re.compile(
    r"^\s*##progress\s+(?:(?P<cur>\d+(?:\.\d+)?)\s*/\s*(?P<tot>\d+(?:\.\d+)?)|(?P<pct>\d+(?:\.\d+)?)\s*%)"
    r"\s*(?P<label>.*?)\s*$")
_NUM = r"\d+(?:\.\d+)?[kMGTP]?"
TQDM = re.compile(
    r"(?:(?P<desc>[^|\r\n]{1,60}?):\s*)?(?:\d{1,3}%\|[^|]*\|\s*)?"
    rf"(?P<cur>{_NUM})/(?P<tot>{_NUM})\s*"
    r"\[(?P<elapsed>[\d:]+)<(?P<eta>[\d:]+|\?),\s*(?P<rate>[\d.]+|\?)\s*(?P<unit>[A-Za-z]*/s|s/[A-Za-z]+)\]")
PIP = re.compile(
    r"(?P<cur>\d+(?:\.\d+)?)/(?P<tot>\d+(?:\.\d+)?)\s*(?P<unit>[kMGT]?i?B)\s+"
    r"(?P<rate>\d+(?:\.\d+)?)\s*(?P<runit>[kMGT]?i?B)/s\s+eta\s+(?P<eta>[\d:]+)")
COUNTER = re.compile(
    r"(?i)\b(?P<label>epoch|step|iter(?:ation)?|batch|sample|item|file|example|task|test|round|page|chunk"
    r"|shard|trial|episode|case|record|row|doc(?:ument)?|image|quer(?:y|ie)|request)s?\b[\s:#]*\[?"
    r"(?P<cur>\d+)\s*(?:/|of)\s*(?P<tot>\d+)")
BRACKET = re.compile(r"[\[(](?P<cur>\d+)\s*/\s*(?P<tot>\d+)[\])]")

RANK = {"counter": 0, "bar": 1, "osc": 2, "marker": 3}
SCALE = {"k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15}
OSC_STATE = {1: "normal", 2: "error", 3: "indeterminate", 4: "paused"}


def _number(text: str) -> float:
    if text and text[-1] in SCALE:
        return float(text[:-1]) * SCALE[text[-1]]
    return float(text)


def _seconds(clock: str) -> float | None:
    try:
        total = 0.0
        for part in clock.split(":"):
            total = total * 60 + float(part)
        return total
    except ValueError:
        return None


def _frame(text: str) -> dict | None:
    """Progress in one rendered line, or None."""
    if match := MARKER.match(text):
        if match["pct"] is not None:
            return {"source": "marker", "current": float(match["pct"]), "total": 100.0, "unit": "%",
                    "label": match["label"] or None}
        return {"source": "marker", "current": float(match["cur"]), "total": float(match["tot"]),
                "label": match["label"] or None}
    if match := TQDM.search(text):
        rate = None if match["rate"] == "?" else float(match["rate"])
        unit = match["unit"]
        if rate is not None and unit.startswith("s/"):
            rate, unit = (1 / rate if rate else None), unit[2:] + "/s"
        label = (match["desc"] or "").strip() or None
        return {"source": "bar", "current": _number(match["cur"]), "total": _number(match["tot"]),
                "label": label, "unit": unit.removesuffix("/s") or None, "rate": rate,
                "eta": None if match["eta"] == "?" else _seconds(match["eta"])}
    if match := PIP.search(text):
        return {"source": "bar", "current": float(match["cur"]), "total": float(match["tot"]),
                "unit": match["unit"], "rate": float(match["rate"]) if match["runit"] == match["unit"] else None,
                "eta": _seconds(match["eta"])}
    if match := COUNTER.search(text):
        label = match["label"].lower()
        plural = {"batch": "batches", "query": "queries", "querie": "queries"}.get(label, label + "s")
        return {"source": "counter", "current": float(match["cur"]), "total": float(match["tot"]),
                "label": plural}
    if match := BRACKET.search(text):
        return {"source": "counter", "current": float(match["cur"]), "total": float(match["tot"])}
    return None


def parse(text: str) -> dict | None:
    """The latest progress in ``text`` (raw output, frames included)."""
    found = None
    for match in OSC_PROGRESS.finditer(text):
        state = int(match[1])
        if state == 0:
            found = {"source": "osc", "clear": True}
        else:
            percent = float(match[2]) if match[2] and state != 3 else None
            found = {"source": "osc", "state": OSC_STATE.get(state, "normal"), "unit": "%",
                     "current": percent, "total": 100.0 if percent is not None else None}
    best = None
    for frame in re.split(r"[\r\n]", ANSI.sub("", text)):
        candidate = _frame(frame)
        if candidate and candidate.get("total") and candidate["current"] <= candidate["total"] * 1.0001:
            if best is None or RANK[candidate["source"]] >= RANK[best["source"]]:
                best = candidate
    if best and (found is None or RANK[best["source"]] > RANK["osc"]):
        return best
    return found or best


def merge(previous: dict | None, found: dict | None, now: float | None = None) -> dict | None:
    """The record's progress after new output reported ``found``."""
    if found is None:
        return previous
    now = time.time() if now is None else now
    if previous and RANK[found["source"]] < RANK[previous["source"]]:
        return previous
    if found.get("clear"):
        return None
    progress = {"source": found["source"], "state": found.get("state", "normal"),
                "current": found.get("current"), "total": found.get("total"),
                "label": found.get("label") or (previous or {}).get("label"),
                "unit": found.get("unit"), "updated_at": now}
    # Speed and time left: the bar's own, or measured since this count began.
    since = (previous or {}).get("since")
    if (not since or previous.get("total") != progress["total"]
            or (progress["current"] or 0) < (previous.get("current") or 0)):
        since = {"t": now, "current": progress["current"] or 0}
    progress["since"] = since
    rate, eta = found.get("rate"), found.get("eta")
    current, total = progress["current"], progress["total"]
    if rate is None and current is not None and now - since["t"] >= 1 and current > since["current"]:
        rate = (current - since["current"]) / (now - since["t"])
    if eta is None and rate and current is not None and total:
        eta = max(0.0, (total - current) / rate)
    progress["rate"], progress["eta"] = rate, eta
    return progress


def public(progress: dict | None) -> dict | None:
    if not progress:
        return None
    return {key: value for key, value in progress.items() if key != "since"}


def collapse(region: bytes, read: int | None = None) -> tuple[bytes, int | None]:
    """``region`` (whole lines plus a partial last one) as a terminal shows it.

    A line redrawn with carriage returns keeps its last non-empty frame; a
    partial line that ends in ``\\r`` keeps it, so the next chunk replaces
    the frame instead of being glued to it. ``read`` is an offset into
    ``region`` already handed to the agent; the result maps it into the
    collapsed bytes (a line it stopped inside is handed out again).
    """
    out = bytearray()
    mapped = None if read is None else 0
    position = 0
    for line in re.findall(rb"[^\n]*\n|[^\n]+$", region):
        end = position + len(line)
        if b"\r" in line:
            ends_newline = line.endswith(b"\n")
            body = line[:-1] if ends_newline else line
            frames = [frame for frame in body.split(b"\r") if frame]
            new = (frames[-1] if frames else b"") + (b"\n" if ends_newline else b"\r" if body.endswith(b"\r") else b"")
        else:
            new = line
        if read is not None:
            if end <= read:
                mapped = len(out) + len(new)
            elif position < read:
                mapped = len(out)
        out += new
        position = end
    return bytes(out), mapped


def last_line(output: bytes) -> str | None:
    """The last line with text in the stored output, without escape codes."""
    tail = output[-4096:].decode("utf-8", errors="replace")
    for line in reversed(re.split(r"[\r\n]", ANSI.sub("", tail))):
        line = line.strip()
        if line and not line.startswith("##progress"):
            return line[:200]
    return None
