"""B 类桥：AuthEvent → 统一 Event 的翻译与安装。"""
from __future__ import annotations

import pytest

from openprogram.agent import event_bridges
from openprogram.agent.event_bus import create_event_bus
from openprogram.agent.event_bridges import (
    install_event_bridges,
    translate_auth_event,
)
from openprogram.auth.types import AuthEvent, AuthEventType


@pytest.fixture(autouse=True)
def _reset_installed():
    event_bridges._installed = False
    yield
    event_bridges._installed = False


class _StubAuthStore:
    def __init__(self):
        self.listeners = []

    def subscribe(self, listener):
        self.listeners.append(listener)

    def fire(self, ev):
        for l in self.listeners:
            l(ev)


def test_translate_mapped_types():
    ev = translate_auth_event(AuthEvent(
        type=AuthEventType.POOL_MEMBER_COOLDOWN,
        provider_id="anthropic", profile_id="p1", credential_id="c1",
        detail={"until_ms": 123},
    ))
    assert ev is not None
    assert ev.type == "credential.cooldown"
    assert ev.origin == "system"
    assert ev.payload["provider"] == "anthropic"
    assert ev.payload["detail"] == {"until_ms": 123}

    assert translate_auth_event(
        AuthEvent(type=AuthEventType.POOL_ROTATED)
    ).type == "credential.rotated"
    assert translate_auth_event(
        AuthEvent(type=AuthEventType.POOL_EXHAUSTED)
    ).type == "credential.exhausted"


def test_unmapped_types_return_none():
    for t in (AuthEventType.LOGIN_STARTED, AuthEventType.PROFILE_CREATED,
              AuthEventType.POOL_MEMBER_ADDED):
        assert translate_auth_event(AuthEvent(type=t)) is None


def test_install_bridges_auth_to_bus():
    store, bus = _StubAuthStore(), create_event_bus()
    got = []
    bus.subscribe(got.append, types={"credential.cooldown"})

    assert install_event_bridges(auth_store=store, bus=bus) is True
    store.fire(AuthEvent(type=AuthEventType.POOL_MEMBER_COOLDOWN,
                         provider_id="x"))
    store.fire(AuthEvent(type=AuthEventType.LOGIN_STARTED))  # 不桥接

    assert len(got) == 1
    assert got[0].type == "credential.cooldown"
    assert got[0].payload["provider"] == "x"


def test_install_is_idempotent():
    store, bus = _StubAuthStore(), create_event_bus()
    assert install_event_bridges(auth_store=store, bus=bus) is True
    assert install_event_bridges(auth_store=store, bus=bus) is False
    assert len(store.listeners) == 1


def test_forward_swallows_bridge_errors():
    store = _StubAuthStore()

    class _BadBus:
        def emit(self, ev):
            raise RuntimeError("bus down")

    install_event_bridges(auth_store=store, bus=_BadBus())
    # 桥内部出错不能炸到 auth 的 emit 路径
    store.fire(AuthEvent(type=AuthEventType.POOL_EXHAUSTED))
