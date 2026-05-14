"""Function source / editor / create / delete endpoints.

Powers the program editor UI: read source, save edits, meta-edit via LLM,
create from description, delete a user function. Also includes
``/api/node/{path}`` which inspects the function tree.
"""
from __future__ import annotations

import importlib
import inspect
import json
import os
import re
import sys
import threading
import uuid

from fastapi.responses import JSONResponse


def register(app):
    @app.get("/api/node/{path:path}")
    async def get_node(path: str):
        from openprogram.webui import server as _s
        trees = _s._get_full_tree()
        for tree in trees:
            node = _s._find_node_by_path(tree, path)
            if node is not None:
                return JSONResponse(content=node)
        return JSONResponse(content={"error": "not found"}, status_code=404)

    @app.get("/api/function/{name}/source")
    async def get_function_source(name: str):
        """Return full source code of a function."""
        from openprogram.webui import server as _s
        base = os.path.dirname(os.path.dirname(_s.__file__))
        for rel_subdir, category in (
            (("programs", "functions", "meta"), "meta"),
            (("programs", "functions", "buildin"), "builtin"),
            (("programs", "functions", "third_party"), "external"),
        ):
            filepath = os.path.join(base, *rel_subdir, f"{name}.py")
            if os.path.isfile(filepath):
                with open(filepath) as f:
                    source = f.read()
                return JSONResponse(content={
                    "name": name,
                    "source": source,
                    "filepath": filepath,
                    "category": category,
                })
        # Subdirectory app projects
        fn_dir = os.path.join(base, "programs", "applications")
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
                            info = _s._extract_function_info(main_py, None, "app")
                            if info and info["name"] == name:
                                return JSONResponse(content={
                                    "name": name,
                                    "source": source,
                                    "filepath": main_py,
                                    "category": "app",
                                })
                            break
        # Internal function via inspect
        fn = _s._load_function(name)
        if fn is None:
            fn = getattr(_s, name, None)
        if fn is not None and callable(fn):
            try:
                inner = getattr(fn, '__wrapped__', None) or getattr(fn, '_fn', None) or fn
                source = inspect.getsource(inner)
                return JSONResponse(content={
                    "name": name,
                    "source": source,
                    "filepath": inspect.getfile(inner),
                    "category": "internal",
                })
            except (OSError, TypeError):
                pass
        # @agentic_function registry
        from openprogram.agentic_programming.function import _registry
        if name in _registry:
            reg_fn = _registry[name]._fn
            try:
                source = inspect.getsource(reg_fn)
                return JSONResponse(content={
                    "name": name,
                    "source": source,
                    "filepath": inspect.getfile(reg_fn),
                    "category": "external",
                })
            except (OSError, TypeError):
                pass

        # Grep app project directories (handles symlinked externals)
        apps_dir = os.path.join(base, "programs", "applications")
        func_pattern = re.compile(rf'def\s+{re.escape(name)}\s*\(')
        if os.path.isdir(apps_dir):
            for root, dirs, files in os.walk(apps_dir, followlinks=True):
                dirs[:] = [d for d in dirs if not d.startswith(('.', '_'))
                           and d not in {'node_modules', 'vendor', '__pycache__',
                                         'desktop_env', 'libs', 'build', 'dist',
                                         'benchmarks', 'docs', 'tests', 'memory',
                                         'cache', 'skills', 'actions', 'platforms'}]
                for f in files:
                    if not f.endswith('.py'):
                        continue
                    filepath = os.path.join(root, f)
                    try:
                        with open(filepath) as fh:
                            source = fh.read()
                        if func_pattern.search(source):
                            return JSONResponse(content={
                                "name": name,
                                "source": source,
                                "filepath": filepath,
                                "category": "external",
                            })
                    except (OSError, UnicodeDecodeError):
                        continue

        return JSONResponse(content={"error": f"Function '{name}' not found"}, status_code=404)

    @app.post("/api/function/{name}/edit")
    async def edit_function_source(name: str, body: dict = None):
        """Save edited source code for a function."""
        from openprogram.webui import server as _s
        if not body or "source" not in body:
            return JSONResponse(content={"error": "no source provided"}, status_code=400)
        base = os.path.dirname(os.path.dirname(_s.__file__))
        filepath = os.path.join(base, "programs", "functions", "third_party", f"{name}.py")
        try:
            compile(body["source"], filepath, "exec")
        except SyntaxError as e:
            return JSONResponse(content={"error": f"Syntax error: {e}"}, status_code=400)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w") as f:
            f.write(body["source"])
        mod_name = f"openprogram.programs.functions.third_party.{name}"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        return JSONResponse(content={"saved": True, "filepath": filepath})

    # NOTE: The original file registers a SECOND handler for the same
    # path that runs the meta-edit() helper. FastAPI keeps both; the
    # second one wins for matching requests. Preserved verbatim.
    @app.post("/api/function/{name}/edit")
    async def edit_function(name: str, body: dict = None):
        """Run meta edit() on a function."""
        from openprogram.webui import server as _s
        instruction = (body or {}).get("instruction", "")
        session_id = (body or {}).get("session_id")
        conv = _s._get_or_create_session(session_id)
        msg_id = str(uuid.uuid4())[:8]

        def _do_edit():
            _s._broadcast_chat_response(session_id, msg_id, {
                "type": "error",
                "content": (
                    "The /edit endpoint has been removed. Open the chat "
                    "and load the agentic-program skill — the agent will "
                    "edit .py files directly using its file-editing tools."
                ),
            })

        threading.Thread(target=_do_edit, daemon=True).start()
        return JSONResponse(content={"session_id": conv["id"], "msg_id": msg_id})

    @app.delete("/api/function/{name}")
    async def delete_function(name: str):
        """Delete a user function file."""
        from openprogram.webui import server as _s
        base = os.path.dirname(os.path.dirname(_s.__file__))
        filepath = os.path.join(base, "programs", "functions", "third_party", f"{name}.py")
        if not os.path.isfile(filepath):
            return JSONResponse(content={"error": "not found"}, status_code=404)
        builtin_names = ["general_action", "agent_loop", "wait", "deep_work", "_utils"]
        if name in builtin_names:
            return JSONResponse(content={"error": "cannot delete built-in function"}, status_code=403)
        os.remove(filepath)
        mod_name = f"openprogram.programs.functions.third_party.{name}"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        return JSONResponse(content={"deleted": True})

    @app.post("/api/function/create")
    async def create_function(body: dict = None):
        """Create a new function from description."""
        from openprogram.webui import server as _s
        if not body or "description" not in body:
            return JSONResponse(content={"error": "no description"}, status_code=400)
        session_id = body.get("session_id")
        conv = _s._get_or_create_session(session_id)
        msg_id = str(uuid.uuid4())[:8]
        name = body.get("name", "new_func")
        desc = body["description"]

        def _do_create():
            _s._broadcast_chat_response(session_id, msg_id, {
                "type": "error",
                "content": (
                    "The /create endpoint has been removed. Open the chat "
                    "and load the agentic-program skill — the agent will "
                    "create the .py file directly using its file-editing tools."
                ),
            })

        threading.Thread(target=_do_create, daemon=True).start()
        return JSONResponse(content={"session_id": conv["id"], "msg_id": msg_id})
