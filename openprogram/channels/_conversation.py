"""Persistent conversation wrapper for chat-channel messages.

Before this module existed, every channel just called ``rt.exec(text)``
— stateless, every message independent, the bot couldn't follow up.

Now each external user gets a named conversation that's:

* stored under ``~/.agentic/sessions/<platform>_<user_id>/`` via the
  same webui persistence module, so it survives process restarts and
  appears in the Web UI's session list alongside REPL-created
  conversations
* loaded fresh each turn (so concurrent CLI REPL, web UI, and channel
  turns on the same conversation don't stomp each other's messages
  in-memory — the on-disk file is the source of truth)
* rendered as a text prefix in front of the incoming user message,
  mirroring what ``webui/server.py:560`` does for its own chat path

The format lines up with the one webui uses (``[User]: ...`` /
``[Assistant]: ...``) so the same history works whichever front-end
displays it later.
"""
from __future__ import annotations

import re
import time
import uuid
from typing import Any

from openprogram.webui import persistence as _persist


# Keep the history prefix bounded so a long-running WeChat thread
# doesn't silently blow past the model's context window. The webui
# uses its own limit (_MAX_CONTEXT_CHARS ≈ 200k) — we mirror the
# approximate intent with a smaller cap since channels tend to turn
# more chat-style back-and-forth.
MAX_HISTORY_CHARS = 60_000


def conv_id_for(platform: str, user_id: str) -> str:
    """Deterministic conv id derived from platform + external user id.

    Safe for use as a directory name — replaces anything outside
    ``[A-Za-z0-9_-]`` with ``-`` so Tencent / Discord / Slack id
    shapes (which may contain ``@``, ``.``, ``:`` etc.) don't break
    the path.
    """
    safe_user = re.sub(r"[^A-Za-z0-9_-]", "-", user_id) or "anon"
    return f"{platform}_{safe_user}"


def turn_with_history(platform: str, user_id: str, user_text: str, rt,
                      *, user_display: str | None = None) -> str:
    """Run one turn with persistent history.

    Loads the conversation (if any), appends the user's message, builds
    a ``[User]: ... [Assistant]: ...`` prefix from the recent history,
    calls ``rt.exec``, appends the reply, and saves. Returns the
    assistant text.

    ``user_display`` is a human-readable label for the user side of
    the conversation — e.g. WeChat nickname or Telegram @handle. If
    omitted we fall back to ``user_id``. Only used for the
    conversation title shown in the Web UI list; the prompt itself
    always says ``[User]:``.
    """
    conv_id = conv_id_for(platform, user_id)
    meta, messages = _load_or_init(conv_id, platform,
                                   user_display or user_id)

    user_msg_id = uuid.uuid4().hex[:12]
    messages.append({
        "role": "user",
        "id": user_msg_id,
        "parent_id": messages[-1]["id"] if messages else None,
        "content": user_text,
        "timestamp": time.time(),
        "source": platform,
    })

    history_prefix = _render_history(messages[:-1])
    exec_content = []
    if history_prefix:
        exec_content.append({"type": "text", "text": history_prefix})
    exec_content.append({"type": "text", "text": user_text})

    try:
        reply = rt.exec(content=exec_content)
        reply_text = str(reply or "").strip() or "(empty reply)"
    except Exception as e:  # noqa: BLE001
        reply_text = f"[error] {type(e).__name__}: {e}"

    messages.append({
        "role": "assistant",
        "id": user_msg_id + "_reply",
        "parent_id": user_msg_id,
        "content": reply_text,
        "timestamp": time.time(),
        "source": platform,
    })

    meta["head_id"] = messages[-1]["id"]
    meta["_last_touched"] = time.time()
    _persist.save_meta(conv_id, meta)
    _persist.save_messages(conv_id, messages)

    # Best-effort: if webui is running in this process, patch its
    # in-memory conversation dict so the UI shows the new messages
    # on its next render pass (e.g. page reload). Live WebSocket
    # push is a follow-up — for now a browser refresh surfaces the
    # turn.
    _poke_live_webui(conv_id, messages, meta)

    return reply_text


def _load_or_init(conv_id: str, platform: str,
                  user_display: str) -> tuple[dict, list[dict]]:
    """Load persisted meta+messages or build fresh structures.

    ``persistence.load_conversation`` returns a flat dict with meta
    fields + ``messages`` + ``function_trees``. We split messages out
    and treat the rest as meta.
    """
    data = _persist.load_conversation(conv_id)
    if data:
        messages = list(data.get("messages", []))
        # Drop the non-meta keys so we can round-trip through save_meta
        # without bloating meta.json with messages / function_trees.
        meta = {k: v for k, v in data.items()
                if k not in ("messages", "function_trees")}
        if meta:
            return meta, messages

    meta = {
        "id": conv_id,
        "title": f"{platform}: {user_display}",
        "provider_name": None,
        "model": None,
        "session_id": None,
        "created_at": time.time(),
        # Tag so webui can group / filter / decorate these.
        "source": platform,
        "channel_user_display": user_display,
        "head_id": None,
        "context_tree": None,
        "_titled": True,
        "_last_touched": time.time(),
    }
    return meta, []


def _render_history(messages: list[dict]) -> str:
    """Render message list as the ``[User]: ...`` prefix webui uses.

    Walks backwards to prioritize recent turns when we hit the char
    budget, then re-reverses so the prompt reads oldest-first (the
    order humans and models expect).
    """
    if not messages:
        return ""
    parts: list[str] = []
    total = 0
    for m in reversed(messages):
        role = m.get("role", "")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            entry = f"[User]: {content}"
        elif role == "assistant":
            entry = f"[Assistant]: {content}"
        else:
            continue
        if total + len(entry) > MAX_HISTORY_CHARS:
            break
        parts.append(entry)
        total += len(entry)
    parts.reverse()
    if not parts:
        return ""
    return (
        "── Conversation history ──\n"
        + "\n".join(parts)
        + "\n── End of history ──\n\n"
    )


def _poke_live_webui(conv_id: str, messages: list[dict], meta: dict) -> None:
    """Patch the running webui's in-memory conversation if applicable.

    Noop when webui isn't imported (CLI chat only, or pure
    ``channels start``) — the on-disk save has already happened.
    Imports ``openprogram.webui.server`` lazily so this module stays
    cheap for the CLI path.
    """
    try:
        import sys
        srv = sys.modules.get("openprogram.webui.server")
        if srv is None:
            return
        with srv._conversations_lock:
            conv = srv._conversations.get(conv_id)
            if conv is None:
                # Build a minimal shape compatible with _save_conversation
                # and the UI's conversation list endpoint.
                from openprogram.agentic_programming.context import Context
                conv = {
                    "id": conv_id,
                    "title": meta.get("title", conv_id),
                    "root_context": Context(name="chat_session", status="idle",
                                             start_time=time.time()),
                    "runtime": None,
                    "provider_name": meta.get("provider_name"),
                    "messages": list(messages),
                    "function_trees": [],
                    "created_at": meta.get("created_at"),
                    "head_id": meta.get("head_id"),
                    "run_active": False,
                    "source": meta.get("source"),
                }
                srv._conversations[conv_id] = conv
            else:
                conv["messages"] = list(messages)
                conv["head_id"] = meta.get("head_id")
    except Exception:
        pass
