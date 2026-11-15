"""Branch-owned settings and the selected branch's compatibility projection."""
from __future__ import annotations

from copy import deepcopy
import hashlib

BRANCH_CONFIG_FIELDS = (
    "agent_id", "provider_override", "model_override", "thinking_effort",
    "tools_override", "web_search", "permission_mode", "permission_state",
    "permission_rules", "sandbox_enabled", "service_tier",
)
BRANCH_UNSET = object()


class BranchConfigConflict(ValueError):
    """A branch-bound settings write lost its branch.

    ``code`` is ``branch_changed`` when an ``expected_branch_id`` guard no
    longer names the selected branch, ``branch_gone`` when a named branch has
    no record (removed, aborted, or never recorded).
    """

    def __init__(self, message: str = "", code: str = "branch_changed"):
        super().__init__(message)
        self.code = code


def branch_identity(session_id: str, head: str | None) -> str:
    digest = hashlib.sha256(f"{session_id}\0{head or 'ROOT'}".encode()).hexdigest()
    return f"branch_{digest[:16]}"


def snapshot(meta: dict) -> dict:
    """Include missing values: restoring a branch must clear old overrides."""
    return {key: deepcopy(meta.get(key)) for key in BRANCH_CONFIG_FIELDS}


def config_for_branch(meta: dict, branch_id: str | None) -> dict:
    """One branch's settings, read from its own record.

    Every branch, the selected one too, keeps its settings in
    ``branch_refs[branch_id]["config"]``; the conversation's top-level
    fields are the selected branch's projection, written together with
    it (``sync_active_config``). A selected branch recorded before its
    settings moved onto its record reads the projection.
    """
    refs = meta.get("branch_refs") or {}
    ref = refs.get(branch_id) if branch_id else None
    if isinstance(ref, dict) and isinstance(ref.get("config"), dict):
        return snapshot(ref["config"])
    if branch_id == meta.get("active_branch_id") or (not meta.get("active_branch_id") and not refs):
        # No branch is recorded yet (a conversation whose first turn is a
        # scheduled run): the conversation's own settings are the only ones.
        return snapshot(meta)
    raise BranchConfigConflict("Branch configuration is unavailable; reload the conversation.",
                               code="branch_gone")


def write_branch_config(meta: dict, session_id: str, branch_id: str | None, values: dict) -> bool:
    """Write settings onto one named branch; ``None`` puts a setting back to inherit.

    The selected branch (or no branch named, or a conversation with no branch
    recorded yet) is written at the top level, which stays its projection.
    Any other branch changes only its own record, whatever is selected.
    Returns whether the selected branch was the one written.
    """
    unknown = set(values) - set(BRANCH_CONFIG_FIELDS)
    if unknown:
        raise ValueError(f"not branch settings: {sorted(unknown)}")
    active = meta.get("active_branch_id")
    refs = meta.get("branch_refs") or {}
    if branch_id is None or branch_id == active or (not active and not refs):
        meta.update(deepcopy(values))
        sync_active_config(meta, session_id)
        return True
    ref = refs.get(branch_id)
    if not isinstance(ref, dict) or ref.get("status") == "aborted":
        raise BranchConfigConflict("The branch no longer exists; reload the conversation.",
                                   code="branch_gone")
    refs = deepcopy(refs)
    config = snapshot(ref["config"] if isinstance(ref.get("config"), dict) else meta)
    config.update(deepcopy(values))
    refs[branch_id] = {**refs[branch_id], "branch_id": branch_id, "config": config}
    meta["branch_refs"] = refs
    return False


