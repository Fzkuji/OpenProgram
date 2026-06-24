"""Turn finalization — phase 6 bookkeeping.

Extracted from dispatcher/__init__.py (dispatcher-split step 4). After the
assistant message is persisted, ``finalize_turn`` runs the best-effort
turn-end bookkeeping, each sub-step independently guarded so a failure
never breaks the turn:

  6.   session head_id / last_prompt_tokens / model
  6.1  context-commit backfill (assistant output + tool sub-calls)
  6.4  feed real provider usage back into the context engine
  6.5  auto-title on the first text turn
  6.8  git-commit the turn (git-as-truth)
  6.9  project auto-commit (entity layer)
  6.95 evict old per-turn file-backup snapshots

Designed to take the resolved agent profile + context window as explicit
args (``agent_profile`` / ``ctx_win``), which the dispatcher resolves
under its test-patch seam and hands down — so this module never calls a
test-patched helper (_load_agent_profile / _resolve_model). It depends
only on ``titles._maybe_auto_title`` at module scope; everything heavy is
pulled in via in-function local imports. See
docs/design/runtime/dispatcher-split.md.
"""
from __future__ import annotations

from typing import Any, Optional

from openprogram.agent.dispatcher.titles import _maybe_auto_title


def finalize_turn(
    *,
    db,
    req: "Any",
    session: dict,
    usage: dict,
    assistant_msg: dict,
    assistant_msg_id: str,
    _project_baseline,
    agent_profile: Optional[dict],
    ctx_win: Optional[int],
    on_event,
) -> None:
    """Run the phase-6 turn-finalization bookkeeping. All side effects;
    returns nothing. Every sub-step is best-effort — the conversation
    persists regardless, and the next turn re-derives anything skipped.

    ``agent_profile`` / ``ctx_win`` are pre-resolved by the caller (under
    its test-patch seam) for the 6.4 usage-feedback step; when either is
    None that step is skipped, matching the old inline behavior where a
    failed resolve fell through the try/except.
    """
    # 6. Update session bookkeeping (head_id, token tracking, model).
    db.update_session(
        req.session_id,
        head_id=assistant_msg_id,
        last_prompt_tokens=int(usage.get("input_tokens") or 0),
        model=req.model_override or session.get("model"),
    )

    # 6.1. Backfill the latest context commit's placeholder item with the
    # final assistant output. The turn-start context commit saw the assistant
    # row as a placeholder (output=""), so the Context panel would
    # otherwise show "(empty)" for every assistant turn. We patch the
    # already-saved context commit in place — keeps the per-turn commit_id
    # stable and avoids ballooning the timeline with a duplicate.
    try:
        from openprogram.context.commit.store import (
            load_commit_for_head,
            save_commit,
        )
        from openprogram.context.commit.types import ContextItem
        _final_text = assistant_msg.get("content") or ""
        # Look up the commit on THIS branch (load_commit_for_head walks
        # the DAG ancestry from assistant_msg_id); the legacy
        # load_latest_commit returns whichever commit was saved last
        # session-wide, which is wrong when N agents are running
        # concurrently on different branches.
        _commit = load_commit_for_head(db, req.session_id, assistant_msg_id)
        if _commit is not None:
            _patched = False
            _assistant_idx = -1
            for _i, _item in enumerate(_commit.items):
                if _item.source_node_id == assistant_msg_id:
                    if _final_text and _item.rendered != _final_text:
                        _item.rendered = _final_text
                        # tokens were estimated from "" at turn-start;
                        # recompute against the final text.
                        _item.tokens = max(4, len(_final_text) // 4)
                    _assistant_idx = _i
                    _patched = True
                    break
            # Also splice in tool sub-calls written during the LLM loop
            # (called_by=assistant_msg_id). ensure_latest_commit ran at
            # turn-start before any tool node existed, so the context commit
            # has no tool items — the Context panel was showing a fake
            # "user → assistant" pair instead of the real "user →
            # assistant_with_tool_calls → tool_result(s)" sequence.
            if _assistant_idx >= 0:
                _all = db.get_messages(req.session_id) or []
                _subs = [m for m in _all if (m.get("caller") or "") == assistant_msg_id]
                _subs.sort(key=lambda x: x.get("seq") or 0)
                _existing_ids = {it.source_node_id for it in _commit.items}
                _to_insert: list[ContextItem] = []
                for _sub in _subs:
                    _sid = _sub.get("id")
                    if not _sid or _sid in _existing_ids:
                        continue
                    _content = _sub.get("content") or ""
                    if not isinstance(_content, str):
                        import json as _json
                        try:
                            _content = _json.dumps(_content, ensure_ascii=False, default=str)
                        except Exception:
                            _content = str(_content)
                    _to_insert.append(ContextItem(
                        source_node_id=_sid,
                        role="tool",
                        state="full",
                        locked=False,
                        rendered=_content,
                        tokens=max(4, len(_content) // 4),
                        state_set_at=_commit.id,
                        reason="new",
                    ))
                if _to_insert:
                    _commit.items = (
                        _commit.items[: _assistant_idx + 1]
                        + _to_insert
                        + _commit.items[_assistant_idx + 1 :]
                    )
                    _commit.total_tokens = sum(
                        i.tokens for i in _commit.items if i.state != "summarized"
                    )
                    _patched = True
            if _patched:
                save_commit(db, _commit)
    except Exception:
        # ContextCommit backfill is best-effort: the conversation persists
        # regardless, and the next turn will rebuild the chain.
        pass

    # 6.4. Feed real provider usage back into the context engine so
    # subsequent prepare() calls budget against true numbers instead of
    # our estimate. The engine is re-resolved here (cheap registry
    # lookup) because _run_loop_blocking's local _ctx_engine is out of
    # scope — and we pass a lightweight prep-equivalent so the engine can
    # still decide whether to emit a recommendation event. ``agent_profile``
    # / ``ctx_win`` are pre-resolved by the caller under its test-patch
    # seam (so this module never calls _load_agent_profile / _resolve_model
    # directly); when either is None — resolution failed at the call site —
    # we skip, matching the old inline try/except fall-through.
    try:
        if agent_profile is not None and ctx_win is not None:
            from openprogram.context import resolve_engine_for as _resolve_eng
            from openprogram.context.types import (
                BudgetAllocation as _BA, TurnPrep as _TurnPrep,
            )
            _engine = _resolve_eng(agent_profile)
            _shim_prep = _TurnPrep(
                system_prompt="",
                budget=_BA(context_window=ctx_win),
            )
            _engine.after_turn(
                req.session_id,
                usage=usage,
                prep=_shim_prep,
                on_event=on_event,
            )
    except Exception:
        pass

    # 6.5. Auto-title: background LLM generation at turn thresholds.
    _assistant_text = assistant_msg.get("content") or ""
    _maybe_auto_title(db, req.session_id, session, req.user_text, _assistant_text)

    # 6.6. Compaction signal: when context is approaching the model's
    # window, surface a "compaction_recommended" event so the UI can
    # offer the user a /compact action. We don't auto-compact mid-
    # turn — that would block the response. The actual compaction
    # call is exposed as ``trigger_compaction(session_id)`` for clients
    # to invoke explicitly.
    #
    # Context-window resolution via context.tokens — reads
    # ``model.context_window`` (the truth), not ``model.max_tokens``
    # (which is the OUTPUT cap, typically 10-30% of the real window
    # and would fire compaction at ~10-30% utilization).
    # (Compaction-recommended emission moved into ctx_engine.after_turn,
    # which uses provider-reported usage instead of re-estimating the
    # whole branch here.)

    # 6.8. Git commit the turn — the session's git repo is the source
    # of truth (git-as-truth). Every successful turn becomes one
    # commit on the session's branch, picking up new history files +
    # rewritten context/messages.json + context/commit.json + meta.json
    # in a single diff. Best-effort: if git fails the data is still
    # on disk, next turn's commit will sweep it up.
    try:
        from openprogram.store import default_store
        _store = default_store()
        if _store is db or hasattr(db, "commit_turn"):
            _msg = (req.user_text or "").strip().splitlines()[0][:60] or "turn"
            db.commit_turn(req.session_id, f"turn: {_msg}")
    except Exception:
        pass

    # 6.9. Project auto-commit (entity layer, half 2): if this session is
    # bound to a real project directory and the agent edited files there,
    # commit them to the project's own git as an attributable agent
    # commit — so the user gets a `git log` / `git revert`-able record of
    # what changed. Refuses (and warns via on_event) when the user has
    # pre-existing uncommitted work, per Strategy A. Off unless opted in
    # (config ``project_auto_commit`` / env). Best-effort.
    try:
        from openprogram.store import project_commit as _pc
        _commit_sha = _pc.commit_turn_changes(
            req.session_id, req.user_text or "",
            _project_baseline, on_event=on_event,
        )
        # Record turn → project-commit sha on the assistant node so a
        # later revert_turn knows which git commit this turn produced
        # (and in which repo), enabling a git-aware undo on top of the
        # file-snapshot restore. Only a real sha is stamped — None /
        # SKIPPED_DIRTY / autoinit-blocked leave no pointer.
        if isinstance(_commit_sha, str) and len(_commit_sha) >= 7:
            try:
                _proj = _pc._project_for(req.session_id)
                _store2 = default_store()
                _pair = _store2._open(req.session_id)
                if _proj is not None and _pair is not None:
                    _g, _idx = _pair
                    _n = _idx.nodes_by_id.get(assistant_msg_id)
                    if _n is not None:
                        _n.metadata = {
                            **(_n.metadata or {}),
                            "project_commit": {
                                "repo": _proj.path, "sha": _commit_sha,
                            },
                        }
                        # Per-node metadata lives in the node's history
                        # file (not meta.json) — rewrite it so the stamp
                        # survives a worker restart, mirroring _revert.py.
                        import json as _json
                        _rl = (_n.role or "x")[0]
                        _fp = _g.path / "history" / f"{_n.seq:04d}-{_rl}-{_n.id}.json"
                        if _fp.exists():
                            _tmp = _fp.with_suffix(".json.tmp")
                            _tmp.write_text(
                                _json.dumps(_n.to_dict(), ensure_ascii=False, default=str),
                                encoding="utf-8",
                            )
                            _tmp.replace(_fp)
            except Exception:
                pass
    except Exception:
        pass

    # 6.95. Evict old per-turn file-backup snapshots beyond the soft cap.
    # The snapshots (checkpoints/<turn>/) are full copies written before
    # each edit; without this they grow unbounded. Cap is per-session and
    # generous (gc.MAX_TURNS); we run it every turn-end since it's a cheap
    # mtime sort + rmtree of only the excess. Best-effort.
    try:
        from openprogram.store import default_store
        from openprogram.store.snapshot.checkpoint import gc_evict_old
        _sdir = default_store()._session_dir(req.session_id)
        gc_evict_old(_sdir)
    except Exception:
        pass
