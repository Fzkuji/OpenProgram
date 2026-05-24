"""Merge turn (task F).

Consolidates N sub-agent branches into one parent reply.

Pipeline:
  1. Resolve each sub-branch's final text (stored on the parent's DAG
     as a ``function="sub_agent"`` tool_result row by
     ``run_sub_agent_turn``).
  2. Build a merge prompt with each summary as a ``<branch name="..">
     ...</branch>`` block, plus the user-supplied ``message``.
  3. Run the parent's dispatcher (``process_user_turn``) on that
     prompt — uses the parent session's runtime, so any tools / model
     overrides the user set on the parent thread are honored.
  4. After completion, save a multi-parent ContextCommit pointing at
     the previous parent commit + every sub-branch's git tip SHA. The
     ``parent_ids`` field already supports lists (see eb2b06a).

MVP scope — what's NOT done here, by design:
  * No git workdir merge. Sub-agents wrote into their own worktrees;
    those files survive on the branch refs but aren't copy-merged
    into the parent's ``workdir/``. The merge turn synthesizes a
    textual answer from each branch's tool_result; if the user wants
    to pull the actual file deltas they can ``git checkout`` the
    sub-branch by hand.
  * No conflict resolution. Without a workdir merge, conflicts can't
    arise on the parent's branch.
  * Sub-branch context commits aren't re-read. The on-disk files
    inside ``_worktrees/<branch>/context/commits/`` go away when
    ``release_sub_agent`` removes the worktree, and the git objects
    aren't trivially reachable as ContextCommits via the
    SessionStore API. We use the tool_result summary instead — it
    carries the agent's final text and the branch name. A future
    enhancement could `git show <branch>:context/commits/<id>.json`
    to recover the per-branch commit.

The merge turn is one of the rare places where parent_ids has more
than one entry, which is exactly what task D (eb2b06a) was preparing
for.
"""
from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MergeTurnResult:
    parent_node_id: Optional[str] = None         # assistant id of the merge reply
    commit_id: Optional[str] = None              # new multi-parent ContextCommit id
    parent_ids: list[str] = field(default_factory=list)
    final_text: str = ""
    failed: bool = False
    error: Optional[str] = None


def _find_sub_agent_row(parent_store, parent_session_id: str, branch: str) -> Optional[dict]:
    """Walk parent's history and find the most recent row that ran
    the named sub-branch.

    The sub_agent metadata can live either at the top level of the
    message dict (assistant rows, where ``_msg_adapter`` hoists
    decoded ``extra`` keys into the dict) or under ``extra.sub_agent``
    (tool rows). Check both shapes so the resolver doesn't depend on
    which role sub_agent_run currently writes.
    """
    msgs = parent_store.get_messages(parent_session_id) or []
    matches: list[dict] = []
    for m in msgs:
        if m.get("function") != "sub_agent":
            continue
        sub = m.get("sub_agent")
        if not isinstance(sub, dict):
            extra = m.get("extra")
            if isinstance(extra, str):
                try:
                    extra = json.loads(extra)
                except (json.JSONDecodeError, TypeError):
                    extra = {}
            if isinstance(extra, dict):
                sub = extra.get("sub_agent")
        if not isinstance(sub, dict):
            continue
        if sub.get("branch") == branch:
            matches.append(m)
    if not matches:
        return None
    matches.sort(key=lambda r: r.get("timestamp") or 0.0)
    return matches[-1]


def _branch_tip_sha(parent_git, branch: str) -> Optional[str]:
    """`git rev-parse refs/heads/<branch>`. None if the ref is missing
    (caller probably passed a stale name)."""
    try:
        out = parent_git._run(
            "rev-parse", f"refs/heads/{branch}", check=False,
        ).strip()
        return out or None
    except Exception:
        return None


def _build_prompt(branches: list[dict], user_message: str) -> str:
    parts = [
        "Several sub-agents ran on parallel branches off this conversation.",
        "Their individual final replies are below — please consolidate them",
        "into a single coherent answer for the user.",
        "",
    ]
    for b in branches:
        name = b.get("name") or b.get("branch") or "unknown"
        text = (b.get("text") or "").strip() or "(no output)"
        parts.append(f'<branch name="{name}">')
        parts.append(text)
        parts.append("</branch>")
        parts.append("")
    if user_message and user_message.strip():
        parts.append("User's merge request:")
        parts.append(user_message.strip())
    return "\n".join(parts)


def _commit_id() -> str:
    return f"commit_{secrets.token_hex(8)}"


