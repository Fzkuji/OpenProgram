"""
Runtime — the execution environment for Functions.

Like Python's interpreter: it runs Functions and returns typed results.
Each execution gets a fresh Session (context isolation).

Two execution modes:
    - execute()           synchronous, one Function at a time
    - execute_async()     async, for non-blocking execution
    - execute_parallel()  async, multiple Functions concurrently

Parallel execution creates independent Sessions for each Function,
so there are no shared-state issues — like running separate processes.
"""

from __future__ import annotations

import asyncio
from typing import Callable, TypeVar, Optional
from pydantic import BaseModel

from harness.function import Function
from harness.session import Session

T = TypeVar("T", bound=BaseModel)


class Runtime:
    """
    The execution environment for Functions.

    Each execution gets a fresh Session (context isolation).

    Args:
        session_factory:  Creates a new Session for each execution.
    """

    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory

    # --- Synchronous ---

    def execute(self, function: Function, context: dict) -> T:
        """
        Execute a Function in an isolated Session (synchronous).

        Creates Session → runs Function → returns result → destroys Session.
        """
        session = self._session_factory()
        return function.call(session=session, context=context)

    # --- Async ---

    async def execute_async(self, function: Function, context: dict) -> T:
        """
        Execute a Function in an isolated Session (async).

        Same as execute(), but runs in a thread pool to avoid blocking
        the event loop. Useful when the Session's send() is synchronous
        but you want concurrent execution.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,  # default thread pool
            self.execute,
            function,
            context,
        )

    # --- Parallel ---

    async def execute_parallel(
        self,
        calls: list[tuple[Function, dict]],
    ) -> list:
        """
        Execute multiple Functions concurrently, each in its own Session.

        Like multiprocessing.Pool — each call is fully isolated.
        No shared state, no race conditions.

        Args:
            calls:  List of (Function, context) tuples

        Returns:
            List of results in the same order as calls.
            Each result is either a typed Pydantic model or a FunctionError.

        Example:
            results = await runtime.execute_parallel([
                (observe, {"task": "check screen A"}),
                (observe, {"task": "check screen B"}),
                (observe, {"task": "check screen C"}),
            ])
        """
        from harness.function import FunctionError

        tasks = [
            self.execute_async(fn, ctx)
            for fn, ctx in calls
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        return results

    # --- Convenience ---

    @staticmethod
    def from_session_class(session_class: type, **kwargs) -> "Runtime":
        """
        Create a Runtime from a Session class and constructor args.

        Example:
            runtime = Runtime.from_session_class(AnthropicSession, model="claude-haiku")
        """
        return Runtime(session_factory=lambda: session_class(**kwargs))
