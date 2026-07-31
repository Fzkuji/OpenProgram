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

from openprogram.agent.dispatcher.titles import (
    _maybe_auto_title,
    maybe_auto_name_branch,
)


def _shadow_root_for(session_id: str, paths: list[str]) -> Optional[str]:
    """Which directory to treat as the project root for this turn.

    Order: the bound project (the old behavior), else the turn's actual
    cwd the same way the dispatcher resolves it (active worktree, then
    ``project_workdir_for``). Whatever we pick must actually CONTAIN the
    changed paths — ``ShadowGitStore.commit_turn`` silently skips any
    path outside the root — so a root that doesn't is replaced by the
    common ancestor of the changed paths.
    """
    from pathlib import Path

    candidates: list[str] = []
    try:
        from openprogram.store.project import project_commit as _pc
        project = _pc._project_for(session_id)
        if project is not None and project.path:
            candidates.append(project.path)
    except Exception:
        pass
    try:
        from openprogram.worktree.context import current_worktree_path
        wt = current_worktree_path()
        if wt:
            candidates.append(wt)
    except Exception:
        pass
    try:
        from openprogram.agent.internals._workdir import project_workdir_for
        wd = project_workdir_for(session_id)
        if wd is not None:
            candidates.append(str(wd))
    except Exception:
        pass

    resolved = [Path(p).resolve() for p in paths]
    for cand in candidates:
        root = Path(cand).expanduser().resolve()
        if all(p == root or root in p.parents for p in resolved):
            return str(root)

    # ponytail: no candidate contains the edits — commit under their
    # common ancestor so the diff exists at all.
    import os
    common = os.path.commonpath([str(p) for p in resolved])
    root = Path(common)
    if root.is_file():
        root = root.parent
    return str(root) if root.is_dir() else None


def commit_turn_to_shadow_git(
    session_id: str, assistant_msg_id: str, user_text: str = "",
) -> Optional[str]:
    """Mirror this turn's file changes into the project's shadow repo.

    The shadow repo (``~/.openprogram/shadow-git/<hash>/``) is entirely
    separate from the user's ``.git``, so per-turn diffs work even when
    the project is not a git repo at all. The checkpoint manifest is the
    changed-file list — every write tool checkpoints before editing, so
    it is exactly the set of paths this turn touched.

    Stamps ``metadata['shadow_git'] = {repo, before, after}`` on the
    assistant node: ``before`` is the shadow HEAD prior to this turn,
    ``after`` the new commit. That pair is all ``turn_file_diff`` needs
    to render a unified diff for the turn.

    The project root is resolved by :func:`_shadow_root_for` — a bound
    project when there is one, else the turn's actual working root. Most
    real sessions are in no project's ``session_ids``, so requiring one
    meant no stamp was ever written and every diff fell back to the
    approximate difflib path.

    Best-effort: returns the new sha, or None when nothing changed /
    anything fails. Never raises — a shadow bookkeeping glitch must not
    break the conversation.
    """
    try:
        from openprogram.store import default_store
        from openprogram.store.shadow_git import ShadowGitStore
        from openprogram.store.snapshot.checkpoint import CheckpointStore

        store = default_store()
        paths = CheckpointStore(
            store._session_dir(session_id)).list_backed_paths(assistant_msg_id)
        if not paths:
            return None

        root = _shadow_root_for(session_id, list(paths))
        if root is None:
            return None

        shadow = ShadowGitStore(root)
        before = shadow.head_sha()
        first_line = (user_text or "").strip().splitlines()
        after = shadow.commit_turn(
            assistant_msg_id, list(paths),
            (first_line[0][:60] if first_line else "") or "turn",
        )
        if not after:
            return None

        pair = store._open(session_id)
        if pair is None:
            return after
        git, idx = pair
        node = idx.nodes_by_id.get(assistant_msg_id)
        if node is None:
            return after
        node.metadata = {
            **(node.metadata or {}),
            "shadow_git": {
                # The store's own resolved root, not the raw candidate:
                # commit_turn relativized under it, so turn_file_diff
                # must relativize under the identical path or the rel
                # names disagree and every diff comes back empty.
                "repo": str(shadow.project_path),
                "before": before, "after": after,
            },
        }
        # Per-node metadata lives in the node's history file (not
        # meta.json) — rewrite it so the stamp survives a worker
        # restart, mirroring the project_commit stamp above.
        import json as _json
        role = (node.role or "x")[0]
        fp = git.path / "history" / f"{node.seq:04d}-{role}-{node.id}.json"
        if fp.exists():
            tmp = fp.with_suffix(".json.tmp")
            tmp.write_text(
                _json.dumps(node.to_dict(), ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            tmp.replace(fp)
        return after
    except Exception:
        import logging
        logging.getLogger(__name__).debug(
            "shadow-git turn commit skipped", exc_info=True)
        return None


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
            # (caller=assistant_msg_id). ensure_latest_commit ran at
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

    # 6.5b. Auto-name the current branch (DAG badge). Bumps the branch's
    # own turn counter and, at thresholds, spawns a background LLM rename
    # unless the user locked the name. head_id was just set to
    # assistant_msg_id above. Best-effort. See branch-naming.md.
    try:
        maybe_auto_name_branch(db, req.session_id, assistant_msg_id)
    except Exception:
        pass

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

    # 6.93. Shadow-git commit — see commit_turn_to_shadow_git.
    commit_turn_to_shadow_git(
        req.session_id, assistant_msg_id, req.user_text or "")

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
