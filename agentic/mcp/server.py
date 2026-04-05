"""
Agentic Programming MCP Server.

Exposes agentic functions as MCP tools so any MCP-compatible client
(Claude Desktop, Cursor, VS Code, etc.) can create, run, fix, and
list agentic functions.

Usage:
    # stdio transport (default for Claude Desktop / IDE extensions)
    python -m agentic.mcp.server

    # Or add to your MCP client config:
    {
        "mcpServers": {
            "agentic": {
                "command": "python",
                "args": ["-m", "agentic.mcp.server"]
            }
        }
    }
"""

import importlib
import inspect
import json
import os
import sys

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Agentic Programming")


def _get_functions_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "functions")


def _get_apps_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "apps")


# ── Tools ──────────────────────────────────────────────────────


@mcp.tool()
def list_functions() -> str:
    """List all saved agentic functions.

    Returns a JSON array of {name, description} objects.
    """
    functions_dir = _get_functions_dir()
    if not os.path.exists(functions_dir):
        return json.dumps([])

    results = []
    for f in sorted(os.listdir(functions_dir)):
        if not f.endswith(".py") or f == "__init__.py":
            continue
        name = f[:-3]
        filepath = os.path.join(functions_dir, f)
        desc = ""
        with open(filepath) as fh:
            content = fh.read()
        if '"""' in content:
            start = content.index('"""') + 3
            end = content.index('"""', start)
            desc = content[start:end].strip().split("\n")[0]
        results.append({"name": name, "description": desc})

    return json.dumps(results, indent=2)


@mcp.tool()
def run_function(name: str, args: str = "{}") -> str:
    """Run a saved agentic function by name.

    Args:
        name: Function name (e.g. "sentiment", "extract_domain").
        args: JSON object of keyword arguments (e.g. '{"text": "hello"}').

    Returns:
        The function's return value as a string.
    """
    try:
        mod = importlib.import_module(f"agentic.functions.{name}")
        fn = getattr(mod, name)
    except (ImportError, AttributeError):
        return f"Error: function '{name}' not found in agentic/functions/"

    kwargs = json.loads(args) if args else {}

    # Inject runtime if the function needs one
    source = ""
    if hasattr(fn, '_fn'):
        try:
            source = inspect.getsource(fn._fn)
        except (OSError, TypeError):
            pass

    if "runtime.exec" in source or "runtime" in str(getattr(fn, '__globals__', {})):
        from agentic.providers import create_runtime
        runtime = create_runtime()
        if hasattr(fn, '_fn') and fn._fn:
            fn._fn.__globals__['runtime'] = runtime
        elif hasattr(fn, '__globals__'):
            fn.__globals__['runtime'] = runtime

    result = fn(**kwargs)
    return str(result)


@mcp.tool()
def create_function(description: str, name: str) -> str:
    """Create a new agentic function from a natural language description.

    The LLM writes the code, the framework validates and sandboxes it,
    and the function is saved for future use.

    Args:
        description: What the function should do (e.g. "Analyze text sentiment").
        name: Function name (e.g. "sentiment").

    Returns:
        Confirmation message with the saved file path.
    """
    from agentic.meta_functions import create
    from agentic.providers import create_runtime

    runtime = create_runtime()
    fn = create(description=description, runtime=runtime, name=name)
    return f"Created function '{name}' → agentic/functions/{name}.py"


@mcp.tool()
def create_application(description: str, name: str = "app") -> str:
    """Create a complete runnable app with runtime setup, functions, and main().

    Generates a self-contained Python script that can be run directly.

    Args:
        description: What the app should do.
        name: App name (used as filename).

    Returns:
        Confirmation message with the saved file path.
    """
    from agentic.meta_functions import create_app
    from agentic.providers import create_runtime

    runtime = create_runtime()
    filepath = create_app(description=description, runtime=runtime, name=name)
    return f"Created app '{name}' → {filepath}"


@mcp.tool()
def fix_function(name: str, instruction: str = "") -> str:
    """Fix a broken agentic function using LLM analysis.

    Reads the function's source code and error history, then rewrites it.

    Args:
        name: Function name to fix.
        instruction: Optional guidance (e.g. "return JSON instead of plain text").

    Returns:
        Confirmation message.
    """
    try:
        mod = importlib.import_module(f"agentic.functions.{name}")
        fn = getattr(mod, name)
    except (ImportError, AttributeError):
        return f"Error: function '{name}' not found in agentic/functions/"

    from agentic.meta_functions import fix
    from agentic.providers import create_runtime

    runtime = create_runtime()
    fix(fn=fn, runtime=runtime, instruction=instruction or None, name=name)
    return f"Fixed function '{name}' → agentic/functions/{name}.py"


# ── Entry point ────────────────────────────────────────────────

def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
