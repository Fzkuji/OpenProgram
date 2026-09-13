"""Agentic runtime: questions."""
from __future__ import annotations













class QuestionsOperations:
    def _ui_session_id(self) -> str:
        """前端路由用的 webui session（dispatcher 在执行上下文里设的
        ContextVar），不是 Runtime 自己的 op-xxx id。无 webui 时为空串。"""
        from openprogram.agentic_programming.function import current_session_id

        return current_session_id()


    def can_ask(self) -> bool:
        """当前是否有人能回答（有前端会话连着）。headless 跑时为 False，
        作者可据此分支（user-input-requests.md API）。"""
        if getattr(self, "_question_transport", None) is not None:
            return True
        try:
            __import__("openprogram.webui")
        except ImportError:
            return False
        return bool(self._ui_session_id())


    def set_question_transport(self, transport) -> None:
        """换掉这个 runtime 的提问通道（QuestionTransport）。子进程入口用它
        装上 QueueTransport，把 runtime.ask 的问题经 mp.Queue 送回父进程。
        传 None 恢复默认（事件层）。"""
        self._question_transport = transport


    def _ask_raw(
        self,
        *,
        kind,
        prompt,
        options=None,
        multi=False,
        allow_custom=True,
        detail="",
        schema=None,
        questions=None,
        timeout=300.0,
    ):
        from openprogram.agent.questions import ask_blocking, emit_question_asked

        transport = getattr(self, "_question_transport", None)  # None → 默认事件层通道

        def _on_asked(q):
            # 经本 runtime 的提问通道把问题送出去：worker 进程默认走事件层
            # （前端卡片 + 总线）；@agentic_function 跑的子进程被 process_runner
            # 换成 QueueTransport（经 mp.Queue 送回父进程 registry）。
            emit_question_asked(
                {
                    "id": q.id,
                    "session_id": q.session_id,
                    "kind": q.kind,
                    "prompt": q.prompt,
                    "options": q.options,
                    "multi": q.multi,
                    "allow_custom": q.allow_custom,
                    "detail": q.detail,
                    "schema": q.schema,  # kind="form" 时非空
                    "questions": q.questions,  # kind="ask_many" 时非空
                    "execution_id": q.execution_id,
                    "wait_generation": q.wait_generation,
                    "expected_version": q.execution_version,
                    "expires_at": q.expires_at,
                },
                transport,
            )

        return ask_blocking(
            session_id=self._ui_session_id(),
            kind=kind,
            prompt=prompt,
            options=options,
            multi=multi,
            allow_custom=allow_custom,
            detail=detail,
            schema=schema,
            questions=questions,
            timeout=timeout,
            on_asked=_on_asked,
            transport=transport,  # 超时收回前端卡片走同一条通道
        )


    def ask(
        self,
        prompt: str | None = None,
        *,
        options=None,
        multi: bool = False,
        allow_custom: bool = True,
        questions: list | None = None,
        timeout: float = 300.0,
        default=None,
    ):
        """问用户，阻塞到有答案。统一入口——可一次问 1 题或多题。

        两种用法（对齐 Claude Code 的 AskUserQuestion：不区分问几个）：

        1) 单题：``ask("你喜欢哪个?", options=["A","B"], multi=False)``
           返回该题答案（multi=True 返回 list[str]，纯文本无 options 返回 str）。

        2) 多题：``ask(questions=[{"prompt": "...", "options": [...],
           "multi": False, "allow_custom": True}, ...], prompt="组标题")``
           前端一屏内在各题间切换着答、全答完一起提交。返回 list（与
           questions 等长，每项是该题答案）。

        三态：答了→返回答案；用户拒绝→抛 UserDeclined；超时→有 default
        返回 default，否则抛 AskTimeout。
        """
        from openprogram.agent.questions import UserDeclined, AskTimeout

        # 多题分支 —— 一屏切换、一起提交（原 ask_many）。
        if questions is not None:
            qs = [
                {
                    "prompt": str(q.get("prompt", "")),
                    "options": list(q.get("options") or []),
                    "multi": bool(q.get("multi")),
                    "allow_custom": q.get("allow_custom", True) is not False,
                }
                for q in (questions or [])
            ]
            outcome, value = self._ask_raw(
                kind="ask_many",
                prompt=prompt or "",
                questions=qs,
                allow_custom=False,
                timeout=timeout,
            )
            if outcome == "answered":
                return value if isinstance(value, list) else []
            if outcome == "cancelled":
                from openprogram.agentic_programming.function import CancelledError
                raise CancelledError(prompt or "ask")
            if outcome == "declined":
                raise UserDeclined(prompt or "ask")
            if default is not None:
                return default
            raise AskTimeout(prompt or "ask")

        # 单题分支。
        outcome, value = self._ask_raw(
            kind="ask",
            prompt=prompt or "",
            options=options,
            multi=multi,
            allow_custom=allow_custom,
            timeout=timeout,
        )
        if outcome == "answered":
            return value
        if outcome == "cancelled":
            from openprogram.agentic_programming.function import CancelledError
            raise CancelledError(prompt or "ask")
        if outcome == "declined":
            raise UserDeclined(prompt or "ask")
        if default is not None:
            return default
        raise AskTimeout(prompt or "ask")


    def confirm(
        self,
        prompt: str,
        *,
        detail: str = "",
        timeout: float = 300.0,
        default: bool = False,
    ) -> bool:
        """问一个是/否，返回 bool。拒绝=False；超时返回 default（不抛）。"""
        outcome, value = self._ask_raw(
            kind="confirm",
            prompt=prompt,
            detail=detail,
            options=["确认", "取消"],
            allow_custom=False,
            timeout=timeout,
        )
        if outcome == "answered":
            if isinstance(value, str):
                return value.strip() in ("确认", "yes", "y", "true", "ok", "是")
            return bool(value)
        if outcome == "cancelled":
            from openprogram.agentic_programming.function import CancelledError
            raise CancelledError(prompt)
        if outcome == "declined":
            return False
        return default  # timeout


    def form(
        self,
        prompt: str,
        fields: dict,
        *,
        detail: str = "",
        timeout: float = 300.0,
        default: dict | None = None,
    ):
        """问用户一个多字段表单（MCP-elicitation 风格），阻塞到提交。

        ``fields`` 是 flat-object 字段 schema：字段名 → 字段定义，例如
        ``{"name": {"type": "string", "title": "名字"},
           "count": {"type": "integer", "default": 1},
           "mode": {"type": "string", "enum": ["fast", "slow"]}}``。
        只支持一层（无嵌套 object/array）；字段类型限 string（可带 enum）/
        integer / number / boolean。

        三态（与 ask 一致）：提交 → 返回 dict（字段名 → 值）；用户拒绝 →
        抛 UserDeclined；超时 → 有 default 返回 default，否则抛 AskTimeout。
        """
        from openprogram.agent.questions import UserDeclined, AskTimeout

        outcome, value = self._ask_raw(
            kind="form",
            prompt=prompt,
            schema=dict(fields or {}),
            allow_custom=False,
            detail=detail,
            timeout=timeout,
        )
        if outcome == "answered":
            return value if isinstance(value, dict) else {}
        if outcome == "cancelled":
            from openprogram.agentic_programming.function import CancelledError
            raise CancelledError(prompt)
        if outcome == "declined":
            raise UserDeclined(prompt)
        if default is not None:
            return default
        raise AskTimeout(prompt)

