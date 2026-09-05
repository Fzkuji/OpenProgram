"""Turn persistence — write the assistant message to SessionDB.

Extracted from dispatcher/__init__.py (dispatcher-split step 5, the
assistant-persistence piece). ``persist_assistant_message`` is phase 5 of
process_user_turn: resolve the model/provider columns, backfill usage via
Anthropic's count-tokens endpoint when the proxy dropped usage chunks,
build the assistant row (terminal status + token columns + ordered
blocks), strip @agentic_function calls out of the slim tool_calls list
(they render as their own runtime-block row), and persist — updating the
turn-start placeholder in place when one exists, else appending.

Returns ``(assistant_msg, blocks, tool_calls, usage)`` because phase 6
(finalize) and phase 7 (the TurnResult) consume the possibly-rewritten
usage + the filtered tool_calls + the ordered blocks. It touches none of
the test-patched helpers (only _is_anthropic_family / _mark_terminal_status,
neither of which any test patches), so it can live in its own module
without breaking the dispatcher tests' monkeypatch seam. The phase-2 user-
message persistence will join this module in a later step.

See docs/design/runtime/dispatcher-split.md.
"""
from __future__ import annotations

import logging
import json
import time

from openprogram.agent.internals._model_tools import is_anthropic_family as _is_anthropic_family
from openprogram.agent.internals._turn_lifecycle import mark_terminal_status as _mark_terminal_status

_log = logging.getLogger(__name__)


