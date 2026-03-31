"""
Function — the fundamental unit of execution in LLM Agent Harness.

Modelled after programming language function terminology:
    - A Function has a name, docstring, body (instructions), params, and return_type
    - Calling a Function sends it to a Session (the runtime) and returns a typed result
    - A Function does not return until its output matches the return_type

Analogy to regular Python functions:
    def observe(task: str) -> ObserveResult:
        \"\"\"Observe the current screen state.\"\"\"
        # ... instructions ...

    # In LLM Agent Harness:
    observe = Function(
        name="observe",
        docstring="Observe the current screen state.",
        body="Take a screenshot and analyze...",
        params=["task"],
        return_type=ObserveResult,
    )
    result = observe.call(session, context)  # returns ObserveResult

Execution flow:
    1. Extract params from context
    2. Assemble call message (docstring + body + params + return_type schema)
    3. Send to session
    4. Parse and validate reply against return_type
    5. If valid   → return typed result
    6. If invalid → retry (up to max_retries), then raise FunctionError
"""

from __future__ import annotations

import json
from typing import Type, TypeVar, Optional
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class FunctionError(Exception):
    """
    Raised when a Function fails to return valid output after all retries.

    Analogous to a RuntimeError in a regular function call.
    """

    def __init__(self, function_name: str, last_reply: str, attempts: int):
        self.function_name = function_name
        self.last_reply = last_reply
        self.attempts = attempts
        super().__init__(
            f"Function '{function_name}' failed to return valid output "
            f"after {attempts} attempts. "
            f"Last reply: {last_reply[:200]}..."
        )


class Function:
    """
    A typed function executed by an LLM Session.

    The LLM Session is the runtime. The Function is the definition.
    The Session is pluggable — any Session implementation works.

    Attributes:
        name        Identifier, e.g. "observe"
        docstring   What this function does (1-2 sentences)
        body        How to do it — the Skill content (natural language)
        params      Which context keys this function reads as input
                    (None = pass full context)
        return_type Pydantic model this function MUST return
        examples    Optional list of {"input": ..., "output": ...} dicts
        max_retries How many times to retry if output is invalid
    """

    def __init__(
        self,
        name: str,
        docstring: str,
        body: str,
        return_type: Type[T],
        params: Optional[list[str]] = None,
        examples: Optional[list[dict]] = None,
        max_retries: int = 3,
    ):
        self.name = name
        self.docstring = docstring
        self.body = body
        self.return_type = return_type
        self.params = params
        self.examples = examples or []
        self.max_retries = max_retries

    def call(self, session: "Session", context: dict) -> T:
        """
        Call this function using the given session as runtime.

        Args:
            session:  The LLM Session to use as runtime
            context:  The current workflow context

        Returns:
            A validated instance of return_type

        Raises:
            FunctionError: if the function cannot return valid output
        """
        arguments = self._extract_arguments(context)
        message = self._assemble_call_message(arguments)

        last_reply = ""
        for attempt in range(1, self.max_retries + 1):
            if attempt == 1:
                reply = session.send(message)
            else:
                retry_message = (
                    f"Your previous response could not be parsed as a valid return value.\n"
                    f"You MUST return a JSON object matching this schema exactly:\n"
                    f"{json.dumps(self.return_type.model_json_schema(), indent=2)}\n\n"
                    f"Previous response was:\n{last_reply}\n\n"
                    f"Please try again."
                )
                reply = session.send(retry_message)

            last_reply = reply
            result = self._parse_return_value(reply)
            if result is not None:
                return result

        raise FunctionError(
            function_name=self.name,
            last_reply=last_reply,
            attempts=self.max_retries,
        )

    # ------------------------------------------------------------------
    # Private methods
    # ------------------------------------------------------------------

    def _extract_arguments(self, context: dict) -> dict:
        """Extract declared params from context to use as function arguments."""
        if self.params is None:
            return context
        return {k: context[k] for k in self.params if k in context}

    def _assemble_call_message(self, arguments: dict) -> str:
        """Assemble the call message to send to the session."""
        parts = [
            f"## Function: {self.name}",
            "",
            "### Docstring",
            self.docstring,
            "",
            "### Body",
            self.body,
        ]

        if arguments:
            parts += [
                "",
                "### Arguments",
                json.dumps(arguments, ensure_ascii=False, indent=2),
            ]

        if self.examples:
            parts += ["", "### Examples"]
            for ex in self.examples:
                parts.append(json.dumps(ex, ensure_ascii=False, indent=2))

        parts += [
            "",
            "### Return type",
            "You MUST respond with a JSON object matching this schema exactly.",
            "Do not add extra fields. Do not wrap in markdown code blocks.",
            json.dumps(self.return_type.model_json_schema(), indent=2),
        ]

        return "\n".join(parts)

    def _parse_return_value(self, reply: str) -> Optional[T]:
        """Try to parse the session's reply into return_type."""
        # Strip markdown code blocks if present
        text = reply.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]).strip()

        try:
            data = json.loads(text)
            return self.return_type(**data)
        except Exception:
            pass

        # Try to find a JSON object anywhere in the reply
        try:
            start = reply.index("{")
            end = reply.rindex("}") + 1
            data = json.loads(reply[start:end])
            return self.return_type(**data)
        except Exception:
            return None
