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
    """Scan agentic/functions/ and agentic/meta_functions/ to build function list."""
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

    # Built-in functions
    fn_dir = os.path.join(base, "functions")
    if os.path.isdir(fn_dir):
        for f in sorted(os.listdir(fn_dir)):
            if f.endswith(".py") and not f.startswith("_"):
                info = _extract_function_info(os.path.join(fn_dir, f), f[:-3], "builtin")
                if info:
                    result.append(info)

    return result


def _extract_function_info(filepath: str, name: str, category: str) -> Optional[dict]:
    """Extract function name and docstring from a .py file."""
    try:
        with open(filepath) as f:
            content = f.read()

        doc = ""
        if '"""' in content:
            start = content.index('"""') + 3
            end = content.index('"""', start)
            doc = content[start:end].strip().split("\n")[0]

        # Try to extract parameter names from function signature
        params = []
        # Look for def <name>(...)
        import re
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
            "category": category,
            "description": doc,
            "params": params,
            "filepath": filepath,
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Chat / execution engine
# ---------------------------------------------------------------------------

def _get_or_create_conversation(conv_id: str = None) -> dict:
    """Get or create a conversation by ID."""
    if conv_id is None:
        conv_id = str(uuid.uuid4())[:8]
    with _conversations_lock:
        if conv_id not in _conversations:
            _conversations[conv_id] = {
                "id": conv_id,
                "title": "New conversation",
                "messages": [],
                "created_at": time.time(),
            }
        return _conversations[conv_id]


def _run_function_in_thread(func_name: str, kwargs: dict, conv_id: str, msg_id: str):
    """Run a function in a background thread, broadcasting results via WebSocket."""
    try:
        # Try meta functions first
        fn = None
        runtime = None
        needs_runtime = False

        # Check meta functions
        meta_names = ["create", "fix", "create_app", "create_skill"]
        if func_name in meta_names:
            try:
                mod = importlib.import_module(f"agentic.meta_functions.{func_name}")
                fn = getattr(mod, func_name)
                needs_runtime = True
            except (ImportError, AttributeError):
                pass

        # Check built-in functions
        if fn is None:
            try:
                mod = importlib.import_module(f"agentic.functions.{func_name}")
                fn = getattr(mod, func_name)
                # Check if it needs runtime
                source = ""
                if hasattr(fn, '_fn'):
                    try:
                        source = inspect.getsource(fn._fn)
                    except (OSError, TypeError):
                        pass
                elif callable(fn):
                    try:
                        source = inspect.getsource(fn)
                    except (OSError, TypeError):
                        pass
                if "runtime" in source:
                    needs_runtime = True
            except (ImportError, AttributeError):
                pass

        if fn is None:
            _broadcast_chat_response(conv_id, msg_id, {
                "type": "error",
                "content": f"Function '{func_name}' not found.",
            })
            return

        # Set up runtime if needed
        if needs_runtime:
            try:
                from agentic.providers import create_runtime
                runtime = create_runtime()
                if "runtime" in (kwargs.keys()):
                    pass  # user provided
                else:
                    # Try to inject runtime
                    sig = inspect.signature(fn._fn if hasattr(fn, '_fn') else fn)
                    if "runtime" in sig.parameters:
                        kwargs["runtime"] = runtime
                    elif hasattr(fn, '_fn') and fn._fn:
                        fn._fn.__globals__['runtime'] = runtime
            except Exception as e:
                _broadcast_chat_response(conv_id, msg_id, {
                    "type": "error",
                    "content": f"Could not set up LLM runtime: {e}\nConfigure a provider first (see `agentic providers`).",
                })
                return

        # Execute
        _broadcast_chat_response(conv_id, msg_id, {
            "type": "status",
            "content": f"Running {func_name}({', '.join(f'{k}={repr(v)}' for k,v in kwargs.items())})...",
        })

        result = fn(**kwargs)

        # Get context tree if available
        context_tree = None
        if hasattr(fn, 'context') and fn.context is not None:
            context_tree = fn.context._to_dict()

        # Format result
        if isinstance(result, str):
            content = result
        else:
            try:
                content = json.dumps(result, indent=2, default=str)
            except (TypeError, ValueError):
                content = str(result)

        _broadcast_chat_response(conv_id, msg_id, {
            "type": "result",
            "content": content,
            "function": func_name,
            "context_tree": context_tree,
        })

    except Exception as e:
        tb = traceback.format_exc()
        _broadcast_chat_response(conv_id, msg_id, {
            "type": "error",
            "content": f"Error running {func_name}: {e}\n\n{tb}",
        })


def _run_general_query(query: str, conv_id: str, msg_id: str):
    """Run a general LLM query."""
    try:
        from agentic.providers import create_runtime
        runtime = create_runtime()

        _broadcast_chat_response(conv_id, msg_id, {
            "type": "status",
            "content": "Thinking...",
        })

        result = runtime.exec(query)

        _broadcast_chat_response(conv_id, msg_id, {
            "type": "result",
            "content": str(result),
            "function": "llm_query",
        })

    except ImportError:
        _broadcast_chat_response(conv_id, msg_id, {
            "type": "error",
            "content": "No LLM provider available. Configure one with `agentic providers`.",
        })
    except Exception as e:
        _broadcast_chat_response(conv_id, msg_id, {
            "type": "error",
            "content": f"Error: {e}",
        })


