"""LLM-driven session summarizer.

Used by ``on_session_end`` to extract durable facts from a finished
conversation into 3–10 short-term notes.

We use the same model the session ran with (no separate cheap model).
The summary prompt is intentionally narrow: extract facts likely to
matter in *future* sessions, ignore execution details and one-shot
debugging context.
"""
from __future__ import annotations

import json
from typing import Any

SYSTEM_PROMPT = """\
You are a memory curator for a long-running AI agent. You read a finished
conversation and extract durable facts worth remembering for future sessions.

Output a JSON array of 0–10 entries. Each entry is an object with:

    - "type":   one of "user-pref" | "env" | "project" | "procedure" | "fact"
    - "text":   one sentence, factual and atomic, < 200 chars
    - "tags":   array of 1–3 lowercase tags
    - "confidence": float 0.0–1.0 (how confident you are this is durable)

Capture:
- User preferences ("user prefers concise responses")
- Environment facts ("project lives at ~/Projects/foo, Python 3.12")
- Decisions made ("user chose to use git pull for auto-update")
- Conventions confirmed ("backend daemon is called 'worker' not 'daemon'")
- Lessons learned ("uvicorn doesn't react to SIGTERM, fall back to SIGKILL")

Skip:
- Specific debug output, stack traces, file diffs
- One-off questions and their answers
- Anything you'd be unsure to repeat next time

Return ONLY the JSON array, no preamble. If no durable facts, return [].
"""


def build_input_text(messages: list[dict[str, Any]], *, max_chars: int = 12000) -> str:
    """Render a message list into a compact text block for the summarizer.

    Truncates to *max_chars* from the tail since end-of-conversation
    decisions are usually the most informative.
    """
    lines: list[str] = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                str(part.get("text", part)) if isinstance(part, dict) else str(part)
                for part in content
            )
        content = str(content).strip()
        if not content:
            continue
        lines.append(f"[{role}] {content}")
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    return "…[earlier conversation truncated]…\n" + text[-max_chars:]


def parse_extraction(raw: str) -> list[dict[str, Any]]:
    """Parse the model's JSON response, tolerating prose around it."""
    raw = raw.strip()
    start = raw.find("[")
    end = raw.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text or len(text) > 400:
            continue
        out.append({
            "type": str(item.get("type", "fact")),
            "text": text,
            "tags": [str(t).lower() for t in (item.get("tags") or []) if t][:3],
            "confidence": _clamp(item.get("confidence"), 0.0, 1.0, default=0.5),
        })
    return out[:10]


def _clamp(v: Any, lo: float, hi: float, *, default: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, x))


def system_prompt() -> str:
    return SYSTEM_PROMPT
