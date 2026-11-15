"""Stop a canonical Agent turn after its admission, before it starts.

A stop that wins the race with a turn's run thread leaves
``CanonicalAgentAdapter.activate`` returning ``None``: the turn never ran.
This patch makes every activation in the test meet that stop first, through
the real control service.
"""
from __future__ import annotations


def stop_every_turn_before_it_starts(monkeypatch) -> list[str]:
    """Cancel each admitted turn just before activation; return the stopped ids."""
    from openprogram.runtime.conversation.production_driver.shared import CanonicalAgentEntry

    stopped: list[str] = []
    activate = CanonicalAgentEntry.activate

    async def stopped_first(self, admission):
        await self.control.request_cancel(
            command_id=f"stop-before-start-{admission.execution_id}",
            execution_id=admission.execution_id,
            expected_version=admission.status_version,
            actor={"surface": "test"},
            reason_code="cancel.user",
        )
        stopped.append(admission.execution_id)
        return await activate(self, admission)

    monkeypatch.setattr(CanonicalAgentEntry, "activate", stopped_first)
    return stopped
