"""Server context stats."""
from __future__ import annotations
from ... import server as state


def _append_msg(conv: dict, msg: dict) -> None:
    """Append ``msg`` to ``conv``: in-memory mirror + SessionDB.

    Single source of truth path for non-dispatcher webui writes (run /
    create / error / system messages). Dispatcher already writes
    user+assistant rows itself; this helper covers everything else.

    Order matters:
      1. ``_raw_advance_head`` mutates ``conv["messages"]`` and
         ``conv["head_id"]`` so existing readers see it immediately.
      2. SessionDB.append_message persists for cross-process readers.
      3. SessionDB.set_head bumps the active leaf — without this,
         a fresh ``_get_messages`` cache miss would walk back to the
         old head and miss the just-appended row.
      4. Cache invalidation is last so step 3 is visible.

    Failures in steps 2-4 are logged but non-fatal; the in-memory
    mirror stays consistent for display. The store is the source of
    truth — mirror rows never sync back (context/compaction.md §5).
    """
    # Streaming-resume: if a placeholder with this id already lives
    # in ``conv["messages"]`` (e.g. ``run.py`` wrote a status=running
    # row before kicking off the function, and now we're back with
    # the final reply), update the existing entry in place instead of
    # appending a duplicate. The on-disk side handles its own
    # dedup — ``SessionStore.append_message`` is idempotent on id
    # and the final reply uses ``SessionNodeWriter.update()`` to patch
    # the persisted node.
    # Dispatcher completion advances the durable head without updating this
    # server's conversation mirror. Resolve an ordinary user append before
    # advance_head fills in a predecessor from that potentially stale mirror.
    # Explicit predecessors, including None for a root fork, remain exact.
    if msg.get("role") == "user" and "predecessor" not in msg and conv.get("id"):
        from openprogram.agent.session_db import default_db
        session = default_db().get_session(conv["id"]) or {}
        msg["predecessor"] = session.get("head_id") or "ROOT"

    _existing_idx = -1
    if msg.get("id"):
        for _i, _existing in enumerate(conv.get("messages") or []):
            if _existing.get("id") == msg["id"]:
                _existing_idx = _i
                break
    if _existing_idx >= 0:
        conv["messages"][_existing_idx] = {**conv["messages"][_existing_idx], **msg}
        conv["head_id"] = msg["id"]
    else:
        state._raw_advance_head(conv, msg)
    cid = conv.get("id")
    msg_id = msg.get("id")
    if not cid or not msg_id:
        return
    try:
        from openprogram.agent.session_db import default_db
        db = default_db()
        if db.get_session(cid) is None:
            create_kwargs = {}
            # Channel binding + presentational fields — these no longer
            # live in the _sessions dict, so pull from the message itself
            # or from run-config fields still on the dict.
            for fld in ("source", "peer_display"):
                v = msg.get(fld)
                if v:
                    create_kwargs[fld] = v
            # Per-session run config — these used to be written via
            # save_session_run_config which create_session'd a ghost row
            # even when the user never sent a real message. Now folded
            # into the same create_session call as the first message so
            # SessionDB only ever holds rows for sessions with content.
            for fld in (
                "tools_enabled", "tools_override", "thinking_effort",
                "permission_mode", "sandbox_enabled",
            ):
                v = conv.get(fld)
                if v is not None:
                    create_kwargs[fld] = v
            db.create_session(cid, msg.get("agent_id") or state._default_agent_id(), **create_kwargs)
        # Ensure ROOT node + user caller=ROOT for session DAG.
        if msg.get("role") == "user":
            try:
                from openprogram.context.nodes import Call as _C, ROLE_USER as _RU
                from openprogram.store import SessionNodeWriter as _GS
                _ROOT_ID = "ROOT"
                if not db.message_exists(cid, _ROOT_ID):
                    _GS(db, cid).append(_C(
                        id=_ROOT_ID, role=_RU, output="",
                        metadata={"display": "root"},
                    ))
                # The predecessor was resolved before mirror mutation above,
                # or supplied explicitly by the caller (None means a root fork).
                _pred = msg.get("predecessor")
                _umeta = {k: v for k, v in msg.items()
                          if k not in {"id", "role", "content", "timestamp",
                                       "predecessor"}
                          and v is not None}
                # The conv edge is the top-level Call field (Decision 1):
                # a real prior turn's reply id, or ROOT explicitly for a
                # first turn / root-level fork.
                _GS(db, cid).append(_C(
                    id=msg_id,
                    role=_RU,
                    output=msg.get("content") or "",
                    caller=_ROOT_ID,
                    predecessor=_pred or _ROOT_ID,
                    metadata=_umeta,
                ))
            except Exception:
                db.append_message(cid, msg)
        else:
            db.append_message(cid, msg)
        db.set_head(cid, msg_id)
    except Exception as e:
        state._log(f"_append_msg: SessionDB write failed for {cid}/{msg_id}: {e}")
    state._invalidate_messages(cid)


