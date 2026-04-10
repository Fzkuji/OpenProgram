"""
Visualization server — FastAPI + WebSocket for real-time Context tree viewing
and interactive chat-style function execution.

Runs in a background thread alongside user code. Streams tree updates to
connected browsers via WebSocket.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import sys
import threading
import time
import traceback
import uuid
from typing import Any, Optional

from agentic.context import Context, _current_ctx, on_event, off_event
from agentic.function import agentic_function
from agentic.runtime import Runtime

# ---------------------------------------------------------------------------
# Pause/resume machinery
# ---------------------------------------------------------------------------
_pause_event = threading.Event()
_pause_event.set()  # starts un-paused


def pause_execution():
    """Block agentic functions from proceeding (cooperative)."""
    _pause_event.clear()


def resume_execution():
    """Resume blocked agentic functions."""
    _pause_event.set()


def wait_if_paused():
    """Called by the event hook; blocks until resumed."""
    _pause_event.wait()


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
_root_contexts: list[dict] = []
_root_contexts_lock = threading.Lock()
_ws_connections: list[Any] = []
_ws_lock = threading.Lock()
_loop: Optional[asyncio.AbstractEventLoop] = None

# Conversation storage (in-memory)
_conversations: dict[str, dict] = {}
_conversations_lock = threading.Lock()

# Global default provider (used when creating new conversations)
_default_provider = None
_default_runtime = None
_runtime_lock = threading.Lock()

_CLI_PROVIDERS = {"codex", "claude-code", "gemini-cli"}


def _create_runtime_for_visualizer(provider: str):
    """Create a runtime appropriate for the visualizer.

    Strategy per provider:
      - Codex CLI:       session_id=None + search=True → stateless, Context tree
                         injects history, Codex handles current-info lookups
      - Claude Code CLI: default (persistent process), has_session=True → process
                         manages its own context, summarize() skipped
      - Gemini CLI:      default → session auto-managed by CLI
      - API providers:   default → stateless, Context tree injects history
    """
    from agentic.providers import create_runtime
    if provider == "codex":
        # Keep visualizer chat stateless so Context tree stays source of truth.
        return create_runtime(provider=provider, session_id=None, search=True)
    # Claude Code, Gemini CLI, APIs: use default behavior
    return create_runtime(provider=provider)


def _detect_default_provider() -> tuple:
    """Auto-detect best provider, return (provider_name, runtime).

    Tries each provider in order. For CLI providers, runs a quick health
    check (a trivial LLM call) to verify the provider is actually usable
    (not just installed). This catches quota exhaustion, auth expiry, etc.
    """
    for p in ("codex", "claude-code", "gemini-cli", "gemini", "anthropic", "openai"):
        try:
            rt = _create_runtime_for_visualizer(p)
            return p, rt
        except Exception:
            continue
    raise RuntimeError("No provider available")


def _get_conv_runtime(conv_id: str, msg_id: str = None):
    """Get runtime for a conversation, creating if needed."""
    global _default_provider, _default_runtime

    conv = _conversations.get(conv_id)
    if conv and conv.get("runtime"):
        return conv["runtime"]

    # Initialize default if needed
    with _runtime_lock:
        if _default_runtime is None:
            _default_provider, _default_runtime = _detect_default_provider()

    # Create runtime for this conversation using default provider + current model
    rt = _create_runtime_for_visualizer(_default_provider)
    if _default_runtime and _default_runtime.model:
        rt.model = _default_runtime.model
    if conv:
        conv["runtime"] = rt
        conv["provider_name"] = _default_provider
    return rt


def _switch_runtime(provider: str, conv_id: str = None, msg_id: str = None):
    """Switch provider. Updates current conversation + global default."""
    global _default_provider, _default_runtime

    with _runtime_lock:
        if conv_id and msg_id:
            _broadcast_chat_response(conv_id, msg_id, {
                "type": "status",
                "content": f"Switching to {provider}...",
            })

        try:
            if provider == "auto":
                name, rt = _detect_default_provider()
            else:
                name, rt = provider, _create_runtime_for_visualizer(provider)
        except Exception as e:
            if conv_id and msg_id:
                _broadcast_chat_response(conv_id, msg_id, {
                    "type": "error",
                    "content": f"Failed to set up {provider}: {e}",
                })
            raise

        # Update global default
        _default_provider = name
        _default_runtime = rt

        # Update current conversation's runtime
        if conv_id:
            with _conversations_lock:
                conv = _conversations.get(conv_id)
            if conv:
                conv["runtime"] = _create_runtime_for_visualizer(name)
                conv["provider_name"] = name

        if conv_id and msg_id:
            _broadcast_chat_response(conv_id, msg_id, {
                "type": "status",
                "content": f"Using {name} ({rt.model})",
            })

        # Broadcast to all clients
        _broadcast(json.dumps({
            "type": "provider_changed",
            "data": _get_provider_info(conv_id),
        }))

        return rt


def _get_provider_info(conv_id: str = None) -> dict:
    """Get provider info. If conv_id given, return that conversation's provider."""
    provider_name = _default_provider
    runtime = _default_runtime

    if conv_id:
        with _conversations_lock:
            conv = _conversations.get(conv_id)
        if conv and conv.get("runtime"):
            runtime = conv["runtime"]
            provider_name = conv.get("provider_name", _default_provider)

    if runtime is None:
        return {"provider": None, "type": None, "model": None, "runtime": None, "session_id": None}

    provider_type = "CLI" if provider_name in _CLI_PROVIDERS else "API"
    session_id = getattr(runtime, '_session_id', None)
    return {
        "provider": provider_name,
        "type": provider_type,
        "model": runtime.model,
        "runtime": type(runtime).__name__,
        "session_id": session_id,
    }


_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".agentic", "config.json")
_SESSIONS_PATH = os.path.join(os.path.expanduser("~"), ".agentic", "visualizer_sessions.json")


def _save_sessions():
    """Persist all conversations to disk so they survive restarts."""
    data = {}
    with _conversations_lock:
        for conv_id, conv in _conversations.items():
            root_ctx = conv.get("root_context")
            if root_ctx is None:
                continue
            runtime = conv.get("runtime")
            session_id = getattr(runtime, '_session_id', None)
            model = getattr(runtime, 'model', None)
            data[conv_id] = {
                "id": conv_id,
                "title": conv.get("title", "Untitled"),
                "provider_name": conv.get("provider_name"),
                "session_id": session_id,
                "model": model,
                "created_at": conv.get("created_at"),
                "context_tree": root_ctx._to_dict(),
            }
    try:
        os.makedirs(os.path.dirname(_SESSIONS_PATH), exist_ok=True)
        with open(_SESSIONS_PATH, "w") as f:
            json.dump(data, f, ensure_ascii=False, default=str, indent=2)
    except Exception as e:
        _log(f"[save_sessions] error: {e}")


def _restore_sessions():
    """Restore conversations from disk on startup."""
    global _default_provider, _default_runtime
    try:
        with open(_SESSIONS_PATH) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return

    for conv_id, conv_data in data.items():
        try:
            root_ctx = Context.from_dict(conv_data.get("context_tree", {}))
            root_ctx.status = "idle"

            provider_name = conv_data.get("provider_name")
            session_id = conv_data.get("session_id")
            model = conv_data.get("model")

            # Recreate runtime with the saved session_id so it can resume
            runtime = None
            if provider_name:
                try:
                    runtime = _create_runtime_for_visualizer(provider_name)
                    if model:
                        runtime.model = model
                    # Restore Codex session state
                    if session_id and hasattr(runtime, '_session_id'):
                        runtime._session_id = session_id
                        runtime._turn_count = 1  # so next call uses resume
                        runtime.has_session = True
                except Exception:
                    pass

            with _conversations_lock:
                _conversations[conv_id] = {
                    "id": conv_id,
                    "title": conv_data.get("title", "Untitled"),
                    "root_context": root_ctx,
                    "runtime": runtime,
                    "provider_name": provider_name,
                    "created_at": conv_data.get("created_at", time.time()),
                    "_titled": True,
                }
            _log(f"[restore] conv {conv_id}: {conv_data.get('title')} (session={session_id})")
        except Exception as e:
            _log(f"[restore] failed for {conv_id}: {e}")


