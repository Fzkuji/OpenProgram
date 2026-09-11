"""Physical application ownership in the canonical execution control registry."""
from openprogram.execution.driver import DriverAck, RuntimeSnapshot, TerminationReceipt
from openprogram.execution.model import CapabilitySet


class ApplicationDriver:
    def __init__(self, service):
        self.service = service

    def capabilities(self):
        return CapabilitySet()

    async def request_pause(self, handle, command_id):
        raise ValueError("Application Python stacks cannot be paused or resumed")

    async def request_cancel(self, handle, command_id):
        self.service.cancel_commands[handle.execution_id] = command_id
        await self.service.cancel(handle.execution_id)
        return DriverAck(command_id, handle.attempt_id)

    async def inspect(self, handle):
        task = self.service.tasks.get(handle.execution_id)
        return RuntimeSnapshot(handle.attempt_id, 0, None, {"done": task is None or task.done()})

    async def terminate(self, handle, reason):
        await self.service.cancel(handle.execution_id)
        process = self.service.processes.get(handle.execution_id)
        return TerminationReceipt(handle.attempt_id, process is None or process.returncode is not None, reason)
