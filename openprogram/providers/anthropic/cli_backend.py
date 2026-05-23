"""CliBackendPlugin for the Claude Code CLI (``claude``).

Fills out the generic ``CliBackendConfig`` + hooks for the Anthropic
``claude`` binary running in stream-json stdin/stdout mode. The generic
``CliRunner`` owns subprocess lifecycle, watchdog, session resume, and
live-session reuse — this file contains only provider-specific details:

- argv layout (``--permission-mode``, ``--input-format``, ``--output-format``)
- stream-json envelope shape (text + base64 images)
- thinking-level → ``--settings defaultEffortLevel`` JSON
- clearing ``ANTHROPIC_API_KEY`` so the CLI uses the subscription path
"""

from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Optional

from openprogram.providers._shared.cli_backend import (
    CliBackendConfig,
    CliBackendPlugin,
)


def _encode_image_source(path: str) -> Optional[dict]:
    """Build an Anthropic base64 image ``source`` dict from a file path.

    Returns ``None`` if the file can't be read — caller should skip the
    block rather than abort the whole turn.
    """
    media_type = mimetypes.guess_type(path)[0] or "image/png"
    try:
        raw = Path(path).read_bytes()
    except (FileNotFoundError, PermissionError, IsADirectoryError):
        return None
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.b64encode(raw).decode("utf-8"),
        },
    }


def _build_turn_envelope(prompt: str, image_paths: tuple[str, ...]) -> str:
    """Build one stream-json ``user`` message for live-session mode.

    Content is a list of Anthropic blocks: a single ``text`` block for
    the prompt, followed by one ``image`` block per path (inlined as
    base64, because the CLI's stream-json transport doesn't accept file
    references). Images that fail to read are dropped silently — the
    turn continues with the remaining blocks.
    """
    content: list[dict] = [{"type": "text", "text": prompt}]
    for p in image_paths:
        block = _encode_image_source(p)
        if block is not None:
            content.append(block)
    return json.dumps({
        "type": "user",
        "message": {"role": "user", "content": content},
    }) + "\n"


# Map the Runtime-level canonical levels to the CLI's ``defaultEffortLevel``
# key. The CLI accepts a narrower set than openprogram's unified knob:
# ``"none"`` folds to ``"low"`` (no way to truly disable), ``"xhigh"`` and
# ``"max"`` pass through, ``"auto"`` is valid at the settings layer but
# rejected by the ``--effort`` flag — the exact reason we write to
# ``--settings`` instead of ``--effort``.
_EFFORT_MAP: dict[str, str] = {
    "off": "low",
    "none": "low",
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "max": "max",
    "auto": "auto",
}


def _build_thinking_args(level: str) -> tuple[str, ...]:
    mapped = _EFFORT_MAP.get(level, level)
    return ("--settings", json.dumps({"defaultEffortLevel": mapped}))


CLAUDE_CODE_CONFIG: CliBackendConfig = CliBackendConfig(
    command="claude",
    args=(
        "--permission-mode", "bypassPermissions",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
    ),
    # Subscription auth — block the API-credit path so usage hits the
    # logged-in Claude Code session, not metered tokens.
    clear_env=("ANTHROPIC_API_KEY",),
    input="stdin",
    output="jsonl",
    jsonl_dialect="claude-stream-json",
    live_session="claude-stdio",
    model_arg="--model",
    # Claude Code manages its own conversation state inside the persistent
    # process — we don't pass --session-id. If the user resumes from a
    # different runtime instance, session resume happens at the CLI's own
    # layer (subscription session), not via our on-disk session_id.
    session_mode="none",
    # Match the legacy ClaudeCodeRuntime default: respawn after 100 turns
    # to bound accumulated CLI-side context.
    max_turns_per_process=100,
    # ``/compact`` must be wrapped as a stream-json user message to be
    # accepted in stream-json input mode, so we do NOT use the runner's
    # raw-line compact_command path. The runtime layer calls
    # ``runner.run("/compact")`` instead, which exercises the envelope
    # hook above.
    compact_command=None,
)


CLAUDE_CODE_PLUGIN: CliBackendPlugin = CliBackendPlugin(
    id="claude-cli",
    config=CLAUDE_CODE_CONFIG,
    build_turn_envelope=_build_turn_envelope,
    build_thinking_args=_build_thinking_args,
    # TODO (1f-γ): prepare_execution hook will plug into Auth v2's isolated
    # HOME when N4 AuthReference + subscription-aware auth lands.
)


__all__ = [
    "CLAUDE_CODE_CONFIG",
    "CLAUDE_CODE_PLUGIN",
]
