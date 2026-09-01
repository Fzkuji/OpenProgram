from __future__ import annotations

from dataclasses import dataclass

import pytest

from openprogram.execution.driver import (
    DriverAck,
    DriverBinding,
    DriverRegistry,
    DriverRegistryConflict,
    RuntimeSnapshot,
    TerminationReceipt,
)
from openprogram.execution.model import CapabilitySet


@dataclass
class Handle:
    name: str


class Driver:
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            pause=True,
            safe_point_kinds=("action.after",),
            state_schema_version=1,
        )

    async def activate(self, attempt, checkpoint):
        return Handle(attempt.attempt_id)

    async def request_pause(self, handle, command_id: str) -> DriverAck:
        return DriverAck(command_id=command_id, attempt_id=handle.name)

    async def request_cancel(self, handle, command_id: str) -> DriverAck:
        return DriverAck(command_id=command_id, attempt_id=handle.name)

    async def inspect(self, handle) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            attempt_id=handle.name,
            state_schema_version=1,
            safe_point_kind="action.after",
            state={"phase": "ready"},
        )

    async def terminate(self, handle, reason: str) -> TerminationReceipt:
        return TerminationReceipt(
            attempt_id=handle.name,
            terminated=True,
            reason=reason,
        )


def _binding(*, attempt_id: str = "attempt_1", generation: int = 1):
    return DriverBinding(
        execution_id="exec_1",
        attempt_id=attempt_id,
        generation=generation,
        driver=Driver(),
        handle=Handle(attempt_id),
    )


def test_registry_resolves_only_the_exact_attempt_generation() -> None:
    registry = DriverRegistry()
    binding = _binding()
    registry.bind(binding)

    assert registry.resolve("exec_1", attempt_id="attempt_1", generation=1) is binding
    with pytest.raises(DriverRegistryConflict) as stale_attempt:
        registry.resolve("exec_1", attempt_id="attempt_old", generation=1)
    assert stale_attempt.value.code == "stale_binding"
    with pytest.raises(DriverRegistryConflict) as stale_generation:
        registry.resolve("exec_1", attempt_id="attempt_1", generation=2)
    assert stale_generation.value.code == "stale_binding"


def test_stale_unbind_cannot_remove_a_new_owner() -> None:
    registry = DriverRegistry()
    first = _binding()
    registry.bind(first)
    assert registry.unbind("exec_1", attempt_id="attempt_1", generation=1)

    second = _binding(attempt_id="attempt_2", generation=2)
    registry.bind(second)
    assert not registry.unbind("exec_1", attempt_id="attempt_1", generation=1)
    assert registry.resolve("exec_1") is second


def test_registry_rejects_two_live_owners_for_one_execution() -> None:
    registry = DriverRegistry()
    registry.bind(_binding())

    with pytest.raises(DriverRegistryConflict) as occupied:
        registry.bind(_binding(attempt_id="attempt_2", generation=2))
    assert occupied.value.code == "owner_exists"


def test_same_binding_registration_is_idempotent() -> None:
    registry = DriverRegistry()
    binding = _binding()

    assert registry.bind(binding) is binding
    assert registry.bind(binding) is binding
    assert len(registry.snapshot()) == 1
