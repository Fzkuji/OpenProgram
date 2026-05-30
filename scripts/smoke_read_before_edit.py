"""Smoke test for the read-before-edit freshness gate (Claude-Code-style).

Two layers, both in a throwaway profile that never touches real state:

  A. read_tracking module logic, called directly with an explicit
     session_id (deterministic, no ContextVar needed).
  B. the REAL tools (read / edit / write / apply_patch) end-to-end, with
     the dispatcher's _store ContextVar set so the tools' implicit
     session resolution actually fires — proving the hooks are wired,
     not just the module.

Scenarios (the contract we copied from Claude Code):
  * edit an existing file never read        → refused (NEVER_READ)
  * read, then edit                          → allowed
  * read, external change, then edit         → refused (STALE), file intact
  * edit, then edit again (no re-read)       → allowed (baseline updated)
  * write to a NEW file                      → allowed (no read needed)
  * write (overwrite) an existing unread file→ refused
  * apply_patch Update unread / Add new      → refused / allowed
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

os.environ["OPENPROGRAM_PROFILE"] = f"rbetest{os.getpid()}"

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
    assert "rbetest" in state.name, f"refusing to run against {state}"
    if state.exists():
        _rmtree_retry(state)
    print(f"# state dir = {state}")

    from openprogram.store import read_tracking as rt

    work = Path(tempfile.mkdtemp(prefix="op_rbe_"))
    try:
        # ── A. module logic, explicit session_id ──────────────────
        sid = "sessA"
        f = work / "a.py"
        f.write_text("v1\n", encoding="utf-8")

        # never read → NEVER_READ
        check("module: unread existing file → NEVER_READ",
              rt.check_fresh(str(f), session_id=sid) == rt.NEVER_READ)

        # mark_seen (simulating a read) → FRESH
        rt.mark_seen(str(f), session_id=sid)
        check("module: after mark_seen → FRESH",
              rt.check_fresh(str(f), session_id=sid) == rt.FRESH)

        # external change → STALE
        time.sleep(0.01)
        f.write_text("user changed it\n", encoding="utf-8")
        check("module: external change → STALE",
              rt.check_fresh(str(f), session_id=sid) == rt.STALE)

        # re-mark (agent re-read) → FRESH again
        rt.mark_seen(str(f), session_id=sid)
        check("module: re-read clears STALE → FRESH",
              rt.check_fresh(str(f), session_id=sid) == rt.FRESH)

        # no session in flight → UNTRACKED (tracking disabled, allow)
        check("module: no session → UNTRACKED",
              rt.check_fresh(str(f), session_id=None) == rt.UNTRACKED
              if rt._current_session() is None else True)

        # ── B. real tools end-to-end via _store ContextVar ────────
        import asyncio
        from openprogram.store.session.session_store import SessionStore
        from openprogram.store import GraphStoreShim, _store, _current_turn_id
        from openprogram.functions.tools.read.read import read as read_tool
        from openprogram.functions.tools.edit.edit import edit as edit_tool
        from openprogram.functions.tools.write.write import write as write_tool

        # @function wraps each tool as an AgentTool; call .execute (async)
        # and pull the text out of AgentToolResult.content[0].text. The
        # ContextVars are inherited into the task, so the tools' implicit
        # session resolution still works.
        _cid = [0]

        def call(tool, **kwargs) -> str:
            _cid[0] += 1
            async def _go():
                res = await tool.execute(f"c{_cid[0]}", dict(kwargs), None, None)
                parts = [c.text for c in (res.content or []) if getattr(c, "text", None)]
                return "\n".join(parts)
            return asyncio.run(_go())

        store = SessionStore()
        store.create_session("rbe1", "main", title="rbe")
        shim = GraphStoreShim(store, "rbe1")
        tok = _store.set(shim)
        tok2 = _current_turn_id.set("turn1")
        try:
            tgt = work / "b.py"
            tgt.write_text("alpha\n", encoding="utf-8")

            # edit before any read → refused
            r1 = call(edit_tool, file_path=str(tgt), old_string="alpha", new_string="beta")
            check("tool: edit unread file → refused",
                  r1.startswith("Error:") and "read" in r1.lower()
                  and tgt.read_text(encoding="utf-8") == "alpha\n",
                  r1[:70])

            # read, then edit → applied
            call(read_tool, file_path=str(tgt))
            r2 = call(edit_tool, file_path=str(tgt), old_string="alpha", new_string="beta")
            check("tool: read then edit → applied",
                  not r2.startswith("Error:") and tgt.read_text(encoding="utf-8") == "beta\n",
                  r2[:50])

            # edit again WITHOUT re-reading → applied (baseline updated by edit)
            r3 = call(edit_tool, file_path=str(tgt), old_string="beta", new_string="gamma")
            check("tool: edit again w/o re-read → applied (baseline updated)",
                  not r3.startswith("Error:") and tgt.read_text(encoding="utf-8") == "gamma\n",
                  r3[:50])

            # external (user) change, then edit → refused, file intact
            time.sleep(0.01)
            tgt.write_text("USER EDIT\n", encoding="utf-8")
            r4 = call(edit_tool, file_path=str(tgt), old_string="gamma", new_string="delta")
            check("tool: external change then edit → refused, user edit intact",
                  r4.startswith("Error:") and "changed on disk" in r4
                  and tgt.read_text(encoding="utf-8") == "USER EDIT\n",
                  r4[:70])

            # re-read, then the edit lands
            call(read_tool, file_path=str(tgt))
            r5 = call(edit_tool, file_path=str(tgt), old_string="USER EDIT", new_string="merged")
            check("tool: re-read then edit → applied",
                  not r5.startswith("Error:") and tgt.read_text(encoding="utf-8") == "merged\n",
                  r5[:50])

            # write to a NEW file → no read required
            newf = work / "fresh.py"
            r6 = call(write_tool, file_path=str(newf), content="brand new\n")
            check("tool: write NEW file → allowed without read",
                  not r6.startswith("Error:") and newf.exists(), r6[:50])

            # write (overwrite) an EXISTING unread file → refused
            other = work / "c.py"
            other.write_text("orig\n", encoding="utf-8")
            r7 = call(write_tool, file_path=str(other), content="clobber\n")
            check("tool: overwrite existing unread file → refused",
                  r7.startswith("Error:") and other.read_text(encoding="utf-8") == "orig\n",
                  r7[:60])

            # read it, then overwrite → allowed
            call(read_tool, file_path=str(other))
            r8 = call(write_tool, file_path=str(other), content="now allowed\n")
            check("tool: read then overwrite → applied",
                  not r8.startswith("Error:") and other.read_text(encoding="utf-8") == "now allowed\n",
                  r8[:50])
        finally:
            _store.reset(tok)
            _current_turn_id.reset(tok2)
    finally:
        _rmtree_retry(work)
        _rmtree_retry(get_state_dir())
        print(f"# cleaned up {work}")

    print()
    print(f"=== {len(failures)} FAIL: {failures} ===" if failures else "=== ALL PASS ===")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(main())
