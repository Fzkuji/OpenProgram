"""render_options — render the LLM-facing options menu from a function list (or legacy registry dict)."""

from __future__ import annotations

from openprogram.programs.functions.buildin._utils import _functions_to_registry


_TYPE_PLACEHOLDERS = {
    "str":   '"<str>"',
    "int":   "0",
    "float": "0.0",
    "bool":  "false",
    "list":  "[]",
    "dict":  "{}",
}


def render_options(available) -> str:
    """Render the LLM-facing options menu.

    Accepts either:
      - a list of callables (recommended; typically ``@agentic_function``-
        decorated). Metadata is auto-extracted from ``fn.__doc__`` and
        ``fn.input_meta``. Plain callables work too (less rich metadata).
      - a registry dict (legacy form; see schema in the
        ``_functions_to_registry`` helper).

    Only shows parameters with source="llm" — parameters the LLM needs
    to decide. Context-filled and runtime params are hidden.

    Returns:
        Formatted string describing the available actions, including a
        per-action ``Call:`` example line whose placeholder values are
        JSON-native literals (``0``, ``false``, ``[]``, ``{}``, or
        ``"<str>"``).
    """
    if isinstance(available, (list, tuple)):
        available = _functions_to_registry(available)

    lines = []

    for name, spec in available.items():
        description = spec.get("description", "")
        input_spec = spec.get("input", {})

        # Collect LLM-visible parameters
        llm_params = []
        param_details = []
        for param_name, param_info in input_spec.items():
            if param_info.get("source") != "llm":
                continue
            type_obj = param_info.get("type", str)
            type_name = getattr(type_obj, "__name__", None) or str(type_obj)
            llm_params.append(f"{param_name}: {type_name}")

            detail = f"    {param_name}"
            if "description" in param_info:
                detail += f": {param_info['description']}"
            if "options" in param_info:
                opts = ", ".join(f'"{o}"' for o in param_info["options"])
                detail += f" (options: {opts})"
            param_details.append(detail)

        sig = f"{name}({', '.join(llm_params)})" if llm_params else f"{name}()"
        lines.append(sig)

        if description:
            lines.append(f"    {description}")

        lines.extend(param_details)

        if llm_params:
            example_args = ", ".join(
                f'"{p.split(":")[0].strip()}": '
                + _TYPE_PLACEHOLDERS.get(p.split(":")[1].strip(), "null")
                for p in llm_params
            )
            lines.append(f'    Call: {{"call": "{name}", "args": {{{example_args}}}}}')
        else:
            lines.append(f'    Call: {{"call": "{name}"}}')

        lines.append("")  # blank line

    return "\n".join(lines)