def persist_assistant_message(
    *,
    db,
    req,
    session: dict,
    usage: dict,
    final_text: str,
    history: list,
    tool_calls: list,
    _ordered_blocks: list,
    _agentic_tool_names: set,
    _placeholder_inserted: bool,
    cancel_event,
    assistant_msg_id: str,
    user_msg_id: str,
):
    """Persist the assistant turn (phase 5). Returns
    ``(assistant_msg, blocks, tool_calls, usage)`` — the caller rebinds
    ``usage`` / ``tool_calls`` because this may rewrite usage (Anthropic
    count fallback) and filters @agentic_function calls out of tool_calls.
    """
    # 5. Persist assistant message.
    # Attach usage + model so session_db.append_message stamps real
    # provider numbers (input/output/cache_read/cache_write) into the
    # messages.* token columns. If provider didn't report usage, leave
    # the columns NULL — we never fabricate counts.
    model_str = req.model_override or session.get("model") or ""
    if isinstance(model_str, dict):
        model_id = model_str.get("id") or model_str.get("model")
        provider_id = model_str.get("provider")
    elif isinstance(model_str, str) and ("/" in model_str or ":" in model_str):
        sep = "/" if "/" in model_str else ":"
        provider_id, model_id = model_str.split(sep, 1)
    else:
        model_id = model_str or None
        provider_id = None
    has_usage = bool(usage.get("input_tokens") or usage.get("output_tokens"))
    # Fallback for Anthropic-family models when the upstream proxy
    # (meridian / claude-max-api-proxy) doesn't forward usage chunks. Hit
    # Anthropic's
    # /v1/messages/count_tokens — it's a real, authoritative count for the
    # full message list we just sent, and it's free.
    token_source = "provider_usage"
    if not has_usage and _is_anthropic_family(model_id, provider_id):
        try:
            from openprogram.providers._shared.anthropic_token_count import (
                count_tokens_via_anthropic,
            )
            counted = count_tokens_via_anthropic(
                history + [{"role": "user", "content": req.user_text},
                           {"role": "assistant", "content": final_text}],
                model_id or "claude-sonnet-4-5",
            )
            if counted and counted.get("input_tokens"):
                usage = {
                    "input_tokens": int(counted["input_tokens"]),
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                }
                has_usage = True
                token_source = "anthropic_count_api"
        except Exception:
            # Fallback token counting is a network call; on failure the turn
            # keeps the provider-reported (or estimated) usage.
            _log.debug("anthropic token count fallback failed", exc_info=True)
    assistant_msg = {
        "id": assistant_msg_id,
        "role": "assistant",
        "content": final_text,
        "timestamp": time.time(),
        "predecessor": getattr(req, "_steering_tail_id", None) or user_msg_id,
        "source": req.source,
        "model": model_id,
        "provider": provider_id,
        # Which agent produced this reply — same field as the matching
        # user_msg above. Lets the UI colour / label both halves of
        # the turn consistently when multiple peer agents live in the
        # same session.
        "agent_id": req.agent_id,
        "execution_kind": "agent",
        "provider_request_count": int(
            usage.get("provider_request_count") or 0
        ),
        "agent_iteration_count": int(
            usage.get("agent_iteration_count") or 0
        ),
    }
    if req.structured_output_mode is not None:
        assistant_msg.update({
            "structured_output": req.structured_output,
            "structured_output_mode": req.structured_output_mode,
            "structured_output_attempt": req.structured_output_attempt,
        })
    from openprogram.agent.authority import runtime_authority, stamp_schema
    assistant_msg.update(runtime_authority(req, f"agent/{req.agent_id}"))
    stamp_schema(assistant_msg)
    # Stamp terminal lifecycle status — see _turn_lifecycle for the
    # state machine. ``cancel_event.is_set()`` here means the user
    # clicked stop mid-stream and the agent loop returned early with
    # partial output → record as "cancelled", not "completed". Leave
    # the streamed content alone — user wants whatever was visible at
    # click time to stay visible, not get replaced. Frontend optimistic
    # update appends a "*[cancelled by user]*" marker right when the
    # stop button is clicked; here we just stamp the terminal status.
    _mark_terminal_status(
        assistant_msg,
        cancelled=bool(cancel_event and cancel_event.is_set()),
    )
    if has_usage:
        assistant_msg.update({
            "input_tokens":  int(usage.get("input_tokens")  or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "cache_read_tokens":  int(usage.get("cache_read_tokens")  or 0),
            "cache_write_tokens": int(usage.get("cache_write_tokens") or 0),
            "token_source": token_source,
            "token_model":  model_id,
        })
    # Keep the unfiltered tool_calls (for tool_call_id → result
    # lookup, including @agentic_function calls) before stripping
    # the agentic ones from the legacy slim list. The ordered blocks
    # need to keep the agentic tool entries so the frontend can
    # position the RuntimeBlock at the exact spot in the LLM output
    # where the call happened.
    _tool_calls_all = list(tool_calls)
    blocks: list[dict] = []
    # Strip @agentic_function calls from the slim tool_calls list —
    # they render as their own runtime-block message (see
    # _wrap_agentic_runtime_block) rather than as collapsed cards
    # under the assistant bubble.
    if tool_calls and _agentic_tool_names:
        tool_calls = [
            t for t in tool_calls
            if (t.get("tool") or "") not in _agentic_tool_names
        ]
    if tool_calls or _ordered_blocks:
        # Persist BOTH shapes:
        #   * tool_calls — legacy slim list (id/tool/result/is_error)
        #     still consumed by older code paths.
        #   * blocks — the structured, ORDERED form the webui expects so
        #     it can render thinking / LLM text / tool cards interleaved
        #     in original emission order. If ordered blocks weren't
        #     captured (older path / streaming abort before turn_end),
        #     fall back to the legacy tool-only blocks layout.
        _tc_by_id = {
            (t.get("tool_call_id") or t.get("id")): t for t in _tool_calls_all
        }
        if _ordered_blocks:
            for blk in _ordered_blocks:
                if blk.get("type") == "tool":
                    _tid = blk.get("tool_call_id")
                    _tc = _tc_by_id.get(_tid, {})
                    blocks.append({
                        "type": "tool",
                        "tool": blk.get("tool") or _tc.get("tool"),
                        "tool_call_id": _tid,
                        "input": blk.get("input") or _tc.get("input"),
                        "result": _tc.get("result"),
                        "is_error": _tc.get("is_error"),
                    })
                else:
                    blocks.append(dict(blk))
        else:
            blocks = [
                {
                    "type": "tool",
                    "tool": t.get("tool"),
                    "tool_call_id": t.get("tool_call_id") or t.get("id"),
                    "input": t.get("input"),
                    "result": t.get("result"),
                    "is_error": t.get("is_error"),
                }
                for t in tool_calls
            ]
        assistant_msg["extra"] = json.dumps(
            {"tool_calls": tool_calls, "blocks": blocks},
            default=str,
        )
    # What the render policy did when this call's prompt was built —
    # replaying with it reproduces the exact bytes the model saw, even
    # after the aging constants move (dag/overview.md §8).
    try:
        from openprogram.context.aging import last_manifest
        _mf = last_manifest()
        if _mf:
            assistant_msg["render_manifest"] = _mf
    except Exception:
        # Without the manifest an exact replay of this prompt is not
        # reproducible, but the turn itself is unaffected.
        _log.debug("render manifest capture failed", exc_info=True)
    if _placeholder_inserted:
        # Update the placeholder row in place — same id, now with
        # final content + tool_calls/blocks. Writes Call fields
        # directly, skipping the _msg_to_node round-trip
        # (dag/overview.md step 5).
        try:
            from openprogram.store import SessionNodeWriter
            _shim = SessionNodeWriter(db, req.session_id)
            _meta = {
                k: v for k, v in assistant_msg.items()
                if k not in {"id", "role", "content", "timestamp"}
                and v is not None
            }
            wanted_status = _meta.pop("status", None)
            _shim.update(
                assistant_msg["id"],
                output=assistant_msg.get("content") or "",
                metadata=_meta,
            )
            from openprogram.agent.run_control import mark_execution_terminal
            if wanted_status == "completed":
                mark_execution_terminal(
                    assistant_msg["id"], "completed", store=db,
                )
            elif wanted_status in {"failed", "error", "interrupted"}:
                mark_execution_terminal(
                    assistant_msg["id"], wanted_status, store=db,
                )
            elif wanted_status == "cancelled":
                mark_execution_terminal(
                    assistant_msg["id"], "cancelled", store=db,
                )
        except Exception:
            db.append_message(req.session_id, assistant_msg)
    else:
        db.append_message(req.session_id, assistant_msg)

    return assistant_msg, blocks, tool_calls, usage
