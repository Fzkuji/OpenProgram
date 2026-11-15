"""Branch identity carried by local settings requests."""
from __future__ import annotations


def branch_guard(body: dict) -> dict:
    """Omitted identity keeps legacy clients compatible; null is an exact guard."""
    return {"expected_branch_id": body["expected_branch_id"]} if "expected_branch_id" in body else {}


def branch_target(body: dict) -> dict:
    """Where a settings write goes: the named ``branch_id`` (the branch the
    pane shows), whatever is selected; else the selected branch, guarded by
    an old client's ``expected_branch_id``."""
    branch_id = body.get("branch_id")
    if isinstance(branch_id, str) and branch_id:
        return {"branch_id": branch_id}
    return {"branch_id": None, **branch_guard(body)}


def write_branch_settings(session_id: str, body: dict, values: dict) -> tuple[dict, bool]:
    """Write settings onto the request's branch; ``(row, selected)``.

    ``row`` is the session with that branch's settings on top; ``selected``
    says whether the selected branch was written (its live mirror follows).
    """
    from openprogram.runtime.context.session_db import default_db
    from openprogram.state.store.session.branch_config import config_for_branch
    db = default_db()
    target = branch_target(body)
    branch_id = target.pop("branch_id")
    db.update_branch_config(session_id, branch_id, values, **target)
    if "thinking_effort" in values:
        # The branch's next request changes its effort on purpose.
        from openprogram.models.usage.prefix_fingerprint import expect_break
        expect_break(session_id, "effort_switch")
    row = db.get_session(session_id) or {}
    selected = not branch_id or branch_id == row.get("active_branch_id")
    if selected:
        row = refresh_mirror(session_id)
        return {**row, "branch_id": row.get("active_branch_id")}, True
    return {**row, **config_for_branch(row, branch_id), "branch_id": branch_id}, False


def current_branch(session_id: str | None):
    from openprogram.runtime.context.session_db import default_db
    return (default_db().get_session(session_id) or {}).get("active_branch_id") if session_id else None


def check_branch(session_id: str | None, body: dict) -> None:
    from openprogram.state.store.session.branch_config import BranchConfigConflict
    if session_id and "expected_branch_id" in body and current_branch(session_id) != body["expected_branch_id"]:
        raise BranchConfigConflict("The selected branch changed; reload its settings and try again.")




def prepare_initial_guard(session_id: str | None, body: dict) -> str | None:
    """Remember only the identity this accepted, uncreated draft can initialize."""
    check_branch(session_id, body)
    if not session_id or "expected_branch_id" not in body or body["expected_branch_id"] is not None:
        return None
    from openprogram.runtime.context.session_db import default_db
    from openprogram.state.store.session.branch_config import branch_identity
    db = default_db()
    row = db.get_session(session_id) or {}
    if not db.is_created(session_id) and row.get("active_branch_id") is None:
        return branch_identity(session_id, row.get("head_id"))
    return None


def materialized_guard(session_id: str, body: dict, initial_branch_id: str | None) -> dict:
    """Advance null only to the exact server-accepted draft identity, never a new selection."""
    if initial_branch_id and body.get("expected_branch_id") is None and current_branch(session_id) == initial_branch_id:
        return {**body, "expected_branch_id": initial_branch_id}
    return body

def refresh_mirror(session_id: str) -> dict:
    """Read current durable settings while owning the mirror update lock.

    Checkout uses the same mirror lock after its durable CAS. Whichever
    operation committed last therefore also supplies the displayed settings.
    """
    from openprogram.runtime.context.session_db import default_db
    from openprogram.state.store.session.branch_config import BRANCH_CONFIG_FIELDS
    from openprogram.interfaces.server import server as server
    with server._sessions_lock:
        row = default_db().get_session(session_id) or {}
        conv = server._sessions.get(session_id)
        if conv is not None:
            conv.update({key: row.get(key) for key in BRANCH_CONFIG_FIELDS})
            conv["active_branch_id"] = row.get("active_branch_id")
            conv["provider_name"] = row.get("provider_override")
        return row


def commit_model(session_id: str, body: dict, provider: str, model: str, conv: dict,
                 *, runtime=None, replace_runtime: bool = False) -> dict:
    """Persist before changing the live mirror; stale requests have no effects.

    Returns the written branch's settings over the session row, with its
    ``branch_id``: a named branch that is not selected reports its own model,
    and ``branch_id != active_branch_id`` tells the caller not to announce
    it as the selected branch's.
    """
    from openprogram.runtime.context.session_db import default_db
    from openprogram.state.store.session.branch_config import branch_identity, BRANCH_CONFIG_FIELDS, config_for_branch
    from openprogram.models.providers.catalog.last_used import record_last_used_model
    from openprogram.interfaces.server import server as server
    db = default_db()
    before = db.get_session(session_id) or {}
    accepted_id = before.get("active_branch_id") or branch_identity(session_id, before.get("head_id"))
    target = branch_target(body)
    named = target.pop("branch_id")
    try:
        db.update_branch_config(session_id, named, {"provider_override": provider, "model_override": model}, **target)
    except Exception:
        if replace_runtime and runtime is not None and hasattr(runtime, "close"):
            runtime.close()
        raise
    old = None
    with server._sessions_lock:
        row = db.get_session(session_id) or {}
        # Another branch's model is its own; the live runtime is the selected one's.
        current = row.get("active_branch_id") == accepted_id and (not named or named == accepted_id)
        conv.update({key: row.get(key) for key in BRANCH_CONFIG_FIELDS})
        conv.update(active_branch_id=row.get("active_branch_id"), provider_name=row.get("provider_override"))
        if current:
            old = conv.get("runtime")
            if replace_runtime:
                conv["runtime"] = runtime
            elif old is not None:
                old.model = model
    disposable = old if current and replace_runtime else runtime if not current and replace_runtime else None
    if disposable is not None and disposable is not (runtime if current else old) and hasattr(disposable, "close"):
        try:
            disposable.close()
        except Exception:
            pass
    record_last_used_model(provider, model)
    if named and named != row.get("active_branch_id"):
        return {**row, **config_for_branch(row, named), "branch_id": named}
    return {**row, "branch_id": row.get("active_branch_id")}


def resolved_settings(session_id: str, branch_id: str | None, *, strict: bool = False) -> dict:
    """``values``, ``sources`` and ``inherited`` of one branch, as JSON: what
    the composer shows a setting resolves to and where from, and what
    "Agent default" (or a switch turned back) puts it back to."""
    from openprogram.runtime.conversation.session_config import resolve_branch_settings
    from openprogram.state.store.session.branch_config import BranchConfigConflict
    try:
        settings = resolve_branch_settings(session_id, branch_id or None)
    except BranchConfigConflict:
        if strict:
            raise
        return {}
    plain = lambda mapping: {key: (vars(value) if hasattr(value, "__dict__") else value)  # noqa: E731
                             for key, value in mapping.items()}
    return {"branch_id": settings.branch_id, "values": plain(settings.values),
            "sources": dict(settings.sources), "inherited": plain(settings.inherited)}
