from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import re
import time

import pytest
from starlette.websockets import WebSocketDisconnect

from openprogram.agent.authority import owner_authority
from openprogram.webui import server, ws_errors
from openprogram.webui.user_errors import UserErrorRecord, UserErrorStore
from openprogram.webui.ws_actions import user_error as user_error_actions


OWNER_ID = "owner/install/0123456789abcdef"
OTHER_OWNER_ID = "owner/install/fedcba9876543210"


class _SequenceWS:
    def __init__(
        self,
        incoming: list[object],
        *,
        principal_id: str = OWNER_ID,
    ) -> None:
        self.incoming = list(incoming)
        self.sent: list[dict] = []
        self.scope = {
            "state": {"authority": owner_authority(principal_id)},
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


class _HeldWS(_SequenceWS):
    def __init__(self, *, principal_id: str) -> None:
        super().__init__([], principal_id=principal_id)
        self.receiving = asyncio.Event()
        self.release = asyncio.Event()

    async def receive_text(self) -> str:
        self.receiving.set()
        await self.release.wait()
        raise WebSocketDisconnect(1000)


class _FailingSendWS(_SequenceWS):
    async def send_text(self, text: str) -> None:
        if len(self.sent) >= 2:
            raise WebSocketDisconnect(1013)
        await super().send_text(text)


@pytest.fixture(autouse=True)
def isolated_websocket_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(server, "_ws_connections", [])
    monkeypatch.setattr(server, "_discover_functions", lambda: [])
    monkeypatch.setattr(server, "_get_provider_info", lambda: {})


@pytest.fixture
def user_error_store(tmp_path, monkeypatch: pytest.MonkeyPatch) -> UserErrorStore:
    store = UserErrorStore(tmp_path / "user_errors.db")
    monkeypatch.setattr(ws_errors, "default_user_error_store", lambda: store)
    monkeypatch.setattr(
        user_error_actions,
        "default_user_error_store",
        lambda: store,
    )
    return store


def _timestamp(epoch: float) -> str:
    return (
        datetime.fromtimestamp(epoch, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _record(
    index: int,
    occurred_at_epoch: float,
    *,
    principal_id: str = OWNER_ID,
) -> UserErrorRecord:
    return UserErrorRecord(
        principal_id=principal_id,
        error_id=f"err_{index:032x}",
        request_id=f"request-source-{index}",
        scope="session",
        code="handler_error",
        message="Action failed",
        action="chat",
        session_id="session-1",
        operation_id="operation-1",
        retryable=False,
        severity="error",
        correlation_id=f"corr_{index:032x}",
        occurred_at=_timestamp(occurred_at_epoch),
        occurred_at_epoch=occurred_at_epoch,
    )


def _frames(ws: _SequenceWS, frame_type: str) -> list[dict]:
    return [frame for frame in ws.sent if frame.get("type") == frame_type]


def _assert_final_invalid_request(
    frame: dict,
    *,
    action: str | None,
) -> None:
    assert frame["type"] == "operation_error"
    data = frame["data"]
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
    assert re.fullmatch(r"err_[0-9a-f]{32}", data["error_id"])
    assert re.fullmatch(r"corr_[0-9a-f]{32}", data["correlation_id"])
    assert data["action"] == action
    assert data["code"] == "invalid_request"
    assert data["retryable"] is False
    assert data["severity"] == "error"
    occurred_at = datetime.fromisoformat(data["occurred_at"].replace("Z", "+00:00"))
    assert occurred_at.utcoffset() == timedelta(0)


def test_list_user_errors_is_owner_scoped_open_and_strictly_paginated(
    user_error_store: UserErrorStore,
) -> None:
    now = time.time()
    owner_records = tuple(_record(i, now - 4 + i) for i in range(1, 4))
    closed = replace(
        _record(4, now),
        closed_at=_timestamp(now + 1),
        close_reason="acknowledged",
    )
    other = _record(5, now + 1, principal_id=OTHER_OWNER_ID)
    for record in (*owner_records, closed, other):
        user_error_store.record(record, now=now + 2)

    expected_first = user_error_store.list_open(OWNER_ID, limit=2, now=now + 2)
    assert expected_first.next_cursor is not None
    expected_second = user_error_store.list_open(
        OWNER_ID,
        cursor=expected_first.next_cursor,
        limit=2,
        now=now + 2,
    )
    ws = _SequenceWS(
        [
            json.dumps(
                {
                    "action": "list_user_errors",
                    "request_id": "request-list-1",
                    "limit": 2,
                }
            ),
            json.dumps(
                {
                    "action": "list_user_errors",
                    "request_id": "request-list-2",
                    "cursor": expected_first.next_cursor,
                    "limit": 2,
                }
            ),
            "ping",
            WebSocketDisconnect(1000),
        ]
    )

    asyncio.run(server._websocket_handler(ws))

    pages = _frames(ws, "user_errors_list")
    assert len(pages) == 2
    assert pages[0] == {
        "type": "user_errors_list",
        "data": {
            "request_id": "request-list-1",
            "errors": [record.wire_data() for record in expected_first.records],
            "next_cursor": expected_first.next_cursor,
        },
    }
    assert pages[1] == {
        "type": "user_errors_list",
        "data": {
            "request_id": "request-list-2",
            "errors": [record.wire_data() for record in expected_second.records],
            "next_cursor": None,
        },
    }
    assert [row["error_id"] for row in pages[0]["data"]["errors"]] == [
        owner_records[2].error_id,
        owner_records[1].error_id,
    ]
    serialized = json.dumps(pages)
    assert "principal_id" not in serialized
    assert closed.error_id not in serialized
    assert other.error_id not in serialized
    assert ws.sent[-1] == {"type": "pong"}


def test_acknowledge_user_error_is_idempotent_and_broadcasts_by_principal(
    user_error_store: UserErrorStore,
) -> None:
    now = time.time()
    target = _record(10, now)
    sibling = _record(11, now + 1)
    user_error_store.record(target, now=now + 2)
    user_error_store.record(sibling, now=now + 2)
    unknown_id = "err_ffffffffffffffffffffffffffffffff"

    async def exercise() -> tuple[_SequenceWS, _HeldWS, _HeldWS]:
        owner_observer = _HeldWS(principal_id=OWNER_ID)
        other_observer = _HeldWS(principal_id=OTHER_OWNER_ID)
        owner_task = asyncio.create_task(server._websocket_handler(owner_observer))
        other_task = asyncio.create_task(server._websocket_handler(other_observer))
        await owner_observer.receiving.wait()
        await other_observer.receiving.wait()

        acknowledger = _SequenceWS(
            [
                json.dumps(
                    {
                        "action": "acknowledge_user_error",
                        "request_id": "request-ack-1",
                        "error_id": target.error_id,
                    }
                ),
                json.dumps(
                    {
                        "action": "acknowledge_user_error",
                        "request_id": "request-ack-2",
                        "error_id": target.error_id,
                    }
                ),
                json.dumps(
                    {
                        "action": "acknowledge_user_error",
                        "request_id": "request-ack-3",
                        "error_id": unknown_id,
                    }
                ),
                "ping",
                WebSocketDisconnect(1000),
            ]
        )
        try:
            await server._websocket_handler(acknowledger)
            for _ in range(5):
                await asyncio.sleep(0)
        finally:
            owner_observer.release.set()
            other_observer.release.set()
            await asyncio.gather(owner_task, other_task)
        return acknowledger, owner_observer, other_observer

    acknowledger, owner_observer, other_observer = asyncio.run(exercise())

    expected_keys = {"error_ids", "scope", "operation_id", "occurred_at"}
    acknowledger_recoveries = _frames(acknowledger, "operation_recovered")
    owner_recoveries = _frames(owner_observer, "operation_recovered")
    assert len(acknowledger_recoveries) == 3
    assert owner_recoveries == acknowledger_recoveries
    assert not _frames(other_observer, "operation_recovered")
    acknowledgements = _frames(acknowledger, "user_error_acknowledged")
    assert acknowledgements == [
        {
            "type": "user_error_acknowledged",
            "data": {
                "request_id": "request-ack-1",
                "error_id": target.error_id,
            },
        },
        {
            "type": "user_error_acknowledged",
            "data": {
                "request_id": "request-ack-2",
                "error_id": target.error_id,
            },
        },
        {
            "type": "user_error_acknowledged",
            "data": {
                "request_id": "request-ack-3",
                "error_id": unknown_id,
            },
        },
    ]
    assert not _frames(owner_observer, "user_error_acknowledged")
    assert not _frames(other_observer, "user_error_acknowledged")

    first, repeated, unknown = (frame["data"] for frame in acknowledger_recoveries)
    assert set(first) == expected_keys
    assert first["error_ids"] == [target.error_id]
    assert first["scope"] == target.scope
    assert first["operation_id"] == target.operation_id
    assert repeated["error_ids"] == [target.error_id]
    assert repeated["scope"] == target.scope
    assert repeated["operation_id"] == target.operation_id
    assert unknown["error_ids"] == [unknown_id]
    assert unknown["scope"] == "system"
    assert unknown["operation_id"] is None
    for recovery in (first, repeated, unknown):
        occurred_at = datetime.fromisoformat(
            recovery["occurred_at"].replace("Z", "+00:00")
        )
        assert occurred_at.utcoffset() == timedelta(0)

    closed = user_error_store.get(OWNER_ID, target.error_id, now=now + 10)
    assert closed is not None
    assert closed.close_reason == "acknowledged"
    assert closed.closed_at == first["occurred_at"]
    assert user_error_store.list_open(OWNER_ID, now=now + 10).records == (sibling,)
    assert {"type": "pong"} in acknowledger.sent


def test_acknowledge_send_failure_does_not_block_other_owner_windows(
    user_error_store: UserErrorStore,
) -> None:
    now = time.time()
    target = _record(12, now)
    user_error_store.record(target, now=now + 1)

    async def exercise() -> _HeldWS:
        owner_observer = _HeldWS(principal_id=OWNER_ID)
        observer_task = asyncio.create_task(
            server._websocket_handler(owner_observer)
        )
        await owner_observer.receiving.wait()
        acknowledger = _FailingSendWS(
            [
                json.dumps(
                    {
                        "action": "acknowledge_user_error",
                        "request_id": "request-ack-failed-send",
                        "error_id": target.error_id,
                    }
                )
            ]
        )
        try:
            await server._websocket_handler(acknowledger)
            for _ in range(5):
                await asyncio.sleep(0)
        finally:
            owner_observer.release.set()
            await observer_task
        return owner_observer

    owner_observer = asyncio.run(exercise())

    recoveries = _frames(owner_observer, "operation_recovered")
    assert len(recoveries) == 1
    assert recoveries[0]["data"]["error_ids"] == [target.error_id]
    closed = user_error_store.get(OWNER_ID, target.error_id, now=now + 2)
    assert closed is not None
    assert closed.close_reason == "acknowledged"


@pytest.mark.parametrize(
    ("command", "expected_action"),
    [
        ({"action": "list_user_errors", "limit": 20}, "list_user_errors"),
        (
            {
                "action": "list_user_errors",
                "request_id": {"unsafe": "value"},
                "limit": 20,
            },
            "list_user_errors",
        ),
        (
            {
                "action": "list_user_errors",
                "request_id": "request-invalid-cursor",
                "cursor": "not-decimal",
                "limit": 20,
            },
            "list_user_errors",
        ),
        (
            {
                "action": "list_user_errors",
                "request_id": "request-zero-cursor",
                "cursor": "0",
                "limit": 20,
            },
            "list_user_errors",
        ),
        (
            {
                "action": "list_user_errors",
                "request_id": "request-overflow-cursor",
                "cursor": (
                    "v1:0x1p+9999999999:"
                    "err_00000000000000000000000000000001"
                ),
                "limit": 20,
            },
            "list_user_errors",
        ),
        (
            {
                "action": "list_user_errors",
                "request_id": "request-invalid-limit",
                "limit": 0,
            },
            "list_user_errors",
        ),
        (
            {
                "action": "list_user_errors",
                "request_id": "request-large-limit",
                "limit": 101,
            },
            "list_user_errors",
        ),
        (
            {
                "action": "list_user_errors",
                "request_id": "request-string-limit",
                "limit": "20",
            },
            "list_user_errors",
        ),
        (
            {
                "action": "acknowledge_user_error",
                "request_id": "request-missing-error",
            },
            "acknowledge_user_error",
        ),
        (
            {
                "action": "acknowledge_user_error",
                "request_id": "request-invalid-error",
                "error_id": "err_not-canonical",
            },
            "acknowledge_user_error",
        ),
    ],
)
def test_user_error_recovery_actions_reject_invalid_request_and_keep_connection(
    user_error_store: UserErrorStore,
    command: dict,
    expected_action: str,
) -> None:
    ws = _SequenceWS(
        [
            json.dumps(command),
            "ping",
            WebSocketDisconnect(1000),
        ]
    )

    asyncio.run(server._websocket_handler(ws))

    errors = _frames(ws, "operation_error")
    assert len(errors) == 1
    _assert_final_invalid_request(errors[0], action=expected_action)
    assert ws.sent[-1] == {"type": "pong"}


@pytest.mark.parametrize("payload", ["null", "[]", "{malformed"])
def test_user_error_recovery_rejects_non_object_or_malformed_json_and_keeps_ping(
    user_error_store: UserErrorStore,
    payload: str,
) -> None:
    ws = _SequenceWS(
        [
            payload,
            "ping",
            WebSocketDisconnect(1000),
        ]
    )

    asyncio.run(server._websocket_handler(ws))

    errors = _frames(ws, "operation_error")
    assert len(errors) == 1
    _assert_final_invalid_request(errors[0], action=None)
    assert errors[0]["data"]["request_id"] is None
    assert ws.sent[-1] == {"type": "pong"}
