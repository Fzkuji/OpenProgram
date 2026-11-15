"""Owned WS/driver/spawn probe; initialize only storage and the lower provider."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import threading
import time

from openprogram import Agent
from openprogram.runtime.agent.runtime import Runtime
from openprogram.runtime.execution import ExecutionStore
from openprogram.state.store import SessionStore

HOME = Path(os.environ["HOME"])
DB = SessionStore(HOME / "sessions")
STORE = ExecutionStore(HOME / "fixture" / "execution.sqlite3")
# Spawn reloads this module. Both processes resolve the same owned stores.
import openprogram.runtime.context.session_db as session_db
import openprogram.runtime.execution as execution
import openprogram.runtime.agent.runtime.factory as runtime_factory
import openprogram.state.store.session.session_store as session_store

# Only the probe process and its spawned child carry the receipt variable.
# Anything else that imports this file (pytest given its path) keeps its own
# stores and provider instead of inheriting these for the rest of the run.
if os.environ.get("OWNER_PROBE_RECEIPT"):
    session_db.default_db = lambda: DB
    session_store.shared._default_store = DB
    execution.default_store = lambda: STORE
    runtime_factory.create_runtime = lambda **_kwargs: Runtime(
        call=lambda *_args, **_opts: "unused"
    )


class OwnerSourceProbeAgent(Agent):
    method_options = {
        "owner_source_probe": {"name": "owner_source_probe", "tool": True},
    }

    def owner_source_probe(self, task: str, fault: str = "") -> str:
        """Inspect original source membership without browser or retrieval effects."""
        from openprogram.runtime.conversation import turn_request_context as context
        from openprogram.runtime.agent import turn_request as agent_turn_request
        from openprogram.programs._authoring.reports.evidence_gate import EvidenceGate

        request = agent_turn_request.get_turn_request()
        source = getattr(context, "original_owner_request", lambda: None)()
        if fault and source is not None:
            if fault == "foreign":
                source = replace(source, principal_id="owner/install/" + "f" * 16)
            elif fault == "other_store":
                source = replace(source, session_dir=str(HOME / "other-sessions"))
            elif fault == "other_db":
                other = SessionStore(HOME / "other-sessions")
                other.create_session(request.session_id, "main")
                session_db.default_db = lambda: other
            elif fault == "other_execution":
                source = replace(source, execution_id="different-execution")
            elif fault in {"replay", "assistant"}:
                rows = DB.get_messages(request.session_id)
                candidates = [
                    r
                    for r in rows
                    if r["role"] == ("user" if fault == "replay" else "assistant")
                    and r["id"] != source.user_msg_id
                    and r.get("content")
                ]
                old = candidates[0]
                source = replace(
                    source, user_msg_id=old["id"], user_text=old["content"]
                )
            else:
                raise AssertionError(fault)
            context.set_original_owner_input(source)
        gate = EvidenceGate(task, "personal")
        return json.dumps(
            {
                "pid": os.getpid(),
                "runtime_speaker": request.speaker_kind,
                "interaction": request.interaction,
                "rows": list(gate.rows.values()),
            },
            ensure_ascii=False,
        )


owner_source_probe = OwnerSourceProbeAgent().owner_source_probe


def main() -> None:
    import pytest
    from tests.component.agent.execution.test_agent_continuation_real import (
        _WebSocket,
        _wait,
        real_agent_chat,
    )
    from tests.component.providers.scripted_provider import (
        ScriptedText,
        ScriptedToolCall,
    )
    from openprogram.runtime.conversation.turn_runtime import loop_runner
    from openprogram.programs._host.runtime import get
    from openprogram.interfaces.server.ws_actions.chat import handle_chat

    fault = os.environ.get("OWNER_PROBE_FAULT", "")
    patch = pytest.MonkeyPatch()
    native_approval = loop_runner._wrap_with_approval
    fixture = real_agent_chat.__wrapped__(HOME / "fixture", patch)
    harness = next(fixture)
    patch.setattr(session_db, "default_db", lambda: DB)
    patch.setattr(session_store.shared, "_default_store", DB)
    DB.create_session(harness.session_id, "main")
    patch.setattr(loop_runner, "_wrap_with_approval", native_approval)
    tool = get("owner_source_probe")
    assert tool is owner_source_probe._agent_tool
    patch.setattr(loop_runner, "_resolve_tools", lambda *_args, **_opts: [tool])
    result = {"parent_pid": os.getpid(), "fault": fault, "turns": []}
    try:
        for index in range(2 if fault in {"replay", "assistant"} else 1):
            if index:
                _wait(
                    lambda: not harness.server._is_run_active(harness.session_id),
                    timeout=8,
                )
            # Replay must reject the prior message ID even when both real
            # admitted human messages have exactly the same text.
            number = 1 if fault == "replay" else index + 1
            task = f"本周2026-W40本人完成实验；下周继续验证。第{number}条原始输入。"
            call_id = f"owner-source-{index}"
            harness.provider.add_response(
                ScriptedToolCall(
                    "owner_source_probe",
                    {"task": task, "fault": fault if index else ""},
                    call_id,
                )
            )
            if fault not in {"replay", "assistant"}:
                harness.provider.responses[-1] = (
                    ScriptedToolCall(
                        "owner_source_probe", {"task": task, "fault": fault}, call_id
                    ),
                )
            harness.provider.add_response(ScriptedText("owned provider completed"))
            ws = _WebSocket()
            asyncio.run(
                handle_chat(
                    ws,
                    {
                        "text": task,
                        "session_id": harness.session_id,
                        "permission_mode": "bypass",
                    },
                )
            )
            ack = next(f for f in ws.frames if f.get("type") == "chat_ack")
            eid = ack["data"]["execution_id"]
            harness.execution_id = eid
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                record = harness.store.get_execution(eid)
                if record.terminal_at is not None:
                    break
                threading.Event().wait(0.01)
            assert record.terminal_at is not None, record
            DB.invalidate_cache(harness.session_id)
            rows = DB.get_messages(harness.session_id)
            output = next(
                r
                for r in reversed(rows)
                if r["role"] == "tool" and r.get("function") == "owner_source_probe"
            )
            result["turns"].append(
                {
                    "task": task,
                    "ack": ack,
                    "status": record.status.value,
                    "output": json.loads(output["content"]),
                    "canonical_users": [dict(r) for r in rows if r["role"] == "user"],
                }
            )
    finally:
        try:
            next(fixture)
        except StopIteration:
            pass
        patch.undo()
    Path(os.environ["OWNER_PROBE_RECEIPT"]).write_text(
        json.dumps(result, ensure_ascii=False)
    )


if __name__ == "__main__":
    main()
