"""
Programmer — the planning and decision-making agent.

Like a human programmer:
    - Reads requirements (the task)
    - Browses available libraries (the Function pool)
    - Writes new functions if needed
    - Calls functions and checks results
    - Maintains a mental model of what happened (Context with call stack + log)

The Programmer has a persistent Session (remembers across iterations).
Each Function it calls runs in an ephemeral Runtime Session (isolated).
"""

from __future__ import annotations

import json
from typing import Optional
from pydantic import BaseModel

from harness.function import Function, FunctionError
from harness.session import Session
from harness.runtime import Runtime
from harness.context import Context


# ------------------------------------------------------------------
# Decision schema
# ------------------------------------------------------------------

class NewFunctionSpec(BaseModel):
    """Specification for a dynamically created Function."""
    name: str
    docstring: str
    body: str
    params: Optional[list[str]] = None
    return_type_schema: dict  # JSON Schema


class ProgrammerDecision(BaseModel):
    """
    The Programmer's decision each iteration.

    action:
        - "call"   → call an existing Function
        - "create" → create a new Function
        - "reply"  → send a message back to the user
        - "done"   → task is complete
        - "fail"   → task cannot be completed
    """
    action: str
    reasoning: str
    function_name: Optional[str] = None
    function_args: Optional[dict] = None
    new_function: Optional[NewFunctionSpec] = None
    reply_text: Optional[str] = None
    failure_reason: Optional[str] = None


# ------------------------------------------------------------------
# Result
# ------------------------------------------------------------------

class ProgrammerResult(BaseModel):
    """Final result of a Programmer run."""
    success: bool
    context: dict
    log: list = []
    reply: Optional[str] = None
    failure_reason: Optional[str] = None
    iterations: int = 0


# ------------------------------------------------------------------
# Programmer
# ------------------------------------------------------------------