def _broadcast_chat_response(conv_id: str, msg_id: str, response: dict):
    """Broadcast a chat response to all WebSocket clients."""
    response["conv_id"] = conv_id
    response["msg_id"] = msg_id
    response["timestamp"] = time.time()

    # Store in conversation
    with _conversations_lock:
        if conv_id in _conversations:
            _conversations[conv_id]["messages"].append({
                "role": "assistant",
                "id": msg_id + "_resp",
                **response,
            })

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
            # Auto-generate name from description
            name = desc.split()[0].lower() if desc else "new_func"
            name = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
        return {"action": "run", "function": "create", "kwargs": {"description": desc, "name": name}, "raw": text}

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
        parts = rest.split()
        func_name = parts[0] if parts else ""
        kwargs = {}
        for p in parts[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
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
# FastAPI app
# ---------------------------------------------------------------------------

def create_app():
    """Create and return the FastAPI application."""
    try:
        from fastapi import FastAPI, WebSocket, WebSocketDisconnect
        from fastapi.responses import HTMLResponse, JSONResponse
    except ImportError:
        raise ImportError(
            "FastAPI is required for the visualizer. "
            "Install with: pip install agentic-programming[visualize]"
        )

    app = FastAPI(title="Agentic Visualizer", docs_url=None, redoc_url=None)

    # Serve the HTML frontend
    @app.get("/", response_class=HTMLResponse)
    async def index():
        html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
        with open(html_path) as f:
            return HTMLResponse(content=f.read())

    # WebSocket endpoint
    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        with _ws_lock:
            _ws_connections.append(ws)
        try:
            # Send current state on connect
            tree = _get_full_tree()
            await ws.send_text(json.dumps(
                {"type": "full_tree", "data": tree}, default=str
            ))
            # Send available functions
            functions = _discover_functions()
            await ws.send_text(json.dumps(
                {"type": "functions_list", "data": functions}, default=str
            ))
            # Send conversation history
            with _conversations_lock:
                history = [
                    {"id": c["id"], "title": c["title"], "created_at": c["created_at"]}
                    for c in _conversations.values()
                ]
            await ws.send_text(json.dumps(
                {"type": "history_list", "data": history}, default=str
            ))

            # Keep alive — receive pings/messages
            while True:
                data = await ws.receive_text()
                if data == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
                else:
                    # Try to parse as JSON command
                    try:
                        cmd = json.loads(data)
                        await _handle_ws_command(ws, cmd)
                    except json.JSONDecodeError:
                        pass

        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            with _ws_lock:
                try:
                    _ws_connections.remove(ws)
                except ValueError:
                    pass

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

            # Update title if first message
            if not conv["messages"]:
                conv["title"] = text[:50] + ("..." if len(text) > 50 else "")

            # Store user message
            conv["messages"].append({
                "role": "user",
                "id": msg_id,
                "content": text,
                "timestamp": time.time(),
            })

            # Send acknowledgment with conv_id
            await ws.send_text(json.dumps({
                "type": "chat_ack",
                "data": {"conv_id": conv_id, "msg_id": msg_id},
            }))

            # Parse and execute
            parsed = _parse_chat_input(text)

            if parsed["action"] == "run":
                threading.Thread(
                    target=_run_function_in_thread,
                    args=(parsed["function"], parsed["kwargs"], conv_id, msg_id),
                    daemon=True,
                ).start()
            elif parsed["action"] == "query":
                threading.Thread(
                    target=_run_general_query,
                    args=(parsed["raw"], conv_id, msg_id),
                    daemon=True,
                ).start()

        elif action == "load_conversation":
            conv_id = cmd.get("conv_id")
            with _conversations_lock:
                conv = _conversations.get(conv_id)
            if conv:
                await ws.send_text(json.dumps({
                    "type": "conversation_loaded",
                    "data": conv,
                }, default=str))

    # REST endpoints
    @app.get("/api/tree")
    async def get_tree():
        return JSONResponse(content=_get_full_tree())

    @app.get("/api/functions")
    async def get_functions():
        return JSONResponse(content=_discover_functions())

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
                target=_run_function_in_thread,
                args=(parsed["function"], parsed["kwargs"], conv_id, msg_id),
                daemon=True,
            ).start()
        elif parsed["action"] == "query":
            threading.Thread(
                target=_run_general_query,
                args=(parsed["raw"], conv_id, msg_id),
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
            target=_run_function_in_thread,
            args=(function_name, kwargs, conv_id, msg_id),
            daemon=True,
        ).start()

        return JSONResponse(content={"conv_id": conv_id, "msg_id": msg_id})

    @app.get("/api/history")
    async def get_history():
        with _conversations_lock:
            history = [
                {"id": c["id"], "title": c["title"], "created_at": c["created_at"],
                 "message_count": len(c["messages"])}
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
                from agentic.providers import create_runtime
                runtime = create_runtime()
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
