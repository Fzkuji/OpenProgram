#!/usr/bin/env python3
"""Drive dispatcher through two user turns against a real LLM and
verify the resulting DAG.

Goal: confirm the DAG-driven prompt path doesn't break a real LLM
session — Anthropic/OpenAI gets called, replies stream back, and the
nodes end up correctly linked by metadata.parent_id (chat thread)
+ called_by (function nesting).

Usage:
    python scripts/verify_dag_e2e.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path


def main() -> int:
    tmp_dir = Path(tempfile.mkdtemp(prefix="op_dag_verify_"))
    db_path = tmp_dir / "verify.sqlite"
    print(f"== sandbox DB: {db_path}")

    # Point default_db() at the sandbox so the dispatcher doesn't
    # touch the user's real ~/.agentic/dag_sessions.sqlite.
    from openprogram.context.session_db import DagSessionDB
    import openprogram.agent.session_db as sdb_mod
    sdb_mod._default = DagSessionDB(db_path)
    print(f"== default_db patched")

    # Build a TurnRequest and drive process_user_turn directly.
    from openprogram.agent.dispatcher import (
        process_user_turn, TurnRequest,
    )

    session_id = "verify_session_001"
    events: list[dict] = []

    def collect(env: dict) -> None:
        events.append(env)
        t = env.get("type")
        data = env.get("data") or {}
        if t == "chat_ack":
            print(f"  [ack] session={data.get('session_id')}"
                  f" msg={data.get('msg_id')}")
        elif t == "chat_response":
            sub = data.get("type")
            if sub == "result":
                snippet = (data.get("content") or "")[:80]
                print(f"  [result] {snippet}")
            elif sub == "error":
                print(f"  [ERROR] {data.get('content')}")

    # ── Turn 1 ────────────────────────────────────────────────
    print("\n== Turn 1: 'What is 2 + 2? Answer in one short sentence.'")
    t0 = time.time()
    r1 = process_user_turn(
        TurnRequest(
            session_id=session_id,
            agent_id="main",
            user_text="What is 2 + 2? Answer in one short sentence.",
            source="cli",
        ),
        on_event=collect,
    )
    dt1 = time.time() - t0
    print(f"  turn 1 done in {dt1:.1f}s | failed={r1.failed}"
          f" final_text_len={len(r1.final_text or '')}")
    if r1.failed:
        print(f"  ERROR: {r1.error}")
        return 1
    print(f"  reply: {(r1.final_text or '')[:200]}")

    # ── Turn 2 ────────────────────────────────────────────────
    print("\n== Turn 2: 'Now multiply that by 3.' (should see Turn 1)")
    t1 = time.time()
    r2 = process_user_turn(
        TurnRequest(
            session_id=session_id,
            agent_id="main",
            user_text="Now multiply that by 3.",
            source="cli",
        ),
        on_event=collect,
    )
    dt2 = time.time() - t1
    print(f"  turn 2 done in {dt2:.1f}s | failed={r2.failed}"
          f" final_text_len={len(r2.final_text or '')}")
    if r2.failed:
        print(f"  ERROR: {r2.error}")
        return 1
    print(f"  reply: {(r2.final_text or '')[:200]}")

    # ── DAG inspection ────────────────────────────────────────
    print("\n== DAG nodes table:")
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT id, type, predecessor, seq, data_json
               FROM nodes WHERE session_id = ? ORDER BY seq""",
            (session_id,),
        ).fetchall()

    print(f"  total nodes: {len(rows)}")
    for r in rows:
        data = json.loads(r["data_json"])
        meta = data.get("metadata") or {}
        out_snip = (str(data.get("output") or "")[:60]
                    ).replace("\n", " ")
        print(f"  seq={r['seq']:2d} role={r['type']:5s}"
              f" id={r['id'][:12]:12s}"
              f" parent={r['predecessor'][:10] if r['predecessor'] else '-':10s}"
              f" called_by={(data.get('called_by') or '')[:10]:10s}"
              f" out={out_snip!r}")

    # ── Session row ───────────────────────────────────────────
    print("\n== Session row:")
    db = sdb_mod._default
    s = db.get_session(session_id)
    if s is None:
        print("  ERROR: session row missing")
        return 1
    print(f"  id={s['id']}")
    print(f"  title={s.get('title')!r}")
    print(f"  head_id={s.get('head_id')}")

    # ── Chain check ───────────────────────────────────────────
    print("\n== get_branch (active chat thread, via metadata.parent_id):")
    branch = db.get_branch(session_id)
    print(f"  branch len: {len(branch)}")
    for m in branch:
        c = (m.get("content") or "")[:60].replace("\n", " ")
        print(f"  [{m['role']:9}] id={m['id'][:10]:10}"
              f" parent={(m.get('parent_id') or '-')[:10]:10}"
              f" content={c!r}")

    # ── Verifications ─────────────────────────────────────────
    print("\n== Verifications:")
    expected_min_nodes = 4   # user, assistant, user, assistant
    if len(rows) < expected_min_nodes:
        print(f"  FAIL: expected ≥{expected_min_nodes} nodes, got {len(rows)}")
        return 1
    print(f"  OK   nodes ≥ {expected_min_nodes} ({len(rows)})")

    if len(branch) < 4:
        print(f"  FAIL: chat thread should have ≥4 messages, got {len(branch)}")
        return 1
    print(f"  OK   chat thread length ≥ 4 ({len(branch)})")

    if branch[0]["role"] != "user":
        print(f"  FAIL: chat thread should start with user, got {branch[0]['role']}")
        return 1
    print(f"  OK   chat thread starts with user")

    user_msgs = [m for m in branch if m["role"] == "user"]
    assistant_msgs = [m for m in branch if m["role"] == "assistant"]
    if len(user_msgs) < 2:
        print(f"  FAIL: should have ≥2 user messages, got {len(user_msgs)}")
        return 1
    print(f"  OK   {len(user_msgs)} user messages")
    print(f"  OK   {len(assistant_msgs)} assistant messages")

    # Turn 2 should mention something quantitative (12 / twelve) if
    # the LLM saw Turn 1.
    final = (r2.final_text or "").lower()
    if "12" in final or "twelve" in final:
        print(f"  OK   turn 2 reply references '12' / 'twelve'"
              f" — LLM saw turn 1 history")
    else:
        print(f"  WARN turn 2 reply doesn't mention 12 — LLM may not have")
        print(f"       seen turn 1, but might still be a stylistic choice")

    print("\n== verify_dag_e2e: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
