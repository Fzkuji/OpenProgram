"""Export one session branch as a shareable Markdown or HTML file.

``transcript.py`` renders a branch for an LLM to read — clipped hard,
budgeted, prompt-shaped. This module renders the same branch for a
*human* to keep: no total budget, timestamps kept, and every string run
through :func:`remove_secret_values` so an exported file can be handed
to someone else.

The DAG walk is the one ``transcript`` established: ``get_branch`` gives
the conversational chain, tool/function nodes hang off their turn via
``caller`` (dag/overview.md), so they are grouped in one pass over
``get_messages``.

HTML output is a single self-contained file — inline ``<style>``, no
scripts, no external fetches — that follows the reader's system theme
through ``prefers-color-scheme``.
"""
from __future__ import annotations

import html
import json
import time
from typing import Any, Optional

from openprogram.providers.recording import remove_secret_values

# Tool results are the one field that can run to megabytes. Cap them well
# above transcript's prompt budget: an export is for reading, not for
# fitting in a context window.
MAX_RESULT_CHARS = 4_000
MAX_ARGS_CHARS = 1_000

FORMATS = ("md", "html")


def _clip(text: Any, limit: int) -> str:
    s = "" if text is None else str(text)
    s = s.strip()
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n… [+{len(s) - limit} chars truncated]"


def _redact(text: str) -> str:
    """Scrub secrets from one rendered string.

    ``remove_secret_values`` returns the input type it was given; for a
    ``str`` that is a ``str``, but coerce defensively so a future change
    upstream cannot inject a non-string into the document.
    """
    return str(remove_secret_values(text))


def _timestamp(value: Any) -> str:
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _tool_use(msg: dict[str, Any]) -> dict[str, Any]:
    """The ``tool_use`` blob a code node stores in ``extra`` (JSON or dict)."""
    extra = msg.get("extra")
    if isinstance(extra, str) and extra:
        try:
            extra = json.loads(extra)
        except (ValueError, TypeError):
            return {}
    if not isinstance(extra, dict):
        return {}
    tu = extra.get("tool_use")
    return tu if isinstance(tu, dict) else {}


