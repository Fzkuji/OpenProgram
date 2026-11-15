"""Tell the model when an earlier step of this conversation has an unknown outcome."""
from __future__ import annotations

from contextlib import closing
import json
import logging
import threading

_log = logging.getLogger(__name__)


def is_inspection_tool(tool) -> bool:
    """Recognize actual builtin implementations, not names or plugin flags.

    Permission wrappers pass the original resolved tool here. Copies retaining
    the builtin execute callable are fine; replacing that callable is not.
    Shell, MCP and custom tools are deliberately not classified as read-only.
    """
    from openprogram.programs.tools.files.read import read
    from openprogram.programs.tools.files.grep import grep
    from openprogram.programs.tools.files.glob import glob_tool
    from openprogram.programs.tools.files.list import list_dir

    return any(tool.name == builtin.name and getattr(tool, "execute", None) is builtin.execute
               for builtin in (read, grep, glob_tool, list_dir))


# Tools that operate a screen. What an interrupted step of theirs changed can
# be read off the screen, and the report of the interruption tells the model
# to look before repeating it, so it needs no notice of its own.
SCREEN_TOOLS = frozenset({"computer_use_agent", "computer_use"})


def _stuck_effects(connection) -> list[dict]:
    """Effects left open by a run that lost its owner, found from the few such runs."""
    lost = connection.rows("executions", status=("reconciliation_required", "interrupted"), current_attempt_id=None)
    if not lost:
        return []
    return connection.rows("effects", execution_id=[run["execution_id"] for run in lost],
                           status=("dispatched", "uncertain"))


def _admissions(connection) -> tuple:
    """A value that changes whenever an execution is admitted or removed."""
    executions = connection.rows("executions")
    stamp = [hash(frozenset(row["execution_id"] for row in executions))]
    for rows in (executions, connection.rows("execution_inputs"), connection.rows("execution_agent_turn_inputs")):
        stamp += [len(rows), max((row["created_at"] for row in rows), default=None)]
    return tuple(stamp)


def _identity(metadata_json: str) -> tuple[object, object]:
    """An effect's kind and the tool it names, when it names one as text."""
    metadata = json.loads(metadata_json)
    if not isinstance(metadata, dict):
        return None, None
    payload = metadata.get("payload")
    tool_name = payload.get("tool_name") if isinstance(payload, dict) else None
    return metadata.get("kind"), tool_name if isinstance(tool_name, str) else None


_cache_lock = threading.Lock()
# (store, effect) -> (kind, tool name). Both are fixed when the effect is
# registered; retention drops replay payloads and keeps them.
_identities: dict[tuple[str, str], tuple[object, object]] = {}
# (store, conversation) -> (the admissions it was derived from, its executions, their parent links)
_scopes: dict[tuple[str, str], tuple[tuple, frozenset, dict]] = {}
_MAX_IDENTITIES = 20_000
_MAX_SCOPES = 256


def _conversation(store, connection, session_id: str) -> tuple[frozenset, dict]:
    """A conversation's executions and their parent links.

    Membership comes from admission rows, which never change, so the answer
    is reused until an execution is admitted or removed. An answer that had
    to read a session graph is not kept: the graph still grows.
    """
    from openprogram.runtime.execution.conversation_scope import conversation_execution_scope

    stamp = _admissions(connection)
    key = (str(store.path), session_id)
    with _cache_lock:
        held = _scopes.get(key)
    if held is not None and held[0] == stamp:
        return held[1], held[2]
    consulted: set[str] = set()
    executions, parents = conversation_execution_scope(store, session_id, consulted=consulted)
    members = frozenset(execution.execution_id for execution in executions)
    if not consulted:
        with _cache_lock:
            if len(_scopes) >= _MAX_SCOPES:
                _scopes.clear()
            _scopes[key] = (stamp, members, parents)
    return members, parents


