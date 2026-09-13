from __future__ import annotations

import asyncio
from datetime import datetime
import json

import pytest
from starlette.websockets import WebSocketDisconnect

from openprogram.agent.authority import owner_authority
from openprogram.webui import server, ws_errors
from openprogram.webui.user_errors import UserErrorStore


class _SequenceWS:
    def __init__(self, incoming: list[object]) -> None:
        self.incoming = list(incoming)
        self.sent: list[dict] = []
        self.scope = {
            "state": {
                "authority": owner_authority("owner/install/0123456789abcdef"),
            },
        }

    async def accept(self) -> None:
        pass

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def receive_text(self) -> str:
        item = self.incoming.pop(0)
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, str)
        return item


class _FailOperationErrorWS(_SequenceWS):
    def __init__(self, incoming: list[object]) -> None:
        super().__init__(incoming)
        self.failed_frame: dict | None = None
        self.failed_frames: list[dict] = []

    async def send_text(self, text: str) -> None:
        frame = json.loads(text)
        if frame.get("type") == "operation_error":
            self.failed_frame = frame
            self.failed_frames.append(frame)
            raise RuntimeError("simulated transport failure")
        self.sent.append(frame)


class _ClosableWS(_SequenceWS):
    def __init__(self, incoming: list[object]) -> None:
        super().__init__(incoming)
        self.closed: tuple[int, str] | None = None

    async def close(self, *, code: int, reason: str) -> None:
        self.closed = (code, reason)


@pytest.fixture(autouse=True)
def isolated_websocket_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(server, "_ws_connections", [])
    monkeypatch.setattr(server, "_discover_functions", lambda: [])
    monkeypatch.setattr(server, "_get_provider_info", lambda: {})


@pytest.fixture
def user_error_store(tmp_path, monkeypatch: pytest.MonkeyPatch) -> UserErrorStore:
    store = UserErrorStore(tmp_path / "user_errors.db")
    monkeypatch.setattr(ws_errors, "default_user_error_store", lambda: store)
    return store