def reserve_task_branch(meta: dict, session_id: str, *, task_id: str, msg_id: str,
                        agent_id: str | None, label=None) -> str:
    """Point a scheduled task's branch record at the message its next run adds.

    The first run copies the selected branch's settings once, with the task's
    Agent; later runs keep the record's own settings and take the task's
    Agent, which the task decides at each firing. A record the user has
    selected is left as it is. Otherwise the record's head names the
    run's not-yet-written message, so admission finds the run's branch by it
    and the run's reply carries the record forward (``carry_branch``).
    """
    refs = deepcopy(meta.get("branch_refs") or {})
    held = [key for key, ref in refs.items()
            if ref.get("scheduled_task_id") == task_id and ref.get("status") != "aborted"]
    if held and held[0] == meta.get("active_branch_id"):
        # The user has this branch selected: the run continues it in place,
        # with the settings on screen.
        return held[0]
    if held:
        branch_id = held[0]
        ref = refs[branch_id]
        config = snapshot(ref["config"] if isinstance(ref.get("config"), dict) else meta)
    else:
        branch_id = branch_identity(session_id, msg_id)
        active = meta.get("active_branch_id")
        source = refs.get(active, {}).get("config") if active else None
        config = snapshot(source if isinstance(source, dict) else meta)
        ref = {"branch_id": branch_id, "scheduled_task_id": task_id,
               **({"parent_branch_id": active} if active else {})}
    if agent_id:
        config["agent_id"] = agent_id
    ref = {key: value for key, value in ref.items() if key != "label"}
    if label and msg_id not in (meta.get("branches") or {}):
        # The name lives in the branch record at the tip, like every
        # branch's; the run's reply carries it on (``carry_branch``).
        meta["branches"] = {**(meta.get("branches") or {}), msg_id: _as_record(label)}
    refs[branch_id] = {**ref, "head_id": msg_id, "config": config, "status": "active"}
    meta["branch_refs"] = refs
    return branch_id


def reserved_branch(meta: dict, msg_id: str | None) -> str | None:
    """The branch record reserved for a message that is about to be written."""
    if not msg_id:
        return None
    held = [key for key, ref in (meta.get("branch_refs") or {}).items()
            if ref.get("head_id") == msg_id and ref.get("status") != "aborted"
            and key != meta.get("active_branch_id")]
    return held[0] if len(held) == 1 else None


def sync_active_config(meta: dict, session_id: str) -> None:
    branch_id = meta.get("active_branch_id") or branch_identity(session_id, meta.get("head_id"))
    refs = deepcopy(meta.get("branch_refs") or {})
    refs[branch_id] = {**refs.get(branch_id, {}), "branch_id": branch_id,
                       "head_id": meta.get("head_id"), "config": snapshot(meta)}
    meta.update(active_branch_id=branch_id, branch_refs=refs)


def check_branch(meta: dict, expected=BRANCH_UNSET) -> None:
    if expected is not BRANCH_UNSET and expected != meta.get("active_branch_id"):
        raise BranchConfigConflict("The selected branch changed; reload its settings and try again.",
                                   code="branch_changed")


def carry_branch(meta: dict, predecessor: str | None, node_id: str) -> bool:
    """Move a branch that is not checked out onto the message added under its tip.

    A background run (a scheduled task's) adds to its own branch while the
    conversation stays where the user left it; the branch's record follows.
    """
    refs = meta.get("branch_refs") or {}
    held = [key for key, ref in refs.items()
            if key != meta.get("active_branch_id") and ref.get("status") != "aborted"
            and predecessor and ref.get("head_id") == predecessor]
    if len(held) != 1:
        return False
    refs = deepcopy(refs)
    refs[held[0]]["head_id"] = node_id
    meta["branch_refs"] = refs
    move_branch_record(meta, predecessor, node_id)
    return True


def point_branch(meta: dict, branch_id: str, *, expected_head: str | None, head: str) -> bool:
    """Move a recorded branch from ``expected_head`` to ``head``.

    A person's message on that branch (a turn running beside the selected
    one) is written under its tip with the head left alone; the record
    follows it here, so the reply, carried by ``carry_branch``, lands on the
    same branch. When the person selected that branch meanwhile, the head
    moves with its record. Refused when the branch is gone or has moved.
    """
    refs = meta.get("branch_refs") or {}
    ref = refs.get(branch_id)
    selected = branch_id == meta.get("active_branch_id")
    if (not isinstance(ref, dict) or ref.get("status") == "aborted"
            or ref.get("head_id") != expected_head
            or (selected and meta.get("head_id") != expected_head)):
        return False
    refs = deepcopy(refs)
    refs[branch_id]["head_id"] = head
    meta["branch_refs"] = refs
    move_branch_record(meta, expected_head, head)
    if selected:
        meta["head_id"] = head
    return True