def _execute_in_context(session_id: str, msg_id: str, action: str, **kwargs):
    """Legacy name kept for ws_actions/chat.py and _chat_routes.py callers.

    The real implementation lives in openprogram/webui/_execute/. This shim
    just forwards so existing import sites keep working.
    """
    from openprogram.webui._execute import execute_in_context
    return execute_in_context(session_id, msg_id, action, **kwargs)


def _broadcast_context_stats(session_id: str, msg_id: str, chat_runtime=None, exec_runtime=None):
    """Broadcast chat & exec token usage stats to frontend.

    Chat usage: use the provider's latest reported value directly.
      - CLI providers report usage that already reflects the full session context.
      - API providers report usage that includes the full conversation in input_tokens.
      - No accumulation — provider knows best about its own usage.
    Exec usage: per-function execution, read from exec_runtime.last_usage.
    """
    conv = state._sessions.get(session_id)
    if not conv:
        return

    _zero = {"input_tokens": 0, "output_tokens": 0, "cache_read": 0}

    # --- Chat usage: use last_usage (per-call = current context window size) ---
    # NOT session_usage (cumulative across all API calls, inflated for Codex).
    # last_usage.input_tokens = total tokens sent in the last call ≈ context size.
    if chat_runtime:
        usage = getattr(chat_runtime, 'last_usage', None)
        if usage and (usage.get("input_tokens") or usage.get("output_tokens") or usage.get("cache_read") or usage.get("cache_create")):
            conv["_chat_usage"] = {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "cache_read": usage.get("cache_read", 0),
                "cache_create": usage.get("cache_create", 0),
                # 最后一次调用的 prompt 体积（input+cache_read）≈ 当前
                # 上下文占用；badge 圆环用它算百分比，不用 turn 累计值。
                "context_tokens": usage.get("context_tokens", 0),
            }

    # --- Exec usage (per-function, not cumulative) ---
    exec_stats = None
    if exec_runtime:
        eu = getattr(exec_runtime, 'last_usage', None)
        if eu and (eu.get("input_tokens") or eu.get("output_tokens") or eu.get("cache_read") or eu.get("cache_create")):
            exec_stats = {
                "input_tokens": eu.get("input_tokens", 0),
                "output_tokens": eu.get("output_tokens", 0),
                "cache_read": eu.get("cache_read", 0),
                "cache_create": eu.get("cache_create", 0),
            }

    # Include provider name so frontend can apply provider-specific formatting
    provider_name = conv.get("provider_name", state._runtime_management._default_provider) or ""

    # Best-effort context window for the current model — frontend uses this
    # to render the input/output % bar. Falls back to None on unknown.
    chat_model = getattr(chat_runtime, "model", None) if chat_runtime else None

    context_window = (state._resolve_context_window(provider_name, chat_model)
                      or state._conv_context_window(conv))

    # The request that just finished measured the real prompt size, so the
    # occupancy record switches to the ``measured`` basis and stores the
    # measured/estimated ratio as calibration.
    measured = int((conv.get("_chat_usage") or {}).get("context_tokens") or 0)
    occupancy = state._build_context_occupancy(
        session_id, conv, measured_total=measured, window=context_window,
    )

    stats = {
        "type": "context_stats",
        "chat": conv.get("_chat_usage", dict(_zero)),
        "exec": exec_stats,
        "provider": provider_name,
        "model": chat_model,
        "context_window": occupancy["window"] or context_window,
        **occupancy,
        "_context_rev": int(conv.get("_context_rev") or 0),
    }
    breakdown = conv.get("_last_context_breakdown")
    if breakdown:
        stats["breakdown"] = breakdown
    conv["_last_context_stats"] = stats
    state._broadcast_chat_response(session_id, msg_id, stats)


def _resolve_context_window(provider_name, model) -> int | None:
    """Real context window for ``provider:model`` from the model registry.

    The runtime object carries no window of its own, so the registry is the
    single place both the ring and the ``/context`` panel look it up. A
    ``provider:model`` string splits on the colon; a bare id uses the
    session's provider.
    """
    if not model:
        return None
    try:
        from openprogram.providers.models import get_model as _get_model
        from openprogram.context.tokens import real_context_window
        prov, mid = provider_name, str(model)
        if ":" in mid:
            prov, mid = mid.split(":", 1)
        m = _get_model(prov, mid) if prov and mid else None
        return real_context_window(m) if m is not None else None
    except Exception:
        return None


def _build_context_occupancy(session_id, conv, *, measured_total=None,
                             window=None, estimated_total=None) -> dict:
    """``{window, total_used, basis, estimated[, calibration]}`` for a session.

    Falls back to the last broadcast record when the estimate cannot be
    computed (a brand-new session with no branch yet, a DB read failure),
    so a transient error never blanks the ring.
    """
    from openprogram.context import session_stats as _cs

    try:
        occupancy = _cs.build_stats(
            session_id,
            head_id=(conv or {}).get("head_id"),
            measured_total=measured_total,
            window=window,
            estimated_total=estimated_total,
        )
        snapshot = occupancy.pop("_breakdown", None)
        if snapshot is not None and conv is not None:
            occ = {
                key: occupancy[key]
                for key in ("window", "total_used", "basis", "estimated", "calibration")
                if key in occupancy
            }
            finalized = _cs.finalize_breakdown(snapshot, occ)
            finalized["head_id"] = conv.get("head_id")
            finalized["_context_rev"] = int(conv.get("_context_rev") or 0)
            conv["_last_context_breakdown"] = finalized
        return occupancy
    except Exception:
        prev = (conv or {}).get("_last_context_stats") or {}
        return {
            "window": int(window or prev.get("window") or 0),
            "total_used": int(measured_total or prev.get("total_used") or 0),
            "basis": "measured" if measured_total else
                     (prev.get("basis") or "estimated"),
            "estimated": int(prev.get("estimated") or 0),
        }


