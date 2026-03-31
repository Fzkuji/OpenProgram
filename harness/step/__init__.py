"""
Step — the fundamental unit of execution in LLM Agent Harness.

A Step is a typed function executed by an LLM session.
It does not return until its output matches the output_schema.

Anatomy of a Step:
    - name:           identifier
    - description:    what this step does (1-2 sentences)
    - instructions:   how to do it — the Skill content, natural language
    - input_schema:   what fields from context this step reads
    - output_schema:  what this step MUST return (Pydantic model)
    - examples:       optional sample input/output pairs

Execution flow:
    1. Assemble message from step definition + context
    2. Send to session
    3. Validate reply against output_schema
    4. If valid → return result
    5. If invalid → retry (up to max_retries), then raise StepFailure
"""

from __future__ import annotations

import json
from typing import Type, TypeVar, Optional
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class StepFailure(Exception):
    """Raised when a Step fails to produce valid output after all retries."""

    def __init__(self, step_name: str, last_reply: str, attempts: int):
        self.step_name = step_name
        self.last_reply = last_reply
        self.attempts = attempts
        super().__init__(
            f"Step '{step_name}' failed after {attempts} attempts. "
            f"Last reply: {last_reply[:200]}..."
        )


class Step:
    """
    A typed function executed by an LLM session.

    The LLM is the runtime. The Step is the function definition.
    The session is pluggable — any Session implementation works.
    """

    def __init__(
        self,
        name: str,
        description: str,
        instructions: str,
        output_schema: Type[T],
        reads: Optional[list[str]] = None,
        examples: Optional[list[dict]] = None,
        max_retries: int = 3,
    ):
        """
        Args:
            name:           Step identifier, e.g. "observe"
            description:    What this step does in 1-2 sentences
            instructions:   How to do it — the Skill content (natural language)
            output_schema:  Pydantic model the step MUST return
            reads:          Which context fields this step needs as input
                            (None means pass the full context)
            examples:       Optional list of {"input": ..., "output": ...} dicts
            max_retries:    How many times to retry if output is invalid
        """
        self.name = name
        self.description = description
        self.instructions = instructions
        self.output_schema = output_schema
        self.reads = reads
        self.examples = examples or []
        self.max_retries = max_retries

    def run(self, session: "Session", context: dict) -> T:
        """
        Execute this step using the given session.

        Args:
            session:  The LLM session to use as runtime
            context:  The current workflow context

        Returns:
            A validated instance of output_schema

        Raises:
            StepFailure: if the step cannot produce valid output
        """
        input_data = self._extract_input(context)
        message = self._assemble_message(input_data)

        last_reply = ""
        for attempt in range(1, self.max_retries + 1):
            if attempt == 1:
                reply = session.send(message)
            else:
                # On retry, be explicit about what went wrong
                retry_message = (
                    f"Your previous response could not be parsed as valid output.\n"
                    f"You MUST respond using exactly this JSON schema:\n"
                    f"{json.dumps(self.output_schema.model_json_schema(), indent=2)}\n\n"
                    f"Previous response was:\n{last_reply}\n\n"
                    f"Please try again."
                )
                reply = session.send(retry_message)

            last_reply = reply
            result = self._parse_reply(reply)
            if result is not None:
                return result

        raise StepFailure(
            step_name=self.name,
            last_reply=last_reply,
            attempts=self.max_retries,
        )

    def _extract_input(self, context: dict) -> dict:
        """Extract only the fields this step needs from context."""
        if self.reads is None:
            return context
        return {k: context[k] for k in self.reads if k in context}

    def _assemble_message(self, input_data: dict) -> str:
        """Assemble the message to send to the session."""
        parts = [
            f"## Step: {self.name}",
            "",
            "### What this step does",
            self.description,
            "",
            "### How to do it",
            self.instructions,
        ]

        if input_data:
            parts += [
                "",
                "### Input",
                json.dumps(input_data, ensure_ascii=False, indent=2),
            ]

        if self.examples:
            parts += ["", "### Examples"]
            for ex in self.examples:
                parts.append(json.dumps(ex, ensure_ascii=False, indent=2))

        parts += [
            "",
            "### Required output format",
            "You MUST respond with a JSON object matching this schema exactly.",
            "Do not add extra fields. Do not wrap in markdown code blocks.",
            json.dumps(self.output_schema.model_json_schema(), indent=2),
        ]

        return "\n".join(parts)

    def _parse_reply(self, reply: str) -> Optional[T]:
        """Try to parse the session's reply into output_schema."""
        # Strip markdown code blocks if present
        text = reply.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last lines (``` markers)
            text = "\n".join(lines[1:-1]).strip()

        try:
            data = json.loads(text)
            return self.output_schema(**data)
        except Exception:
            pass

        # Try to find JSON object in the reply
        try:
            start = reply.index("{")
            end = reply.rindex("}") + 1
            data = json.loads(reply[start:end])
            return self.output_schema(**data)
        except Exception:
            return None