def record_on_line(refs: dict, target: str | None, nodes: dict, *, stop: set) -> str | None:
    """The recorded branch a switch to ``target`` selects.

    The record whose head is ``target``; else the nearest record whose head
    lies on ``target``'s own line above it, up to (not into) ``stop``, the
    chain of the branch being left. Such a record is that line's branch and
    is reused, moved down to ``target``: a switch back to a branch keeps its
    record (and its settings) even when its record fell behind its tip.
    ``None``: no record holds that line, and a new one is made.
    """
    exact = [key for key, ref in refs.items() if isinstance(ref, dict) and ref.get("head_id") == target]
    if exact:
        return exact[0]
    live = {key: ref for key, ref in refs.items()
            if isinstance(ref, dict) and ref.get("status") != "aborted"}
    by_head: dict = {}
    for key, ref in live.items():
        by_head.setdefault(ref.get("head_id"), key)
    node = nodes.get(target) if target else None
    seen = {target}
    cursor = getattr(node, "predecessor", None) if node is not None else None
    while cursor and cursor not in seen and cursor not in stop and cursor != "ROOT":
        seen.add(cursor)
        if cursor in by_head:
            return by_head[cursor]
        node = nodes.get(cursor)
        cursor = getattr(node, "predecessor", None) if node is not None else None
    return None


#: What a branch record keeps about the branch's own runs, beside its
#: settings: the last request's occupancy on its chain.
BRANCH_RECORD_FIELDS = ("last_prompt_tokens",)


def set_branch_fields(meta: dict, branch_id: str, fields: dict) -> bool:
    """Record facts about one branch's runs on its own record.

    Any recorded branch, selected or not; the selected branch's top-level
    copy is written by whoever writes the conversation's own fields. False
    when the branch has no record (removed, aborted or never recorded).
    """
    unknown = set(fields) - set(BRANCH_RECORD_FIELDS)
    if unknown:
        raise ValueError(f"not branch record fields: {sorted(unknown)}")
    refs = meta.get("branch_refs") or {}
    ref = refs.get(branch_id)
    if not isinstance(ref, dict) or ref.get("status") == "aborted":
        return False
    refs = deepcopy(refs)
    refs[branch_id] = {**ref, **deepcopy(fields)}
    meta["branch_refs"] = refs
    return True


def advance_branch(meta: dict, session_id: str, target: str | None, nodes: dict) -> None:
    """Continue the active line, or copy settings once for a true new fork.

    Raw append paths may already have updated HEAD. The stable ref remembers
    the prior tip and is therefore the source for detecting a sibling fork.
    """
    active_id = meta.get("active_branch_id")
    refs = deepcopy(meta.get("branch_refs") or {})
    source = refs.get(active_id, {}).get("head_id", meta.get("head_id"))
    if not active_id:
        active_id = branch_identity(session_id, source)
    config = snapshot(meta)
    refs[active_id] = {**refs.get(active_id, {}), "branch_id": active_id,
                       "head_id": source, "config": config}
    park_record(meta, refs, active_id)
    cursor = target
    target_chain = []
    seen = set()
    while cursor and cursor not in seen:
        target_chain.append(cursor)
        seen.add(cursor)
        node = nodes.get(cursor)
        cursor = node.predecessor if node is not None else None
    # Checking out another recorded branch is a switch even when that branch
    # leaves from this one's tip (a scheduled task's): this one stays where it is.
    other = any(key != active_id and ref.get("head_id") == target and ref.get("status") != "aborted"
                for key, ref in refs.items())
    continues = not other and (source is None or source in target_chain)
    if continues:
        # The record follows every message the branch adds, so a name or
        # lock written mid-turn lands on the record that holds the
        # branch's turn count (branch-naming: one turn on a branch).
        follow_line(meta, source, target)
    if not continues:
        # The first divergent user anchors the fork even if a placeholder
        # advances HEAD directly from the preceding branch's assistant.
        source_chain = set()
        cursor = source
        while cursor and cursor not in source_chain:
            source_chain.add(cursor)
            node = nodes.get(cursor)
            cursor = node.predecessor if node is not None else None
        selected = record_on_line({key: ref for key, ref in refs.items() if key != active_id},
                                  target, nodes, stop=source_chain)
        divergent = []
        for node_id in target_chain:
            if node_id in source_chain:
                break
            divergent.append(node_id)
        anchor = divergent[-1] if divergent else target
        selected = selected or branch_identity(session_id, anchor)
        target_ref = refs.get(selected, {})
        # A new branch (a retry, an edit, a fork) starts with a copy of the
        # settings the branch it leaves from has now (Codex's fork takes the
        # current config too); later changes on either side stay apart.
        config = snapshot(target_ref.get("config", config))
        if selected not in refs:
            # What else the new branch copies once (its todo board) is read
            # from the branch it was made from.
            target_ref = {"parent_branch_id": active_id}
        refs[selected] = {**refs.get(selected, {}), **target_ref}
        active_id = selected
    refs[active_id] = {**refs.get(active_id, {}), "branch_id": active_id,
                       "head_id": target, "config": config, "status": "active"}
    meta.update(config)
    meta.update(head_id=target, active_branch_id=active_id, branch_refs=refs)


