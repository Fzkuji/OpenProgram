"""Separate what the user typed from what the host agent injected.

Both agents store their own scaffolding in user-role records: system
reminders, environment context, AGENTS.md, slash-command stdout, task
notifications. None of it is the user's words, and replaying it would
show up as a wall of tags in the chat and as stale instructions to the
model, so it is removed before a turn is built.

:func:`remove_secrets` then strips credential-shaped values from what is
left — a key pasted into a prompt, a token echoed by ``cat .env`` — so
an import never copies a credential into OpenProgram's own store, where
later turns would replay it to whichever model runs them.
"""
from __future__ import annotations

import re
from typing import Any

from openprogram.state.secret_removal import SECRET_FIELD_NAMES, remove_secrets as _remove_secret_text

SECRET_PLACEHOLDER = "[secret removed]"

# Elements that are always host scaffolding, wherever they appear.
_INJECTED_TAGS = (
    "system-reminder", "local-command-stdout", "local-command-stderr",
    "local-command-caveat", "task-notification", "turn_aborted",
    "environment_context", "user_instructions", "app-context",
    "in-app-browser-context", "external_codex_apps_open_page",
    "recommended_plugins", "codex_internal_context", "skill",
    "permissions instructions", "user_action", "subagent_notification",
    "send_user_message_question_response", "command-message",
)
_INJECTED_RE = re.compile(
    r"<(" + "|".join(re.escape(t) for t in _INJECTED_TAGS) + r")\b[^>]*>.*?</\1\s*>",
    re.DOTALL,
)
# Markdown preambles Codex prepends as their own content blocks.
_INJECTED_HEADERS = (
    "# AGENTS.md instructions", "# Files mentioned by the user:",
    "# Files pasted by the user:", "# Response annotations:",
)
_COMMAND_NAME_RE = re.compile(r"<command-name>\s*(.*?)\s*</command-name>", re.DOTALL)
_COMMAND_ARGS_RE = re.compile(r"<command-args>\s*(.*?)\s*</command-args>", re.DOTALL)
_WHOLE_ELEMENT_RE = re.compile(r"^\s*<([A-Za-z][\w-]*)\b[^>]*>.*</\1\s*>\s*$", re.DOTALL)
_IMAGE_OPEN_RE = re.compile(r'^\s*<image name=\[?([^\]\s>]+(?: #\d+)?)\]?[^>]*>\s*$')


def clean_user_text(text: str) -> str:
    """The user's own words in ``text``, or ``""`` when there are none.

    A slash-command record keeps its visible form (``/review focus``):
    the command is what the user typed, and the reply that follows
    answers it.
    """
    if not text:
        return ""
    command = _COMMAND_NAME_RE.search(text)
    if command:
        args = _COMMAND_ARGS_RE.search(text)
        shown = command.group(1).strip()
        if args and args.group(1).strip():
            shown = f"{shown} {args.group(1).strip()}"
        return shown
    text = _INJECTED_RE.sub("", text)
    stripped = text.strip()
    if any(stripped.startswith(h) for h in _INJECTED_HEADERS):
        return ""
    return stripped


def is_command(text: str) -> bool:
    return bool(_COMMAND_NAME_RE.search(text or ""))


def clean_block_text(text: str) -> str:
    """Like :func:`clean_user_text`, for one Codex content block.

    Codex gives each injected piece a block of its own, so any block that
    is a single unknown element is scaffolding too.
    """
    cleaned = clean_user_text(text)
    if cleaned and not is_command(text) and _WHOLE_ELEMENT_RE.match(cleaned):
        return ""
    return cleaned


def image_label(text: str) -> str | None:
    """``[Image #1]`` for a Codex ``<image name=[Image #1] …>`` opener."""
    m = _IMAGE_OPEN_RE.match(text or "")
    return f"[{m.group(1)}]" if m else None



def remove_secret_values(value: Any) -> Any:
    """``value`` with credentials replaced; dicts and lists are walked.

    Strings lose the high-precision secret shapes requests are redacted
    for (provider keys, GitHub / Slack / AWS / Google tokens, JWTs,
    private keys, ``Bearer …``, ``password=…``); a dict entry named like a
    credential (``api_key``, ``token``, ``authorization`` …) loses its
    whole value. Ordinary prose that only mentions a key or a token is
    left alone, so the conversation still reads as it did.
    """
    if isinstance(value, str):
        return _remove_secret_text(value, SECRET_PLACEHOLDER)
    if isinstance(value, dict):
        return {
            k: SECRET_PLACEHOLDER if (str(k).lower() in SECRET_FIELD_NAMES
                                      and isinstance(v, str) and v)
            else remove_secret_values(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [remove_secret_values(v) for v in value]
    return value


def remove_secrets(session: Any) -> Any:
    """Redact credentials in a parsed session, in place.

    Covers the title, every user message, reply and thinking text, and
    each tool call's arguments and result. A turn keeps the fingerprint
    of its text as read, so whether a source grew or was rewound is
    judged on the transcript itself; the reply digest is taken after
    redaction, so a turn written with a secret in it is rewritten clean.
    """
    from openprogram.state.authority import unwrap_owner_input
    from .model import ToolBlock
    from .timeparse import fallback_title

    for turn in session.turns:
        turn.source_fingerprint = turn.fingerprint()
        # A message OpenProgram once sent in its speaker envelope shows as typed.
        turn.user_text = remove_secret_values(unwrap_owner_input(turn.user_text))
        for block in turn.blocks:
            if isinstance(block, ToolBlock):
                block.arguments = remove_secret_values(block.arguments)
                if block.result is not None:
                    block.result = remove_secret_values(block.result)
            elif isinstance(block.get("text"), str):
                block["text"] = remove_secret_values(block["text"])
    title = session.title or ""
    if title.lstrip().startswith(ENVELOPE_TITLE) and session.turns:
        title = fallback_title(session.turns[0].user_text)  # named after an envelope
    session.title = remove_secret_values(title)
    return session


#: How a title taken from a message sent in OpenProgram's speaker envelope begins.
ENVELOPE_TITLE = '{"speaker_kind"'
