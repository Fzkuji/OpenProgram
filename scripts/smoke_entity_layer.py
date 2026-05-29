"""End-to-end smoke test for the ENTITY memory layer (Session-Git +
Project-Git), run in a throwaway profile so it never touches the user's
real ~/.openprogram state.

What it exercises (no LLM, no network — pure storage layer):

  1. Ad-hoc session  → repo lands in home root <state>/sessions/<id>/
  2. Project-bound    → resolve_project(dir) git-inits the dir; session
                        repo lands inside <dir>/.openprogram/sessions/<id>/
                        and is reachable again after a store restart
                        (locations.json persistence)
  3. project_for_session reverse index + bind_session
  4. Non-ASCII (CJK) commit messages + file content round-trip through
     git on this platform's default encoding
  5. ProjectGit.commit_agent_changes Strategy A (clean tree commits;
     dirty user tree is NOT swept up)

Prints a PASS/FAIL line per check and a final summary. Exit code is the
number of failures (0 = all good).
"""
from __future__ import annotations

import os
import sys
import tempfile
import shutil
from pathlib import Path

# Make our own stdout UTF-8 so printing CJK / emoji test detail doesn't
# crash on a cp1252 Windows console. (This is about the TEST harness's
# print, not the code under test.)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Isolated profile BEFORE importing anything that resolves state paths.
# Unique per run (pid-suffixed) so a previous run's half-removed tree
# (Windows can leave an empty .git shell that git rejects) can never
# poison the next run. The "entitytest" stem keeps the safety assert
# below meaningful.
os.environ["OPENPROGRAM_PROFILE"] = f"entitytest{os.getpid()}"

failures: list[str] = []


def _rmtree_retry(p: Path, tries: int = 5) -> None:
    """rmtree that tolerates Windows holding git subprocess handles for
    a beat after the calls return — retry with a short backoff."""
    import time
    for i in range(tries):
        shutil.rmtree(p, ignore_errors=True)
        if not p.exists():
            return
        time.sleep(0.2 * (i + 1))
    shutil.rmtree(p, ignore_errors=True)  # last shot, swallow remnants


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    line = f"[{mark}] {name}"
    if detail:
        line += f"  — {detail}"
    print(line)
    if not cond:
        failures.append(name)


