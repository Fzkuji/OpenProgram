"""Misc endpoints — /healthz liveness probe and external module registration."""
from __future__ import annotations

import functools
import logging
import importlib
import os
import re
import subprocess
from pathlib import Path

from fastapi.responses import JSONResponse
from fastapi import Request

_log = logging.getLogger(__name__)

_HEAD_SHA: str | None = None


def _read_revision() -> str:
    """The source identity on disk now, or empty without evidence."""
    try:
        import openprogram
        root = Path(openprogram.__file__).resolve().parents[1]
        if (root / ".git").exists():
            res = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=str(root),
                capture_output=True, text=True, timeout=5,
            )
            return res.stdout.strip() if res.returncode == 0 else ""
        marker = Path(openprogram.__file__).resolve().parent / "_build_revision.txt"
        if marker.is_file() and not marker.is_symlink() and marker.stat().st_size < 128:
            revision = marker.read_text(encoding="ascii").strip()
            if re.fullmatch(r"[0-9a-f]{40}(?:-dirty)?", revision):
                return revision
    except Exception:
        # No git checkout and no build marker: the revision is unknown.
        _log.debug("build revision unavailable", exc_info=True)
    return ""


def _head_sha() -> str:
    """Frozen identity of the code this worker loaded at startup."""
    global _HEAD_SHA
    if _HEAD_SHA is None:
        _HEAD_SHA = _read_revision()
    return _HEAD_SHA


def _installed_revision() -> str:
    """Identity of the installed files, which a hot swap changes in place.

    Requests read the Web export, docs and terminal UI from disk, so after a
    hot swap this, not the startup revision, names what users are served.
    """
    import openprogram
    root = Path(openprogram.__file__).resolve().parents[1]
    return _head_sha() if (root / ".git").exists() else _read_revision()


@functools.cache
def _product_version() -> str:
    """The release this worker loaded; a running worker never changes it."""
    from openprogram.operations.self_update.github_release.availability import current_version

    return current_version()


def _runtime_version() -> str | None:
    """The sealed version this worker runs from, when it runs from one."""
    from openprogram.host._platform.runtime_layout import current_runtime

    runtime = current_runtime()
    return runtime.version if runtime is not None else None


def _subsystems() -> dict:
    from openprogram.operations.worker.subsystems import snapshot

    return snapshot()