def follow_agent_resets(meta: dict, agent_settings) -> dict:
    """The branch settings to put back to "follow the Agent": ``{branch_id:
    [field, ...]}``, ``None`` naming the conversation's own (top-level)
    settings when it has no branch recorded yet.

    A model that equals the branch Agent's own model was copied onto the
    branch when the Agent was picked (the Agent switch used to copy it), so
    it is cleared and the branch follows the Agent again; any other model
    is the person's pick and stays. A web search switch equal to the
    Agent's choice was written by every send, and is cleared the same way.
    ``agent_settings(agent_id)`` answers ``{"model": "provider/id",
    "web_search": bool}`` for an Agent (``{}`` for one that is gone).
    Running it again finds nothing to do.
    """
    refs = meta.get("branch_refs") or {}
    configs: dict = {key: ref["config"] for key, ref in refs.items()
                     if isinstance(ref, dict) and isinstance(ref.get("config"), dict)}
    if not configs:
        configs = {None: meta}
    resets: dict = {}
    for key, config in configs.items():
        agent = agent_settings(config.get("agent_id") or meta.get("agent_id")) or {}
        fields = []
        provider, model = config.get("provider_override"), config.get("model_override")
        pinned = f"{provider}/{model}" if provider and model else model
        if pinned and agent.get("model") and pinned == agent["model"]:
            fields += ["provider_override", "model_override"]
        if isinstance(config.get("web_search"), bool) and config["web_search"] is agent.get("web_search"):
            fields.append("web_search")
        if fields:
            resets[key] = fields
    return resets


def last_written_branch(meta: dict) -> str | None:
    """The branch a person's message was last written to, else the selected
    one: where an entry that names no branch continues (Pi opens a session
    at the entry written last)."""
    last = meta.get("last_branch_id")
    refs = meta.get("branch_refs") or {}
    ref = refs.get(last) if isinstance(last, str) else None
    if isinstance(ref, dict) and ref.get("status") != "aborted":
        return last
    return meta.get("active_branch_id")


def _as_record(label) -> dict:
    """A name as a branch record: a record stays one, a bare name becomes ``{"name"}``."""
    return deepcopy(label) if isinstance(label, dict) else {"name": label}


def _merged_record(older, newer) -> dict:
    """A branch record carried onto a tip that already has one: the
    fields written at the new tip win, every other field comes along
    (a name written mid-turn keeps the turn count from before)."""
    merged = _as_record(older) if older is not None else {}
    if newer is not None:
        merged.update(_as_record(newer))
    return merged


def follow_line(meta: dict, source: str | None, target: str | None) -> None:
    """The selected branch moved from tip ``source`` down its own line to
    ``target``: its record moves with it, as an unselected branch's does
    (``carry_branch``). Another branch that still ends at ``source``
    reads its parked record (``park_record``)."""
    named = meta.get("branches") or {}
    if not source or not target or source == target or source not in named:
        return
    named = dict(named)
    named[target] = _merged_record(named.pop(source), named.get(target))
    meta["branches"] = named


def _live_at(meta: dict, tip, *, but=None) -> list[str]:
    """The live recorded branches that end at ``tip``, except ``but``."""
    return [key for key, ref in (meta.get("branch_refs") or {}).items()
            if key != but and isinstance(ref, dict) and ref.get("status") != "aborted"
            and ref.get("head_id") == tip]


