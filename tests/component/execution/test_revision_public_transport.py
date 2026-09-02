from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import openprogram.execution as execution_package
from openprogram.execution import RevisionControlService
from openprogram.execution.revision_public import (
    RevisionPublicError,
    submit_revision_request,
    validate_revision_request,
)

from tests.component.execution.test_revision_control import _hash, _source


def _actor(subject: str) -> dict[str, str]:
    return {
        "speaker_kind": "human",
        "speaker_id": subject,
        "speaker_display": subject,
        "principal_id": "owner/install/0123456789abcdef",
        "authority_tier": "owner",
        "interaction": "interactive",
    }


def _change(ref: str) -> list[dict[str, str]]:
    return [
        {
            "kind": "program_artifact",
            "target": "workflow",
            "before_hash": _hash("before"),
            "after_ref": ref,
            "rationale": "replace workflow entrypoint",
        }
    ]


def _mapping(frontier: dict[str, object]) -> list[dict[str, str]]:
    return [
        {
            "old_step_id": "collect",
            "new_step_id": "collect",
            "relation": "preserved",
            "old_contract_hash": str(frontier["contract_hash"]),
            "new_contract_hash": str(frontier["contract_hash"]),
        }
    ]


def _create_command(source, checkpoint, artifact, frontier):
    return {
        "type": "revision.draft",
        "action": "revision.draft.create",
        "execution_id": source.execution_id,
        "expected_draft_version": 0,
        "payload": {
            "source_checkpoint_id": checkpoint.checkpoint_id,
            "changes": _change(artifact.artifact_ref),
            "frontier_mapping": _mapping(frontier),
        },
    }


def test_public_revision_flow_derives_binding_and_returns_canonical_state(tmp_path):
    store, _control, source, checkpoint, frontier = _source(tmp_path)
    revisions = RevisionControlService(store)
    artifact = revisions.put_artifact(
        kind="program_artifact",
        content={"entrypoint": "workflow.revised", "source_hash": _hash("revised")},
    )
    created = submit_revision_request(
        store,
        _create_command(source, checkpoint, artifact, frontier),
        "revision.draft.create",
        actor=_actor("author"),
        surface="test",
    )
    draft = created["draft"]
    assert draft["source_execution_id"] == source.execution_id
    assert set(draft["project_binding"]) == {
        "project_id",
        "worktree_id",
        "root_identity",
        "source_commit",
    }
    assert draft["requested_by"] == {"subject": "author"}
    assert created["validation"] is None

    read = submit_revision_request(
        store,
        {
            "type": "revision.draft",
            "action": "revision.draft.get",
            "execution_id": source.execution_id,
            "draft_id": draft["draft_id"],
        },
        "revision.draft.get",
        actor=_actor("author"),
        surface="test",
    )
    assert read["draft"] == draft

    validate = {
        "type": "revision.draft",
        "action": "revision.validate",
        "execution_id": source.execution_id,
        "draft_id": draft["draft_id"],
        "expected_draft_version": draft["draft_version"],
        "payload": {},
    }
    validated = submit_revision_request(
        store,
        validate,
        "revision.validate",
        actor=_actor("author"),
        surface="test",
    )
    validation = validated["validation"]
    assert validation is not None
    assert validation["draft_version"] == draft["draft_version"]
    assert validated["manifest"] is None

    approve = {
        "type": "revision.draft",
        "action": "revision.approve",
        "execution_id": source.execution_id,
        "draft_id": draft["draft_id"],
        "expected_draft_version": draft["draft_version"],
        "payload": {"validation_id": validation["validation_id"]},
    }
    approved = submit_revision_request(
        store,
        approve,
        "revision.approve",
        actor=_actor("reviewer"),
        surface="test",
    )
    approval = approved["approval"]
    assert approval is not None and approval["actor"] == {"subject": "reviewer"}

    publish = {
        "type": "revision.draft",
        "action": "revision.publish",
        "execution_id": source.execution_id,
        "draft_id": draft["draft_id"],
        "expected_draft_version": draft["draft_version"],
        "payload": {
            "validation_id": validation["validation_id"],
            "approval_id": approval["approval_id"],
        },
    }
    published = submit_revision_request(
        store,
        publish,
        "revision.publish",
        actor=_actor("publisher"),
        surface="test",
    )
    assert published["draft"]["status"] == "published"
    assert published["manifest"]["source_execution_id"] == source.execution_id
    audits = store.list_audit_events(source.execution_id, actor=_actor("author"))
    assert [event.action for event in audits[-5:]] == [
        "revision.draft.create",
        "revision.draft.get",
        "revision.validate",
        "revision.approve",
        "revision.publish",
    ]


