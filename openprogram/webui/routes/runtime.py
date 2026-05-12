"""Runtime / provider / model switch endpoints + agent_settings.

Routes here mutate `_user_pinned_*` and `_runtime_management.*` on the
server module. Reads of those module-level singletons inside server
(e.g. `_get_or_create_session` reads `_user_pinned_provider`) keep
working because we write back via `setattr(_s, ...)`, which targets the
exact same module-level name the readers resolve.

Endpoints:
  GET  /api/providers
  POST /api/provider/{name}
  GET  /api/models
  POST /api/model
  GET  /api/agent_settings
  POST /api/agent_settings
"""
from __future__ import annotations

import asyncio
import json

from fastapi.responses import JSONResponse


def register(app):
    @app.get("/api/providers")
    async def get_providers():
        from openprogram.webui import server as _s
        return JSONResponse(content=_s._list_providers())

    @app.post("/api/provider/{name}")
    async def switch_provider(name: str, body: dict = None):
        from openprogram.webui import server as _s
        session_id = body.get("session_id") if body else None
        if session_id:
            with _s._sessions_lock:
                conv = _s._sessions.get(session_id)
            if conv and conv.get("provider_name") == name:
                return JSONResponse(content={"switched": False, "already_active": True, "provider": name})
        elif name == _s._runtime_management._default_provider:
            return JSONResponse(content={"switched": False, "already_active": True, "provider": name})
        try:
            _s._switch_runtime(name, session_id=session_id)
            return JSONResponse(content={"switched": True, "provider": name})
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=400)

    @app.get("/api/models")
    async def list_models():
        """List available models for the current provider."""
        from openprogram.webui import server as _s
        with _s._runtime_management._runtime_lock:
            if _s._runtime_management._default_provider is None:
                (_s._runtime_management._default_provider,
                 _s._runtime_management._default_runtime) = _s._detect_default_provider()

        provider = _s._runtime_management._default_provider or "none"
        runtime = _s._runtime_management._default_runtime
        current_model = runtime.model if runtime else None

        model_list = []
        if runtime and hasattr(runtime, "list_models"):
            try:
                model_list = runtime.list_models()
            except Exception as e:
                print(f"[list_models] {provider} error: {e}")
        if current_model and current_model not in model_list:
            model_list = [current_model] + model_list

        return JSONResponse(content={
            "provider": provider,
            "current": current_model,
            "models": model_list,
        })

    @app.post("/api/model")
    async def switch_model(body: dict = None):
        """Switch model (and optionally provider) for the active runtime.

        Body: {"model": str (bare id or "provider:id"), "provider": str?,
               "session_id": str?}
        """
        from openprogram.webui import server as _s
        _s._log(f"[/api/model] body={body!r}")
        if not body or "model" not in body:
            return JSONResponse(content={"error": "Missing model"}, status_code=400)
        model = body["model"].strip()
        explicit_provider = (body.get("provider") or "").strip() or None
        session_id = body.get("session_id")

        inferred_provider = None
        bare_model = model
        if explicit_provider is None and ":" in model:
            head, tail = model.split(":", 1)
            from openprogram.providers import get_providers as _get_providers
            known = set(_get_providers())
            known.update({"claude-code", "openai-codex", "gemini-cli",
                          "anthropic", "openai", "gemini"})
            if head in known:
                inferred_provider = head
                bare_model = tail
        target_provider = explicit_provider or inferred_provider

        async def _build_rt(provider: str):
            return await asyncio.to_thread(
                _s._create_runtime_for_visualizer, provider, bare_model
            )

        if session_id:
            with _s._sessions_lock:
                conv = _s._sessions.get(session_id)
            _s._log(
                f"[/api/model] session_id={session_id!r} conv_found={conv is not None} "
                f"target_provider={target_provider!r} bare_model={bare_model!r}"
            )
            if conv:
                old_rt = conv.get("runtime")
                cur_prov = conv.get("provider_name", _s._runtime_management._default_provider)
                prov = target_provider or cur_prov
                need_new_rt = (target_provider and target_provider != cur_prov) or (old_rt is None)
                if need_new_rt:
                    new_rt = await _build_rt(prov)
                    if old_rt and hasattr(old_rt, "close"):
                        try: old_rt.close()
                        except Exception: pass
                    conv["runtime"] = new_rt
                    conv["provider_name"] = prov
                    conv["provider_override"] = prov
                    conv["model_override"] = bare_model
                else:
                    if old_rt is not None:
                        old_rt.model = bare_model
                    conv["provider_override"] = prov
                    conv["model_override"] = bare_model
                _s._log(
                    f"[/api/model] post-write conv id={id(conv)} "
                    f"provider_override={conv.get('provider_override')!r} "
                    f"model_override={conv.get('model_override')!r} "
                    f"sessions[sid] id={id(_s._sessions.get(session_id))}"
                )
                _s._user_pinned_provider = prov
                _s._user_pinned_model = bare_model
                info = _s._get_provider_info(session_id)
                _s._broadcast(json.dumps({"type": "provider_changed", "data": info}))
                return JSONResponse(content={"switched": True, "provider": prov, "model": bare_model})

        if target_provider and target_provider != _s._runtime_management._default_provider:
            new_rt = await _build_rt(target_provider)
            if _s._runtime_management._default_runtime and hasattr(
                _s._runtime_management._default_runtime, "close"
            ):
                try: _s._runtime_management._default_runtime.close()
                except Exception: pass
            _s._runtime_management._default_runtime = new_rt
            _s._runtime_management._default_provider = target_provider
        elif _s._runtime_management._default_runtime:
            _s._runtime_management._default_runtime.model = bare_model
        else:
            return JSONResponse(content={"error": "No active runtime"}, status_code=400)

        _s._user_pinned_provider = target_provider or _s._runtime_management._default_provider
        _s._user_pinned_model = bare_model

        info = _s._get_provider_info()
        _s._broadcast(json.dumps({"type": "provider_changed", "data": info}))
        return JSONResponse(content={
            "switched": True,
            "provider": target_provider or _s._runtime_management._default_provider,
            "model": bare_model,
        })

    @app.get("/api/agent_settings")
    async def get_agent_settings(session_id: str = None):
        """Return current chat/exec provider+model. Optionally per-session."""
        from openprogram.webui import server as _s
        _s._init_providers()

        chat_session_id = None
        chat_locked = False
        chat_provider = _s._runtime_management._chat_provider
        chat_model = _s._runtime_management._chat_model

        if session_id:
            with _s._sessions_lock:
                conv = _s._sessions.get(session_id)
            if conv:
                rt = conv.get("runtime")
                if rt:
                    chat_session_id = getattr(rt, "_session_id", None)
                if conv.get("provider_name"):
                    chat_provider = conv["provider_name"]
                if rt and getattr(rt, "model", None):
                    chat_model = rt.model

        return JSONResponse(content={
            "chat": {
                "provider": chat_provider,
                "model": chat_model,
                "session_id": chat_session_id,
                "locked": chat_locked,
                "thinking": _s._get_thinking_config_for_model(chat_provider, chat_model),
            },
            "exec": {
                "provider": _s._runtime_management._exec_provider,
                "model": _s._runtime_management._exec_model,
                "thinking": _s._get_thinking_config_for_model(
                    _s._runtime_management._exec_provider,
                    _s._runtime_management._exec_model,
                ),
            },
            "available": _s._runtime_management._available_providers,
        })

    @app.post("/api/agent_settings")
    async def set_agent_settings(body: dict = None):
        """Update chat and/or exec agent provider/model."""
        from openprogram.webui import server as _s
        _s._init_providers()
        changed = False

        if body and "chat" in body:
            chat = body["chat"]
            new_provider = chat.get("provider", _s._runtime_management._chat_provider)
            new_model = chat.get("model", _s._runtime_management._chat_model)
            if (new_provider != _s._runtime_management._chat_provider
                    or new_model != _s._runtime_management._chat_model):
                # Build runtimes OFF-LOOP first. Some providers (codex)
                # call sync credential-acquisition paths in __init__ that
                # refuse to run inside an event loop; run_in_threadpool
                # keeps them happy and guarantees we only touch globals
                # AFTER the runtimes construct successfully.
                with _s._sessions_lock:
                    session_ids = list(_s._sessions.keys())
                new_rts = {}
                for cid in session_ids:
                    new_rts[cid] = await asyncio.to_thread(
                        _s._create_runtime_for_visualizer, new_provider, new_model
                    )
                _s._runtime_management._chat_provider = new_provider
                _s._runtime_management._chat_model = new_model
                with _s._sessions_lock:
                    for cid, new_rt in new_rts.items():
                        conv = _s._sessions.get(cid)
                        if not conv:
                            continue
                        old_rt = conv.get("runtime")
                        if old_rt and hasattr(old_rt, "close"):
                            try: old_rt.close()
                            except Exception: pass
                        conv["runtime"] = new_rt
                        conv["provider_name"] = new_provider
                changed = True

        if body and "exec" in body:
            exec_cfg = body["exec"]
            _s._runtime_management._exec_provider = exec_cfg.get(
                "provider", _s._runtime_management._exec_provider
            )
            _s._runtime_management._exec_model = exec_cfg.get(
                "model", _s._runtime_management._exec_model
            )
            changed = True

        if changed:
            _s._broadcast(json.dumps({
                "type": "agent_settings_changed",
                "data": {
                    "chat": {"provider": _s._runtime_management._chat_provider,
                             "model": _s._runtime_management._chat_model},
                    "exec": {"provider": _s._runtime_management._exec_provider,
                             "model": _s._runtime_management._exec_model},
                },
            }))

        return JSONResponse(content={
            "chat": {"provider": _s._runtime_management._chat_provider,
                     "model": _s._runtime_management._chat_model},
            "exec": {"provider": _s._runtime_management._exec_provider,
                     "model": _s._runtime_management._exec_model},
        })
