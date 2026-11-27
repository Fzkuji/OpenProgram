"""Which model each outside agent uses (Settings → Third-Party Harnesses → Models).

  GET  /api/harnesses/models    the agents of the registry that are here or
                                connected, each with its picks, whether its own
                                config has the gateway, drift, and a running
                                app behind its config
  PUT  /api/harnesses/models    {"harness", "model", "tiers"?, "terminal"?}
  POST /api/harnesses/restart   {"harness"}: restart its app on the new config
  POST /api/harnesses/import    {"harness"}: hand an app its import link

The rows come from the agent registry (``models/gateway/agents``). ``model``
is ``""`` for the agent's own setup or a ``provider/model`` ref of an
OpenProgram model; for an agent with only its own models (``gateway``
false: Cursor CLI, Devin) it is one of its model field's choices, and for
one whose own models are offered beside OpenProgram's (``with_catalog``:
DeepSeek Harness, whose models the runtime reads from dsh itself) it is
either.
``launch`` is the command that starts an agent that takes the gateway only
from how it is started (agy). For an agent OpenProgram also runs in its own chats
(Codex, Claude Code) the pick is stored as settings (``harnesses.<id>.*``,
``state/settings.py``) and ``terminal`` says whether the agent's own config
points at the gateway too; for any other agent the pick is its config.
Turning it off, or going back to the agent's own setup, restores what was
there. A ``kept`` field among ``tiers`` (agy's fast tools) is the user's own
setting and is set whether the agent is connected or not. An agent with no
model to pick (``switch``: Cursor Private Inference, Avibe, which list the
gateway's models themselves) is turned on and off by ``terminal``. An app that takes the gateway only through its own import
link (``link``: Cindy, AionUi, Cherry Studio, Lody) is handed that link,
which the user confirms in the app; its row says whether the app has it.
"""
from __future__ import annotations

from typing import Any

from fastapi import Body
from fastapi.responses import JSONResponse


def _agents():
    from openprogram.models.gateway.agents import all_agents
    return all_agents()


def _row(agent) -> dict[str, Any]:
    from openprogram.models.gateway.agents.engine import status
    from openprogram.state.settings import setting_value
    try:
        row = status(agent.id)
    except Exception as exc:  # noqa: BLE001 - shown on the card, never hidden
        row = {"id": agent.id, "label": agent.name, "icon": agent.icon, "kind": agent.kind,
               "harness": agent.harness, "installed": True, "cliGone": False, "values": {}, "notice": "",
               "restart": False, "fields": [], "gateway": agent.gateway, "launch": "",
               "switch": agent.switch, "link": agent.link is not None,
               "config": {"path": "", "applied": False, "drift": [], "error": str(exc)}}
    tiers = [f.key for f in agent.fields if f.quiet]
    if agent.harness:
        row["model"] = setting_value(f"harnesses.{agent.id}.model") or ""
        row["terminal"] = bool(setting_value(f"harnesses.{agent.id}.terminal"))
        if tiers:
            saved = setting_value(f"harnesses.{agent.id}.tiers") or {}
            row["tiers"] = {tier: saved.get(tier, "") for tier in tiers}
    else:
        values = row.get("values") or {}
        row["model"] = values.get("model", "")
        row["terminal"] = bool(row["config"].get("applied"))
        if tiers:
            row["tiers"] = {tier: values.get(tier, "") for tier in tiers}
    return row


def _listed(agent) -> bool:
    from openprogram.models.gateway import stash
    try:
        return agent.harness or agent.installed() or stash.entry(agent.id) is not None
    except stash.StashUnreadable:
        return True


def _save(harness: str, key: str, value: Any) -> str | None:
    from openprogram.state.settings import set_setting
    result = set_setting(f"harnesses.{harness}.{key}", value)
    return result.get("error")


def _codex_options() -> dict[str, Any]:
    from openprogram.runtime.harnesses.codex.maintenance import prompt_versions
    return {"codex_versions": prompt_versions()}


def _dsh_models() -> list[dict[str, Any]]:
    """DeepSeek Harness's own models as dsh lists them, from the kept
    reading of it; none yet starts one in the background."""
    from openprogram.runtime.harnesses.models import cached_harness, warm
    entry = cached_harness("deepseek-harness")
    if entry is None:
        warm("deepseek-harness")
        return []
    return [{"id": m["id"], "label": m.get("label") or m["id"]} for m in entry.get("models") or [] if m.get("id")]


def _own(agent) -> set[str]:
    """The agent's own models offered beside OpenProgram's (``Field.with_catalog``)."""
    field = agent.field("model")
    if field is None or not field.with_catalog or field.options is None:
        return set()
    try:
        return {value for value, _label in field.options()}
    except Exception:  # noqa: BLE001 - an agent that can't list its models offers none
        return set()