def test_public_revision_rejects_spoofed_binding_inline_approval_and_stale_version(
    tmp_path,
):
    store, _control, source, checkpoint, frontier = _source(tmp_path)
    revisions = RevisionControlService(store)
    artifact = revisions.put_artifact(
        kind="program_artifact",
        content={"entrypoint": "workflow.revised", "source_hash": _hash("revised")},
    )
    command = _create_command(source, checkpoint, artifact, frontier)
    command["project_binding"] = {"project_id": "forged"}
    assert (
        validate_revision_request(command, "revision.draft.create") == "invalid_command"
    )
    del command["project_binding"]
    created = submit_revision_request(
        store,
        command,
        "revision.draft.create",
        actor=_actor("author"),
        surface="test",
    )
    draft = created["draft"]
    replace = {
        "type": "revision.draft",
        "action": "revision.draft.replace",
        "execution_id": source.execution_id,
        "draft_id": draft["draft_id"],
        "expected_draft_version": draft["draft_version"] + 1,
        "payload": {
            "changes": _change(artifact.artifact_ref),
            "frontier_mapping": _mapping(frontier),
        },
    }
    try:
        submit_revision_request(
            store,
            replace,
            "revision.draft.replace",
            actor=_actor("author"),
            surface="test",
        )
    except RevisionPublicError as exc:
        assert exc.code == "draft_version_conflict"
    else:
        raise AssertionError("stale draft version was accepted")
    assert (
        validate_revision_request(
            {
                "type": "revision.draft",
                "action": "revision.approve",
                "execution_id": source.execution_id,
                "draft_id": draft["draft_id"],
                "expected_draft_version": draft["draft_version"],
                "payload": {
                    "validation_id": "validation_x",
                    "approval": {"actor": "forged"},
                },
            },
            "revision.approve",
        )
        == "invalid_payload"
    )


def test_rest_and_websocket_actions_expose_only_the_revision_envelope(
    tmp_path, monkeypatch
):
    store, _control, source, checkpoint, frontier = _source(tmp_path)
    artifact = RevisionControlService(store).put_artifact(
        kind="program_artifact",
        content={"entrypoint": "workflow.revised", "source_hash": _hash("revised")},
    )
    monkeypatch.setattr(execution_package, "default_store", lambda: store)
    app = FastAPI()
    app.state.owner_auth = SimpleNamespace(authority=_actor("author"))
    from openprogram.webui.routes.lifecycle import register
    from openprogram.webui.ws_actions.runtime import ACTIONS

    register(app)
    assert set(ACTIONS) >= {
        "revision.draft.create",
        "revision.draft.get",
        "revision.draft.replace",
        "revision.draft.discard",
        "revision.validate",
        "revision.approve",
        "revision.publish",
    }
    response = TestClient(app).post(
        "/api/execution/revision/draft",
        json=_create_command(source, checkpoint, artifact, frontier),
    )
    assert response.status_code == 200
    body = response.json()
    draft = body["draft"]
    response = TestClient(app).get(
        f"/api/execution/{source.execution_id}/revision/draft/{draft['draft_id']}",
    )
    assert response.status_code == 200
    assert response.json()["draft"]["draft_id"] == draft["draft_id"]

    class WebSocket:
        scope = {"state": {"authority": _actor("author")}}

        def __init__(self):
            self.frames: list[str] = []

        async def send_text(self, value: str) -> None:
            self.frames.append(value)

    ws = WebSocket()
    asyncio.run(
        ACTIONS["revision.draft.get"](
            ws,
            {
                "type": "revision.draft",
                "action": "revision.draft.get",
                "execution_id": source.execution_id,
                "draft_id": draft["draft_id"],
            },
        )
    )
    assert json.loads(ws.frames[0])["draft"]["draft_id"] == draft["draft_id"]