def _load_config() -> dict:
    """Load config from ~/.agentic/config.json."""
    try:
        with open(_CONFIG_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_config(config: dict):
    """Save config to ~/.agentic/config.json."""
    os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
    with open(_CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def _get_api_key(env_var: str) -> str:
    """Get API key from environment or config file."""
    val = os.environ.get(env_var)
    if val:
        return val
    config = _load_config()
    return config.get("api_keys", {}).get(env_var, "")


def _apply_config_keys():
    """Inject config file API keys into environment (if not already set)."""
    config = _load_config()
    for env_var, val in config.get("api_keys", {}).items():
        if val and not os.environ.get(env_var):
            os.environ[env_var] = val


# Apply config keys on module load
_apply_config_keys()


def _list_providers() -> list[dict]:
    """List available providers and their status."""
    import shutil
    result = []
    checks = [
        # (name, label, available_check, env_keys_for_config_or_None_if_CLI)
        ("codex", "Codex CLI", lambda: shutil.which("codex") is not None, None),
        ("claude-code", "Claude Code CLI", lambda: shutil.which("claude") is not None, None),
        ("gemini-cli", "Gemini CLI", lambda: shutil.which("gemini") is not None, None),
        ("anthropic", "Anthropic API", lambda: bool(_get_api_key("ANTHROPIC_API_KEY")), ["ANTHROPIC_API_KEY"]),
        ("openai", "OpenAI API", lambda: bool(_get_api_key("OPENAI_API_KEY")), ["OPENAI_API_KEY"]),
        ("gemini", "Gemini API", lambda: bool(_get_api_key("GOOGLE_API_KEY") or _get_api_key("GOOGLE_GENERATIVE_AI_API_KEY")), ["GOOGLE_API_KEY"]),
    ]
    for name, label, check, env_keys in checks:
        available = check()
        result.append({
            "name": name,
            "label": label,
            "available": available,
            "active": name == _default_provider,
            "configurable": env_keys is not None,
            "configured": available if env_keys else None,
            "env_keys": env_keys,
        })
    return result


def _find_root(ctx_data: dict) -> Optional[dict]:
    """Walk up to the root of a context path and find the stored root."""
    path = ctx_data.get("path", "")
    root_name = path.split("/")[0] if "/" in path else path
    with _root_contexts_lock:
        for r in _root_contexts:
            if r.get("name") == root_name or r.get("path") == root_name:
                return r
    return None


def _on_context_event(event_type: str, data: dict):
    """Callback registered with the Context event system."""
    _log(f"[event] {event_type}: {data.get('path', '?')} status={data.get('status', '?')}")
    # If we're paused and a node just got created, wait
    if event_type == "node_created":
        wait_if_paused()

    # Store/update root contexts
    path = data.get("path", "")
    if "/" not in path:
        # This is a root node
        with _root_contexts_lock:
            # Update existing or add new
            found = False
            for i, r in enumerate(_root_contexts):
                if r.get("path") == path:
                    _root_contexts[i] = data
                    found = True
                    break
            if not found:
                _root_contexts.append(data)

    # Broadcast to all connected WebSocket clients
    msg = json.dumps({"type": "event", "event": event_type, "data": data}, default=str)
    _broadcast(msg)


def _broadcast(msg: str):
    """Send a message to all connected WebSocket clients."""
    if not _ws_connections or _loop is None:
        return
    with _ws_lock:
        conns = list(_ws_connections)
    for ws in conns:
        try:
            asyncio.run_coroutine_threadsafe(ws.send_text(msg), _loop)
        except Exception:
            pass


def _log(text: str):
    """Print to terminal AND broadcast to frontend as a visible log."""
    print(text)
    try:
        msg = json.dumps({"type": "server_log", "text": text}, default=str)
        _broadcast(msg)
    except Exception:
        pass


def _log(text: str):
    """Print to terminal AND broadcast to frontend as a visible log."""
    print(text)
    try:
        msg = json.dumps({"type": "server_log", "text": text}, default=str)
        _broadcast(msg)
    except Exception:
        pass


def _get_full_tree() -> list[dict]:
    """Get current root-level context trees by walking active contexts."""
    # First check if there's a currently running context
    try:
        current = _current_ctx.get(None)
        if current is not None:
            # Walk up to root
            root = current
            while root.parent is not None:
                root = root.parent
            return [root._to_dict()]
    except Exception:
        pass

    with _root_contexts_lock:
        return list(_root_contexts)


def _find_node_by_path(tree: dict, path: str) -> Optional[dict]:
    """Find a node in a tree dict by its path."""
    if tree.get("path") == path:
        return tree
    for child in tree.get("children", []):
        result = _find_node_by_path(child, path)
        if result is not None:
            return result
    return None


# ---------------------------------------------------------------------------
# Function discovery
# ---------------------------------------------------------------------------

def _discover_functions() -> list[dict]:
    """Scan agentic/functions/ and agentic/meta_functions/ to build function list.

    Supports three kinds of entries in functions/:
      1. Single .py files (e.g. sentiment.py)
      2. Subdirectories with a main.py entry point (e.g. Research-Agent-Harness/main.py)
         The function name is extracted from the @agentic_function in main.py.
    """
    result = []
    base = os.path.dirname(os.path.dirname(__file__))

    # Meta functions
    meta_dir = os.path.join(base, "meta_functions")
    if os.path.isdir(meta_dir):
        for f in sorted(os.listdir(meta_dir)):
            if f.endswith(".py") and not f.startswith("_"):
                info = _extract_function_info(os.path.join(meta_dir, f), f[:-3], "meta")
                if info:
                    result.append(info)

    # Built-in functions (single files + subdirectory projects)
    fn_dir = os.path.join(base, "functions")
    if os.path.isdir(fn_dir):
        for f in sorted(os.listdir(fn_dir)):
            full_path = os.path.join(fn_dir, f)
            if f.endswith(".py") and not f.startswith("_"):
                # Scan for ALL @agentic_function decorated functions in file
                infos = _extract_all_functions(full_path, "builtin")
                result.extend(infos)
            elif os.path.isdir(full_path) and not f.startswith("_"):
                # Subdirectory project — find main.py at any depth
                for root, dirs, files in os.walk(full_path):
                    dirs[:] = [d for d in dirs if not d.startswith(("_", "."))]
                    if "main.py" in files:
                        main_py = os.path.join(root, "main.py")
                        info = _extract_function_info(main_py, None, "app")
                        if info:
                            result.append(info)
                        break  # one entry point per subdirectory project

    # Apps — scan apps/ for subdirectories with main.py at any depth
    apps_dir = os.path.join(base, "apps")
    if os.path.isdir(apps_dir):
        for f in sorted(os.listdir(apps_dir)):
            full_path = os.path.join(apps_dir, f)
            if os.path.isdir(full_path) and not f.startswith(("_", ".")):
                for root, dirs, files in os.walk(full_path):
                    dirs[:] = [d for d in dirs if not d.startswith(("_", "."))]
                    if "main.py" in files:
                        main_py = os.path.join(root, "main.py")
                        info = _extract_function_info(main_py, None, "app")
                        if info:
                            result.append(info)
                        break

    return result


def _extract_function_info(filepath: str, name: Optional[str], category: str) -> Optional[dict]:
    """Extract function name and docstring from a .py file.

    Args:
        filepath: Path to the .py file.
        name:     Function name. If None, auto-detect from @agentic_function.
        category: "meta", "builtin", "app", etc.
    """
    try:
        import re

        with open(filepath) as f:
            content = f.read()

        # If name not given, find the first @agentic_function decorated function
        if name is None:
            match = re.search(
                r"@agentic_function[^\n]*\s*def\s+(\w+)\s*\(",
                content,
            )
            if not match:
                return None
            name = match.group(1)

        doc = ""
        # Try to find the docstring of the specific function
        func_doc_pattern = rf'def\s+{re.escape(name)}\s*\([^)]*\)[^:]*:\s*\n\s*(?:\'\'\'|""")(.+?)(?:\'\'\'|""")'
        func_doc_match = re.search(func_doc_pattern, content, re.DOTALL)
        if func_doc_match:
            doc = func_doc_match.group(1).strip().split("\n")[0]
        elif '"""' in content:
            # Fallback: first docstring in file (module-level)
            start = content.index('"""') + 3
            end = content.index('"""', start)
            doc = content[start:end].strip().split("\n")[0]

        # Auto-generated functions get their own category
        effective_category = category
        if category == "builtin" and "Auto-generated by create()" in content:
            effective_category = "generated"

        # Try to extract parameter names from function signature
        params = []
        pattern = rf"def\s+{re.escape(name)}\s*\(([^)]*)\)"
        match = re.search(pattern, content)
        if match:
            param_str = match.group(1)
            for p in param_str.split(","):
                p = p.strip()
                if p and p != "self" and not p.startswith("*"):
                    pname = p.split(":")[0].split("=")[0].strip()
                    if pname:
                        params.append(pname)

        return {
            "name": name,
            "category": effective_category,
            "description": doc,
            "params": params,
            "filepath": filepath,
            "mtime": os.path.getmtime(filepath),
        }
    except Exception:
        return None


def _extract_all_functions(filepath: str, category: str) -> list[dict]:
    """Extract ALL @agentic_function decorated functions from a .py file.

    Unlike _extract_function_info (which finds one function by name),
    this scans for every @agentic_function in the file.
    """
    import re as _re
    results = []
    try:
        with open(filepath) as f:
            content = f.read()

        # Find all @agentic_function decorated functions (skip private _names)
        for match in _re.finditer(r"@agentic_function[^\n]*\s*def\s+(\w+)\s*\(", content):
            name = match.group(1)
            if name.startswith("_"):
                continue
            info = _extract_function_info(filepath, name, category)
            if info:
                results.append(info)

        # Fallback: if no @agentic_function found, try file-name match
        if not results:
            basename = os.path.splitext(os.path.basename(filepath))[0]
            info = _extract_function_info(filepath, basename, category)
            if info:
                results.append(info)
    except Exception:
        pass
    return results


# ---------------------------------------------------------------------------
# Agentic chat functions — the visualizer eats its own dog food
# ---------------------------------------------------------------------------

@agentic_function
def _chat_query(query: str, runtime: Runtime) -> str:
    """You are a helpful assistant for the Agentic Programming framework.
    Answer the user's question based on the conversation context.
    The context of previous messages and function results is automatically
    provided to you — just respond naturally."""
    return runtime.exec(content=[{"type": "text", "text": query}])


@agentic_function(compress=True)
def _run_user_function(func_name: str, kwargs: dict, runtime: Runtime) -> str:
    """Execute a user's agentic function. The result is compressed so the
    chat agent only sees the final output, not internal execution details."""
    loaded_func = _load_function(func_name)
    if loaded_func is None:
        return f"Function '{func_name}' not found."

    # Inject runtime if needed
    source = ""
    unwrapped_func = loaded_func._fn if hasattr(loaded_func, '_fn') else loaded_func
    try:
        source = inspect.getsource(unwrapped_func)
    except (OSError, TypeError):
        pass

    if "runtime" in source:
        sig = inspect.signature(unwrapped_func)
        if "runtime" in sig.parameters and "runtime" not in kwargs:
            kwargs["runtime"] = runtime
        elif hasattr(loaded_func, '_fn') and loaded_func._fn:
            loaded_func._fn.__globals__['runtime'] = runtime

    result = loaded_func(**kwargs)

    # Format result
    if callable(result):
        result_name = getattr(result, '__name__', 'unknown')
        result_doc = (getattr(result, '__doc__', '') or '').strip().split('\n')[0]
        try:
            result_sig = inspect.signature(result._fn if hasattr(result, '_fn') else result)
            params = [p for p in result_sig.parameters if p not in ('runtime', 'callback', 'self')]
        except (ValueError, TypeError):
            params = []
        msg = f"Created function `{result_name}`."
        if params:
            msg += f"\nUsage: `run {result_name} {' '.join(f'{p}=\"...\"' for p in params)}`"
        if result_doc:
            msg += f"\nDescription: {result_doc}"
        # Refresh function list
        functions = _discover_functions()
        _broadcast(json.dumps({"type": "functions_list", "data": functions}, default=str))
        return msg
    elif isinstance(result, str):
        return result
    else:
        try:
            return json.dumps(result, indent=2, default=str)
        except (TypeError, ValueError):
            return str(result)


def _retry_node(conv_id: str, msg_id: str, node_path: str, params_override: dict = None):
    """Re-execute a function at a specific node, creating a new sibling under the same parent.

    Removes all sibling nodes that came AFTER the target (rollback),
    then re-runs the function with original or overridden params.
    """
    try:
        _log(f"[retry] _retry_node started: conv_id={conv_id}, node_path={node_path}")
        conv = _conversations.get(conv_id)
        if not conv:
            _log(f"[retry] conversation not found: {conv_id}")
            _log(f"[retry] available conversations: {list(_conversations.keys())}")
            _broadcast_chat_response(conv_id, msg_id, {"type": "error", "content": f"Conversation not found: {conv_id}. Try sending a message first."})
            return

        root_ctx = conv["root_context"]
        _log(f"[retry] root context: {root_ctx.name}, path: {root_ctx.path}, children: {[c.name for c in root_ctx.children]}")
        target = root_ctx.find_by_path(node_path)
        if not target:
            # Debug: list all paths in tree
            all_paths = []
            def _collect_paths(node):
                all_paths.append(node.path)
                for c in node.children:
                    _collect_paths(c)
            _collect_paths(root_ctx)
            _log(f"[retry] node not found: {node_path}")
            _log(f"[retry] available paths: {all_paths}")
            _broadcast_chat_response(conv_id, msg_id, {"type": "error", "content": f"Node not found: {node_path}\nAvailable: {', '.join(all_paths[:10])}"})
            return

        parent = target.parent
        if parent is None:
            _log(f"[retry] cannot retry root node")
            _broadcast_chat_response(conv_id, msg_id, {"type": "error", "content": "Cannot retry root node"})
            return

        # Rollback: remove the target node AND all siblings after it
        target_idx = next((i for i, c in enumerate(parent.children) if c is target), None)
        if target_idx is not None:
            removed = [c.name for c in parent.children[target_idx:]]
            parent.children = parent.children[:target_idx]
            _log(f"[retry] rolled back {len(removed)} nodes (including target): {removed}")

        func_name = target.name
        # Use overridden params if provided, otherwise original (minus internal keys)
        if params_override:
            params = {k: v for k, v in params_override.items() if k not in ("runtime", "callback")}
        else:
            params = {k: v for k, v in (target.params or {}).items() if k not in ("runtime", "callback")}
        _log(f"[retry] func_name={func_name}, params keys={list(params.keys())}")
        runtime = _get_conv_runtime(conv_id, msg_id=msg_id)

        root_ctx.status = "running"

        # Broadcast the tree state AFTER rollback so frontend is in sync
        _broadcast_chat_response(conv_id, msg_id, {
            "type": "status",
            "content": f"Retrying {func_name}...",
            "context_tree": root_ctx._to_dict(),
        })
        token = _current_ctx.set(parent)
        try:
            # Look up function: user functions first, then server-internal ones
            fn = _load_function(func_name)
            if fn is None:
                loaded_func = globals().get(func_name)
            if loaded_func is None or not callable(loaded_func):
                _log(f"[retry] function not found: {func_name}")
                _broadcast_chat_response(conv_id, msg_id, {"type": "error", "content": f"Function '{func_name}' not found."})
                return

            _log(f"[retry] found function: {loaded_func}, calling with {list(params.keys())}")

            # Inject runtime (same logic as _run_user_function)
            unwrapped_func = loaded_func._fn if hasattr(loaded_func, '_fn') else loaded_func
            try:
                src = inspect.getsource(unwrapped_func)
            except (OSError, TypeError):
                src = ""
            if "runtime" in src:
                sig = inspect.signature(unwrapped_func)
                if "runtime" in sig.parameters:
                    params["runtime"] = runtime

            result = loaded_func(**params)
            result_str = str(result) if isinstance(result, str) else json.dumps(result, indent=2, default=str)
            _log(f"[retry] function completed successfully, result length: {len(result_str)}")

            _broadcast_chat_response(conv_id, msg_id, {
                "type": "result",
                "content": result_str,
                "function": func_name,
                "context_tree": root_ctx._to_dict(),
                "is_retry": True,
            })
        finally:
            _current_ctx.reset(token)
            root_ctx.status = "idle"

    except Exception as e:
        import traceback
        _log(f"[retry] exception: {e}\n{traceback.format_exc()}")
        _broadcast_chat_response(conv_id, msg_id, {
            "type": "error",
            "content": f"Retry failed: {e}\n\n{traceback.format_exc()}",
        })


def _load_function(func_name: str):
    """Load a function by name from meta_functions, functions, or subdirectory apps.

    Always reloads modules to pick up file changes without server restart.
    """
    meta_names = ["create", "fix", "create_app", "create_skill"]
    if func_name in meta_names:
        try:
            mod = importlib.import_module(f"agentic.meta_functions.{func_name}")
            importlib.reload(mod)
            return getattr(mod, func_name)
        except (ImportError, AttributeError):
            pass
    # Try single-file function: first by module name, then scan all modules
    from agentic.function import auto_trace_module, auto_trace_package
    fn_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "functions")
    try:
        mod = importlib.import_module(f"agentic.functions.{func_name}")
        importlib.reload(mod)
        auto_trace_module(mod, trace_pkg=os.path.abspath(fn_dir))
        return getattr(mod, func_name)
    except (ImportError, AttributeError):
        pass
    # Scan all .py files in functions/ for the function name
    if os.path.isdir(fn_dir):
        for f in sorted(os.listdir(fn_dir)):
            if f.endswith(".py") and not f.startswith("_"):
                mod_name = f"agentic.functions.{f[:-3]}"
                try:
                    mod = importlib.import_module(mod_name)
                    importlib.reload(mod)
                    auto_trace_module(mod, trace_pkg=os.path.abspath(fn_dir))
                    fn = getattr(mod, func_name, None)
                    if fn is not None:
                        return fn
                except ImportError:
                    pass
    # Try subdirectory projects — scan functions/ and apps/ for main.py at any depth
    import importlib.util as _imputil
    base = os.path.dirname(os.path.dirname(__file__))
    for search_dir in (os.path.join(base, "functions"), os.path.join(base, "apps")):
        if not os.path.isdir(search_dir):
            continue
        for d in os.listdir(search_dir):
            full_path = os.path.join(search_dir, d)
            if not os.path.isdir(full_path) or d.startswith(("_", ".")):
                continue
            for root, dirs, files in os.walk(full_path):
                dirs[:] = [x for x in dirs if not x.startswith(("_", "."))]
                if "main.py" in files:
                    main_py = os.path.join(root, "main.py")
                    spec = _imputil.spec_from_file_location(f"agentic.apps.{d}.main", main_py)
                    mod = _imputil.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    # Auto-trace all functions in the entire app package
                    pkg_dir = os.path.dirname(main_py)
                    auto_trace_package(pkg_dir)
                    # Re-fetch the function since module may have been reloaded
                    mod = sys.modules.get(mod.__name__, mod)
                    fn = getattr(mod, func_name, None)
                    if fn is not None:
                        return fn
                    break
    return None


# ---------------------------------------------------------------------------
# Conversation management — each conversation has a Context tree
# ---------------------------------------------------------------------------

def _get_or_create_conversation(conv_id: str = None) -> dict:
    """Get or create a conversation with its own Context tree and Runtime."""
    if conv_id is None:
        conv_id = str(uuid.uuid4())[:8]
    with _conversations_lock:
        if conv_id not in _conversations:
            _conversations[conv_id] = {
                "id": conv_id,
                "title": "New conversation",
                "root_context": Context(name="chat_session", status="idle", start_time=time.time()),
                "runtime": None,          # created lazily on first message
                "provider_name": None,
                "created_at": time.time(),
            }
        return _conversations[conv_id]


def _execute_in_context(conv_id: str, msg_id: str, action: str,
                        func_name: str = None, kwargs: dict = None, query: str = None):
    """Execute a chat query or function call within the conversation's Context tree.

    This is the core execution engine. Everything runs under the conversation's
    root Context, so summarize() automatically provides conversation history.
    """
    try:
        conv = _get_or_create_conversation(conv_id)
        root_ctx = conv["root_context"]
        runtime = _get_conv_runtime(conv_id, msg_id=msg_id)

        # Mark conversation as running
        root_ctx.status = "running"

        # Set the conversation root as the current context so that
        # @agentic_function calls become children of this root
        token = _current_ctx.set(root_ctx)

        try:
            if action == "query":
                # Chat query — @agentic_function with auto context
                _log(f"[exec] query: {query[:80]}...")
                _broadcast_chat_response(conv_id, msg_id, {
                    "type": "status", "content": "Thinking...",
                })
                result = _chat_query(query=query, runtime=runtime)
                _log(f"[exec] query completed, result length: {len(str(result))}")
                _broadcast_chat_response(conv_id, msg_id, {
                    "type": "result",
                    "content": str(result),
                    "function": "chat",
                    "context_tree": root_ctx._to_dict(),
                })

            elif action == "run":
                # Validate create() description
                if func_name == "create" and kwargs and "description" in kwargs:
                    desc = kwargs["description"].strip()
                    if len(desc) < 5:
                        _broadcast_chat_response(conv_id, msg_id, {
                            "type": "result",
                            "content": "Description too short. What function would you like to create?",
                            "function": func_name,
                        })
                        return
                    try:
                        check = runtime.exec(
                            f'Is this a clear description of a Python function? '
                            f'Reply ONLY "yes" or "no, <reason>".\n\nDescription: "{desc}"'
                        )
                        if check.strip().lower().startswith("no"):
                            reason = check.strip()[2:].strip().lstrip(",:").strip() or "unclear"
                            _broadcast_chat_response(conv_id, msg_id, {
                                "type": "result",
                                "content": f"Unclear description: {reason}\n\nPlease describe what the function should **do**.",
                                "function": func_name,
                            })
                            return
                    except Exception:
                        pass

                _log(f"[exec] running function: {func_name}({', '.join(f'{k}=...' for k in (kwargs or {}))})")
                _broadcast_chat_response(conv_id, msg_id, {
                    "type": "status",
                    "content": f"Running {func_name}...",
                })

                # Execute function under context tree (compress=True via _run_user_function)
                result = _run_user_function(
                    func_name=func_name,
                    kwargs=kwargs or {},
                    runtime=runtime,
                )
                _log(f"[exec] {func_name} completed, result length: {len(str(result))}")

                _broadcast_chat_response(conv_id, msg_id, {
                    "type": "result",
                    "content": str(result),
                    "function": func_name,
                    "context_tree": root_ctx._to_dict(),
                })

        finally:
            _current_ctx.reset(token)
            root_ctx.status = "idle"

        # Broadcast updated provider info (session_id may have been set)
        info = _get_provider_info(conv_id)
        _broadcast(json.dumps({"type": "provider_changed", "data": info}))

        # Update conversation title from first user message
        if not conv.get("_titled"):
            title = (query or func_name or "")[:50]
            if title:
                conv["title"] = title + ("..." if len(title) >= 50 else "")
                conv["_titled"] = True

        # Persist sessions to disk after each execution
        _save_sessions()

    except Exception as e:
        _broadcast_chat_response(conv_id, msg_id, {
            "type": "error",
            "content": f"Error: {e}\n\n{traceback.format_exc()}",
        })


def _broadcast_chat_response(conv_id: str, msg_id: str, response: dict):
    """Broadcast a chat response to all WebSocket clients."""
    response["conv_id"] = conv_id
    response["msg_id"] = msg_id
    response["timestamp"] = time.time()

    # No need to store in messages list — Context tree IS the storage
    msg = json.dumps({"type": "chat_response", "data": response}, default=str)
    _broadcast(msg)


def _parse_chat_input(text: str) -> dict:
    """Parse user input to determine intent.

    Returns dict with keys:
      - action: "run", "create", "fix", "query"
      - function: function name (if applicable)
      - kwargs: dict of arguments (if applicable)
      - raw: original text
    """
    text = text.strip()
    lower = text.lower()

    # "create ..." -> meta create
    if lower.startswith("create "):
        rest = text[7:].strip()
        # Check if it's "create app" or "create skill"
        if lower.startswith("create app "):
            return {"action": "run", "function": "create_app", "kwargs": {"description": text[11:].strip()}, "raw": text}
        if lower.startswith("create skill "):
            return {"action": "run", "function": "create_skill", "kwargs": {"name": text[13:].strip()}, "raw": text}
        # Parse: create "description" --name xxx  OR  create a function that...
        name = None
        desc = rest
        if "--name " in rest:
            idx = rest.index("--name ")
            name = rest[idx + 7:].strip().split()[0]
            desc = rest[:idx].strip().strip('"').strip("'")
        elif " as " in rest:
            parts = rest.rsplit(" as ", 1)
            desc = parts[0].strip().strip('"').strip("'")
            name = parts[1].strip()
        if not name:
            name = None  # Let create() auto-generate from description
        kwargs = {"description": desc}
        if name is not None:
            kwargs["name"] = name
        return {"action": "run", "function": "create", "kwargs": kwargs, "raw": text}

    # "fix ..." -> meta fix
    if lower.startswith("fix "):
        rest = text[4:].strip()
        parts = rest.split(maxsplit=1)
        name = parts[0]
        instruction = parts[1] if len(parts) > 1 else None
        kwargs = {"name": name}
        if instruction:
            kwargs["instruction"] = instruction
        return {"action": "run", "function": "fix", "kwargs": kwargs, "raw": text}

    # "run func_name key=val ..." -> direct run
    if lower.startswith("run "):
        rest = text[4:].strip()
        try:
            import shlex
            parts = shlex.split(rest)
        except ValueError:
            parts = rest.split()
        func_name = parts[0] if parts else ""
        kwargs = {}
        for p in parts[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
                # Strip surrounding quotes that shlex preserves on values
                if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                    v = v[1:-1]
                # Try to parse as JSON value
                try:
                    v = json.loads(v)
                except (json.JSONDecodeError, ValueError):
                    pass
                kwargs[k] = v
        return {"action": "run", "function": func_name, "kwargs": kwargs, "raw": text}

    # Check if text starts with a known function name
    available = _discover_functions()
    for f in available:
        fname = f["name"]
        if lower.startswith(fname + " ") or lower == fname:
            rest = text[len(fname):].strip()
            kwargs = {}
            # Try to parse remaining as key=value pairs
            for p in rest.split():
                if "=" in p:
                    k, v = p.split("=", 1)
                    try:
                        v = json.loads(v)
                    except (json.JSONDecodeError, ValueError):
                        pass
                    kwargs[k] = v
                elif f["params"]:
                    # Assign positionally to first unfilled param
                    for param_name in f["params"]:
                        if param_name not in kwargs and param_name != "runtime":
                            kwargs[param_name] = rest
                            break
                    break
            return {"action": "run", "function": fname, "kwargs": kwargs, "raw": text}

    # Default: general LLM query
    return {"action": "query", "raw": text}


# ---------------------------------------------------------------------------
# WebSocket handler (module-level to avoid FastAPI closure issues)
# ---------------------------------------------------------------------------

async def _websocket_handler(ws):
    """WebSocket endpoint for real-time Context tree updates and chat."""
    await ws.accept()

    with _ws_lock:
        _ws_connections.append(ws)
    try:
        # Send current state on connect
        tree = _get_full_tree()
        await ws.send_text(json.dumps(
            {"type": "full_tree", "data": tree}, default=str
        ))
        functions = _discover_functions()
        await ws.send_text(json.dumps(
            {"type": "functions_list", "data": functions}, default=str
        ))
        with _conversations_lock:
            history = [
                {"id": c["id"], "title": c["title"], "created_at": c["created_at"]}
                for c in _conversations.values()
            ]
        await ws.send_text(json.dumps(
            {"type": "history_list", "data": history}, default=str
        ))
        # Send current provider info
        await ws.send_text(json.dumps(
            {"type": "provider_info", "data": _get_provider_info()}, default=str
        ))

        # Keep alive — receive pings/messages
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
            else:
                try:
                    cmd = json.loads(data)
                    await _handle_ws_command(ws, cmd)
                except json.JSONDecodeError:
                    pass

    except Exception:
        pass
    finally:
        with _ws_lock:
            try:
                _ws_connections.remove(ws)
            except ValueError:
                pass


# ---------------------------------------------------------------------------
# WebSocket command handler (module-level so _websocket_handler can call it)
# ---------------------------------------------------------------------------

async def _handle_ws_command(ws, cmd: dict):
    """Handle a WebSocket command from the client."""
    action = cmd.get("action")

    if action == "chat":
        text = cmd.get("text", "").strip()
        conv_id = cmd.get("conv_id")
        if not text:
            return

        conv = _get_or_create_conversation(conv_id)
        conv_id = conv["id"]
        msg_id = str(uuid.uuid4())[:8]

        # Update title from first message
        if not conv.get("_titled"):
            conv["title"] = text[:50] + ("..." if len(text) > 50 else "")
            conv["_titled"] = True

        # Send acknowledgment with conv_id
        await ws.send_text(json.dumps({
            "type": "chat_ack",
            "data": {"conv_id": conv_id, "msg_id": msg_id},
        }))

        # Parse and execute
        parsed = _parse_chat_input(text)

        if parsed["action"] == "run":
            threading.Thread(
                target=_execute_in_context,
                args=(conv_id, msg_id, "run"),
                kwargs={"func_name": parsed["function"], "kwargs": parsed["kwargs"]},
                daemon=True,
            ).start()
        elif parsed["action"] == "query":
            threading.Thread(
                target=_execute_in_context,
                args=(conv_id, msg_id, "query"),
                kwargs={"query": parsed["raw"]},
                daemon=True,
            ).start()

    elif action == "retry_node":
        node_path = cmd.get("node_path")
        conv_id = cmd.get("conv_id")
        params_override = cmd.get("params")  # optional edited params
        _log(f"[retry] received retry_node: conv_id={conv_id}, node_path={node_path}")
        if not node_path or not conv_id:
            _log(f"[retry] missing node_path or conv_id, aborting")
            await ws.send_text(json.dumps({
                "type": "chat_response",
                "data": {"type": "error", "content": "Retry failed: missing node_path or conv_id", "conv_id": conv_id or "", "msg_id": "err"},
            }))
            return
        msg_id = str(uuid.uuid4())[:8]
        await ws.send_text(json.dumps({
            "type": "chat_ack",
            "data": {"conv_id": conv_id, "msg_id": msg_id},
        }))
        _log(f"[retry] starting retry thread msg_id={msg_id}")
        threading.Thread(
            target=_retry_node,
            args=(conv_id, msg_id, node_path, params_override),
            daemon=True,
        ).start()

    elif action == "delete_conversation":
        conv_id = cmd.get("conv_id")
        if conv_id:
            with _conversations_lock:
                conv = _conversations.pop(conv_id, None)
            if conv and conv.get("runtime") and hasattr(conv["runtime"], 'reset'):
                conv["runtime"].reset()
            _save_sessions()

    elif action == "clear_conversations":
        with _conversations_lock:
            for conv in _conversations.values():
                if conv.get("runtime") and hasattr(conv["runtime"], 'reset'):
                    conv["runtime"].reset()
            _conversations.clear()
        _save_sessions()

    elif action == "load_conversation":
        conv_id = cmd.get("conv_id")
        with _conversations_lock:
            conv = _conversations.get(conv_id)
        if conv:
            # Send conversation with Context tree + provider info
            tree_data = conv["root_context"]._to_dict() if conv.get("root_context") else {}
            await ws.send_text(json.dumps({
                "type": "conversation_loaded",
                "data": {
                    "id": conv["id"],
                    "title": conv["title"],
                    "context_tree": tree_data,
                    "provider_info": _get_provider_info(conv_id),
                },
            }, default=str))
        else:
            await ws.send_text(json.dumps({
                "type": "conversation_loaded",
                "data": {
                    "id": conv_id,
                    "title": "New conversation",
                    "context_tree": {},
                    "provider_info": _get_provider_info(),
                },
            }, default=str))


    elif action == "list_conversations":
        conv_list = []
        with _conversations_lock:
            for cid, conv in _conversations.items():
                conv_list.append({
                    "id": cid,
                    "title": conv.get("title", "Untitled"),
                    "created_at": conv.get("created_at"),
                })
        conv_list.sort(key=lambda c: c.get("created_at") or 0)
        await ws.send_text(json.dumps({
            "type": "conversations_list",
            "data": conv_list,
        }, default=str))


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

def create_app():
    """Create and return the FastAPI application."""
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse

    app = FastAPI(title="Agentic Visualizer", docs_url=None, redoc_url=None)

    # Serve the HTML frontend
    @app.get("/", response_class=HTMLResponse)
    async def index():
        from starlette.responses import Response
        html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
        with open(html_path) as f:
            content = f.read()
        return Response(
            content=content,
            media_type="text/html",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    @app.get("/config", response_class=HTMLResponse)
    async def config_page():
        from starlette.responses import Response
        html_path = os.path.join(os.path.dirname(__file__), "static", "config.html")
        with open(html_path) as f:
            content = f.read()
        return Response(
            content=content,
            media_type="text/html",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    @app.get("/programs", response_class=HTMLResponse)
    async def programs_page():
        from starlette.responses import Response
        html_path = os.path.join(os.path.dirname(__file__), "static", "programs.html")
        with open(html_path) as f:
            content = f.read()
        return Response(
            content=content,
            media_type="text/html",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    # WebSocket — use Starlette's raw WebSocketRoute to avoid FastAPI routing issues
    from starlette.routing import WebSocketRoute
    app.routes.insert(0, WebSocketRoute("/ws", _websocket_handler))

    # REST endpoints
    @app.get("/api/tree")
    async def get_tree():
        return JSONResponse(content=_get_full_tree())

    @app.get("/api/functions")
    async def get_functions():
        return JSONResponse(content=_discover_functions())

    @app.get("/api/programs/meta")
    async def get_programs_meta():
        meta_path = os.path.join(os.path.dirname(__file__), "programs_meta.json")
        if os.path.isfile(meta_path):
            with open(meta_path) as f:
                return JSONResponse(content=json.load(f))
        return JSONResponse(content={"favorites": [], "folders": {}})

    @app.post("/api/programs/meta")
    async def save_programs_meta(body: dict = None):
        meta_path = os.path.join(os.path.dirname(__file__), "programs_meta.json")
        with open(meta_path, "w") as f:
            json.dump(body, f, indent=2)
        return JSONResponse(content={"ok": True})

    @app.post("/api/chat")
    async def post_chat(body: dict = None):
        """Handle chat message via REST (alternative to WebSocket)."""
        if body is None:
            return JSONResponse(content={"error": "no body"}, status_code=400)
        text = body.get("text", "").strip()
        conv_id = body.get("conv_id")
        if not text:
            return JSONResponse(content={"error": "empty message"}, status_code=400)

        conv = _get_or_create_conversation(conv_id)
        conv_id = conv["id"]
        msg_id = str(uuid.uuid4())[:8]

        if not conv["messages"]:
            conv["title"] = text[:50]

        conv["messages"].append({
            "role": "user",
            "id": msg_id,
            "content": text,
            "timestamp": time.time(),
        })

        parsed = _parse_chat_input(text)

        if parsed["action"] == "run":
            threading.Thread(
                target=_execute_in_context,
                args=(conv_id, msg_id, "run"),
                kwargs={"func_name": parsed["function"], "kwargs": parsed["kwargs"]},
                daemon=True,
            ).start()
        elif parsed["action"] == "query":
            threading.Thread(
                target=_execute_in_context,
                args=(conv_id, msg_id, "query"),
                kwargs={"query": parsed["raw"]},
                daemon=True,
            ).start()

        return JSONResponse(content={"conv_id": conv_id, "msg_id": msg_id})

    @app.post("/api/run/{function_name}")
    async def run_function(function_name: str, body: dict = None):
        """Directly run a specific function."""
        kwargs = body or {}
        conv_id = kwargs.pop("_conv_id", None)
        conv = _get_or_create_conversation(conv_id)
        conv_id = conv["id"]
        msg_id = str(uuid.uuid4())[:8]

        threading.Thread(
            target=_execute_in_context,
            args=(conv_id, msg_id, "run"),
            kwargs={"func_name": function_name, "kwargs": kwargs},
            daemon=True,
        ).start()

        return JSONResponse(content={"conv_id": conv_id, "msg_id": msg_id})

    @app.get("/api/history")
    async def get_history():
        with _conversations_lock:
            history = [
                {"id": c["id"], "title": c["title"], "created_at": c["created_at"],
                 "message_count": len(c.get("root_context", Context()).children)}
                for c in sorted(_conversations.values(), key=lambda c: c["created_at"], reverse=True)
            ]
        return JSONResponse(content=history)

    @app.post("/api/history")
    async def save_history(body: dict = None):
        if body and "conv_id" in body:
            conv_id = body["conv_id"]
            with _conversations_lock:
                if conv_id in _conversations:
                    return JSONResponse(content={"saved": True})
        return JSONResponse(content={"saved": False})

    @app.post("/api/pause")
    async def api_pause():
        pause_execution()
        _broadcast(json.dumps({"type": "status", "paused": True}))
        return JSONResponse(content={"paused": True})

    @app.post("/api/resume")
    async def api_resume():
        resume_execution()
        _broadcast(json.dumps({"type": "status", "paused": False}))
        return JSONResponse(content={"paused": False})

    @app.get("/api/providers")
    async def get_providers():
        return JSONResponse(content=_list_providers())

    @app.post("/api/provider/{name}")
    async def switch_provider(name: str, body: dict = None):
        conv_id = body.get("conv_id") if body else None
        # Check if already active for this conversation
        if conv_id:
            with _conversations_lock:
                conv = _conversations.get(conv_id)
            if conv and conv.get("provider_name") == name:
                return JSONResponse(content={"switched": False, "already_active": True, "provider": name})
        elif name == _default_provider:
            return JSONResponse(content={"switched": False, "already_active": True, "provider": name})
        try:
            _switch_runtime(name, conv_id=conv_id)
            return JSONResponse(content={"switched": True, "provider": name})
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=400)

    @app.get("/api/models")
    async def list_models():
        """List available models for the current provider."""
        # Ensure runtime is initialized
        global _default_provider, _default_runtime
        with _runtime_lock:
            if _default_runtime is None:
                _default_provider, _default_runtime = _detect_default_provider()

        provider = _default_provider or "unknown"
        runtime = _default_runtime
        current_model = runtime.model if runtime else "default"

        # Auto-detect models from the runtime
        model_list = []
        if runtime and hasattr(runtime, 'list_models'):
            try:
                model_list = runtime.list_models()
            except Exception as e:
                print(f"[list_models] {provider} error: {e}")
        # Ensure current model is in the list
        if current_model and current_model not in model_list:
            model_list = [current_model] + model_list

        return JSONResponse(content={
            "provider": provider,
            "current": current_model,
            "models": model_list,
        })

    @app.post("/api/model")
    async def switch_model(body: dict = None):
        """Switch model for the current conversation's runtime."""
        if not body or "model" not in body:
            return JSONResponse(content={"error": "Missing model"}, status_code=400)
        model = body["model"].strip()
        conv_id = body.get("conv_id")
        if conv_id:
            with _conversations_lock:
                conv = _conversations.get(conv_id)
            if conv and conv.get("runtime"):
                conv["runtime"].model = model
                # Reset session — Codex threads are bound to a model
                if hasattr(conv["runtime"], 'reset'):
                    conv["runtime"].reset()
                info = _get_provider_info(conv_id)
                _broadcast(json.dumps({"type": "provider_changed", "data": info}))
                return JSONResponse(content={"switched": True, "model": model})
        # Update default runtime
        if _default_runtime:
            _default_runtime.model = model
            if hasattr(_default_runtime, 'reset'):
                _default_runtime.reset()
            info = _get_provider_info()
            _broadcast(json.dumps({"type": "provider_changed", "data": info}))
            return JSONResponse(content={"switched": True, "model": model})
        return JSONResponse(content={"error": "No active runtime"}, status_code=400)

    @app.get("/api/config")
    async def get_config():
        """Get current API key configuration (masked)."""
        config = _load_config()
        keys = config.get("api_keys", {})
        # Mask values: show first 8 chars + "..."
        masked = {k: (v[:8] + "..." if len(v) > 8 else "***") for k, v in keys.items() if v}
        return JSONResponse(content={"api_keys": masked})

    @app.post("/api/config")
    async def save_config(body: dict = None):
        """Save pre-verified API keys to config file and apply to environment."""
        if not body or "api_keys" not in body:
            return JSONResponse(content={"error": "Missing api_keys"}, status_code=400)
        config = _load_config()
        if "api_keys" not in config:
            config["api_keys"] = {}
        for key, val in body["api_keys"].items():
            val = val.strip()
            if val:
                config["api_keys"][key] = val
                os.environ[key] = val
            else:
                config["api_keys"].pop(key, None)
                os.environ.pop(key, None)
        _save_config(config)
        return JSONResponse(content={"saved": True})


    def _validate_api_key(env_var: str, value: str) -> str | None:
        """Validate an API key by making a lightweight test call. Returns error string or None."""
        try:
            if env_var == "OPENAI_API_KEY":
                import openai
                client = openai.OpenAI(api_key=value)
                client.models.list()
                return None
            elif env_var == "ANTHROPIC_API_KEY":
                import anthropic
                client = anthropic.Anthropic(api_key=value)
                client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=1,
                    messages=[{"role": "user", "content": "hi"}],
                )
                return None
            elif env_var in ("GOOGLE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY"):
                import google.generativeai as genai
                genai.configure(api_key=value)
                # Try models in order until one works
                for m in ("gemini-2.5-flash", "gemini-2.0-flash-lite", "gemini-1.5-flash"):
                    try:
                        model = genai.GenerativeModel(m)
                        model.generate_content("hi", generation_config={"max_output_tokens": 1})
                        return None
                    except Exception:
                        continue
                # Last resort: list models to verify key is valid
                list(genai.list_models())
                return None
            else:
                return None  # Unknown key type, skip validation
        except Exception as e:
            return str(e)

    @app.post("/api/config/verify")
    async def verify_key(body: dict = None):
        """Verify a single API key without saving."""
        if not body or "env" not in body:
            return JSONResponse(content={"error": "Missing env"}, status_code=400)
        value = body.get("value", "")
        # If masked value, use the stored real key
        if not value or value.endswith("..."):
            config = _load_config()
            value = config.get("api_keys", {}).get(body["env"], "")
        if not value:
            return JSONResponse(content={"valid": False, "error": "No key provided"})
        error = _validate_api_key(body["env"], value)
        return JSONResponse(content={"valid": error is None, "error": error})

    @app.get("/api/node/{path:path}")
    async def get_node(path: str):
        trees = _get_full_tree()
        for tree in trees:
            node = _find_node_by_path(tree, path)
            if node is not None:
                return JSONResponse(content=node)
        return JSONResponse(content={"error": "not found"}, status_code=404)

    # --- Function source code and meta-function operations ---

    @app.get("/api/function/{name}/source")
    async def get_function_source(name: str):
        """Return full source code of a function."""
        base = os.path.dirname(os.path.dirname(__file__))
        for subdir in ["functions", "meta_functions"]:
            filepath = os.path.join(base, subdir, f"{name}.py")
            if os.path.isfile(filepath):
                with open(filepath) as f:
                    source = f.read()
                return JSONResponse(content={
                    "name": name,
                    "source": source,
                    "filepath": filepath,
                    "category": "meta" if subdir == "meta_functions" else "builtin",
                })
        # Search subdirectory projects (app category)
        fn_dir = os.path.join(base, "functions")
        if os.path.isdir(fn_dir):
            for d in os.listdir(fn_dir):
                full_path = os.path.join(fn_dir, d)
                if os.path.isdir(full_path) and not d.startswith("_"):
                    for root, dirs, files in os.walk(full_path):
                        dirs[:] = [x for x in dirs if not x.startswith(("_", "."))]
                        if "main.py" in files:
                            main_py = os.path.join(root, "main.py")
                            with open(main_py) as f:
                                source = f.read()
                            info = _extract_function_info(main_py, None, "app")
                            if info and info["name"] == name:
                                return JSONResponse(content={
                                    "name": name,
                                    "source": source,
                                    "filepath": main_py,
                                    "category": "app",
                                })
                            break
        # Fallback: try to find as an internal function via inspect
        fn = _load_function(name)
        if fn is None:
            # Check server-module globals (e.g. _chat_query)
            fn = globals().get(name)
        if fn is not None and callable(fn):
            try:
                inner = getattr(fn, '_fn', fn)
                source = inspect.getsource(inner)
                return JSONResponse(content={
                    "name": name,
                    "source": source,
                    "filepath": inspect.getfile(inner),
                    "category": "internal",
                })
            except (OSError, TypeError):
                pass
        # Fallback: try to find as an internal function via inspect
        fn = _load_function(name)
        if fn is None:
            # Check server-module globals (e.g. _chat_query)
            fn = globals().get(name)
        if fn is not None and callable(fn):
            try:
                inner = getattr(fn, '_fn', fn)
                source = inspect.getsource(inner)
                return JSONResponse(content={
                    "name": name,
                    "source": source,
                    "filepath": inspect.getfile(inner),
                    "category": "internal",
                })
            except (OSError, TypeError):
                pass
        return JSONResponse(content={"error": f"Function '{name}' not found"}, status_code=404)

    @app.post("/api/function/{name}/edit")
    async def edit_function_source(name: str, body: dict = None):
        """Save edited source code for a function."""
        if not body or "source" not in body:
            return JSONResponse(content={"error": "no source provided"}, status_code=400)
        base = os.path.dirname(os.path.dirname(__file__))
        filepath = os.path.join(base, "functions", f"{name}.py")
        # Only allow editing user/builtin functions, not meta functions
        if not os.path.isfile(filepath):
            # Create new file
            pass
        try:
            # Validate syntax
            compile(body["source"], filepath, "exec")
        except SyntaxError as e:
            return JSONResponse(content={"error": f"Syntax error: {e}"}, status_code=400)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w") as f:
            f.write(body["source"])
        # Reload the module
        mod_name = f"agentic.functions.{name}"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        return JSONResponse(content={"saved": True, "filepath": filepath})

    @app.post("/api/function/{name}/fix")
    async def fix_function(name: str, body: dict = None):
        """Run meta fix() on a function."""
        instruction = (body or {}).get("instruction", "")
        conv_id = (body or {}).get("conv_id")
        conv = _get_or_create_conversation(conv_id)
        msg_id = str(uuid.uuid4())[:8]

        def _do_fix():
            try:
                from agentic.meta_functions import fix
                from agentic.providers import create_runtime
                mod = importlib.import_module(f"agentic.functions.{name}")
                fn = getattr(mod, name)
                runtime = create_runtime()
                fixed = fix(fn=fn, runtime=runtime, instruction=instruction or None)
                _broadcast_chat_response(conv_id, msg_id, {
                    "type": "result",
                    "content": f"Fixed function '{name}' successfully.",
                })
            except Exception as e:
                _broadcast_chat_response(conv_id, msg_id, {
                    "type": "error",
                    "content": f"Fix failed: {e}",
                })

        threading.Thread(target=_do_fix, daemon=True).start()
        return JSONResponse(content={"conv_id": conv["id"], "msg_id": msg_id})

    @app.delete("/api/function/{name}")
    async def delete_function(name: str):
        """Delete a user function file."""
        base = os.path.dirname(os.path.dirname(__file__))
        filepath = os.path.join(base, "functions", f"{name}.py")
        if not os.path.isfile(filepath):
            return JSONResponse(content={"error": "not found"}, status_code=404)
        # Don't allow deleting built-in functions
        builtin_names = ["general_action", "agent_loop", "wait", "deep_work", "_utils"]
        if name in builtin_names:
            return JSONResponse(content={"error": "cannot delete built-in function"}, status_code=403)
        os.remove(filepath)
        mod_name = f"agentic.functions.{name}"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        return JSONResponse(content={"deleted": True})

    @app.post("/api/function/create")
    async def create_function(body: dict = None):
        """Create a new function from description."""
        if not body or "description" not in body:
            return JSONResponse(content={"error": "no description"}, status_code=400)
        conv_id = body.get("conv_id")
        conv = _get_or_create_conversation(conv_id)
        msg_id = str(uuid.uuid4())[:8]
        name = body.get("name", "new_func")
        desc = body["description"]

        def _do_create():
            try:
                from agentic.meta_functions import create
                runtime = _get_runtime()
                fn = create(description=desc, runtime=runtime, name=name)
                _broadcast_chat_response(conv_id, msg_id, {
                    "type": "result",
                    "content": f"Created function '{name}' successfully.",
                })
                # Refresh functions list
                functions = _discover_functions()
                _broadcast(json.dumps({"type": "functions_list", "data": functions}, default=str))
            except Exception as e:
                _broadcast_chat_response(conv_id, msg_id, {
                    "type": "error",
                    "content": f"Create failed: {e}",
                })

        threading.Thread(target=_do_create, daemon=True).start()
        return JSONResponse(content={"conv_id": conv["id"], "msg_id": msg_id})

    @app.post("/api/register")
    async def register_external(body: dict = None):
        """Register an external module's functions (for GUI/Research Agent Harness integration)."""
        if not body or "module" not in body:
            return JSONResponse(content={"error": "no module path"}, status_code=400)
        module_path = body["module"]
        try:
            mod = importlib.import_module(module_path)
            # Scan for @agentic_function decorated callables
            registered = []
            for attr_name in dir(mod):
                obj = getattr(mod, attr_name)
                if callable(obj) and hasattr(obj, '_fn'):
                    registered.append(attr_name)
            return JSONResponse(content={
                "registered": True,
                "module": module_path,
                "functions": registered,
            })
        except ImportError as e:
            return JSONResponse(content={"error": f"Cannot import: {e}"}, status_code=400)

    return app


# ---------------------------------------------------------------------------
# Server runner (in background thread)
# ---------------------------------------------------------------------------

_server_thread: Optional[threading.Thread] = None


def start_server(port: int = 8765, open_browser: bool = True) -> threading.Thread:
    """
    Start the visualization server in a background daemon thread.

    Returns the thread object. The server runs until the process exits.
    """
    global _server_thread, _loop

    if _server_thread is not None and _server_thread.is_alive():
        print(f"Visualizer already running")
        return _server_thread

    # Restore saved sessions from disk
    _restore_sessions()

    # Register our event callback
    on_event(_on_context_event)

    def _run():
        global _loop
        try:
            import uvicorn
        except ImportError:
            raise ImportError(
                "uvicorn is required for the visualizer. "
                "Install with: pip install agentic-programming[visualize]"
            )

        app = create_app()
        config = uvicorn.Config(
            app, host="0.0.0.0", port=port,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config)
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
        _loop.run_until_complete(server.serve())

    _server_thread = threading.Thread(target=_run, daemon=True, name="agentic-visualizer")
    _server_thread.start()

    url = f"http://localhost:{port}"
    print(f"Agentic Visualizer running at {url}")

    if open_browser:
        # Small delay to let the server start
        def _open():
            import time
            time.sleep(0.8)
            import webbrowser
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    return _server_thread


def stop_server():
    """Clean up event callbacks."""
    off_event(_on_context_event)
