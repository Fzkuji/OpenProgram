"""Bind execution settings to a branch before an input becomes durable."""
from __future__ import annotations

from copy import deepcopy


def _default_agent_id() -> str:
    from openprogram.runtime.agent_registry import manager
    try:
        spec = manager.get_default()
    except Exception:  # noqa: BLE001 - an unreadable registry runs the built-in Agent
        spec = None
    return spec.id if spec is not None else "main"


def _branch_permission(sid: str, branch_id: str | None, config: dict, recorded: bool) -> tuple[str, object]:
    """The permission mode and merged rules a turn on this branch runs with."""
    from openprogram.runtime.permissions.rule import load_merged_rules
    from openprogram.runtime.permissions.state import permission_state_from_row
    mode = permission_state_from_row(sid, config)["mode"]
    rules = load_merged_rules(sid, **({"branch_id": branch_id} if recorded and branch_id else {}))
    return mode, rules


def freeze_branch_request(values: dict, *, branch_authority: bool = False) -> dict:
    """Fill omitted execution settings once from the turn's own branch.

    The owning branch is, in order: an explicit ``branch_id``; the branch
    record reserved for this turn's message (a scheduled run's); the live
    same-session parent's; the record at ``branch_from``; the selected one.

    Omitted values (``None``) come from that branch: the model, effort, Fast,
    tool intent, Sandbox, the Agent (``agent_id``) and the permission mode and
    rules (``permission_mode``). A same-session child inherits its live
    parent's values instead, even if another branch is selected.

    ``branch_authority`` is set by a turn admitted for its conversation (a
    chat, scheduled, background or headless turn): such a caller can only
    have read the selected branch, so on a recorded branch that is not the
    selected one the branch's Agent and permission replace what it passed.
    A Job keeps the Agent and permission it was given.

    Two facts are frozen with the settings. ``owns_conversation_effects``
    says whether the turn carries the conversation's own effects (not on a
    turn beside the selected branch, nor on a background check-in).
    ``turn_settings`` records each setting's value, where it came from
    (``request``, ``parent``, ``branch``, or ``agent`` for a value left to
    the Agent at run time) and the Agent it belongs to; the turn writes it
    on its user message.
    """
    from openprogram.runtime.agent.turn_request import get_turn_request
    from openprogram.runtime.context.session_db import default_db
    from openprogram.state.store.session.branch_config import (
        branch_identity, config_for_branch, reserved_branch,
    )
    result = dict(values)
    if result.get("owns_conversation_effects") is None:
        result["owns_conversation_effects"] = not (result.get("side_branch") or result.get("background"))
    sources = {key: "request" for key in _RECORDED_SETTINGS if result.get(key) is not None}
    sid = result.get("session_id")
    if not sid:
        return _record_settings(result, sources)
    row = default_db().get_session(sid) or {}
    if not row:
        if not result.get("agent_id"):
            result["agent_id"] = _default_agent_id()
            sources["agent_id"] = "agent"
        return _record_settings(result, sources)
    parent = get_turn_request()
    same_parent = parent is not None and getattr(parent, "session_id", None) == sid
    parent_branch = getattr(parent, "branch_id", None) if same_parent else None
    refs = row.get("branch_refs") or {}
    active = row.get("active_branch_id")
    predecessor = result.get("branch_from")
    targets = [key for key, ref in refs.items()
               if isinstance(predecessor, str) and ref.get("head_id") == predecessor]
    target_branch = (parent_branch if parent_branch in targets else
                     active if active in targets else
                     targets[0] if len(targets) == 1 else None)
    reserved = None if result.get("branch_id") else reserved_branch(row, result.get("user_msg_id"))
    branch_id = (result.get("branch_id") or reserved or target_branch or parent_branch or active)
    same_parent = same_parent and (not parent_branch or branch_id == parent_branch)
    recorded = False
    if not branch_id:
        branch_id = branch_identity(sid, row.get("head_id"))
        config = row
    else:
        user_id = result.get("user_msg_id")
        from openprogram.runtime.agent.turn_request import INHERIT_PARENT
        pending_fork = (
            branch_id not in refs and branch_id != active
            and user_id and branch_id == branch_identity(sid, user_id)
            and (result.get("branch_from", INHERIT_PARENT) is None
                 or isinstance(result.get("branch_from"), str))
            and not default_db().message_exists(sid, user_id)
        )
        config = row if pending_fork else config_for_branch(row, branch_id)
        recorded = not pending_fork
    result["branch_id"] = branch_id
    if not result.get("sandbox_config_frozen", False):
        enabled = result.get("sandbox_enabled")
        if enabled is None:
            if same_parent and getattr(parent, "sandbox_config_frozen", False):
                enabled = parent.sandbox_enabled
                sources["sandbox_enabled"] = "parent"
            else:
                enabled = config.get("sandbox_enabled")
                sources["sandbox_enabled"] = "branch" if enabled is not None else "agent"
        result["sandbox_enabled"] = enabled
        result["sandbox_config_frozen"] = True
    provider, model = config.get("provider_override"), config.get("model_override")
    defaults = {
        "model_override": f"{provider}/{model}" if provider and model else model,
        "thinking_effort": config.get("thinking_effort"),
        "service_tier": config.get("service_tier"),
    }
    from openprogram.runtime.conversation.session_config import _config_from_row, tools_override_from_config
    defaults["tools_override"] = tools_override_from_config(_config_from_row(config))
    for key, fallback in defaults.items():
        if result.get(key) is None:
            result[key] = deepcopy(getattr(parent, key, fallback) if same_parent else fallback)
            sources[key] = ("parent" if same_parent else "branch") if result[key] is not None else "agent"

    # The Agent and the permission of the branch the turn runs on. A turn
    # on a branch other than the selected one must not run under the
    # selected branch's, which is all a conversation-level caller can read.
    off_selection = (branch_authority and recorded and not same_parent
                     and branch_id != active and not result.get("profile_snapshot"))
    if off_selection and config.get("agent_id"):
        result["agent_id"] = config["agent_id"]
        sources["agent_id"] = "branch"
    if not result.get("agent_id"):
        parent_agent = getattr(parent, "agent_id", None) if same_parent else None
        result["agent_id"] = parent_agent or config.get("agent_id") or _default_agent_id()
        sources["agent_id"] = ("parent" if parent_agent else
                               "branch" if config.get("agent_id") else "agent")
    if off_selection or result.get("permission_mode") is None:
        if same_parent and result.get("permission_mode") is None:
            mode, rules = parent.permission_mode, deepcopy(getattr(parent, "permission_rules", None))
            sources["permission_mode"] = "parent"
        else:
            mode, rules = _branch_permission(sid, branch_id, config, recorded)
            sources["permission_mode"] = "branch"
        result["permission_mode"] = mode
        if off_selection or result.get("permission_rules") is None:
            result["permission_rules"] = rules
    if not isinstance(result.get("turn_settings"), dict):
        model, model_source = _effective_model(result, sources)
        previous = _previous_turn_model(
            sid, refs.get(branch_id, {}).get("head_id") if recorded and branch_id in refs
            else (row.get("head_id") if recorded else result.get("branch_from")))
        result = _record_settings(result, sources)
        result["turn_settings"]["model"] = model
        result["turn_settings"]["model_source"] = model_source
        if previous and model and previous != model and model_source != "branch":
            # The branch sets no model and its Agent's changed since the
            # last turn here: the turn says so in the conversation, and its
            # request breaks the cached prefix on purpose.
            result["turn_settings"]["model_changed_from"] = previous
            from openprogram.models.usage.prefix_fingerprint import expect_break
            expect_break(sid, "model_switch")
        return result
    return _record_settings(result, sources)


