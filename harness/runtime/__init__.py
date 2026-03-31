"""
Runtime — the execution environment for Functions.

Executes Functions according to their Scope settings:

    Scope.isolated()  → fresh Session, no context
    Scope.chained()   → sees sibling I/O summaries (separate Sessions)
    Scope.full()      → shares Session with siblings (full reasoning visible)
    Custom Scope(...)  → any combination of depth/detail/peer

Session lifecycle is determined by scope.shares_session:
    - True  → siblings share a Session (peer="full")
    - False → each Function gets its own Session
"""

from __future__ import annotations

import asyncio
import json
from typing import Callable, TypeVar, Optional
from pydantic import BaseModel

from harness.function import Function
from harness.session import Session
from harness.scope import Scope
from harness.memory import Memory

T = TypeVar("T", bound=BaseModel)


class Runtime:
    """
    The execution environment for Functions.

    Args:
        session_factory:  Creates a new Session when needed.
    """

    def __init__(self, session_factory: Callable[[], Session], memory: Memory = None):
        self._session_factory = session_factory
        self.memory = memory

    # ------------------------------------------------------------------
    # Single execution (always isolated)
    # ------------------------------------------------------------------

    def execute(self, function: Function, context: dict) -> T:
        """Execute a single Function in a fresh Session."""
        session = self._session_factory()

        if self.memory:
            import time
            start = time.time()
            scope_str = str(function.scope) if function.scope else None
            session_type = type(session).__name__
            self.memory.log_function_call(
                function.name,
                params=function._extract_arguments(context),
                scope=scope_str,
                session_type=session_type,
            )

        try:
            result = function.call(session=session, context=context)
            if self.memory:
                self.memory.log_function_return(
                    function.name,
                    result=result.model_dump(),
                    status="success",
                    duration_ms=(time.time() - start) * 1000,
                )
            return result
        except Exception as e:
            if self.memory:
                self.memory.log_function_return(
                    function.name,
                    status="error",
                    error=str(e),
                    duration_ms=(time.time() - start) * 1000,
                )
            raise

    # ------------------------------------------------------------------
    # Chain execution (respects Scope)
    # ------------------------------------------------------------------

    def execute_chain(
        self,
        functions: list[Function],
        context: dict,
    ) -> list:
        """
        Execute a sequence of Functions, respecting each Function's Scope.

        How Scope affects execution:

        scope.shares_session (peer="full"):
            → Shares a Session with other "full" peers
            → Can see complete reasoning of prior siblings
            → KV cache prefix preserved (append-only)

        scope.needs_peers and not shares_session (peer="io"):
            → Gets its own Session
            → Receives I/O summaries of prior siblings as context

        not scope.needs_peers (peer="none"):
            → Gets its own Session
            → No information from siblings

        scope.needs_call_stack (depth > 0):
            → Receives call stack info in context

        Args:
            functions:  Ordered list of Functions
            context:    Initial context

        Returns:
            List of results in order. FunctionError on failure (stops chain).
        """
        from harness.function import FunctionError

        shared_session = None       # for peer="full" Functions
        peer_summaries = []         # I/O summaries for peer="io" Functions
        results = []

        for fn in functions:
            scope = fn.scope

            # Build the context this Function will see
            fn_context = self._build_scoped_context(fn, context, peer_summaries)

            try:
                if scope.shares_session:
                    # peer="full" → share Session (append-only, prefix preserved)
                    if shared_session is None:
                        shared_session = self._session_factory()
                    result = fn.call(session=shared_session, context=fn_context)

                else:
                    # peer="io" or peer="none" → own Session
                    session = self._session_factory()
                    result = fn.call(session=session, context=fn_context)

                result_dict = result.model_dump()
                context[fn.name] = result_dict
                results.append(result)

                # Record I/O summary for subsequent peers
                peer_summaries.append({
                    "function": fn.name,
                    "input_params": fn.params,
                    "output": result_dict,
                })

            except FunctionError as e:
                results.append(e)
                break

        return results

    # ------------------------------------------------------------------
    # Context building based on Scope
    # ------------------------------------------------------------------

    def _build_scoped_context(
        self,
        function: Function,
        context: dict,
        peer_summaries: list,
    ) -> dict:
        """
        Build the context a Function sees, based on its Scope.

        Extracts:
            - The Function's declared params from context
            - Call stack info (if scope.depth > 0)
            - Peer summaries (if scope.peer != "none")
        """
        scope = function.scope

        # Start with the Function's declared params
        if function.params is not None:
            fn_context = {k: context[k] for k in function.params if k in context}
        else:
            fn_context = dict(context)

        # Always include task
        if "task" in context:
            fn_context["task"] = context["task"]

        # Add call stack (depth)
        if scope.needs_call_stack and "_call_stack" in context:
            stack = context["_call_stack"]
            if scope.depth == -1:
                fn_context["_call_stack"] = stack
            else:
                fn_context["_call_stack"] = stack[-scope.depth:]

        # Add peer info (injected regardless of params — framework-level context)
        if scope.needs_peers and peer_summaries:
            if scope.peer == "io":
                fn_context["_prior_results"] = peer_summaries
            # peer="full" doesn't need explicit summaries —
            # the shared Session already has the full conversation

        # Add call stack (injected regardless of params)
        # Already handled above

        return fn_context

    # ------------------------------------------------------------------
    # Async
    # ------------------------------------------------------------------

    async def execute_async(self, function: Function, context: dict) -> T:
        """Async version of execute()."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.execute, function, context)

    async def execute_parallel(self, calls: list[tuple[Function, dict]]) -> list:
        """Execute multiple Functions concurrently, each isolated."""
        tasks = [self.execute_async(fn, ctx) for fn, ctx in calls]
        return await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @staticmethod
    def from_session_class(session_class: type, **kwargs) -> "Runtime":
        """Create a Runtime from a Session class and constructor args."""
        return Runtime(session_factory=lambda: session_class(**kwargs))