def register(app):
    _head_sha()  # Freeze before listening, not on the first post-update request.
    @app.get("/api/doctor")
    async def doctor_api():
        """Run the same checks as ``openprogram doctor`` and return the
        results as JSON for the web UI / slash command."""
        from openprogram.operations.diagnostics.checks import run_checks
        results = run_checks()
        return JSONResponse(content={
            "results": results,
            "all_ok": all(r["ok"] for r in results),
        })

    @app.get("/api/system/access")
    def system_access_api():
        from openprogram.host.system_access import report
        return JSONResponse(report(), headers={"Cache-Control": "no-store"})

    @app.get("/api/system/features")
    def system_features_api():
        """Which platform-dependent features this worker's computer has."""
        from openprogram.interfaces.server.host_features import report
        return JSONResponse(report(), headers={"Cache-Control": "no-store"})

    @app.get("/api/system/access/waits")
    def system_access_waits_api(request: Request, session_id: str | None = None):
        """Return owner-authorized durable desktop access waits for reconnect."""
        from openprogram.runtime.execution import default_store
        from openprogram.runtime.execution.waits import DurableWaitStore
        from openprogram.interfaces.server.routes.execution.lifecycle import _actor_and_session, _authorize_read

        actor, bound_session = _actor_and_session(request)
        if bound_session is not None:
            if session_id is not None and session_id != bound_session:
                return JSONResponse({"waits": []}, status_code=404)
            session_id = bound_session
        if session_id is None:
            scoped_sessions = actor.get("session_ids") if isinstance(actor, dict) else None
            if not isinstance(scoped_sessions, (list, tuple, set, frozenset)) or not scoped_sessions:
                return JSONResponse({"waits": []}, status_code=404)

        store = default_store()
        waits = DurableWaitStore(store).list_open(session_id=session_id)
        if session_id is None:
            allowed = {str(item) for item in scoped_sessions}
            waits = [
                wait for wait in waits
                if (execution := store.get_execution(wait.execution_id)) is not None
                and str(execution.session_id) in allowed
            ]
        visible = []
        for wait in waits:
            if wait.kind != "system_access":
                continue
            execution = store.get_execution(wait.execution_id)
            if execution is None or not _authorize_read(actor, session_id, execution, "execution.snapshot"):
                continue
            visible.append({
                "wait_id": wait.wait_id,
                "kind": wait.kind,
                "session_id": execution.session_id,
                "execution_id": wait.execution_id,
                "wait_generation": wait.claim_generation,
                "expected_version": execution.status_version,
                "required_capabilities": list(wait.request.get("required_capabilities", [])),
                "capabilities": list(wait.request.get("capabilities", [])),
                "expires_at": wait.expires_at,
                "reason_code": "system_access_required",
            })
        return JSONResponse({"waits": visible}, headers={"Cache-Control": "no-store"})

    def _local_owner_refusal(request: Request) -> JSONResponse | None:
        """Refuse anyone but the local owner on the execution computer."""
        from openprogram.host.security.backend_endpoint import is_loopback_host
        from openprogram.operations.self_update.control.projection import ProjectionAccessError
        from ..self_updates import require_owner
        try:
            require_owner(request)
            from urllib.parse import urlsplit
            origin = urlsplit(request.headers.get("origin", ""))
            forwarded = any(key.lower() == "forwarded" or key.lower().startswith("x-forwarded-") for key in request.headers)
            if (not request.client or not is_loopback_host(request.client.host)
                    or not is_loopback_host(request.url.hostname or "")
                    or origin.scheme not in {"http", "https"}
                    or not is_loopback_host(origin.hostname or "") or forwarded):
                raise ProjectionAccessError("Authorize on the execution computer.")
        except ProjectionAccessError:
            return JSONResponse({"error": "System access setup requires the local owner on the execution computer."}, status_code=403)
        return None

    @app.post("/api/system/access/guide")
    async def guide_system_access_api(request: Request):
        """Open each missing capability's System Settings pane with the drag guide.

        Body ``{"capabilities": [id, ...]}``, taken in order in the background:
        a dialog capability gets macOS's own dialog, a list capability its pane
        and the accessory window that closes itself once the switch is on.
        Returns ``{"ok": true, "started": [ids it will take]}``.
        """
        refusal = _local_owner_refusal(request)
        if refusal is not None:
            return refusal
        try:
            body = await request.json()
        except ValueError:
            body = None
        capabilities = body.get("capabilities") if isinstance(body, dict) else None
        if (not isinstance(capabilities, list) or not capabilities
                or not all(isinstance(item, str) for item in capabilities)):
            return JSONResponse({"error": "capabilities must be a non-empty list of capability ids."}, status_code=400)
        from starlette.concurrency import run_in_threadpool
        from openprogram.host.system_access import guide
        try:
            started = await run_in_threadpool(guide.start, capabilities)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception:
            _log.warning("system settings guide could not start", exc_info=True)
            return JSONResponse({"error": "System Settings could not be opened on the execution computer."}, status_code=503)
        return JSONResponse({"ok": True, "started": started}, headers={"Cache-Control": "no-store"})

    @app.post("/api/system/access/setup")
    def setup_system_access_api(request: Request):
        """Explicitly initialize all native capabilities for this executor."""
        refusal = _local_owner_refusal(request)
        if refusal is not None:
            return refusal
        try:
            from openprogram.host.system_access import setup_all_access
            return JSONResponse(setup_all_access(), headers={"Cache-Control": "no-store"})
        except Exception:
            return JSONResponse({"error": "The unified system access setup could not be completed. Open System Settings on the execution computer."}, status_code=503)

    @app.post("/api/system/access/{capability}")
    def request_system_access_api(capability: str, request: Request):
        from openprogram.host.system_access import request_access
        refusal = _local_owner_refusal(request)
        if refusal is not None:
            return refusal
        try:
            open_settings = request.query_params.get("open_settings", "false").lower() in {"1", "true", "yes"}
            return JSONResponse(request_access(capability, open_settings=open_settings), headers={"Cache-Control": "no-store"})
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except Exception:
            return JSONResponse({"error": "The system permission request could not be opened. Open System Settings on the execution computer."}, status_code=503)

    @app.get("/healthz")
    async def healthz():
        """Liveness and product version, available before authentication.

        The desktop shell compares the version with its own after an update.
        It names the product release only, nothing about this machine or user.
        """
        return JSONResponse(content={"status": "ok", "version": _product_version()})

    @app.get("/api/diagnostics")
    async def runtime_diagnostics(request: Request):
        """Authenticated runtime and storage diagnostics."""
        import time as _time
        from openprogram.interfaces.server import server as _s

        info: dict = {
            "status": "ok",
            "checked_at": _time.time(),
            "uptime_seconds": int(_time.time() - _s._SERVER_START_TIME),
            "revision": _head_sha(),
            "installed_revision": _installed_revision(),
            "runtime_version": _runtime_version(),
            "subsystems": _subsystems(),
            "worker_pid": os.getpid(),
            "principal_id": getattr(request.state, "authority", {}).get("principal_id"),
        }
        with _s._ws_lock:
            connections = list(_s._ws_connections)
        delivery = [
            metrics
            for connection in connections
            if isinstance(
                metrics := getattr(connection, "delivery_metrics", None),
                dict,
            )
        ]
        info["websocket_delivery"] = {
            "connections": len(connections),
            "managed_connections": len(delivery),
            "queue_frames": sum(int(row["queue_frames"]) for row in delivery),
            "queue_bytes": sum(int(row["queue_bytes"]) for row in delivery),
            "oldest_age": max(
                (float(row["oldest_age"]) for row in delivery), default=0.0
            ),
            "coalesced": sum(int(row["coalesced"]) for row in delivery),
            "dropped": sum(int(row["dropped"]) for row in delivery),
            "send_failures": sum(int(row["send_failures"]) for row in delivery),
        }
        with _s._ws_lock:
            connections = list(_s._ws_connections)
        delivery = [
            metrics
            for connection in connections
            if isinstance(
                metrics := getattr(connection, "delivery_metrics", None),
                dict,
            )
        ]
        info["websocket_delivery"] = {
            "connections": len(connections),
            "managed_connections": len(delivery),
            "queue_frames": sum(int(row["queue_frames"]) for row in delivery),
            "queue_bytes": sum(int(row["queue_bytes"]) for row in delivery),
            "oldest_age": max(
                (float(row["oldest_age"]) for row in delivery), default=0.0
            ),
            "coalesced": sum(int(row["coalesced"]) for row in delivery),
            "dropped": sum(int(row["dropped"]) for row in delivery),
            "send_failures": sum(int(row["send_failures"]) for row in delivery),
        }
        try:
            from openprogram.runtime.context.session_db import default_db

            db = default_db()
            info["database_ok"] = True
            info["has_visible_sessions"] = bool(db.list_sessions(limit=1))
            # Counting reads each conversation under its lock: off the loop.
            from openprogram.state.store.session.conversation_work import run_for_conversation
            info["message_count_24h"] = await run_for_conversation(
                "diagnostics", db.count_recent_nodes, _time.time() - 24 * 3600,
            )
        except Exception as exc:  # noqa: BLE001
            info["database_ok"] = False
            info["database_error"] = f"{type(exc).__name__}: {exc}"
            info["status"] = "degraded"
        try:
            from openprogram.programs import list_registered_agent_tools

            info["registered_tool_count"] = len(list_registered_agent_tools())
        except Exception:  # noqa: BLE001
            info["registered_tool_count"] = 0
        return JSONResponse(content=info)

    @app.post("/api/register")
    async def register_external(body: dict = None):
        """Register an external module's Agent method callables."""
        if not body or "module" not in body:
            return JSONResponse(content={"error": "no module path"}, status_code=400)
        module_path = body["module"]
        try:
            mod = importlib.import_module(module_path)
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