def register(app) -> None:
    # The runtime hands the models layer what Codex's catalog needs (the
    # instructions of the installed Codex's release) and the models
    # DeepSeek Harness reaches on its own.
    from openprogram.models.gateway.agents.lifecycle import provide_options, provide_own_models
    provide_options("codex", _codex_options)
    provide_own_models("dsh", _dsh_models)

    @app.get("/api/harnesses/models")
    def api_harness_models():
        return JSONResponse({"harnesses": [_row(agent) for agent in _agents() if _listed(agent)]})

    @app.put("/api/harnesses/models")
    def api_set_harness_model(body: Any = Body(default=None)):
        from openprogram.models.gateway.agents import find
        from openprogram.models.gateway.agents.engine import AgentConfigError, connect, disconnect, set_kept
        agent = find(body.get("harness", "")) if isinstance(body, dict) and isinstance(body.get("harness"), str) else None
        if agent is None:
            return JSONResponse({"error": "harness must be one of the agents listed"}, status_code=400)
        if agent.link is not None:
            return JSONResponse({"error": f"{agent.name} takes OpenProgram through its own import link"},
                                status_code=400)
        current = _row(agent)
        tier_keys = {f.key for f in agent.fields if f.quiet}
        model = body.get("model", current["model"])
        if agent.switch:
            terminal = body.get("terminal", current["terminal"])
            tiers = body.get("tiers", current.get("tiers")) or {}
            if not isinstance(terminal, bool):
                return JSONResponse({"error": "terminal must be true or false"}, status_code=400)
            if not isinstance(tiers, dict) or any(k not in tier_keys or not isinstance(v, str) for k, v in tiers.items()):
                return JSONResponse({"error": f"tiers maps {', '.join(sorted(tier_keys)) or 'nothing'} to choices"},
                                    status_code=400)
            try:
                if terminal:
                    from openprogram.models.gateway.agents.lifecycle import options_for
                    # a switch's choices (Cursor Private Inference's effort) go with it
                    connect(agent.id, {k: v for k, v in tiers.items() if v}, options=options_for(agent.id))
                elif current["config"].get("applied") or current["config"].get("error"):
                    disconnect(agent.id)
            except AgentConfigError as exc:
                return JSONResponse({"error": str(exc), **_row(agent)}, status_code=409)
            return JSONResponse(_row(agent))
        terminal = body.get("terminal", current["terminal"]) if agent.harness else bool(model)
        tiers = body.get("tiers", current.get("tiers"))
        if not isinstance(model, str) or (model and agent.gateway and "/" not in model and model not in _own(agent)):
            return JSONResponse({"error": "model must be empty, provider/model or one of the agent's own"},
                                status_code=400)
        if not isinstance(terminal, bool):
            return JSONResponse({"error": "terminal must be true or false"}, status_code=400)
        if tiers is not None and (not isinstance(tiers, dict) or any(
                k not in tier_keys or not isinstance(v, str) for k, v in tiers.items())):
            return JSONResponse({"error": f"tiers maps {', '.join(sorted(tier_keys)) or 'nothing'} to models"},
                                status_code=400)
        terminal = terminal and bool(model)
        values = {"model": model, **{k: v for k, v in (tiers or {}).items() if v}}
        try:
            if terminal:
                from openprogram.models.gateway.agents.lifecycle import options_for
                connect(agent.id, values, options=options_for(agent.id))
            elif current["config"].get("applied") or current["config"].get("error"):
                # an unreadable stash makes this refuse rather than forget
                disconnect(agent.id)
            if not terminal and isinstance(body.get("tiers"), dict):
                # kept choices are the user's own, set while disconnected too
                # (agy's fast tools); a connect above has kept them already
                set_kept(agent.id, body["tiers"])
        except AgentConfigError as exc:
            return JSONResponse({"error": str(exc), **_row(agent)}, status_code=409)
        if agent.harness:
            for key, value in (("model", model), ("terminal", terminal)):
                error = _save(agent.id, key, value)
                if error:
                    return JSONResponse({"error": error}, status_code=400)
            if tier_keys and tiers is not None:
                error = _save(agent.id, "tiers", {k: v for k, v in tiers.items() if v})
                if error:
                    return JSONResponse({"error": error}, status_code=400)
        return JSONResponse(_row(agent))

    @app.post("/api/harnesses/restart")
    def api_restart_harness_app(body: Any = Body(default=None)):
        from openprogram.models.gateway import stash
        from openprogram.models.gateway.agents import find
        from openprogram.models.gateway.agents.running import restart_app
        agent = find(body.get("harness", "")) if isinstance(body, dict) and isinstance(body.get("harness"), str) else None
        if agent is None or agent.app is None:
            return JSONResponse({"error": "this agent has no app to restart"}, status_code=400)
        try:
            changed_at = (stash.entry(agent.id) or {}).get("at")
        except stash.StashUnreadable as exc:
            return JSONResponse({"error": str(exc), **_row(agent)}, status_code=409)
        problem = restart_app(agent, changed_at)
        if problem:
            return JSONResponse({"error": problem, **_row(agent)}, status_code=409)
        return JSONResponse(_row(agent))

    @app.post("/api/harnesses/import")
    def api_import_harness_link(body: Any = Body(default=None)):
        from openprogram.models.gateway.agents import applink, find
        from openprogram.models.gateway.agents.engine import AgentConfigError, import_link
        agent = find(body.get("harness", "")) if isinstance(body, dict) and isinstance(body.get("harness"), str) else None
        if agent is None or agent.link is None:
            return JSONResponse({"error": "this agent takes no import link"}, status_code=400)

        def opener(link: str) -> None:
            applink.open_app_link(link, applink.system_open)
        try:
            link = import_link(agent.id)
            if agent.open_link is not None:
                agent.open_link(link, opener)
            else:
                opener(link)
        except (AgentConfigError, applink.NoLinkHandler, OSError) as exc:
            return JSONResponse({"error": str(exc), **_row(agent)}, status_code=409)
        return JSONResponse(_row(agent))
