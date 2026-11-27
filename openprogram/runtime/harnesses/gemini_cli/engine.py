"""One conversation turn run by Google's Gemini CLI over ACP (``gemini --acp``).

The turn is DeepSeek Harness's ACP turn (``harnesses.deepseek.engine``) with
Gemini CLI's own calls: a saved session reopens with ``session/load``, the
model is picked with ``session/set_model`` from the ``models`` its sessions
offer, and the prompt's answer carries the turn's token use (``usage``).
Gemini CLI asks before a tool with ``session/request_permission``, which may
come before any ``tool_call`` of that tool, so the request opens its card.
The session is a real Gemini CLI session, kept per branch in the
conversation's ``harness_threads``; Gemini CLI has no documented format for
its saved sessions, so its own conversations are not synced here.

``session/load`` replays the session's history as updates. Gemini CLI 0.71
sends it all before answering; earlier releases answer first and replay
behind the answer. So the prompt waits until updates have been quiet a
moment, and updates only a replay sends (the person's messages, a tool call
already finished) are never this turn's. Gemini CLI records the person's
message as the prompt starts, so a turn that fails after it was sent leaves
the message in the session.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from openprogram.runtime.harnesses.codex.client import AppServer, AppServerError
from openprogram.runtime.harnesses.deepseek.engine import DshTurn, _content_text, _error_reason
from openprogram.runtime.harnesses.gemini_cli import client

_log = logging.getLogger(__name__)

PROVIDER = "gemini-cli-harness"
HARNESS_ID = "gemini-cli"
#: How long updates must be quiet after a session opens before the prompt goes.
REPLAY_QUIET_SECONDS = 0.3
#: The longest wait for a replay to end.
REPLAY_WAIT_SECONDS = 10.0


class GeminiCliEngine:
    """The engine registered for ``gemini-cli-harness``."""

    provider = PROVIDER
    label = "Gemini CLI"

    def run_turn(self, *, req, history, on_event, cancel_event, assistant_msg_id,
                 ordered_blocks_out, execution_context) -> tuple[str, dict, list[dict]]:
        return GeminiTurn(
            req=req, history=history or [], on_event=on_event, cancel_event=cancel_event,
            assistant_msg_id=assistant_msg_id or f"{req.user_msg_id}_reply",
            ordered_blocks_out=ordered_blocks_out, execution_context=execution_context,
        ).run()


def _arguments(update: dict) -> dict:
    """A tool call's arguments as Gemini CLI shows them: ``rawInput`` when sent, else the
    files it touches (its title already says what it does)."""
    if isinstance(update.get("rawInput"), dict):
        return update["rawInput"]
    paths = [str(loc.get("path")) for loc in update.get("locations") or [] if isinstance(loc, dict) and loc.get("path")]
    return {"paths": paths} if paths else {}


def _result_text(content: Any) -> str:
    """A tool's result: its text, and each file it changed (ACP ``diff`` entries)."""
    text = _content_text(content)
    changed = [str(c.get("path")) for c in content or [] if isinstance(c, dict) and c.get("type") == "diff" and c.get("path")]
    return "\n".join(part for part in (text, *(f"Changed {path}" for path in changed)) if part)


class GeminiTurn(DshTurn):
    provider = PROVIDER
    harness_id = HARNESS_ID
    label = "Gemini CLI"
    acp: Any = client
    resume_method = "session/load"
    holds_sent_prompt = True
    #: When the last update before the prompt came (a replay's), or None.
    _early_at: Optional[float] = None

    def _launch(self) -> tuple[list[str], dict[str, str]]:
        # Its own sign-in first; else the Model Services key, as ``GEMINI_API_KEY``.
        _, key = client.sign_in_now()
        return [], client.launch_env(key)

    def _options_of(self, answer: Any) -> list:
        return client.options_of(answer)

    def _remember_models(self, options: list) -> None:
        client.save_options(options)
        super()._remember_models(options)

    def _reopened(self) -> None:
        # Its replay may follow the answer: the quiet wait counts from here.
        self._early_at = time.monotonic()

    def _listen(self, message: dict) -> bool:
        if message.get("method") == "session/update" and not self.prompt_sent.is_set():
            self._early_at = time.monotonic()  # a replay of the session's history
        return super()._listen(message)

    def _session(self, server: AppServer, fresh: bool) -> tuple[Optional[str], list]:
        opened = super()._session(server, fresh)
        # A replay still coming in after the answer is not this turn's: wait for it to end.
        deadline = time.monotonic() + REPLAY_WAIT_SECONDS
        while (self._early_at is not None and time.monotonic() - self._early_at < REPLAY_QUIET_SECONDS
               and time.monotonic() < deadline and not self.cancel_event.is_set() and server.alive):
            time.sleep(0.05)
        return opened

    def _reason(self, message: str):
        from openprogram.models.providers.utils.failures.errors import ErrorReason
        text = message.lower()
        if "authentication" in text or "not configured" in text or "log in" in text or "login" in text:
            return ErrorReason.AUTHENTICATION
        return _error_reason(message)

    def _configure(self, server: AppServer, options: list) -> None:
        """Pick the branch's model for the session, where it differs; no effort is set over ACP."""
        model = self.model_id
        if not model or model.lower() in ("default", "auto"):
            return
        current = next((o.get("currentValue") for o in options if isinstance(o, dict) and o.get("id") == "model"), None)
        if current == model:
            return
        try:
            server.request("session/set_model", {"sessionId": self.session_id, "modelId": model})
        except AppServerError as exc:
            if exc.exited:
                raise
            _log.info("Gemini CLI kept its model for %s (%s)", self.session_id, exc)

    def _settled(self, answer: Any) -> None:
        usage = (answer or {}).get("usage") if isinstance(answer, dict) else None
        if not isinstance(usage, dict):
            return
        for ours, theirs in (("input_tokens", "inputTokens"), ("output_tokens", "outputTokens"),
                             ("cache_read_tokens", "cachedReadTokens")):
            if isinstance(usage.get(theirs), int):
                self.usage[ours] = usage[theirs]

    def _update(self, update: dict) -> None:
        kind = update.get("sessionUpdate")
        if kind == "user_message_chunk" or (kind == "tool_call" and update.get("status") in ("completed", "failed")):
            return  # only a replay of the session's history sends these
        if kind == "tool_call":
            self._start_card(str(update.get("toolCallId") or ""), str(update.get("title") or "tool"), _arguments(update))
        elif kind == "tool_call_update" and update.get("status") in ("completed", "failed"):
            failed = update["status"] == "failed"
            self._end_card(str(update.get("toolCallId") or ""), _result_text(update.get("content")), failed,
                           "error" if failed else "completed")
        else:
            super()._update(update)

    def _answer(self, message: dict) -> None:
        params = message.get("params") or {}
        call = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
        call_id = str(call.get("toolCallId") or "")
        if call_id and call_id not in self.open_cards:
            # Asked before its card was opened: the request names the tool.
            self._start_card(call_id, str(call.get("title") or "tool"), _arguments(call))
        super()._answer(message)