def branch_record(meta: dict, branch_id: str | None) -> dict:
    """The name record of a recorded branch, selected or not.

    A branch's name and naming state (``name``, ``name_locked``,
    ``turns``, ``archived``) is the record in ``meta["branches"]`` at the
    branch's tip, where renames, automatic names and archiving write it.
    Every reader takes it from there. Only when several branches end at
    the same message (two lines rewound to one point) can one tip not
    name them all: the selected one owns the record at the tip, and each
    other one keeps its own record parked on its ref as ``label``
    (``park_record``), put back at the tip when it is selected again
    (``unpark_record``). A tip with no record falls back to the parked one.
    """
    ref = (meta.get("branch_refs") or {}).get(branch_id) if branch_id else None
    if not isinstance(ref, dict):
        return {}
    named = meta.get("branches") or {}
    tip = ref.get("head_id")
    owns = not _live_at(meta, tip, but=branch_id) or branch_id == meta.get("active_branch_id")
    if tip in named and owns:
        entry = named[tip]
        return deepcopy(entry) if isinstance(entry, dict) else {"name": entry}
    label = ref.get("label")
    return _as_record(label) if label else {}


def park_record(meta: dict, refs: dict, branch_id: str | None) -> None:
    """Park the record at ``refs[branch_id]``'s tip on that ref, as
    ``label``, before another branch may take that tip's record.

    ``meta`` and ``refs`` are the record read inside the session lock and
    the caller's own copy of its ``branch_refs``.
    """
    ref = refs.get(branch_id) if branch_id else None
    if not isinstance(ref, dict):
        return
    entry = (meta.get("branches") or {}).get(ref.get("head_id"))
    if entry is not None:
        refs[branch_id] = {**ref, "label": deepcopy(entry)}


def unpark_record(meta: dict, refs: dict, branch_id: str | None) -> None:
    """Give the branch being selected its parked record back at its tip
    when the tip is shared with another branch or has no record; a tip
    record of its own (a rename while it was not selected) is kept."""
    ref = refs.get(branch_id) if branch_id else None
    if not isinstance(ref, dict) or not ref.get("label"):
        return
    tip = ref.get("head_id")
    named = meta.get("branches") or {}
    shared = any(key != branch_id and isinstance(other, dict) and other.get("status") != "aborted"
                 and other.get("head_id") == tip for key, other in refs.items())
    if tip and (shared or tip not in named):
        meta["branches"] = {**named, tip: _as_record(ref["label"])}


def move_branch_record(meta: dict, source: str | None, target: str | None, *,
                       owner: str | None = None) -> bool:
    """Move the name record at tip ``source`` onto tip ``target``.

    Without ``owner`` (an unselected branch carried forward) the record
    stays when another recorded branch still ends at ``source`` (it names
    that one), and a record already at ``target`` is kept. With ``owner``
    (the selected branch moved by a rewind or its undo) the record goes
    with ``owner``: other branches ending at ``target`` park theirs first,
    and the ones left at ``source`` read their parked records.
    """
    named = meta.get("branches") or {}
    if not source or not target or source == target:
        return False
    if owner is None:
        if source not in named or _live_at(meta, source):
            return False
        named = dict(named)
        named[target] = _merged_record(named.pop(source), named.get(target))
        meta["branches"] = named
        return True
    refs = deepcopy(meta.get("branch_refs") or {})
    for key in _live_at(meta, target, but=owner):
        park_record(meta, refs, key)
    meta["branch_refs"] = refs
    named = dict(named)
    if source in named:
        named[target] = named.pop(source)
    else:
        named.pop(target, None)
    meta["branches"] = named
    return True


#: The session fields that hold branch records. They are changed only on
#: the session record read inside the session write lock, never written
#: back from a copy read earlier (the cache, or a read before the lock).
BRANCH_RECORD_KEYS = ("branches", "branch_refs", "active_branch_id")


def take_durable_branch_records(meta: dict, durable: dict) -> None:
    """Put the branch records of ``durable`` (the record on disk, read
    inside the lock) into ``meta`` before ``meta`` is written whole.

    A session with no record on disk yet keeps the ones in ``meta``.
    """
    if not durable:
        return
    for key in BRANCH_RECORD_KEYS:
        if key in durable:
            meta[key] = durable[key]
        else:
            meta.pop(key, None)
