"""generate_code — base meta function that asks the LLM to write or
modify an @agentic_function.

Every code-generation meta function (``create``, ``edit``, ``fix``,
``improve``) calls this. The function's docstring is the framework-side
ruleset given to the LLM as system prompt; the body additionally
prepends the canonical ``docs/design/function/function_metadata.md`` to
the user content so the LLM has the full spec available.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime


# ── Canonical metadata specification, loaded once and injected into
#    generate_code's prompt so all create/edit/fix/improve flows produce
#    code that conforms to docs/design/function/function_metadata.md.

_METADATA_SPEC_CACHE: Optional[str] = None


def _load_metadata_spec() -> str:
    """Return the contents of function_metadata.md, cached."""
    global _METADATA_SPEC_CACHE
    if _METADATA_SPEC_CACHE is not None:
        return _METADATA_SPEC_CACHE
    try:
        spec_path = (
            Path(__file__).resolve().parents[4]
            / "docs" / "design" / "function" / "function_metadata.md"
        )
        _METADATA_SPEC_CACHE = spec_path.read_text()
    except (FileNotFoundError, OSError):
        _METADATA_SPEC_CACHE = ""
    return _METADATA_SPEC_CACHE


@agentic_function
def generate_code(task: str, runtime: Runtime) -> str:
    """Generate or modify Python code for an @agentic_function.

    The canonical metadata + style specification is in
    docs/design/function/function_metadata.md and is automatically prepended
    to the task below. Follow it strictly. Framework-side rules that
    complement the spec:

    1. Function shape
       - If the function needs LLM reasoning: decorate with @agentic_function,
         take `runtime: Runtime`, and call `runtime.exec(content=[...])` at
         most ONCE for the LLM-driven work. For multiple LLM calls split into
         several @agentic_function and have one orchestrate the others.
       - Pure Python (no LLM): no decorator, no runtime parameter,
         no `runtime.exec`.

    2. Imports already provided by the framework — do NOT re-import:
           from openprogram.agentic_programming.function import agentic_function
           from openprogram.agentic_programming.runtime import Runtime

    3. Where the LLM instructions go
       The instruction + data for each specific `runtime.exec` call MUST
       be written inside its `content=[...]`. The docstring can describe
       what this function does — including, in detail, what each LLM
       call asks the model — but that description does not propagate into
       the LLM call itself. You still have to write the per-call prompt
       in `content`, even when the docstring already explains it.

           # CORRECT — instruction + data together in content (docstring
           # is free to also explain what's happening; it just doesn't
           # replace this)
           runtime.exec(content=[{"type": "text", "text": (
               f"Classify the sentiment of the following text. Reply "
               f"with exactly one word: positive, negative, or neutral.\n\n"
               f"Text:\n{text}"
           )}])

           # WRONG — content is bare data, relying on docstring to
           # tell the LLM what to do. Some providers (codex CLI /
           # chatgpt subscription) ignore docstring as instruction and
           # will reply conversationally to the raw data.
           runtime.exec(content=[{"type": "text", "text": text}])

       Content shape (hard rule):
       - `content` is ALWAYS a `list[dict]`. Each item is a dict like
         `{"type": "text", "text": ...}` or `{"type": "image", "path": ...}`.
       - NEVER pass a bare string in the list: `content=[text]` is wrong.
       - NEVER pass a string instead of a list: `content=text` is wrong.

       runtime.exec accepted kwargs (use only these):
         content, response_format, model, tools, toolset, tools_source,
         tools_allow, tools_deny, tool_choice, parallel_tool_calls,
         max_iterations
       runtime.exec does NOT accept a `system=` parameter. Do not pass one.

    4. LLM-driven dispatch uses native tool_use
       To let the LLM choose between sub-functions, pass them to
       `runtime.exec(tools=[...])`. Do not hand-roll prompt menus — the
       framework auto-generates the JSON-schema tool spec from each
       @agentic_function's signature + docstring + `input=` metadata.

    5. Output format
       Reply with ONE ```python code fence containing the complete function
       (and any helpers it needs). No commentary outside the fence.

    6. Editing constraints
       When modifying existing code: preserve function name, parameter names
       and order, type hints, and existing `@agentic_function(input=..., ...)`
       arguments unless the instruction explicitly asks to change them.
       Never change `runtime: Runtime` to `Any`.

    7. Standard library imports allowed (os, json, re, pathlib, etc.).
       No async/await. Type hints required on every parameter and return.

    8. Robustness
       - Define exact output format inside the relevant `content=[...]`;
         do not let the LLM guess.
       - Validate external inputs (files, APIs) and raise on bad input.
       - When a result feeds another function, prefer structured types
         (TypedDict / dataclass / dict).
       - Never role-play ("You are a helpful assistant") or write filler
         ("Complete the task", "Please do X").

    Args:
        task: Complete task description (source code, errors, instructions).
              Any prior Q/A is context, not a new instruction to repeat.
        runtime: LLM runtime instance.

    Returns:
        str: LLM reply containing the code in a ```python fence.
    """
    spec = _load_metadata_spec()

    # Hard rules that LLMs (especially codex/gpt-5.5) tend to miss when
    # they live only in the system-side docstring. Repeat them in the
    # user-side content where they sit right next to the task.
    body_rules = (
        "=== runtime.exec hard rules (do not violate) ===\n"
        "1. content MUST be a list[dict]. Each item is a dict like\n"
        "     {\"type\": \"text\", \"text\": \"...\"}\n"
        "   or {\"type\": \"image\", \"path\": \"...\"}.\n"
        "   Never pass a bare string in the list (`content=[text]` is wrong).\n"
        "   Never pass a string instead of a list.\n"
        "2. runtime.exec accepts ONLY these kwargs:\n"
        "     content, response_format, model, tools, toolset, tools_source,\n"
        "     tools_allow, tools_deny, tool_choice, parallel_tool_calls,\n"
        "     max_iterations.\n"
        "   It does NOT accept `system=`. Do not invent kwargs.\n"
        "3. Where per-call LLM instructions live\n"
        "   The instructions for THIS specific exec call (what task, what\n"
        "   output format, any constraints) MUST go INSIDE the content\n"
        "   text, alongside the data. The function's docstring may also\n"
        "   describe what each LLM call does in as much detail as is\n"
        "   useful for readers — but the docstring does NOT propagate\n"
        "   into the LLM call. You still write the per-call prompt in\n"
        "   content, even if the docstring already explains it. (Some\n"
        "   providers, e.g. codex CLI / chatgpt subscription, ignore\n"
        "   docstring as instruction entirely.)\n"
        "\n"
        "Example of a correct call that classifies sentiment:\n"
        "    reply = runtime.exec(content=[{\"type\": \"text\", \"text\": (\n"
        "        \"Classify the sentiment of the following text. Reply with \"\n"
        "        \"exactly one word: positive, negative, or neutral.\\n\\n\"\n"
        "        f\"Text:\\n{text}\"\n"
        "    )}])\n"
        "\n"
        "Bad (instructions left only in docstring, content is bare data):\n"
        "    reply = runtime.exec(content=[{\"type\": \"text\", \"text\": text}])\n"
        "=== End of hard rules ===\n\n"
    )

    parts = []
    if spec:
        parts.append("=== Function metadata specification (must follow) ===\n\n")
        parts.append(spec)
        parts.append("\n\n=== End of specification ===\n\n")
    parts.append(body_rules)
    parts.append(task)
    return runtime.exec(content=[
        {"type": "text", "text": "".join(parts)},
    ])