def process_merge_turn(
    parent_session_id: str,
    sub_branches: list[str],
    message: str,
    agent_id: str,
) -> MergeTurnResult:
    """Run a merge turn that consolidates several sub-agent branches.

    ``sub_branches`` is the list of branch names (each one minted by
    ``run_sub_agent_turn`` and recorded in the parent's DAG as a
    ``function="sub_agent"`` tool_result row). Order is preserved in
    the prompt.

    Returns ``MergeTurnResult`` with the new parent assistant id, the
    multi-parent ContextCommit id, and the ``parent_ids`` actually
    written (sub-branch SHAs that resolved).
    """
    from openprogram.agent.session_db import default_db
    from openprogram.context.commit.store import save_commit, load_commit_for_head
    from openprogram.context.commit.types import (
        ContextCommit, CURRENT_RULES_VERSION,
    )

    parent_store = default_db()
    parent_pair = parent_store._open(parent_session_id)
    if parent_pair is None:
        return MergeTurnResult(
            failed=True,
            error=f"parent session {parent_session_id!r} not found",
        )
    parent_git, parent_idx = parent_pair
    parent_git._ensure_init()

    # 1. Resolve each branch -> {name, text, sha}.
    bundled: list[dict] = []
    missing: list[str] = []
    for br in sub_branches:
        row = _find_sub_agent_row(parent_store, parent_session_id, br)
        if row is None:
            missing.append(br)
            continue
        bundled.append({
            "name": br,
            "text": row.get("content") or "",
            "sha": _branch_tip_sha(parent_git, br),
            "source_node_id": row.get("id"),
        })

    if not bundled:
        return MergeTurnResult(
            failed=True,
            error=(
                "no sub-agent rows resolved for any of "
                f"{sub_branches!r}; missing={missing!r}"
            ),
        )

    # 2. Build prompt + run a normal parent turn.
    merge_prompt = _build_prompt(bundled, message)
    from openprogram.agent.dispatcher import TurnRequest, process_user_turn

    # Parent's history contains the synthetic sub_agent tool_result
    # rows whose tool_use stubs OpenAI / Codex can't reconcile when
    # building a fresh conversation. Run merge with an empty history;
    # everything the LLM needs is in the prompt we just built.
    req = TurnRequest(
        session_id=parent_session_id,
        user_text=merge_prompt,
        agent_id=agent_id,
        source="merge_turn",
        history_override=[],
    )
    try:
        turn_result = process_user_turn(req)
    except Exception as e:  # noqa: BLE001
        return MergeTurnResult(
            failed=True,
            error=f"{type(e).__name__}: {e}",
        )

    final_text = turn_result.final_text or ""
    parent_node_id = turn_result.assistant_msg_id
    if turn_result.failed:
        return MergeTurnResult(
            parent_node_id=parent_node_id,
            final_text=final_text,
            failed=True,
            error=turn_result.error,
        )

    # 3. Write a multi-parent ContextCommit. parent_ids carries the
    #    sub-branch tip SHAs (one entry per resolved branch) plus the
    #    previous parent-branch ContextCommit id when available.
    parents: list[str] = []
    prev = load_commit_for_head(
        parent_store, parent_session_id, parent_node_id,
    )
    if prev is not None and prev.id:
        parents.append(prev.id)
    for b in bundled:
        if b.get("sha"):
            parents.append(b["sha"])

    commit = ContextCommit(
        id=_commit_id(),
        session_id=parent_session_id,
        parent_id=parents[0] if parents else None,
        parent_ids=parents,
        created_at=time.time(),
        head_node_id=parent_node_id,
        rules_version=CURRENT_RULES_VERSION,
        total_tokens=len(final_text) // 4,
        items=[],
        summary=(
            "merge_turn: "
            + ", ".join(b["name"] for b in bundled)
        ),
    )
    try:
        save_commit(parent_store, commit)
    except Exception as e:  # noqa: BLE001
        return MergeTurnResult(
            parent_node_id=parent_node_id,
            commit_id=None,
            parent_ids=parents,
            final_text=final_text,
            failed=True,
            error=f"save_commit failed: {type(e).__name__}: {e}",
        )

    # Bump the parent session's HEAD-marker commit so a future
    # load_commit_for_head off this branch tip finds the merge commit.
    # (commit_turn commits whatever is staged in the parent's workdir,
    # which after process_user_turn includes the new context/commits/<id>.json.)
    try:
        parent_store.commit_turn(
            parent_session_id,
            f"merge_turn: {' + '.join(b['name'] for b in bundled)}",
        )
    except Exception:
        pass

    return MergeTurnResult(
        parent_node_id=parent_node_id,
        commit_id=commit.id,
        parent_ids=parents,
        final_text=final_text,
        failed=False,
    )
