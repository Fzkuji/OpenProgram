"""build_catalog — generate a function catalog for the LLM from a registry."""

from __future__ import annotations


def build_catalog(available: dict) -> str:
    """Build a function catalog string from a function registry.

    Only shows parameters with source="llm" — parameters the LLM needs
    to decide. Context-filled and runtime params are hidden.

    Args:
        available: Function registry dict. Each entry:
            {
                "function": callable,
                "description": str,
                "input": {
                    "param_name": {
                        "source": "llm" or "context",
                        "type": type,           # optional
                        "options": [...],        # optional
                        "description": str,      # optional
                    },
                },
                "output": {field: type, ...},
            }

    Returns:
        Formatted string describing available functions.
    """
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
            opts = param_info.get("options")
            if opts:
                opts_str = ", ".join(f'"{o}"' for o in opts)
                detail += f" (options: {opts_str})"
            param_details.append(detail)

        # Function signature
        sig = f"{name}({', '.join(llm_params)})" if llm_params else f"{name}()"
        lines.append(sig)

        # Description
        if description:
            lines.append(f"    {description}")

        # Parameter details
        for detail in param_details:
            lines.append(detail)

        # Call example
        if llm_params:
            example_args = ", ".join(
                f'"{p.split(":")[0].strip()}": "..."' for p in llm_params
            )
            lines.append(f'    call: {{"call": "{name}", "args": {{{example_args}}}}}')
        else:
            lines.append(f'    call: {{"call": "{name}"}}')

        lines.append("")  # blank line

    return "\n".join(lines)
