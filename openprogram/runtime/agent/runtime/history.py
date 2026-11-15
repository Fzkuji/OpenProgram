"""Agentic runtime: history."""
from __future__ import annotations

import time









from typing import Optional


from .shared import _build_pi_context
import logging

_log = logging.getLogger(__name__)

class HistoryOperations:
    def _ensure_nested_tool_node(
        self,
        *,
        parent_node_id: str,
        tool_call_id: str,
        occurrence_id: str = "",
        tool_name: str,
        arguments=None,
        expose: str | None = None,
    ) -> str:
        """Create or find the DAG node referenced by a streamed tool block.

        Provider events arrive before the tool body returns. The node is
        therefore created here as a normal ``code`` Call, with the same stable
        id that an ``Agent method`` wrapper uses when it is the tool.
        This keeps ``tool_ref.ref_node_id`` valid during live rendering and
        after a reload without copying tool payloads into the stream.
        """
        if not parent_node_id or not tool_call_id:
            return ""
        try:
            from openprogram.state.store import _store
            from openprogram.state.store.session.nodes import Call, ROLE_CODE
            from openprogram.runtime.agent.call_state import tool_node_id

            # ``hidden`` means no DAG node and therefore no persisted input or
            # output. This decision is made before loading or appending so a
            # stream event cannot create a full-exposure placeholder first.
            dag_expose = str(expose or "full")
            if dag_expose == "hidden":
                return ""
            identity = str(occurrence_id or tool_call_id)

            store = _store.get()
            if store is None:
                return ""
            graph = store.load()
            for node in graph.nodes.values():
                metadata = node.metadata or {}
                if (
                    node.caller == parent_node_id
                    and (
                        metadata.get("tool_call_occurrence_id") == identity
                        if occurrence_id
                        else metadata.get("tool_call_id") == tool_call_id
                    )
                ):
                    return node.id
            node_id = tool_node_id(parent_node_id, identity)
            if node_id in graph.nodes:
                return node_id
            args = arguments if arguments is not None else {}
            store.append(
                Call(
                    id=node_id,
                    role=ROLE_CODE,
                    name=tool_name or "tool",
                    input=args,
                    output=None,
                    caller=parent_node_id,
                    metadata={
                        "status": "running",
                        "expose": dag_expose,
                        "tool_call_id": tool_call_id,
                        "tool_call_occurrence_id": identity,
                        "source": "nested_llm_tool",
                        "started_at": time.time(),
                    },
                )
            )
            return node_id
        except Exception:
            return ""

    def _finish_nested_tool_node(
        self,
        *,
        parent_node_id: str,
        tool_call_id: str,
        occurrence_id: str = "",
        node_id: str = "",
        result=None,
        is_error: bool = False,
        outcome: str | None = None,
        details: dict | None = None,
    ) -> None:
        """Persist the terminal state of a streamed nested tool node.

        ``details`` are the result's durable details (``shared.tool_loads``),
        kept in the node's metadata for rendering history."""
        if not parent_node_id or not tool_call_id:
            return
        try:
            from openprogram.state.store import _store
            from openprogram.runtime.agent.call_state import tool_node_id

            store = _store.get()
            if store is None:
                return
            identity = str(occurrence_id or tool_call_id)
            resolved_id = node_id or tool_node_id(parent_node_id, identity)
            node = store.load().nodes.get(resolved_id)
            if node is None:
                return
            current = node.metadata or {}
            current_status = current.get("status")
            # An Agent method wrapper may have already recorded the
            # actual return value. Do not replace it with the provider's
            # envelope after it became terminal.
            if current_status in {"completed", "error", "cancelled"}:
                return
            status = (
                "cancelled" if outcome == "cancelled"
                else "error" if is_error or outcome == "failed"
                else "pending" if outcome == "not_started"
                else "completed"
            )
            output = result if status != "pending" else None
            metadata = {
                "status": status,
                "tool_call_id": tool_call_id,
                "tool_call_occurrence_id": identity,
                "outcome": outcome or status,
                "is_error": bool(is_error),
                "ended_at": time.time(),
            }
            if details:
                metadata["details"] = details
            store.update(resolved_id, output=output, metadata=metadata)
        except Exception:
            return

    def _skills_key(self) -> object:
        """Normalize the constructor's ``skills`` argument into a cache key.

        None / False → ``()``. True → ``True`` (the four external sources).
        list → the tuple of directories, read instead of those sources.
        """
        cfg = self._skills_config
        if cfg is True:
            return True
        if isinstance(cfg, (list, tuple)) and cfg:
            return tuple(str(d) for d in cfg)
        return ()


    def _skills_block(self) -> str:
        """Return the ``<available_skills>`` XML block for this runtime.

        Same loader and same renderer as the chat path's ``capabilities``
        component (openprogram/extensions/skills/loader.py) — one registry, one
        listing format. Cached per config so repeat exec() calls don't
        rescan unless it changes. Empty string when skills are disabled or
        no SKILL.md files were found, so callers can concatenate freely.
        """
        key = self._skills_key()
        if self._skills_cache_key == key:
            return self._skills_prompt_block
        from openprogram.extensions.skills import (
            format_skills_for_prompt,
            list_skills,
            load_skills,
        )

        if key is True:
            skills = list_skills()
        elif key:
            skills = load_skills(key)
        else:
            skills = []
        self._skills_prompt_block = format_skills_for_prompt(skills)
        self._skills_cache_key = key
        return self._skills_prompt_block


    def _render_history_messages(self, content, context=None, model=None) -> Optional[list]:
        """Build the provider message list for an in-progress exec()
        from the DAG.

        Source of truth: the ``_store`` ContextVar set by the dispatcher
        at turn entry (``openprogram.runtime.context.storage._store``). When no
        store is installed (standalone scripts, tests without the
        dispatcher), returns ``None`` so the caller falls back to the
        tree-Context render path.

        Algorithm:
          1. Load the DAG state from the store.
          2. Read the enclosing ``Agent method`` call id from
             ``_call_id`` ContextVar; pull its node from the graph to
             get seq + render_range.
          3. Compute reads → render pi-ai messages.
          4. Append a fresh UserMessage built from ``content``.

        ``model`` is the model the request goes to: earlier replies it wrote
        keep their reasoning (``render_dag_messages(reasoning_for=...)``).
        """
        from openprogram.state.store import _store
        from openprogram.runtime.context.model import Context

        context = context if context is not None else Context.current()
        if context is None:
            context = Context()
        store = _store.get()
        if store is None:
            return None

        try:
            from openprogram.runtime.context.nodes import render_context
            from openprogram.runtime.context.render import render_dag_messages
            from openprogram.runtime.context.scope import _call_id

            graph = store.load()
            frame_node_id = _call_id.get() or context.call_id

            frame_entry_seq = -1
            render_range = None
            if frame_node_id and frame_node_id in graph.nodes:
                frame_node = graph.nodes[frame_node_id]
                frame_entry_seq = frame_node.seq
                render_range = (frame_node.metadata or {}).get("render_range")

            # §6 head: the frame's nearest ROOT-level ancestor along
            # ``caller``. Its predecessor chain is the pre-frame history
            # the function may see; the frame's own progress rides in as
            # that ancestor's caller-subtree. Passing the frame node
            # itself would be wrong for a NESTED frame — a nested code
            # node has no predecessor, so its spine would be one node
            # and all conversation history would vanish.
            head_id = frame_node_id if frame_node_id in graph.nodes else None
            seen: set[str] = set()
            while head_id and head_id not in seen:
                seen.add(head_id)
                caller = graph.nodes[head_id].caller
                if not caller or caller not in graph.nodes:
                    break
                head_id = caller
            if context.head_id is not None and context.head_id not in graph.nodes:
                raise ValueError(f"Context head {context.head_id!r} does not exist.")
            if head_id is None and context.head_id is not None:
                head_id = context.head_id
            if head_id is None:
                head_id = (
                    max(
                        graph.nodes.values(),
                        key=lambda n: n.seq,
                    ).id
                    if graph.nodes
                    else None
                )
            read_ids = render_context(
                graph,
                head_id=head_id,
                frame_entry_seq=frame_entry_seq,
                render_range=render_range,
            )
            read_ids = context.select_history(graph, read_ids, call_id=frame_node_id)
            model_node_id = getattr(self, "_active_llm_node_id", None)
            if model_node_id and model_node_id in graph.nodes:
                read_ids = [node_id for node_id in read_ids if node_id != model_node_id]
                store.update(model_node_id, reads=read_ids)
            # The session's directory identifies it on disk for the
            # renderer. Best-effort: any failure passes None.
            session_directory = None
            try:
                session_directory = str(store.store._session_dir(store.session_id))
            except Exception:
                session_directory = None
            history = render_dag_messages(graph, read_ids, session_directory, reasoning_for=model)

            # Synthesize the current turn from ``content`` blocks via
            # the same helper the no-store fallback uses, so image /
            # video / audio blocks survive the DAG render path. The old
            # version concatenated text parts only — any screenshot the
            # caller attached to this turn (an Agent method's vision
            # sub-calls do this) was silently dropped, so the LLM
            # ended up reasoning over the accompanying text alone and
            # missed the current frame.
            ctx, _sp = _build_pi_context(content or [])
            return history + [ctx.messages[0]]
        except Exception as exc:
            raise RuntimeError("Cannot construct context from the session DAG.") from exc


    def _open_model_call_node(
        self,
        *,
        model: str,
        execution_kind: str = "agent",
        system_prompt: Optional[str] = None,
        content_text: str = "",
    ) -> Optional[str]:
        """Write a *running* llm-role Call node at the start of one exec()
        LLM call. Returns its node id (or None when no store is installed).

        One ``runtime.exec`` == one llm node (the same way one
        ``Agent method`` == one code node). The node is written with
        ``output=None`` / ``status=running`` here; :meth:`_close_model_call_node`
        fills in the reply and flips the status on return.

        Note: this does NOT repoint ``_call_id``. The history renderer
        (``_render_history_messages``) reads ``_call_id`` to locate the
        enclosing *function* frame, and that read happens inside ``_call``
        AFTER this node is opened — so flipping ``_call_id`` here would
        corrupt history rendering. The repoint to this llm node (so the
        tool loop attributes its tool calls here) is done later, inside
        ``_call_via_providers`` once the prompt is already built. See
        :meth:`_enter_model_frame`.

        History construction fills ``reads`` with the selected DAG sources.
        """
        try:
            from openprogram.state.store import _store
            from openprogram.state.store.session.nodes import Call, ROLE_LLM
            from openprogram.runtime.context.scope import _call_id

            store = _store.get()
            if store is None:
                return None

            caller = _call_id.get() or ""
            predecessor = None
            if not caller:
                from openprogram.runtime.context.model import Context
                context = Context.current()
                predecessor = context.head_id if context is not None else None
                if predecessor is None:
                    pair = store.store._open(store.session_id)
                    predecessor = pair[1].head_id if pair is not None else None
                predecessor = predecessor or "ROOT"
            node = Call(
                role=ROLE_LLM,
                predecessor=predecessor,
                name=model or self.model or "",
                input=({"system": system_prompt} if system_prompt else None),
                output=None,
                reads=[],
                caller=caller,
                metadata={
                    "status": "running",
                    "execution_kind": execution_kind,
                    "provider_request_count": 0,
                    "agent_iteration_count": 0,
                    **({"prompt_text": content_text[:8000]} if content_text else {}),
                },
            )
            store.append(node)
            return node.id
        except Exception:
            # DAG bookkeeping failure must not break the LLM call.
            return None


    def _close_model_call_node(
        self,
        node_id: Optional[str],
        *,
        reply: str,
        status: str = "completed",
        usage: Optional[dict] = None,
        blocks: Optional[list] = None,
        execution_kind: str = "agent",
        provider_request_count: int = 0,
        agent_iteration_count: int = 0,
        error: Optional[BaseException] = None,
    ) -> None:
        """Fill in the reply + terminal status on the running llm node
        opened by :meth:`_open_model_call_node`.

        Per dag/overview.md decision 3, an llm node carries the SAME
        fields regardless of entry point: besides ``output`` + ``status``,
        an exec-path llm node now also records ``usage`` (token columns —
        a function call costs tokens just like chat) and ``blocks`` (the
        reply's thinking/text/tool structure). When ``usage``/``blocks``
        are None they fall back to the runtime's last-call values, since
        ``_call`` has populated ``self.last_usage`` / ``self.last_blocks``
        by the time this runs.

        Status vocabulary is unified with the chat path (decision 2):
        ``completed`` / ``error`` / ``cancelled`` — not ``success``.

        When ``error`` is provided (step 6-C), the same structured error
        fields the chat path writes (error, error_type, trace) are
        included in metadata — unified shape across both entry points.

        No-op when ``node_id`` is None (no store was installed at open time).
        """
        if node_id is None:
            return
        try:
            from openprogram.state.store import _store

            store = _store.get()
            if store is None:
                return
            _usage = usage if usage is not None else getattr(self, "last_usage", None)
            _blocks = (
                blocks if blocks is not None else getattr(self, "last_blocks", None)
            )
            meta: dict = {
                "status": status,
                "execution_kind": execution_kind,
                "provider_request_count": provider_request_count,
                "agent_iteration_count": agent_iteration_count,
            }
            from openprogram.models.providers.utils.recovery import current_recovery
            recovery = current_recovery.get()
            if recovery is not None:
                meta["recovery"] = recovery.snapshot()
                if recovery.requests:
                    meta["provider_request_count"] = recovery.requests
            if _usage:
                meta["usage"] = _usage
            if _blocks:
                meta["blocks"] = _blocks
            # 工具名单（论文仓库 spec §5 ①）：供事后 compute_breakdown_from_node
            # 重算这次调用的 tools schema / per-tool，得 context 增长时间序列。
            _tools = getattr(self, "_pending_tool_names", None)
            if _tools:
                meta["tools_available"] = _tools
            # system prompt 原料：供 web /context 算 system 类真实 token。
            _sysp = getattr(self, "_pending_system_prompt", None)
            if _sysp:
                meta["system_prompt"] = _sysp
            if error is not None:
                import traceback as _tb

                meta["error"] = str(error)
                meta["error_type"] = type(error).__name__
                meta["trace"] = "".join(
                    _tb.format_exception(type(error), error, error.__traceback__)
                )[:2000]
            # execution_stream.v1: preview lives in metadata.stream;
            # Call.output stays the verified final return value only.
            try:
                from openprogram.runtime.agent.runtime.execution_stream.adapter import (
                    finalize_call_stream,
                )
                from openprogram.runtime.agent.runtime.shared import (
                    _current_exec_state,
                )

                stream_meta = finalize_call_stream(
                    _current_exec_state.get(),
                    status=status,
                    result=reply if status == "completed" else None,
                    reason_code=status,
                )
                if stream_meta:
                    meta["stream"] = stream_meta
            except Exception:
                # The node output is still written below without stream metadata.
                _log.warning("call stream not finalized for node %s", node_id, exc_info=True)
            store.update(node_id, output=reply, metadata=meta)
        except Exception:
            # Recording the reply must not fail the call that produced it.
            _log.warning("LLM node %s output not saved", node_id, exc_info=True)
