"""Server context stats."""
from __future__ import annotations
from .. import server as state


def _append_msg(conv: dict, msg: dict, *, strict: bool = False) -> None:
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

    ``strict`` is for a user message that starts a turn: a store failure
    is raised after the mirror is put back, so the caller can refuse the
    turn instead of running one whose message is gone on reload.
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
        from openprogram.runtime.context.session_db import default_db
        session = default_db().get_session(conv["id"]) or {}
        msg["predecessor"] = session.get("head_id") or "ROOT"

    if strict:
        _prior_messages = list(conv.get("messages") or [])
        _prior_head = conv.get("head_id")
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
        from openprogram.runtime.context.session_db import default_db
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
                "tools_override", "thinking_effort",
                "permission_mode", "sandbox_enabled",
            ):
                v = conv.get(fld)
                if v is not None:
                    create_kwargs[fld] = v
            db.create_session(cid, msg.get("agent_id") or state._default_agent_id(), **create_kwargs)
        # Ensure ROOT node + user caller=ROOT for session DAG.
        if msg.get("role") == "user":
            try:
                from openprogram.state.store.session.nodes import Call as _C, ROLE_USER as _RU
                from openprogram.state.store import SessionNodeWriter as _GS
                _ROOT_ID = "ROOT"
                if not db.message_exists(cid, _ROOT_ID):
                    # The root marker is no branch's message: a conversation
                    # whose earlier messages were written without it keeps
                    # its branch where it is, and this message continues it.
                    _GS(db, cid, advance_head=False).append(_C(
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
        if strict:
            conv["messages"] = _prior_messages
            conv["head_id"] = _prior_head
            state._invalidate_messages(cid)
            raise
    state._invalidate_messages(cid)


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
                "request_input_estimate": usage.get("request_input_estimate", 0),
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

    # The window is the one of the model the request ran on: the turn
    # hands its resolved Model's window (``context_window``) and
    # ``provider/model`` with the usage. A caller without them falls back
    # to the conversation's current model. Unknown stays None: the ring
    # and panel then show tokens without a limit.
    turn_model = getattr(chat_runtime, "model", None) if chat_runtime else None
    if turn_model:
        from openprogram.models.providers.catalog.models import split_model_ref
        provider_name, chat_model = split_model_ref(turn_model, _conv_provider(conv))
        context_window = (int(getattr(chat_runtime, "context_window", None) or 0)
                          or state._resolve_context_window(provider_name, turn_model))
    else:
        provider_name, chat_model = state._conv_model(conv)
        context_window = state._conv_context_window(conv)
    provider_name = provider_name or ""

    # The request that just finished measured the real prompt size, so the
    # occupancy record switches to the ``measured`` basis and stores the
    # measured/estimated ratio as calibration.
    measured = int((conv.get("_chat_usage") or {}).get("context_tokens") or 0)
    occupancy = state._build_context_occupancy(
        session_id, conv, measured_total=measured, window=context_window,
        request_input_estimate=int((conv.get("_chat_usage") or {}).get("request_input_estimate") or 0),
        running_turn=bool(getattr(chat_runtime, "running_turn", False)),
    )

    stats = {
        "type": "context_stats",
        "context_usage_version": 2,
        "chat": conv.get("_chat_usage", dict(_zero)),
        "exec": exec_stats,
        "provider": provider_name,
        "model": chat_model,
        "context_window": occupancy["window"],
        **occupancy,
        "_context_rev": int(conv.get("_context_rev") or 0),
    }
    breakdown = conv.get("_last_context_breakdown")
    if breakdown:
        stats["breakdown"] = breakdown
    conv["_last_context_stats"] = stats
    state._broadcast_chat_response(session_id, msg_id, stats)


def _resolve_context_window(provider_name, model) -> int | None:
    """Real context window for a model reference from the model registry.

    The runtime object carries no window of its own, so the registry is the
    single place both the ring and the ``/context`` panel look it up.
    ``provider/model`` and ``provider:model`` both name their provider
    (``catalog.models.split_model_ref``); a bare id uses ``provider_name``.
    ``None`` when the model does not resolve.
    """
    from openprogram.runtime.context.token_accounting.session_stats import model_ref_window
    return model_ref_window(str(model) if model else None, provider_name or None)


def _build_context_occupancy(session_id, conv, *, measured_total=None,
                             window=None, estimated_total=None,
                             request_input_estimate=None, calibration=None,
                             running_turn=False) -> dict:
    """``{window, total_used, basis, estimated[, calibration]}`` for a session.

    Falls back to the last broadcast record when the estimate cannot be
    computed (a brand-new session with no branch yet, a DB read failure),
    so a transient error never blanks the ring.
    """
    from openprogram.runtime.context.token_accounting import session_stats as _cs

    try:
        occupancy = _cs.build_stats(
            session_id,
            head_id=(conv or {}).get("head_id"),
            measured_total=measured_total,
            window=window,
            estimated_total=estimated_total,
            request_input_estimate=request_input_estimate,
            calibration=calibration,
        )
        snapshot = occupancy.pop("_breakdown", None)
        if snapshot is not None and conv is not None:
            occ = {
                key: occupancy[key]
                for key in ("window", "total_used", "basis", "estimated",
                            "calibration", "calibration_source")
                if key in occupancy
            }
            finalized = _cs.finalize_breakdown(
                snapshot, occ, running_turn=running_turn,
                request_estimate=request_input_estimate,
            )
            finalized["head_id"] = conv.get("head_id")
            finalized["_context_rev"] = int(conv.get("_context_rev") or 0)
            conv["_last_context_breakdown"] = finalized
        return occupancy
    except Exception:
        prev = (conv or {}).get("_last_context_stats") or {}
        if prev.get("context_usage_version") != 2:
            prev = {}
        return {
            # The window is resolved by the caller, never carried over.
            "window": int(window or 0) or None,
            "total_used": int(measured_total or prev.get("total_used") or 0),
            "basis": "measured" if measured_total else
                     (prev.get("basis") or "estimated"),
            "estimated": int(prev.get("estimated") or 0),
        }


def with_window(record: dict, window: int | None) -> dict:
    """``record`` (stats or a breakdown snapshot) against ``window``.

    A saved record keeps the window it was computed with; this replaces
    it with the one resolved now and recomputes what follows from it.
    """
    window = int(window or 0) or None
    out = {**record, "window": window, "context_window": window}
    if "free_space" in record or "input_used_pct" in record:
        total = int(record.get("total_used") or record.get("input_used") or 0)
        out["free_space"] = max(0, window - total) if window else None
        out["input_used_pct"] = round(total / window, 4) if window else None
    return out


def _conv_provider(conv) -> str:
    """The provider a bare model id on this conversation belongs to."""
    conv = conv or {}
    return (conv.get("provider_override")
            or conv.get("provider_name")
            or state.runtime_management._default_provider) or ""


def _branch_at_head(conv, head_id) -> dict | None:
    """Stored settings of the recorded branch whose tip is ``head_id``, when
    that branch is not the selected one.

    ``None`` for the selected head, no head named, or a head no recorded
    branch ends at: the conversation's own model then answers.
    """
    conv = conv or {}
    if not head_id or not conv.get("id") or head_id == conv.get("head_id"):
        return None
    from openprogram.state.store.session.branch_config import (
        BranchConfigConflict, config_for_branch,
    )
    try:
        from openprogram.runtime.context.session_db import default_db
        row = default_db().get_session(conv["id"]) or {}
    except Exception:
        return None
    active = row.get("active_branch_id")
    for branch_id, ref in (row.get("branch_refs") or {}).items():
        if (not isinstance(ref, dict) or branch_id == active
                or ref.get("head_id") != head_id or ref.get("status") == "aborted"):
            continue
        try:
            return config_for_branch(row, branch_id)
        except BranchConfigConflict:
            continue
    return None


def _branch_model_ref(conv, branch: dict) -> tuple[str | None, str]:
    """``_conv_model_ref`` for a branch that is not selected: its own model
    (``branch_config``), else its Agent's as it is now. The selected
    branch's pin, turn and runtime say nothing about it."""
    provider = (branch.get("provider_override")
                or state.runtime_management._default_provider) or ""
    if branch.get("model_override"):
        return str(branch["model_override"]), provider
    try:
        from openprogram.runtime.conversation.session_settings.session_model import (
            new_session_chat_model, override_string,
        )
        agent_ref = override_string(*new_session_chat_model(
            branch.get("agent_id") or (conv or {}).get("agent_id")))
        if agent_ref:
            return agent_ref, provider
    except Exception:
        state._log("context window: agent model unavailable")
    return None, provider


def _conv_model_ref(conv, head_id=None) -> tuple[str | None, str]:
    """``(model reference, provider for a bare id)`` the session runs on now.

    ``head_id`` names the branch tip a reader looks at. A recorded branch
    that is not the selected one answers with its own settings
    (``_branch_model_ref``), so its window is its model's, not the
    selected branch's.

    Otherwise the first source that names a model decides, in the order the
    next request would pick it (``session_model.ensure_session_chat_model``):

    1. the picker pin (``model_override``) — a model switch writes it
       immediately, while the persisted model catches up on the next save;
    2. the model the turn last resolved (``_turn_model_ref``, set from the
       turn's own usage report, dropped when another branch is selected);
    3. the live runtime's model;
    4. the session row's pin;
    5. for a chat that follows its Agent, the Agent's model as it is now;
    6. the model the session's last turn ran (row ``model``,
       ``provider/model``).
    """
    conv = conv or {}
    other = _branch_at_head(conv, head_id)
    if other is not None:
        return _branch_model_ref(conv, other)
    provider = _conv_provider(conv)
    for model in (conv.get("model_override"), conv.get("_turn_model_ref"),
                  getattr(conv.get("runtime"), "model", None)):
        if model:
            return str(model), provider
    row: dict = {}
    if conv.get("id"):
        try:
            from openprogram.runtime.context.session_db import default_db
            row = default_db().get_session(conv["id"]) or {}
        except Exception:
            row = {}
    if row.get("model_override"):
        return str(row["model_override"]), (row.get("provider_override") or provider)
    try:
        from openprogram.runtime.conversation.session_settings.session_model import (
            new_session_chat_model, override_string,
        )
        agent_id = conv.get("agent_id") or row.get("agent_id")
        agent_ref = override_string(*new_session_chat_model(agent_id))
        if agent_ref:
            return agent_ref, provider
    except Exception:
        state._log("context window: agent model unavailable")
    if row.get("model"):
        return str(row["model"]), (row.get("provider_name") or provider)
    return None, provider


def _conv_model(conv, head_id=None) -> tuple[str, str | None]:
    """``(provider, bare model id)`` of the session's current model, or of
    the branch ending at ``head_id``."""
    from openprogram.models.providers.catalog.models import split_model_ref
    model, provider = _conv_model_ref(conv, head_id)
    found_provider, model_id = split_model_ref(model, provider)
    return found_provider or provider or "", model_id


def _conv_context_window(conv, head_id=None) -> int | None:
    """Window for whatever model the session is pointed at right now, or
    the branch ending at ``head_id`` is (``_conv_model_ref``); ``None`` when
    that model does not resolve."""
    model, provider = _conv_model_ref(conv, head_id)
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

    The window is resolved now from the model of the branch ``head_id``
    ends (the session's selected one without it), never taken from the
    stored record: a record saved while the lookup failed carried the
    128k default, and that number must not outlive the fix.
    """
    conv = state._sessions.get(session_id) or {}
    window = int(window or 0) or state._conv_context_window(conv, head_id)
    prev = conv.get("_last_context_stats") or {}
    same_rev = int(prev.get("_context_rev") or 0) == int(
        conv.get("_context_rev") or 0
    )
    if prev.get("context_usage_version") == 2 and prev.get("basis") in ("measured", "estimated") and (
        head_id is None or head_id == conv.get("head_id")
    ) and same_rev:
        out = {k: prev[k] for k in
                ("window", "total_used", "basis", "estimated",
                 "calibration", "calibration_source", "_context_rev")
                if k in prev}
        if estimated_total is not None:
            out["estimated"] = int(estimated_total)
            if int(estimated_total) > 0 and out.get("calibration_source") != "request":
                out["calibration"] = round(
                    int(out.get("total_used") or 0) / int(estimated_total), 4
                )
        out["window"] = int(window or 0) or None
        return out
    return state._build_context_occupancy(
        session_id,
        {**conv, "head_id": head_id or conv.get("head_id")},
        window=window,
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
    # Older canonical WebSocket turns persisted provider usage but never
    # broadcast context_stats. Recover that measurement on session load when
    # it is newer than the saved badge and no later branch edit occurred.
    measured_total = None
    try:
        import json
        from openprogram.runtime.context.session_db import default_db
        session = default_db().get_session(session_id) or {}
        usage = session.get("_usage") or {}
        if isinstance(usage, str):
            usage = json.loads(usage)
        previous = conv.get("_last_context_stats") or {}
        usage_at = float(usage.get("last_updated_at") or 0)
        previous_at = float(previous.get("timestamp") or 0)
        session_at = float(session.get("updated_at") or 0)
        if (usage.get("context_usage_version") == 2
                and usage_at > previous_at and session_at <= usage_at
                and int(usage.get("last_prompt_tokens") or 0) > 0):
            measured_total = (int(usage["last_prompt_tokens"])
                              + int(usage.get("last_cache_read_tokens") or 0))
    except (ValueError, TypeError, AttributeError, OSError):
        pass
    prev = conv.get("_last_context_stats") or {}
    current_provider, current_model = state._conv_model(conv)
    reusable_calibration = (
        prev.get("calibration")
        if (not measured_total and prev.get("context_usage_version") == 2
            and prev.get("calibration_source") == "request"
            and prev.get("provider") == current_provider
            and prev.get("model") == current_model)
        else None
    )
    occupancy = state._build_context_occupancy(
        session_id, conv, measured_total=measured_total,
        window=state._conv_context_window(conv),
        calibration=reusable_calibration,
    )
    stats = {
        **{k: v for k, v in prev.items()
           if k not in ("calibration", "calibration_source", "breakdown")},
        "type": "context_stats",
        "context_usage_version": 2,
        "session_id": session_id,
        "provider": current_provider,
        "model": current_model,
        # Resolved now; the stored record's window is never carried over.
        "context_window": occupancy["window"],
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
    if response.get("type") == "tree_update" and isinstance(response.get("tree"), dict):
        from openprogram.interfaces.server.transcript import publisher

        publisher.notice_tree(session_id, msg_id, response["tree"], response.get("function"))
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