class Programmer:
    """
    The planning agent.

    Args:
        session:          Persistent LLM Session for thinking
        runtime:          Runtime for executing Functions (ephemeral Sessions)
        functions:        Initial Function pool
        programmer_fn:    How the Programmer thinks (default provided)
        max_iterations:   Safety limit
    """

    def __init__(
        self,
        session: Session,
        runtime: Runtime,
        functions: Optional[list[Function]] = None,
        programmer_fn: Optional[Function] = None,
        max_iterations: int = 50,
    ):
        self.session = session
        self.runtime = runtime
        self.functions: dict[str, Function] = {}
        if functions:
            for fn in functions:
                self.functions[fn.name] = fn
        self.programmer_fn = programmer_fn or self._default_programmer_fn()
        self.max_iterations = max_iterations

    def run(self, task: str, initial_context: Optional[dict] = None) -> ProgrammerResult:
        """
        Run the Programmer on a task.

        Loop: think → decide → execute → log → repeat.
        """
        ctx = Context(task=task)
        if initial_context:
            ctx.update(initial_context)

        for iteration in range(1, self.max_iterations + 1):
            # Build scoped input for the Programmer Function
            programmer_input = self._build_input(ctx)

            # Ask the Programmer what to do
            try:
                decision = self.programmer_fn.call(
                    session=self.session,
                    context=programmer_input,
                )
            except FunctionError as e:
                return ProgrammerResult(
                    success=False,
                    context=ctx.to_dict(),
                    log=[str(entry) for entry in ctx.log],
                    failure_reason=f"Programmer failed to decide: {e}",
                    iterations=iteration,
                )

            action = decision.action

            if action == "call":
                self._do_call(decision, ctx)

            elif action == "create":
                self._do_create(decision, ctx)

            elif action == "reply":
                return ProgrammerResult(
                    success=True,
                    context=ctx.to_dict(),
                    log=[str(entry) for entry in ctx.log],
                    reply=decision.reply_text,
                    iterations=iteration,
                )

            elif action == "done":
                return ProgrammerResult(
                    success=True,
                    context=ctx.to_dict(),
                    log=[str(entry) for entry in ctx.log],
                    iterations=iteration,
                )

            elif action == "fail":
                return ProgrammerResult(
                    success=False,
                    context=ctx.to_dict(),
                    log=[str(entry) for entry in ctx.log],
                    failure_reason=decision.failure_reason or decision.reasoning,
                    iterations=iteration,
                )

        return ProgrammerResult(
            success=False,
            context=ctx.to_dict(),
            log=[str(entry) for entry in ctx.log],
            failure_reason=f"Max iterations ({self.max_iterations}) reached",
            iterations=self.max_iterations,
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _do_call(self, decision: ProgrammerDecision, ctx: Context):
        """Execute a Function call with call stack tracking."""
        fn_name = decision.function_name
        if fn_name not in self.functions:
            ctx.push("programmer", fn_name or "unknown", reason=decision.reasoning)
            ctx.pop(status="error", error=f"Function '{fn_name}' not found")
            return

        fn = self.functions[fn_name]

        # Push frame onto call stack
        ctx.push("programmer", fn_name, reason=decision.reasoning)

        # Build scoped context for this Function (only its declared params)
        call_context = ctx.scope_for(fn.params)
        if decision.function_args:
            call_context.update(decision.function_args)

        try:
            result = self.runtime.execute(fn, call_context)
            result_dict = result.model_dump()
            ctx[fn_name] = result_dict
            ctx.pop(status="success", output=result_dict)
        except FunctionError as e:
            ctx.pop(status="error", error=str(e))

    def _do_create(self, decision: ProgrammerDecision, ctx: Context):
        """Create a new Function and add to pool."""
        spec = decision.new_function
        if spec is None:
            ctx.push("programmer", "create", reason=decision.reasoning)
            ctx.pop(status="error", error="No function spec provided")
            return

        ctx.push("programmer", f"create:{spec.name}", reason=decision.reasoning)

        try:
            return_type = self._schema_to_model(spec.name, spec.return_type_schema)
            new_fn = Function(
                name=spec.name,
                docstring=spec.docstring,
                body=spec.body,
                return_type=return_type,
                params=spec.params,
            )
            self.functions[new_fn.name] = new_fn
            ctx.pop(status="success", output={"created": spec.name})
        except Exception as e:
            ctx.pop(status="error", error=str(e))

    # ------------------------------------------------------------------
    # Input building
    # ------------------------------------------------------------------

    def _build_input(self, ctx: Context) -> dict:
        """Build the input the Programmer Function sees."""
        available_functions = []
        for name, fn in self.functions.items():
            available_functions.append({
                "name": name,
                "docstring": fn.docstring,
                "params": fn.params,
                "return_type": fn.return_type.model_json_schema(),
            })

        # Include recent log entries as history (compressed)
        history = [str(entry) for entry in ctx.log[-20:]]  # last 20 entries

        return {
            "task": ctx.get("task", ""),
            "history": history,
            "available_functions": available_functions,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _schema_to_model(name: str, schema: dict) -> type:
        """Build a Pydantic model from a JSON Schema dict."""
        from pydantic import create_model

        type_map = {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
        }

        fields = {}
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))

        for field_name, field_schema in properties.items():
            field_type_str = field_schema.get("type", "string")

            if field_type_str == "array":
                item_type_str = field_schema.get("items", {}).get("type", "string")
                item_type = type_map.get(item_type_str, str)
                field_type = list[item_type]
            else:
                field_type = type_map.get(field_type_str, str)

            if field_name in required:
                fields[field_name] = (field_type, ...)
            else:
                fields[field_name] = (Optional[field_type], None)

        return create_model(f"Dynamic_{name}", **fields)

    @staticmethod
    def _default_programmer_fn() -> Function:
        """Create the default Programmer Function."""
        default_body = """You are a Programmer. Your job is to accomplish the given task
by selecting and calling available Functions, or creating new ones when needed.

## How to think

1. Read the task carefully
2. Look at the available functions — is there one that helps with the next step?
3. If yes → call it
4. If no → create a new function that does what you need
5. After each function returns, check the result in history
6. Decide: continue? try something else? done? give up?

## Rules

- You NEVER execute tasks yourself. You ALWAYS delegate to Functions.
- You only see structured return values, not execution details.
- Think step by step. One Function at a time.
- If a Function fails, analyze why and try a different approach.
- If the task is impossible, say so (action: "fail").

## Actions

- "call": call an existing Function
- "create": define a new Function
- "reply": send a message to the user
- "done": task complete
- "fail": task impossible
"""
        return Function(
            name="programmer",
            docstring="Decide the next step to accomplish the task.",
            body=default_body,
            return_type=ProgrammerDecision,
            params=["task", "history", "available_functions"],
        )