def _effective_model(result: dict, sources: dict) -> tuple[str | None, str]:
    """The model this turn will run on and where it comes from: the
    branch's own (or the request's), else its Agent's as it is now, else
    the product fallback. Read at admission, so a branch that sets no model
    follows its Agent from one turn to the next."""
    if result.get("model_override"):
        source = sources.get("model_override", "branch")
        return result["model_override"], "branch" if source in ("branch", "request", "parent") else source
    profile = result.get("profile_snapshot")
    if isinstance(profile, dict):
        ref = profile.get("model") or {}
        if isinstance(ref, dict) and ref.get("provider") and ref.get("id"):
            return f"{ref['provider']}/{ref['id']}", "agent"
    from openprogram.runtime.conversation.session_config import FALLBACK_ORDER, _agent_settings, _global_settings
    for layer in FALLBACK_ORDER["model"]:
        value = (_agent_settings(result.get("agent_id")).get("model") if layer == "agent"
                 else _global_settings("model"))
        if value:
            return value, layer
    return None, "global"


def _previous_turn_model(session_id: str, head: str | None) -> str | None:
    """The model the last turn on this chain ran on (its ``turn_settings``)."""
    if not head or head == "ROOT":
        return None
    from openprogram.runtime.context.session_db import default_db
    try:
        chain = default_db().get_branch(session_id, head) or []
    except Exception:
        return None
    for message in reversed(chain):
        if message.get("role") != "user":
            continue
        recorded = message.get("turn_settings") or (message.get("metadata") or {}).get("turn_settings")
        if isinstance(recorded, dict) and recorded.get("model"):
            return recorded["model"]
    return None


#: The settings a turn's ``turn_settings`` record names.
_RECORDED_SETTINGS = (
    "agent_id", "model_override", "thinking_effort", "service_tier",
    "tools_override", "permission_mode", "sandbox_enabled",
)


def _record_settings(result: dict, sources: dict) -> dict:
    """Freeze ``turn_settings``: each recorded setting's value and source.

    A request that already carries the record (a turn resumed from its
    admitted input) keeps it: what the turn was admitted with does not
    change on a later read.
    """
    if not isinstance(result.get("turn_settings"), dict):
        result["turn_settings"] = {
            "agent_id": result.get("agent_id"),
            "values": {key: deepcopy(result.get(key)) for key in _RECORDED_SETTINGS},
            "sources": {key: sources.get(key, "agent") for key in _RECORDED_SETTINGS},
        }
    return result


def branch_holding(session_id: str, msg_id: str | None) -> str | None:
    """The recorded branch a message lies on, when that branch is not the
    selected one; ``None`` when the selected branch holds it (or none does).

    Work that returns to a turn (a sub-agent's result) goes to that turn's
    branch, which the person may have switched away from meanwhile."""
    from openprogram.runtime.context.session_db import default_db
    if not session_id or not msg_id:
        return None
    db = default_db()
    row = db.get_session(session_id) or {}
    active = row.get("active_branch_id")
    holding = []
    for key, ref in (row.get("branch_refs") or {}).items():
        if not isinstance(ref, dict) or ref.get("status") == "aborted" or not ref.get("head_id"):
            continue
        try:
            chain = db.get_branch(session_id, ref["head_id"]) or []
        except Exception:
            continue
        if any(message.get("id") == msg_id for message in chain):
            if key == active:
                return None
            holding.append(key)
    return holding[0] if holding else None
