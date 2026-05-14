"""DagRuntime — LLM runtime built on the flat DAG context model.

This is the clean-slate runtime that uses ``context.nodes.Graph`` as the
sole source of truth. Each ``exec()`` call:

  1. Reads the node ids listed in ``reads``, renders each into a
     message.
  2. Appends the per-call ``content`` as the latest user-side input.
  3. Calls the provider with the assembled messages.
  4. Records a ModelCall node (with ``reads`` and the reply as ``output``)
     into the graph.
  5. If a ``GraphStore`` is attached, persists / commits the new node.

The caller is responsible for choosing ``reads``. Helpers in
``context.nodes`` (``last_user_message`` / ``linear_back_to`` /
``branch_terminals`` / ``fold_history`` / etc.) are convenience functions
that compute commonly-wanted ``reads`` lists.

Unlike the legacy ``Runtime``, DagRuntime does NOT walk a Context tree
to invent context, does NOT manipulate ``_current_ctx``, and does NOT
care about @agentic_function decorators. It is purely a thin wrapper:
"given a Graph state + this turn's input, ask the model".
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from openprogram.context.nodes import (
    Call,
    Graph,
)


# Provider call signature:
#   call(messages: list[dict], *, model: str, system: Optional[str], tools: Optional[list], **kwargs) -> str
ProviderCall = Callable[..., str]


class DagRuntime:
    """Runtime backed by a flat-DAG context model.

    Args:
        provider_call: a callable that takes messages + model + optional
                       system/tools, makes the LLM call, returns the
                       model's reply text. Examples: a wrapped OpenAI
                       client, an Anthropic SDK call, a mock for tests.
        graph:         an existing Graph to attach to. Defaults to a
                       fresh empty Graph.
        store:         optional GraphStore for persistence. If given,
                       each new node is appended + git-committed
                       immediately.
        default_model: model id to use when an ``exec()`` call omits one.
    """

    def __init__(
        self,
        provider_call: ProviderCall,
        *,
        graph: Optional[Graph] = None,
        store: Optional[Any] = None,
        default_model: str = "",
    ):
        self.provider_call = provider_call
        # Explicit None check — Graph has __len__, so an empty Graph
        # is falsy and `graph or Graph()` would silently create a new
        # one, decoupling the runtime's graph from the caller's.
        self.graph: Graph = graph if graph is not None else Graph()
        self.store = store
        self.default_model = default_model

    # ── Public API ────────────────────────────────────────────────────

    def add_user_message(self, content: str) -> Call:
        """Record a user-facing message and (if a store is bound) persist it."""
        node = self.graph.add_user_message(content)
        self._persist(node)
        return node

    def exec(
        self,
        content: list[dict],
        *,
        reads: list[str],
        model: Optional[str] = None,
        system: Optional[str] = None,
        tools: Optional[list] = None,
        **provider_kwargs: Any,
    ) -> str:
        """Issue one LLM call.

        Args:
            content: the per-call user-side input as a list of content
                     blocks. Most callers use a single
                     ``{"type": "text", "text": "..."}`` block. The list
                     mirrors the legacy Runtime.exec signature so
                     existing helpers that build content blocks keep
                     working.
            reads:   ids of nodes whose content should be included in
                     the prompt for this call. Becomes the
                     ``reads`` field of the recorded ModelCall.
            model:   override the runtime's default_model.
            system:  system prompt for this single call.
            tools:   optional tools list (passed to provider verbatim).
            **provider_kwargs: forwarded to provider_call.

        Returns:
            The model's reply text.
        """
        model = model or self.default_model
        if not model:
            raise ValueError("DagRuntime.exec needs a model (pass model= or set default_model)")
        # Validate reads up front so we fail before spending a token.
        unknown = [r for r in reads if r not in self.graph]
        if unknown:
            raise ValueError(f"exec(reads=...) contains unknown node ids: {unknown}")

        messages = self._build_messages(reads, content)
        reply = self.provider_call(
            messages=messages,
            model=model,
            system=system,
            tools=tools,
            **provider_kwargs,
        )
        node = self.graph.add_model_call(
            model=model,
            reads=list(reads),
            system_prompt=system,
            output=str(reply),
        )
        self._persist(node)
        return reply

    def record_function_call(
        self,
        *,
        function_name: str,
        arguments: dict,
        called_by: str,
        result: Any,
    ) -> Call:
        """Record a tool/function execution into the graph.

        The DagRuntime doesn't execute the function — the caller does
        (that's just normal Python). This is the "log it to the graph"
        step. Mirrors the lifecycle: ModelCall says "call X" → caller
        runs X → caller logs the result here.
        """
        node = self.graph.add_function_call(
            function_name=function_name,
            arguments=arguments,
            called_by=called_by,
            result=result,
        )
        self._persist(node)
        return node

    # ── Convenience accessors ────────────────────────────────────────

    def last_node_id(self) -> Optional[str]:
        return self.graph._last_id

    # ── Internals ────────────────────────────────────────────────────

    def _persist(self, node) -> None:
        if self.store is not None:
            self.store.append(node)

    def _build_messages(
        self,
        read_ids: list[str],
        content_blocks: list[dict],
    ) -> list[dict]:
        """Render ``reads`` into messages and append the per-call content.

        Each node is rendered to a single ``{"role": ..., "content": ...}``
        message in the order given. The per-call content blocks become
        the final user message (joining text blocks with newlines).
        """
        messages: list[dict] = []
        for rid in read_ids:
            messages.append(self._node_to_message(self.graph[rid]))
        if content_blocks:
            text_parts = [
                b.get("text", "")
                for b in content_blocks
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            text = "\n".join(p for p in text_parts if p)
            if text:
                messages.append({"role": "user", "content": text})
        return messages

    @staticmethod
    def _node_to_message(node: Call) -> dict:
        """Convert a graph node into a chat message for the provider call."""
        if node.is_user():
            return {"role": "user", "content": node.output or ""}
        if node.is_llm():
            return {"role": "assistant", "content": node.output or ""}
        if node.is_code():
            # Flatten code Call into a tool-style message. Providers
            # that want native tool_use can be plugged in later; this
            # default works for text-based providers.
            return {
                "role": "tool",
                "content": f"[{node.name} args={node.input}] -> {node.output}",
            }
        raise ValueError(f"Unknown Call role: {node.role!r}")


__all__ = ["DagRuntime", "ProviderCall"]