def _call_summary(msg: dict[str, Any]) -> dict[str, str]:
    """One tool call flattened to the fields both renderers print."""
    args = _tool_use(msg).get("arguments")
    if args not in (None, "", {}) and not isinstance(args, str):
        try:
            args = json.dumps(args, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            args = str(args)
    return {
        "name": str(msg.get("function") or _tool_use(msg).get("name")
                    or "(unnamed call)"),
        "status": "failed" if msg.get("is_error") else "ok",
        "args": _redact(_clip(args, MAX_ARGS_CHARS)) if args else "",
        "result": _redact(_clip(msg.get("content"), MAX_RESULT_CHARS)),
    }


def _turn_label(msg: dict[str, Any]) -> str:
    role = str(msg.get("role") or "unknown")
    if (msg.get("function") or "") == "context/summary":
        return "compaction summary"
    if msg.get("source") == "agent_spawn" or msg.get("spawn_branch_root"):
        return f"{role} (spawned sub-branch root)"
    return role


def collect_turns(
    session_id: str,
    head_id: Optional[str] = None,
    include_tool_calls: bool = True,
    store: Any = None,
) -> list[dict[str, Any]]:
    """Walk one branch and return its turns, redacted and ready to render.

    Each turn is ``{index, role, timestamp, content, calls}`` where
    ``calls`` is the list produced by :func:`_call_summary`. Shared by
    both renderers so Markdown and HTML can never disagree about what a
    session contained.
    """
    if store is None:
        from openprogram.agent.session_db import default_db
        store = default_db()

    branch = store.get_branch(session_id, head_id)
    if not branch:
        return []

    calls_by_caller: dict[str, list[dict[str, Any]]] = {}
    if include_tool_calls:
        for msg in store.get_messages(session_id):
            if msg.get("role") != "tool":
                continue
            caller = msg.get("caller") or ""
            if caller:
                calls_by_caller.setdefault(caller, []).append(msg)

    turns: list[dict[str, Any]] = []
    for index, msg in enumerate(branch, 1):
        turns.append({
            "index": index,
            "role": _turn_label(msg),
            "timestamp": _timestamp(msg.get("timestamp")),
            "content": _redact(str(msg.get("content") or "").strip()),
            "calls": [_call_summary(c)
                      for c in calls_by_caller.get(msg.get("id") or "", [])],
        })
    return turns


def _session_title(session_id: str, store: Any) -> str:
    try:
        meta = store.get_session(session_id) or {}
    except Exception:  # noqa: BLE001 — a title is cosmetic, never fail an export
        return session_id
    return str(meta.get("title") or session_id)


def render_markdown(session_id: str, turns: list[dict[str, Any]],
                    title: str = "") -> str:
    """Render collected turns as Markdown."""
    out = [f"# {title or session_id}", "", f"- Session: `{session_id}`",
           f"- Turns: {len(turns)}",
           f"- Exported: {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    for turn in turns:
        stamp = f" · {turn['timestamp']}" if turn["timestamp"] else ""
        out.append(f"## [{turn['index']}] {turn['role']}{stamp}")
        out.append("")
        if turn["content"]:
            out.append(turn["content"])
            out.append("")
        for call in turn["calls"]:
            out.append(f"**Tool call: `{call['name']}` → {call['status']}**")
            out.append("")
            if call["args"]:
                out.append("Arguments:")
                out.append("")
                out.append("```")
                out.append(call["args"])
                out.append("```")
                out.append("")
            if call["result"]:
                out.append("Result:")
                out.append("")
                out.append("```")
                out.append(call["result"])
                out.append("```")
                out.append("")
    return "\n".join(out).rstrip() + "\n"


# Inline stylesheet — the whole point of the HTML export is one file that
# opens anywhere, so no webfonts, no CDN, no script tag. Colours are
# declared as custom properties once and flipped by prefers-color-scheme.
_HTML_STYLE = """
:root {
  color-scheme: light dark;
  --bg: #ffffff; --fg: #1a1a1a; --muted: #6b7280; --border: #e5e7eb;
  --panel: #f6f7f9; --accent: #2563eb; --error: #b91c1c;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16181d; --fg: #e6e6e6; --muted: #9aa0aa; --border: #2c3038;
    --panel: #1f232a; --accent: #7aa2f7; --error: #f87171;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0 auto; padding: 2rem 1.25rem; max-width: 52rem;
  background: var(--bg); color: var(--fg); line-height: 1.6;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
               "Helvetica Neue", Arial, sans-serif;
}
h1 { font-size: 1.6rem; margin: 0 0 .5rem; }
.meta { color: var(--muted); font-size: .85rem; margin-bottom: 2rem; }
.meta code { background: var(--panel); padding: .1rem .35rem; border-radius: 4px; }
.turn { border-top: 1px solid var(--border); padding: 1.25rem 0; }
.turn-head {
  display: flex; gap: .6rem; align-items: baseline;
  font-size: .8rem; color: var(--muted); margin-bottom: .5rem;
}
.role { font-weight: 600; color: var(--accent); text-transform: capitalize; }
.content { white-space: pre-wrap; word-wrap: break-word; }
.call {
  margin: .85rem 0 0; border: 1px solid var(--border); border-radius: 6px;
  background: var(--panel); padding: .6rem .75rem;
}
.call-head { font-size: .82rem; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.call-head .failed { color: var(--error); }
.call pre {
  margin: .4rem 0 0; padding: .5rem; overflow-x: auto;
  background: var(--bg); border-radius: 4px;
  font-size: .8rem; white-space: pre-wrap; word-wrap: break-word;
}
.call .label { font-size: .72rem; color: var(--muted); text-transform: uppercase;
  letter-spacing: .04em; margin-top: .45rem; }
"""


def render_html(session_id: str, turns: list[dict[str, Any]],
                title: str = "") -> str:
    """Render collected turns as one self-contained HTML document."""
    esc = html.escape
    heading = title or session_id
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{esc(heading)}</title>",
        f"<style>{_HTML_STYLE}</style>",
        "</head>",
        "<body>",
        f"<h1>{esc(heading)}</h1>",
        f'<div class="meta">Session <code>{esc(session_id)}</code> · '
        f"{len(turns)} turns · exported "
        f"{esc(time.strftime('%Y-%m-%d %H:%M:%S'))}</div>",
    ]
    for turn in turns:
        parts.append('<section class="turn">')
        stamp = (f'<span class="time">{esc(turn["timestamp"])}</span>'
                 if turn["timestamp"] else "")
        parts.append(
            f'<div class="turn-head"><span class="role">'
            f'{esc(turn["role"])}</span>'
            f'<span>#{turn["index"]}</span>{stamp}</div>'
        )
        if turn["content"]:
            parts.append(f'<div class="content">{esc(turn["content"])}</div>')
        for call in turn["calls"]:
            status_cls = " class=\"failed\"" if call["status"] == "failed" else ""
            parts.append('<div class="call">')
            parts.append(
                f'<div class="call-head">{esc(call["name"])} → '
                f'<span{status_cls}>{esc(call["status"])}</span></div>'
            )
            if call["args"]:
                parts.append('<div class="label">Arguments</div>')
                parts.append(f'<pre>{esc(call["args"])}</pre>')
            if call["result"]:
                parts.append('<div class="label">Result</div>')
                parts.append(f'<pre>{esc(call["result"])}</pre>')
            parts.append("</div>")
        parts.append("</section>")
    parts.extend(["</body>", "</html>", ""])
    return "\n".join(parts)


def export_session(
    session_id: str,
    export_format: str = "md",
    head_id: Optional[str] = None,
    include_tool_calls: bool = True,
    store: Any = None,
) -> str:
    """Render one session branch as a shareable document.

    Args:
        session_id: session to export.
        export_format: ``"md"`` or ``"html"``.
        head_id: branch tip to export. Defaults to the active head.
        include_tool_calls: include the tool / function calls each turn made.
        store: SessionStore override, for tests.

    Returns:
        The document text. Secrets are scrubbed with
        ``remove_secret_values`` before rendering.

    Raises:
        ValueError: unknown ``export_format``.
    """
    if export_format not in FORMATS:
        raise ValueError(
            f"unknown export format {export_format!r} (expected one of "
            f"{', '.join(FORMATS)})"
        )
    if store is None:
        from openprogram.agent.session_db import default_db
        store = default_db()

    turns = collect_turns(session_id, head_id=head_id,
                          include_tool_calls=include_tool_calls, store=store)
    title = _session_title(session_id, store)
    if export_format == "html":
        return render_html(session_id, turns, title)
    return render_markdown(session_id, turns, title)


__all__ = [
    "FORMATS",
    "collect_turns",
    "export_session",
    "render_html",
    "render_markdown",
]