def _conv_context_window(conv) -> int | None:
    """Window for whatever model the session is pointed at right now.

    Reads the picker override first — a model switch writes it
    immediately, while the session's persisted model only catches up on
    the next save — then the live runtime.
    """
    conv = conv or {}
    provider = (conv.get("provider_override")
                or conv.get("provider_name")
                or state._runtime_management._default_provider) or ""
    model = conv.get("model_override") or getattr(
        conv.get("runtime"), "model", None,
    )
    return state._resolve_context_window(provider, model)


def session_context_stats(
    session_id: str,
    head_id: str | None = None,
    *,
    estimated_total: int | None = None,
    window: int | None = None,
) -> dict:
    """The occupancy record for a session, recomputed against the graph now.

    ``/context`` calls this so the panel's headline total is byte-identical
    to the ring's. A measured reading stays measured only while the graph
    has not moved since; ``refresh_context_stats`` is what flips it back to
    ``estimated`` when it has.
    """
    conv = state._sessions.get(session_id) or {}
    prev = conv.get("_last_context_stats") or {}
    same_rev = int(prev.get("_context_rev") or 0) == int(
        conv.get("_context_rev") or 0
    )
    if prev.get("basis") == "measured" and (
        head_id is None or head_id == conv.get("head_id")
    ) and same_rev:
        out = {k: prev[k] for k in
                ("window", "total_used", "basis", "estimated",
                 "calibration", "_context_rev")
                if k in prev}
        if estimated_total is not None:
            out["estimated"] = int(estimated_total)
            if int(estimated_total) > 0:
                out["calibration"] = round(
                    int(out.get("total_used") or 0) / int(estimated_total), 4
                )
        if window:
            out["window"] = int(window)
        return out
    return state._build_context_occupancy(
        session_id,
        {**conv, "head_id": head_id or conv.get("head_id")},
        window=window or state._conv_context_window(conv),
        estimated_total=estimated_total,
    )


def refresh_context_stats(session_id: str, msg_id: str = "") -> None:
    """Re-estimate and broadcast after the graph moved under the session.

    Compaction landing, a model switch, a branch checkout or delete all
    change what the next request will carry while no request is in flight.
    Each one calls this, and the ring follows the graph immediately instead
    of waiting for the next reply.
    """
    conv = state._sessions.get(session_id)
    if conv is None:
        return
    conv["_context_rev"] = int(conv.get("_context_rev") or 0) + 1
    occupancy = state._build_context_occupancy(
        session_id, conv, window=state._conv_context_window(conv),
    )
    prev = conv.get("_last_context_stats") or {}
    stats = {
        **{k: v for k, v in prev.items() if k not in ("calibration", "breakdown")},
        "type": "context_stats",
        "session_id": session_id,
        "context_window": occupancy["window"] or prev.get("context_window"),
        **occupancy,
        "_context_rev": conv["_context_rev"],
    }
    breakdown = conv.get("_last_context_breakdown")
    if breakdown:
        stats["breakdown"] = breakdown
    conv["_last_context_stats"] = stats
    state._broadcast_chat_response(session_id, msg_id or "", stats)


def _broadcast_chat_response(session_id: str, msg_id: str, response: dict):
    """Broadcast a chat response to all WebSocket clients.

    Post-stop suppression: when this session has been cancelled
    (``mark_cancelled`` flag is up), drop any further chat_response
    envelopes for it. The in-flight worker thread can keep producing
    output for up to ~1.2s after stop while cooperative cancel
    reaches a hook point; without this gate, the UI would keep
    receiving streaming text / tree updates / partial tool results
    after the user explicitly asked for silence. The DB writes
    continue underneath (so the partial state is preserved if the
    user comes back), only the WS broadcast is gagged. The cancel
    flag is cleared by the cleanup path so subsequent turns can
    broadcast normally.
    """
    if state._is_cancelled(session_id):
        # Always let the explicit ``stopped`` status frame through —
        # that's how the UI flips its own state to stopped. Anything
        # else (stream_event / tree_update / result / status≠stopped)
        # is post-stop noise and gets dropped.
        if not (response.get("type") == "status"
                and response.get("stopped")):
            return
    response["session_id"] = session_id
    response["msg_id"] = msg_id
    response["timestamp"] = state.time.time()

    # No need to store in messages list — the DAG in SessionDB IS the storage
    msg = state.json.dumps({"type": "chat_response", "data": response}, default=str)
    state._broadcast(msg)