def _assert_final_error_data(data: dict) -> None:
    assert set(data) == {
        "error_id",
        "request_id",
        "scope",
        "code",
        "message",
        "action",
        "session_id",
        "operation_id",
        "retryable",
        "severity",
        "correlation_id",
        "occurred_at",
    }
    assert data["error_id"].startswith("err_")
    assert data["correlation_id"].startswith("corr_")
    parsed = datetime.fromisoformat(data["occurred_at"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def test_handler_failure_persists_final_operation_error_and_correlates_log(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    user_error_store: UserErrorStore,
) -> None:
    async def fail(ws, cmd) -> None:
        raise RuntimeError("private exception detail")

    monkeypatch.setattr(server, "WS_ACTIONS", {"bad": fail})
    ws = _SequenceWS([
        json.dumps({
            "action": "bad",
            "session_id": "session-1",
            "request_id": "request-1",
            "credential": "do-not-store",
        }),
        json.dumps({
            "action": "bad",
            "session_id": "session-1",
            "request_id": "request-2",
        }),
        "ping",
        WebSocketDisconnect(1000),
    ])

    asyncio.run(server._websocket_handler(ws))

    frames = [frame for frame in ws.sent if frame["type"] == "operation_error"]
    assert len(frames) == 2
    first, second = (frame["data"] for frame in frames)
    _assert_final_error_data(first)
    _assert_final_error_data(second)
    assert first["error_id"] != second["error_id"]
    assert first["correlation_id"] != second["correlation_id"]
    assert first["scope"] == "session"
    assert first["action"] == "bad"
    assert first["operation_id"] is None
    assert first["severity"] == "error"
    assert ws.sent[-1] == {"type": "pong"}

    principal_id = ws_errors.principal_id_for_websocket(ws)
    persisted = UserErrorStore(user_error_store.path).get(
        principal_id,
        first["error_id"],
    )
    assert persisted is not None
    assert persisted.wire_data() == first
    assert first["correlation_id"] in caplog.text
    serialized = json.dumps({"wire": frames, "stored": persisted.wire_data()})
    assert "private exception detail" not in serialized
    assert "do-not-store" not in serialized


def test_operation_error_is_persisted_before_transport_send_failure(
    monkeypatch: pytest.MonkeyPatch,
    user_error_store: UserErrorStore,
) -> None:
    async def fail(ws, cmd) -> None:
        raise RuntimeError("private exception detail")

    monkeypatch.setattr(server, "WS_ACTIONS", {"bad": fail})
    ws = _FailOperationErrorWS([
        json.dumps({
            "action": "bad",
            "session_id": "session-1",
            "request_id": "request-1",
        }),
    ])

    asyncio.run(server._websocket_handler(ws))

    assert ws.failed_frame is not None
    data = ws.failed_frame["data"]
    _assert_final_error_data(data)
    principal_id = ws_errors.principal_id_for_websocket(ws)
    reopened = UserErrorStore(user_error_store.path)
    assert reopened.get(principal_id, data["error_id"]) is not None


def test_unknown_action_transport_failure_does_not_record_second_error(
    user_error_store: UserErrorStore,
) -> None:
    ws = _FailOperationErrorWS([
        json.dumps({
            "action": "not_registered",
            "request_id": "request-1",
        }),
    ])

    asyncio.run(server._websocket_handler(ws))

    principal_id = ws_errors.principal_id_for_websocket(ws)
    records = UserErrorStore(user_error_store.path).list_open(principal_id).records
    assert len(ws.failed_frames) == 1
    assert len(records) == 1
    assert records[0].code == "unknown_action"


def test_persistence_failure_closes_without_sending_unrecorded_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingStore:
        def record(self, record, *, now=None) -> None:
            raise OSError("simulated persistence failure")

    async def fail(ws, cmd) -> None:
        raise RuntimeError("private exception detail")

    monkeypatch.setattr(server, "WS_ACTIONS", {"bad": fail})
    monkeypatch.setattr(
        ws_errors,
        "default_user_error_store",
        lambda: _FailingStore(),
    )
    ws = _ClosableWS([
        json.dumps({
            "action": "bad",
            "session_id": "session-1",
            "request_id": "request-1",
        }),
    ])

    asyncio.run(server._websocket_handler(ws))

    assert ws.closed == (1011, "state_recovery_required")
    assert not any(frame.get("type") == "operation_error" for frame in ws.sent)


@pytest.mark.parametrize(
    "scope",
    [
        {},
        {"state": {"authority": {"principal_id": "owner/install/not-valid"}}},
        {
            "state": {
                "authority": owner_authority("owner/install/0123456789abcdef")
                | {"authority_tier": "paired"},
            },
        },
        {
            "state": {
                "authority": owner_authority("owner/install/0123456789abcdef")
                | {"speaker_kind": "attacker"},
            },
        },
    ],
)
def test_principal_id_requires_valid_authenticated_owner(scope: dict) -> None:
    ws = _SequenceWS([])
    ws.scope = scope

    with pytest.raises(PermissionError):
        ws_errors.principal_id_for_websocket(ws)


@pytest.mark.parametrize(
    ("kwargs", "error_type"),
    [
        ({"code": None}, ValueError),
        ({"code": "Not Safe"}, ValueError),
        ({"code": "handler_error", "scope": "project"}, ValueError),
        ({"code": "handler_error", "severity": "critical"}, ValueError),
        ({"code": "handler_error", "retryable": "yes"}, TypeError),
        ({"code": "handler_error", "occurred_at_epoch": float("inf")}, ValueError),
    ],
)
def test_operation_error_builder_rejects_invalid_contract_fields(
    kwargs: dict,
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type):
        ws_errors.operation_error_frame({}, **kwargs)


def test_operation_error_persistence_rejects_tampered_frame(tmp_path) -> None:
    ws = _SequenceWS([])
    frame = ws_errors.operation_error_frame({}, code="handler_error")
    frame["data"]["retryable"] = "false"

    with pytest.raises(ValueError, match="retryable"):
        ws_errors.persist_operation_error_frame(
            ws,
            frame,
            store=UserErrorStore(tmp_path / "user_errors.db"),
        )
