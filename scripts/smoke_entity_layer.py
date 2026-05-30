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

    from openprogram.store.session.session_store import SessionStore, _default_root
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
        # Binding alone must NOT create a .git — git-init is deferred to
        # the first auto-commit (turn end), not the bind. But binding
        # SHOULD drop the footprint .gitignore so our .openprogram/ stays
        # invisible to the user's git if the folder is / becomes a repo.
        check(
            "binding alone does NOT create .git (init deferred)",
            not (proj_dir / ".git").exists(),
            str(proj_dir),
        )
        check(
            "binding drops .openprogram/.gitignore (footprint hidden)",
            (proj_dir / ".openprogram" / ".gitignore").read_text(encoding="utf-8") == "*\n"
            if (proj_dir / ".openprogram" / ".gitignore").exists() else False,
            "gitignore = *",
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

        # 5d. project-git log readable
        log = pg.log(limit=10)
        check("project-git log readable", isinstance(log, list) and len(log) >= 1,
              f"{len(log)} commits")

        # ── 7. project_commit dispatcher wiring (auto-commit, default ON) ──
        from openprogram.store import project_commit as PC

        # 7a. default is ON (no env, no config).
        os.environ.pop("OPENPROGRAM_PROJECT_AUTOCOMMIT", None)
        check("auto-commit ON by default", PC.is_enabled() is True, "default on")
        # env can still force it off.
        os.environ["OPENPROGRAM_PROJECT_AUTOCOMMIT"] = "0"
        check("env can force auto-commit OFF", PC.is_enabled() is False, "env=0")
        os.environ.pop("OPENPROGRAM_PROJECT_AUTOCOMMIT", None)

        # 7b. AUTO-INIT: binding a NON-git folder still creates NO .git at
        #     bind time (deferred). But when the agent edits it, the
        #     turn-end commit auto-inits the folder — with a baseline
        #     commit of pre-existing files FIRST, then the agent's edit on
        #     top as a clean diff.
        plain_dir = Path(tempfile.mkdtemp(prefix="op_plain_"))
        try:
            # user already had a file in this (non-git) folder
            (plain_dir / "preexisting.py").write_text("# user's prior file 旧\n", encoding="utf-8")
            store.create_session("plain1", "main", title="plain", work_dir=str(plain_dir))
            store.append_message("plain1", {"role": "user", "content": "hi", "id": "p1"})
            store.commit_turn("plain1", "turn: hi")
            check("binding a non-git folder creates NO .git (deferred)",
                  not (plain_dir / ".git").exists(),
                  f".git exists={(plain_dir / '.git').exists()}")

            base_pre = PC.snapshot_baseline("plain1")   # set() — pre-init
            (plain_dir / "agent_made.py").write_text("# agent 新\n", encoding="utf-8")
            sha = PC.commit_turn_changes("plain1", "do work", base_pre)
            pg_plain = P.ProjectGit(plain_dir)
            check("auto-init: non-git folder → git-init'd + agent edit committed",
                  isinstance(sha, str) and (plain_dir / ".git").exists(),
                  f"sha={sha} git={(plain_dir / '.git').exists()}")
            # history: baseline commit (user files) THEN agent commit
            logs = pg_plain.log(limit=5)
            msgs = [c["message"] for c in logs]
            baseline_first = any("baseline" in m for m in msgs)
            agent_on_top = msgs and msgs[0].startswith("[agent ")
            check("auto-init: baseline commit of pre-existing files, agent on top",
                  baseline_first and agent_on_top, f"msgs={msgs}")
            # the agent's commit is a clean diff (only agent_made.py), NOT
            # bulk-adding preexisting.py
            agent_files = pg_plain._run("show", "--name-only", "--pretty=format:", "HEAD").split()
            check("auto-init: agent commit is a clean diff (only its own file)",
                  agent_files == ["agent_made.py"], f"changed={agent_files}")
        finally:
            _rmtree_retry(plain_dir)

        # 7b2. AUTO-INIT BLOCKED: a heavy dep dir (node_modules) present →
        #      refuse to auto-init, emit autoinit_blocked notice, no .git.
        heavy_dir = Path(tempfile.mkdtemp(prefix="op_heavy_"))
        try:
            (heavy_dir / "node_modules").mkdir()
            (heavy_dir / "node_modules" / "junk.js").write_text("//big\n", encoding="utf-8")
            store.create_session("heavy1", "main", title="heavy", work_dir=str(heavy_dir))
            store.commit_turn("heavy1", "turn: x")
            (heavy_dir / "app.py").write_text("# agent edit\n", encoding="utf-8")
            evs: list[dict] = []
            r = PC.commit_turn_changes("heavy1", "do work",
                                       PC.snapshot_baseline("heavy1"),
                                       on_event=evs.append)
            blocked_notice = any(
                (e.get("data") or {}).get("reason") == "autoinit_blocked" for e in evs
            )
            check("auto-init blocked by node_modules → skip + notice, no .git",
                  r is None and blocked_notice and not (heavy_dir / ".git").exists(),
                  f"res={r} notice={blocked_notice}")
        finally:
            _rmtree_retry(heavy_dir)

        # 7c. folder that IS a git repo → default-on commits the agent edit.
        proj_dir2 = Path(tempfile.mkdtemp(prefix="op_proj2_"))
        try:
            # user makes it a git repo themselves (we never do).
            P.ProjectGit(proj_dir2)._run("init", "--quiet", "--initial-branch=main")
            P.ProjectGit(proj_dir2)._run("config", "user.email", "u@u")
            P.ProjectGit(proj_dir2)._run("config", "user.name", "User")
            store.create_session("wired1", "main", title="wired", work_dir=str(proj_dir2))
            store.append_message("wired1", {"role": "user", "content": "edit a file", "id": "w1"})
            store.commit_turn("wired1", "turn: edit a file")

            base = PC.snapshot_baseline("wired1")           # clean tree
            (proj_dir2 / "feature.py").write_text("# agent wrote this 功能\n", encoding="utf-8")
            sha = PC.commit_turn_changes("wired1", "add feature 功能", base)
            check("default-on + git repo + agent edit → commits",
                  isinstance(sha, str) and len(sha) >= 7, str(sha))

            pg2 = P.ProjectGit(proj_dir2)
            top = pg2.log(limit=1)
            top_msg = top[0]["message"] if top else ""
            author = ""
            try:
                author = pg2._run("log", "-1", "--pretty=format:%an").strip()
            except Exception as e:
                author = f"ERR {e}"
            check("commit attributed to agent identity",
                  bool(top) and author == P.ProjectGit.AGENT_NAME and "feature" in top_msg,
                  f"author={author!r} msg={top_msg!r}")
            check("feature.py committed (not left dirty)",
                  "feature.py" not in pg2.dirty_paths(), "clean after commit")

            # dirty-refusal (Strategy A) still emits a skip event
            (proj_dir2 / "user_wip2.txt").write_text("user editing 用户改", encoding="utf-8")
            base2 = PC.snapshot_baseline("wired1")
            (proj_dir2 / "agent2.py").write_text("# agent again\n", encoding="utf-8")
            evs2: list[dict] = []
            res_skip = PC.commit_turn_changes("wired1", "more work", base2, on_event=evs2.append)
            emitted = any((e.get("data") or {}).get("type") == "project_commit_skipped" for e in evs2)
            check("dirty user tree → refuses + emits skip event",
                  res_skip is None and emitted, f"res={res_skip}")
        finally:
            _rmtree_retry(proj_dir2)

        # 7d. RULE B: an active worktree makes the real-repo commit yield.
        #     We monkeypatch _has_active_worktree to simulate an active wt
        #     (exercising the dispatcher's real call path without spinning
        #     up a worktree).
        proj_dir3 = Path(tempfile.mkdtemp(prefix="op_proj3_"))
        try:
            P.ProjectGit(proj_dir3)._run("init", "--quiet", "--initial-branch=main")
            P.ProjectGit(proj_dir3)._run("config", "user.email", "u@u")
            P.ProjectGit(proj_dir3)._run("config", "user.name", "User")
            store.create_session("wt1", "main", title="wt", work_dir=str(proj_dir3))
            store.commit_turn("wt1", "turn: x")
            (proj_dir3 / "agent_wt.py").write_text("# agent in wt era\n", encoding="utf-8")
            _orig = PC._has_active_worktree
            PC._has_active_worktree = lambda sid: True
            try:
                yield_base = PC.snapshot_baseline("wt1")
                yield_res = PC.commit_turn_changes("wt1", "work", yield_base)
            finally:
                PC._has_active_worktree = _orig
            check("rule B: active worktree → real-repo commit yields",
                  yield_base is None and yield_res is None,
                  f"base={yield_base} res={yield_res}")
            # With no active worktree, a fresh agent edit on a clean
            # baseline DOES commit. (We commit the yielded file first so
            # the tree is clean, then capture a baseline, then edit anew —
            # mirroring how a real turn captures baseline at turn start.)
            P.ProjectGit(proj_dir3).commit_agent_changes(
                "[setup] land yielded file", baseline=set())
            base_clean = PC.snapshot_baseline("wt1")          # now clean
            (proj_dir3 / "agent_wt2.py").write_text("# fresh agent edit\n", encoding="utf-8")
            commit_res = PC.commit_turn_changes("wt1", "work", base_clean)
            check("rule B: without worktree the same edit commits",
                  isinstance(commit_res, str), str(commit_res))
        finally:
            _rmtree_retry(proj_dir3)

        # ── 8. snapshot GC actually evicts beyond the cap ──
        from openprogram.store.snapshot.file_backup import BackupStore, gc_evict_old
        store.create_session("gc1", "main", title="gc")
        gc_dir = store._session_dir("gc1")
        bs = BackupStore(gc_dir)
        # create 5 turn-dirs by backing up a throwaway file under 5 turn ids
        tmpf = gc_dir / "scratch.txt"
        tmpf.write_text("x", encoding="utf-8")
        for i in range(5):
            bs.backup_before_edit(f"turn{i}", str(tmpf))
        from openprogram.store.snapshot.file_backup.paths import session_backup_root
        root = session_backup_root(gc_dir)
        before = len([p for p in root.iterdir() if p.is_dir()])
        removed = gc_evict_old(gc_dir, max_turns=2)
        after = len([p for p in root.iterdir() if p.is_dir()])
        check("GC evicts turn-dirs beyond the cap",
              before == 5 and removed == 3 and after == 2,
              f"before={before} removed={removed} after={after}")

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