def _identify(store, connection, effect_ids: list[str]) -> dict[str, tuple[object, object]]:
    """Each effect's kind and tool name, read from its payload once per process."""
    path = str(store.path)
    with _cache_lock:
        known = {effect_id: _identities[(path, effect_id)] for effect_id in effect_ids
                 if (path, effect_id) in _identities}
    missing = [effect_id for effect_id in effect_ids if effect_id not in known]
    if missing:
        for row in connection.rows("effects", effect_id=missing):
            known[row["effect_id"]] = _identity(row["metadata_json"])
        with _cache_lock:
            if len(_identities) + len(missing) > _MAX_IDENTITIES:
                _identities.clear()
            for effect_id in missing:
                if effect_id in known:
                    _identities[(path, effect_id)] = known[effect_id]
    return known


def uncertain_operations(request) -> list[dict[str, str]]:
    """Read canonical unresolved effects without resolving or replaying them.

    Only ownerless interrupted runs are recovery hazards. An active sibling's
    normal dispatched operation is not a failed restart. Include exact called
    Jobs/descendants, never unrelated work in their target session. Ancestor
    session hints only widen what is reported.

    This runs before every tool call, several times, so it parses no payload
    it does not need: the open effects by id, the conversation they must
    belong to, then kind and tool name of the few that do.
    """
    from openprogram.runtime.execution import default_store

    store = default_store()
    sessions = {sid for sid in (getattr(request, key, None)
                                for key in ("session_id", "spawned_from_session")) if sid}
    with closing(store._connect()) as connection:
        rows = _stuck_effects(connection)
        if not rows:
            return []
        members: set[str] = set()
        for sid in sessions:
            members |= _conversation(store, connection, sid)[0]
        rows = sorted((row for row in rows if row["execution_id"] in members),
                      key=lambda row: row["effect_id"])
        if not rows:
            return []
        identities = _identify(store, connection, [row["effect_id"] for row in rows])
    result = []
    for row in rows:
        kind, name = identities.get(row["effect_id"], (None, None))
        if kind == "provider.before":
            continue
        if name is None and kind == "gui.primitive":
            # A step of computer_use_agent code, recorded by its GUI broker.
            name = "computer_use_agent"
        result.append({"effect_id": row["effect_id"], "execution_id": row["execution_id"],
                       "action_id": row["action_id"], "tool": name if isinstance(name, str) else "unknown"})
    return result


# Executions already told (or found to have nothing to be told).
_noticed: set[str] = set()
_MAX_NOTICED = 4096
_MAX_NAMED = 3


def _note(execution_id: str) -> bool:
    """Whether this is the execution's first non-inspection call."""
    with _cache_lock:
        if execution_id in _noticed:
            return False
        if len(_noticed) >= _MAX_NOTICED:
            _noticed.clear()
        _noticed.add(execution_id)
    return True


def uncertain_notice(request, tool) -> str | None:
    """A line for the first non-inspection tool result of an execution, or ``None``.

    An operation of an interrupted run may or may not have finished. The model
    is told once and decides what to check; nothing is held back, and the
    effect store still fences a replay inside one execution.
    """
    from openprogram.runtime.permissions.session_facts import facts

    if is_inspection_tool(tool):
        return None
    if not _note(facts().current_execution_id() or f"session:{request.session_id}"):
        return None
    try:
        operations = [item for item in uncertain_operations(request) if item["tool"] not in SCREEN_TOOLS]
    except Exception:  # noqa: BLE001 - a notice never stops the call it would accompany
        _log.warning("uncertain operations unavailable for session %s", request.session_id, exc_info=True)
        return None
    if not operations:
        return None
    named = ", ".join(f"{item['tool']} ({item['action_id'] or item['effect_id']})" for item in operations[:_MAX_NAMED])
    more = f" and {len(operations) - _MAX_NAMED} more" if len(operations) > _MAX_NAMED else ""
    return f"[notice] An earlier step may or may not have finished: {named}{more}. Check before repeating it."
