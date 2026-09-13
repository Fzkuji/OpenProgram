"""Agentic runtime: history."""
from __future__ import annotations










from typing import Optional


from .shared import (
    _build_pi_context,
    _compute_call_path,
    _situational_prefix,
)

class HistoryOperations:
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

        Same loader and same renderer as the chat path's ``skills_index``
        component (openprogram/skills/loader.py) — one registry, one
        listing format. Cached per config so repeat exec() calls don't
        rescan unless it changes. Empty string when skills are disabled or
        no SKILL.md files were found, so callers can concatenate freely.
        """
        key = self._skills_key()
        if self._skills_cache_key == key:
            return self._skills_prompt_block
        from openprogram.skills import (
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


    def _render_history_messages(self, content) -> Optional[list]:
        """Build the provider message list for an in-progress exec()
        from the DAG.

        Source of truth: the ``_store`` ContextVar set by the dispatcher
        at turn entry (``openprogram.context.storage._store``). When no
        store is installed (standalone scripts, tests without the
        dispatcher), returns ``None`` so the caller falls back to the
        tree-Context render path.

        Algorithm:
          1. Load the DAG state from the store.
          2. Read the enclosing ``@agentic_function`` call id from
             ``_call_id`` ContextVar; pull its node from the graph to
             get seq + render_range.
          3. Compute reads → render pi-ai messages.
          4. Append a fresh UserMessage built from ``content``.
        """
        from openprogram.store import _store

        store = _store.get()
        if store is None:
            return None

        try:
            from openprogram.context.nodes import render_context
            from openprogram.context.render import render_dag_messages
            from openprogram.agentic_programming.function import _call_id

            graph = store.load()
            frame_node_id = _call_id.get()

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
            # Resolve the session's history/ dir so an over-cap node's
            # truncation marker can cite the exact node file the agent can
            # ``read`` back. Best-effort: any failure → generic marker.
            history_dir = None
            try:
                _sess_dir = store.store._session_dir(store.session_id)
                history_dir = str(_sess_dir / "history")
            except Exception:
                history_dir = None
            history = render_dag_messages(graph, read_ids, history_dir)

            # Inject current-frame identity so the inner model knows
            # which function it is executing (prevents self-recursion
            # and gives the model its role context).
            frame_prefix_blocks: list[dict] = []
            if frame_node_id and frame_node_id in graph.nodes:
                fn_name = frame_node.name
                fn_doc = (frame_node.metadata or {}).get("doc") or ""
                if fn_name:
                    call_path = _compute_call_path(graph, frame_node_id)
                    text = _situational_prefix(
                        fn_name,
                        fn_doc,
                        call_path=call_path,
                    )
                    frame_prefix_blocks.append(
                        {
                            "type": "text",
                            "text": text,
                        }
                    )

            # Synthesize the current turn from ``content`` blocks via
            # the same helper the no-store fallback uses, so image /
            # video / audio blocks survive the DAG render path. The old
            # version concatenated text parts only — any screenshot the
            # caller attached to this turn (gui_agent's verify / plan /
            # locate sub-calls all do this) was silently dropped, so
            # the LLM ended up reasoning over the OCR/component text
            # alone and missed the current frame.
            ctx, _sp = _build_pi_context(frame_prefix_blocks + (content or []))
            return history + [ctx.messages[0]]
        except Exception:
            # If anything goes wrong building DAG messages, fall back
            # to the legacy render_messages path. Never break exec().
            return None


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
        ``@agentic_function`` == one code node). The node is written with
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

        ``reads`` is intentionally left empty for now — wiring the exact
        read-id set the prompt consumed is a future refinement.
        """
        try:
            from openprogram.store import _store
            from openprogram.context.nodes import Call, ROLE_LLM
            from openprogram.agentic_programming.function import _call_id

            store = _store.get()
            if store is None:
                return None

            node = Call(
                role=ROLE_LLM,
                name=model or self.model or "",
                input=({"system": system_prompt} if system_prompt else None),
                output=None,
                reads=[],
                caller=_call_id.get() or "",
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
            from openprogram.store import _store

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
            # What the render policy actually did for this call — replaying
            # with it reproduces the exact prompt bytes even after the
            # global aging constants move (dag/overview.md §8).
            try:
                from openprogram.context.aging import last_manifest

                _mf = last_manifest()
                if _mf:
                    meta["render_manifest"] = _mf
            except Exception:
                pass
            if error is not None:
                import traceback as _tb

                meta["error"] = str(error)
                meta["error_type"] = type(error).__name__
                meta["trace"] = "".join(
                    _tb.format_exception(type(error), error, error.__traceback__)
                )[:2000]
            store.update(node_id, output=reply, metadata=meta)
        except Exception:
            pass

