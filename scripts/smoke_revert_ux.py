"""Smoke test for the revert UX — snapshot + git undo together.

Throwaway profile; never touches real state. Covers:

  A. ProjectGit.revert_agent_commit decision logic, directly:
     * agent commit is HEAD, clean, unpushed  → "reset" (clean undo)
     * a user commit sits ON TOP of it        → "revert" (additive),
                                                 user commit preserved
     * absent sha                             → "absent"
  B. revert_turn end-to-end: a session whose turn produced a project
     commit → revert_turn returns git_undo + leaves the repo consistent.
"""
from __future__ import annotations

import os
import sys
import tempfile
import shutil
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

os.environ["OPENPROGRAM_PROFILE"] = f"revuxtest{os.getpid()}"

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def _rmtree_retry(p: Path, tries: int = 5) -> None:
    for i in range(tries):
        shutil.rmtree(p, ignore_errors=True)
        if not p.exists():
            return
        time.sleep(0.15 * (i + 1))


def main() -> int:
    from openprogram.paths import get_state_dir
    state = get_state_dir()
    assert "revuxtest" in state.name, f"refusing to run against {state}"
    if state.exists():
        _rmtree_retry(state)
    print(f"# state dir = {state}")

    from openprogram.store.project.project_store import ProjectGit

    # ── A1. clean reset path ──────────────────────────────────────
    d1 = Path(tempfile.mkdtemp(prefix="op_revux1_"))
    try:
        pg = ProjectGit(d1)
        pg._run("init", "--quiet", "--initial-branch=main")
        pg._run("config", "user.email", "u@u")
        pg._run("config", "user.name", "User")
        (d1 / "base.txt").write_text("base\n", encoding="utf-8")
        pg._run("add", "-A"); pg._run("commit", "-m", "base", "--quiet")
        # agent commit on top
        (d1 / "feature.py").write_text("agent feature\n", encoding="utf-8")
        sha = pg.commit_agent_changes("[agent] add feature", baseline=set())
        check("A1: agent commit landed", isinstance(sha, str), str(sha))
        out = pg.revert_agent_commit(sha)
        check("A1: HEAD+clean+unpushed → reset",
              out.get("action") == "reset" and out.get("ok"),
              str(out))
        check("A1: feature.py gone after reset",
              not (d1 / "feature.py").exists() and (d1 / "base.txt").exists(),
              "files rolled back")
        # the agent commit is no longer in history
        log = pg._run("log", "--pretty=%s", check=False)
        check("A1: agent commit removed from history",
              "add feature" not in log, log.replace(chr(10), " | "))
    finally:
        _rmtree_retry(d1)

    # ── A2. user commit on top → revert (additive), user kept ─────
    d2 = Path(tempfile.mkdtemp(prefix="op_revux2_"))
    try:
        pg = ProjectGit(d2)
        pg._run("init", "--quiet", "--initial-branch=main")
        pg._run("config", "user.email", "u@u")
        pg._run("config", "user.name", "User")
        (d2 / "base.txt").write_text("base\n", encoding="utf-8")
        pg._run("add", "-A"); pg._run("commit", "-m", "base", "--quiet")
        (d2 / "feature.py").write_text("agent feature\n", encoding="utf-8")
        sha = pg.commit_agent_changes("[agent] add feature", baseline=set())
        # USER commits their own work on top (different file → no conflict)
        (d2 / "my_work.py").write_text("user's important work\n", encoding="utf-8")
        pg._run("add", "-A"); pg._run("commit", "-m", "my work", "--quiet")
        out = pg.revert_agent_commit(sha)
        check("A2: agent commit not HEAD → revert (additive)",
              out.get("action") == "revert" and out.get("ok"), str(out))
        check("A2: agent's feature.py undone",
              not (d2 / "feature.py").exists(), "feature reverted")
        check("A2: user's commit + file preserved",
              (d2 / "my_work.py").exists()
              and "my work" in pg._run("log", "--pretty=%s", check=False),
              "user work intact")
    finally:
        _rmtree_retry(d2)

    # ── A3. absent sha ────────────────────────────────────────────
    d3 = Path(tempfile.mkdtemp(prefix="op_revux3_"))
    try:
        pg = ProjectGit(d3)
        pg._run("init", "--quiet", "--initial-branch=main")
        pg._run("config", "user.email", "u@u"); pg._run("config", "user.name", "User")
        pg._run("commit", "--allow-empty", "-m", "init", "--quiet")
        out = pg.revert_agent_commit("0" * 40)
        check("A3: unknown sha → absent",
              out.get("action") == "absent" and not out.get("ok"), str(out))
    finally:
        _rmtree_retry(d3)

    # ── B. revert_turn end-to-end with a project commit ───────────
    from openprogram.store.session.session_store import SessionStore
    from openprogram.store import project_commit as PC
    from openprogram.agent._revert import revert_turn

    proj = Path(tempfile.mkdtemp(prefix="op_revux_e2e_"))
    try:
        pgi = ProjectGit(proj)
        pgi._run("init", "--quiet", "--initial-branch=main")
        pgi._run("config", "user.email", "u@u"); pgi._run("config", "user.name", "User")
        (proj / "seed.txt").write_text("seed\n", encoding="utf-8")
        pgi._run("add", "-A"); pgi._run("commit", "-m", "seed", "--quiet")

        store = SessionStore()
        store.create_session("rv1", "main", title="rev", work_dir=str(proj))
        amid = "u1_reply"
        store.append_message("rv1", {"role": "user", "content": "do it", "id": "u1"})
        store.append_message("rv1", {"role": "assistant", "content": "done", "id": amid})
        store.commit_turn("rv1", "turn")

        # simulate the turn's auto-commit
        (proj / "added_by_agent.py").write_text("agent code\n", encoding="utf-8")
        sha = PC.commit_turn_changes("rv1", "do it", set())
        # stamp the node like the dispatcher does
        _g, _idx = store._open("rv1")
        node = _idx.nodes_by_id.get(amid)
        node.metadata = {**(node.metadata or {}),
                         "project_commit": {"repo": str(proj), "sha": sha}}
        import json as _json
        rl = (node.role or "x")[0]
        fp = _g.path / "history" / f"{node.seq:04d}-{rl}-{node.id}.json"
        if fp.exists():
            fp.write_text(_json.dumps(node.to_dict(), ensure_ascii=False, default=str),
                          encoding="utf-8")

        res = revert_turn("rv1", amid)
        gu = res.get("git_undo") or {}
        check("B: revert_turn returns a git_undo outcome",
              gu.get("ok") and gu.get("action") in ("reset", "revert"), str(gu))
        check("B: agent's file undone in the project",
              not (proj / "added_by_agent.py").exists(), "rolled back")
        check("B: revert_turn stamped the node reverted",
              res.get("metadata_stamped"), str(res.get("metadata_stamped")))
    finally:
        _rmtree_retry(proj)
        _rmtree_retry(get_state_dir())
        print(f"# cleaned up")

    print()
    print(f"=== {len(failures)} FAIL: {failures} ===" if failures else "=== ALL PASS ===")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(main())
