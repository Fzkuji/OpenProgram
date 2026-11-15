"""Agent-specific durable safe-point errors."""
from .store import ExecutionConflict


class AgentSafePointConflict(ExecutionConflict):
    pass