def main() -> int:
    from openprogram.paths import get_state_dir
    state = get_state_dir()
    print(f"# state dir = {state}")
    # Safety assertion: we must be in the throwaway profile, not real state.
    assert "entitytest" in state.name, f"refusing to run against {state}"
    # Pre-clean: a previous interrupted run may have left a half-removed
    # tree (Windows holds git handles, so rmtree can delete a repo's
    # subdirs but leave its .git, which then confuses create_session).
    # Start from a guaranteed-empty state dir.
    if state.exists():
        _rmtree_retry(state)
        state.mkdir(parents=True, exist_ok=True)

    from openprogram.store.session_store import SessionStore, _default_root
    from openprogram.store import project_store as P

    # A scratch dir to act as the user's "real project directory".
    proj_dir = Path(tempfile.mkdtemp(prefix="op_proj_"))

    try:
        # ── 1. ad-hoc session lands in home root ──────────────────
        store = SessionStore()
        store.create_session("adhoc1", "main", title="ad-hoc chat")
        store.append_message("adhoc1", {"role": "user", "content": "hello", "id": "m1"})
        store.commit_turn("adhoc1", "turn: hello")
        adhoc_dir = store._session_dir("adhoc1")
        check(
            "ad-hoc session in home root",
            adhoc_dir == _default_root() / "adhoc1" and (adhoc_dir / ".git").exists(),
            str(adhoc_dir),
        )
        sess = store.get_session("adhoc1")
        check(
            "ad-hoc carries project_id=default",
            (sess or {}).get("extra_meta", {}).get("project_id") == "default"
            or (sess or {}).get("project_id") == "default",
            str((sess or {}).get("extra_meta")),
        )

        # ── 2. project-bound session lands inside the project ─────
        store.create_session("bound1", "main", title="work chat", work_dir=str(proj_dir))
        store.append_message("bound1", {"role": "user", "content": "fix bug", "id": "b1"})
        store.commit_turn("bound1", "turn: fix bug")
        bound_dir = store._session_dir("bound1")
        expected = proj_dir / ".openprogram" / "sessions" / "bound1"
        check(
            "project-bound session inside project dir",
            bound_dir == expected and (bound_dir / ".git").exists(),
            str(bound_dir),
        )
        check(
            "binding git-init'd the project dir",
            (proj_dir / ".git").exists(),
            str(proj_dir),
        )

        # ── 3. reverse index survives a store restart ─────────────
        store2 = SessionStore()  # fresh instance → reloads locations.json
        reloaded = store2._session_dir("bound1")
        check(
            "locations.json persists across restart",
            reloaded == expected,
            str(reloaded),
        )
        proj = P.project_for_session("bound1")
        check(
            "project_for_session reverse lookup",
            proj is not None and not proj.is_default and "bound1" in proj.session_ids,
            (proj.id if proj else "None"),
        )
        listed = {s["id"] for s in store2.list_sessions(limit=100)}
        check(
            "list_sessions sees both home + in-project",
            {"adhoc1", "bound1"} <= listed,
            str(sorted(listed)),
        )

        # ── 4. CJK round-trip through git (encoding) ──────────────
        store.create_session("cjk1", "main", title="中文标题 🚀")
        store.append_message("cjk1", {"role": "user", "content": "修复 Windows 编码 bug", "id": "c1"})
        cjk_ok = True
        cjk_detail = ""
        try:
            store.commit_turn("cjk1", "提交：修复编码 bug 涉及 38 个文件")
            git, _idx = store._open("cjk1")
            logs = git.log(limit=5)
            cjk_detail = logs[0].message if logs else "(no log)"
            # The CJK commit message must survive the round-trip intact.
            cjk_ok = any("修复编码" in c.message for c in logs)
        except Exception as e:  # UnicodeDecodeError etc.
            cjk_ok = False
            cjk_detail = f"{type(e).__name__}: {e}"
        check("CJK commit message round-trips through git.log()", cjk_ok, cjk_detail)

        # 4b. Every other _run read-path must also survive CJK history.
        #     (commit_all returned a sha, log worked; now branch ops.)
        branch_ok = True
        branch_detail = ""
        try:
            git, _idx = store._open("cjk1")
            cur = git.current_branch()
            branches = git.list_branches()
            # create a branch off HEAD, list again, switch back
            git.checkout("main", create_branch="试验分支")
            after = set(git.list_branches())
            git.checkout(cur)
            branch_detail = f"cur={cur} branches={sorted(after)}"
            branch_ok = "试验分支" in after and cur in after
        except Exception as e:
            branch_ok = False
            branch_detail = f"{type(e).__name__}: {e}"
        check("CJK branch name round-trips (checkout/list_branches)", branch_ok, branch_detail)

        # ── 5. ProjectGit.commit_agent_changes Strategy A ─────────
        pg = P.ProjectGit(proj_dir)
        pg.ensure_init()

        # 5a. clean tree + new agent file (baseline empty) → commits.
        baseline = pg.dirty_paths()  # snapshot BEFORE the "agent turn"
        (proj_dir / "agent_edit.txt").write_text("written by agent 中文", encoding="utf-8")
        sha1 = pg.commit_agent_changes("[agent] add file", baseline=baseline)
        check(
            "Strategy A: agent-only change on clean tree → commits",
            isinstance(sha1, str) and sha1 not in (None, P.ProjectGit.SKIPPED_DIRTY),
            str(sha1),
        )

        # 5b. user has uncommitted WIP, THEN agent edits → must REFUSE
        #     (don't sweep the user's half-done work into an agent commit).
        (proj_dir / "user_wip.txt").write_text("user half-done work 用户", encoding="utf-8")
        baseline2 = pg.dirty_paths()           # user_wip.txt is dirty now
        check("Strategy A: baseline captures user WIP", "user_wip.txt" in baseline2,
              str(sorted(baseline2)))
        (proj_dir / "agent_edit2.txt").write_text("agent more", encoding="utf-8")
        res = pg.commit_agent_changes("[agent] should be skipped", baseline=baseline2)
        check(
            "Strategy A: refuses to commit over user's dirty tree",
            res == P.ProjectGit.SKIPPED_DIRTY,
            str(res),
        )
        check(
            "Strategy A: user's WIP left uncommitted after refusal",
            "user_wip.txt" in pg.dirty_paths(),
            "still dirty",
        )

        # 5c. no-baseline call on a dirty tree is conservative → refuse.
        res3 = pg.commit_agent_changes("[agent] no baseline")
        check("Strategy A: no-baseline + dirty tree → refuses", res3 == P.ProjectGit.SKIPPED_DIRTY,
              str(res3))

        # ── 6. is commit_agent_changes wired anywhere? (informational)
        # (grep done outside; here we just confirm the method works)
        log = pg.log(limit=10)
        check("project-git log readable", isinstance(log, list) and len(log) >= 1,
              f"{len(log)} commits")

    finally:
        store_root = get_state_dir()
        # Clean up BOTH the throwaway profile state and the scratch
        # project. Retry — git subprocesses may still hold handles.
        _rmtree_retry(proj_dir)
        _rmtree_retry(store_root)
        print(f"# cleaned up {proj_dir}")
        print(f"# cleaned up {store_root}")

    print()
    if failures:
        print(f"=== {len(failures)} FAIL: {failures} ===")
    else:
        print("=== ALL PASS ===")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(main())
