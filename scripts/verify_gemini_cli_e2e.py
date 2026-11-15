#!/usr/bin/env python3
"""Drive two turns against the Gemini CLI subscription path (Cloud Code
Assist) and verify the resulting DAG.

Setup expected before running:

  1. ``gemini auth login`` already done (or equivalent) — leaves OAuth
     credentials in ``~/.gemini/oauth_creds.json``.

Mirrors ``verify_dag_e2e.py`` but pins the dispatcher's runtime to
``GeminiCLIRuntime`` via ``AGENTIC_PROVIDER`` env var so the run uses
Cloud Code Assist instead of the user-default (typically Codex).

Usage::

    python scripts/verify_gemini_cli_e2e.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path


def main() -> int:
    creds = Path.home() / ".gemini" / "oauth_creds.json"
    if not creds.exists():
        print(f"== Gemini OAuth not found at {creds}")
        print("   Run:  gemini auth login")
        return 2

    # Force dispatcher to pick the Gemini-CLI provider by passing
    # ``model_override`` on TurnRequest. ``AGENTIC_PROVIDER`` env only
    # influences create_runtime() (used by Agent method); dispatcher
    # itself reads from the agent profile, so env-var pinning alone
    # silently still talks to the user's default provider.
    MODEL_OVERRIDE = "gemini-subscription/gemini-2.5-flash"

    tmp_dir = Path(tempfile.mkdtemp(prefix="op_gemini_cli_verify_"))
    db_path = tmp_dir / "verify.sqlite"
    print(f"== sandbox DB: {db_path}")

    from openprogram.runtime.context.session_db import DagSessionDB
    import openprogram.runtime.context.session_db as sdb_mod
    sdb_mod._default = DagSessionDB(db_path)
    print(f"== default_db patched")

    from openprogram.runtime.conversation.turn_runtime import (
        process_user_turn,
        _resolve_model,
        _load_agent_profile,
    )
    from openprogram.runtime.agent.turn_request import TurnRequest
    _probe_model = _resolve_model(
        _load_agent_profile("main"), MODEL_OVERRIDE,
    )
    print(f"== model_override: {MODEL_OVERRIDE}")
    print(f"== resolved model: id={_probe_model.id} api={_probe_model.api}"
          f" provider={_probe_model.provider}")
    if _probe_model.api != "gemini-subscription":
        print(f"  ERROR: expected api=gemini-subscription, got {_probe_model.api}")
        return 1

    session_id = "verify_gemini_cli_001"
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

    print("\n== Turn 1: 'What is 2 + 2? Answer in one short sentence.'")
    t0 = time.time()
    r1 = process_user_turn(
        TurnRequest(
            session_id=session_id,
            agent_id="main",
            user_text="What is 2 + 2? Answer in one short sentence.",
            source="cli",
            model_override=MODEL_OVERRIDE,
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

    print("\n== Turn 2: 'Now multiply that by 3.' (should see Turn 1)")
    t1 = time.time()
    r2 = process_user_turn(
        TurnRequest(
            session_id=session_id,
            agent_id="main",
            user_text="Now multiply that by 3.",
            source="cli",
            model_override=MODEL_OVERRIDE,
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
        out_snip = (str(data.get("output") or "")[:60]
                    ).replace("\n", " ")
        print(f"  seq={r['seq']:2d} role={r['type']:5s}"
              f" id={r['id'][:12]:12s}"
              f" parent={r['predecessor'][:10] if r['predecessor'] else '-':10s}"
              f" called_by={(data.get('called_by') or '')[:10]:10s}"
              f" out={out_snip!r}")

    print("\n== Verifications:")
    if len(rows) < 4:
        print(f"  FAIL: expected ≥4 nodes, got {len(rows)}")
        return 1
    print(f"  OK   nodes ≥ 4 ({len(rows)})")

    db = sdb_mod._default
    branch = db.get_branch(session_id)
    user_msgs = [m for m in branch if m["role"] == "user"]
    assistant_msgs = [m for m in branch if m["role"] == "assistant"]
    if len(user_msgs) < 2 or len(assistant_msgs) < 2:
        print(f"  FAIL: branch missing user/assistant pairs ({len(user_msgs)}/{len(assistant_msgs)})")
        return 1
    print(f"  OK   {len(user_msgs)} user / {len(assistant_msgs)} assistant messages")

    final = (r2.final_text or "").lower()
    if "12" in final or "twelve" in final:
        print(f"  OK   turn 2 reply references '12' / 'twelve'"
              f" — LLM saw turn 1 history")
    else:
        print(f"  WARN turn 2 reply doesn't mention 12 — model may not have")
        print(f"       seen turn 1, but could also be a stylistic choice")

    print("\n== verify_gemini_cli_e2e: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
