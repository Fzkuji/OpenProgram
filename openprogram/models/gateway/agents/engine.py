"""Connecting an agent to the gateway and putting it back, for every agent alike.

After yetone/magpie ``internal/agent`` (``stash.go``, ``applied.go``,
``agents.go`` ``beforeMagpie``, ``Disconnect``; MIT License, Copyright (c)
yetone). Connecting:

1. asks the agent what it writes for the picks (:attr:`Agent.wire`);
2. reads every file first and works out each edit; one that cannot be made
   (a file that does not parse, an edit that would change anything else)
   leaves every file alone;
3. stashes each key's value before OpenProgram first writes it, and, for an
   agent that asks (``before_copy``), a copy of the file whole;
4. writes the files, putting back the ones already written when a later one
   fails (magpie's ``atomic``);
5. records what was written, the picks, the gateway address and the time, so
   later edits by others show as drift and a moved port is rewired;
6. runs the agent's route hook (the Codex app's history and app-server)
   and its after hook (a running app given the change live).

An agent whose wiring is not a file edit (``Agent.live``: a running app's
API, a daemon's database, the user's environment, a credential store) does
its part through its hooks before any file is written; what a hook keeps to
put back is stored in the record (``live``) and handed back to it. An app
that takes the gateway only through its own import link (``Agent.link``)
is never connected here: its row offers the link and reads whether the app
has it (:func:`import_link`, ``_link_status``).

Disconnecting puts back each stashed value whose key still holds what
OpenProgram wrote; a key changed since is the user's and stays. A parent
object OpenProgram had to add goes again once it is empty, a file that was
not there before goes again once nothing is left in it, and a copy taken
whole is put back where what is left reads the same.
"""
from __future__ import annotations

import copy
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .. import edit, stash
from ..edit import ConfigText, ConfigUnreadable, get_path
from .base import ROOT, Agent, AppGone, Wiring, Write


class AgentConfigError(Exception):
    """An agent's config was left alone; the message says why."""


def strip_ref(model_ref: str) -> str:
    """``provider/model`` from a ref that may carry ``openprogram:``."""
    from ..conversion import REF_PREFIX
    value = (model_ref or "").strip()
    return value[len(REF_PREFIX):] if value.startswith(REF_PREFIX) else value


def _find(agent_id: str) -> Agent:
    from . import find
    agent = find(agent_id)
    if agent is None:
        raise AgentConfigError(f"unknown agent {agent_id!r}")
    return agent


def agent_key(agent: Agent) -> str:
    """The key an agent is given: one that names it (``keys.token_for``),
    never the gateway key itself, which no agent's file holds."""
    from ..keys import token_for
    return token_for(agent.key_of or agent.id)


def key_mark(key: str) -> str:
    """What the stash keeps of the key an agent was given: enough to tell it changed."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def key_stale(agent: Agent, record: dict[str, Any]) -> bool:
    """The agent's files hold another key than the one it is given now (one
    connected before every agent had a key of its own): its next sync, or
    the Worker's start (``lifecycle.rewire``), writes it all again."""
    return record.get("key") != key_mark(agent_key(agent))


def wiring(agent: Agent, values: dict[str, str], options: Optional[dict[str, Any]] = None, *,
           syncing: bool = False) -> Wiring:
    from ..address import worker_base_url
    from ..conversion import enabled_model_refs
    return Wiring(agent=agent, values=values, worker=worker_base_url(stable=True), key=agent_key(agent),
                  models=enabled_model_refs(), options=dict(options or {}),
                  previous=copy.deepcopy(stash.entry(agent.id) or {}), syncing=syncing)


# Plans

@dataclass
class _Plan:
    write: Write
    text: ConfigText
    new: str
    originals: dict[str, tuple[bool, Any]] = field(default_factory=dict)
    parents: list[str] = field(default_factory=list)
    wrote: dict[str, dict[str, Any]] = field(default_factory=dict)
    kept: dict[str, Any] = field(default_factory=dict)


def _ancestors(dotted: str) -> list[str]:
    parts = edit.split_path(dotted)
    escaped = [p.replace(".", "\\.") for p in parts]
    return [".".join(escaped[:i]) for i in range(1, len(parts))]


def _plans(writes: list[Write], agent_id: str = "") -> list[_Plan]:
    """A plan per write; a second write to a file edits what the first leaves
    (an agent's model list and its model in one file, the list alone synced)."""
    files = ((stash.entry(agent_id) or {}).get("files") or {}) if agent_id else {}
    plans: list[_Plan] = []
    latest: dict[Path, _Plan] = {}
    for write in writes:
        before = latest.get(Path(write.path))
        text = None
        if before is not None:
            was = before.text
            text = ConfigText(was.path, before.new, was.exists, was.newline, was.bom)
        if text is None:
            text = ConfigText.read(write.path)
            if not text.exists and write.base is not None:
                # the file as the agent itself would have made it: not OpenProgram's
                text = ConfigText(text.path, write.base, False, text.newline, text.bom)
        plan = _plan(write, text, (files.get(str(text.path)) or {}).get("wrote") or {})
        latest[Path(write.path)] = plan
        plans.append(plan)
    return plans


def _plan(write: Write, text: Optional[ConfigText] = None, previous: Optional[dict[str, Any]] = None) -> _Plan:
    text = text if text is not None else ConfigText.read(write.path)
    where = str(write.path)
    previous = previous or {}
    if write.format == "file":
        whole: dict[str, Any] = {"sha": _sha(write.content or "")}
        if ROOT in write.owns:
            whole["owns"] = True
        return _Plan(write, text, write.content or "", wrote={"": whole})
    doc = _load(write.format, text.text, where)
    lists: dict[str, Any] = {}
    had_lists: dict[str, tuple[bool, Any]] = {}
    for key, members in write.members.items():
        present, current = _get(doc, key, where)
        # whether the list was there, so disconnect leaves it, empty or not
        had_lists[key] = (present, [])
        items = _kept_items(members, current or [], ((previous.get(key) or {}).get("members") or {}).get("items"))
        theirs = [item for item in (current or []) if not matches(item, members.match)]
        place = next((i for i, item in enumerate(current or []) if matches(item, members.match)), None)
        if members.keep_place and place is not None:
            before = sum(1 for item in (current or [])[:place] if not matches(item, members.match))
            merged = [*theirs[:before], *items, *theirs[before:]]
        elif members.first:
            merged = [*items, *theirs]
        else:
            merged = [*theirs, *items]
        if merged or present:
            lists[key] = merged
    if ROOT in write.members:
        wrote = {ROOT: {"members": _members_record(write.members[ROOT])}}
        return _Plan(write, text, _dump_list(lists.get(ROOT, [])), wrote=wrote)
    # a delete inside an object OpenProgram owns takes the field out of the
    # object written (OpenCode's model list, which its plugin then fetches)
    inside = {key: [d[len(key) + 1:] for d in write.deletes if d.startswith(key + ".")] for key in write.owns}
    deletes = [key for key in write.deletes if get_path(doc, key)[0]
               and not any(key.startswith(owner + ".") for owner in write.owns)]
    originals = {**had_lists, **{key: get_path(doc, key) for key in [*write.sets, *deletes]}}
    parents = sorted({parent for key in [*write.sets, *write.keep, *write.members] for parent in _ancestors(key)
                      if not get_path(doc, parent)[0]})
    sets = dict(write.sets)
    for key in write.owns:
        # what the user added inside OpenProgram's own object stays (magpie theirsKept)
        present, current = get_path(doc, key)
        if present and isinstance(current, dict) and isinstance(sets.get(key), dict):
            merged = copy.deepcopy({**current, **sets[key]})
            for inner in inside.get(key) or []:
                edit.delete_path(merged, inner)
            sets[key] = merged
    new = edit.edit(write.format, text.text, sets={**write.keep, **sets, **lists}, deletes=deletes,
                    where=where, members=_matchers({key: m.match for key, m in write.members.items()}))
    wrote: dict[str, Any] = {}
    picks = {key: pick for pick in write.picks for key in pick.keys}
    for key, value in write.sets.items():
        record: dict[str, Any] = {"set": value}
        if key in picks:
            record["pick"] = {"on": picks[key].on, "want": picks[key].want}
        elif key in write.owns:
            record["owns"] = True
            inside = sorted(loose[len(key) + 1:] for loose in write.loose if loose.startswith(key + "."))
            if inside:
                record["loose"] = inside
        elif key in write.loose:
            record["loose"] = True
        wrote[key] = record
    wrote.update({key: {"deleted": True} for key in deletes})
    wrote.update({key: {"members": _members_record(members)} for key, members in write.members.items()})
    kept = {key: value for key, value in write.keep.items() if key not in write.loose}
    if ROOT in write.owns:
        wrote[""] = {"owns": True}
    return _Plan(write, text, new, originals, parents, wrote, kept)


def _kept_items(members, current: list[Any], last: Optional[list[Any]]) -> list[Any]:
    """``members.items`` with the user's values of its kept fields (magpie
    ``droidKept``/``droidMerge``): what the file holds where it differs from
    what OpenProgram last wrote, by entry ``key``."""
    if not members.kept or not members.key:
        return list(members.items)
    key = members.key
    had = {item.get(key): item for item in current if isinstance(item, dict) and matches(item, members.match)}
    wrote = {item.get(key): item for item in (last or []) if isinstance(item, dict)}
    out = []
    for item in members.items:
        file, before = had.get(item.get(key)), wrote.get(item.get(key))
        if not isinstance(item, dict) or file is None or before is None:
            out.append(item)
            continue
        merged = dict(item)
        for name in members.kept:
            now, then = (name in file, file.get(name)), (name in before, before.get(name))
            if now != then:  # the user's
                if now[0]:
                    merged[name] = now[1]
                else:
                    merged.pop(name, None)
        for name, value in file.items():
            if name not in item and name not in members.kept and name not in members.drop and name not in merged:
                merged[name] = value
        out.append(members.derive(merged) if members.derive else merged)
    return out


def _members_record(members) -> dict[str, Any]:
    record: dict[str, Any] = {"match": members.match, "items": list(members.items)}
    loose = list(dict.fromkeys([*members.loose, *members.kept]))
    if loose:
        record["loose"] = loose
    if not members.checked:
        record["checked"] = False
    return record


def _holds(item: Any, wrote: Any, loose: list[str]) -> bool:
    """Whether an entry still holds what OpenProgram wrote in it, its loose fields aside."""
    if isinstance(item, dict) and isinstance(wrote, dict):
        return all(item.get(k) == v for k, v in wrote.items() if k not in loose)
    return item == wrote


# Lists shared with the user's own entries (base.Members)

def matches(item: Any, match: dict[str, Any]) -> bool:
    """Whether a list entry is OpenProgram's by ``match`` (``Members.match``)."""
    for name, want in match.items():
        if name:
            if not isinstance(item, dict) or name not in item:
                return False
            got = item[name]
        else:
            got = item
        if isinstance(want, dict) and "prefix" in want:
            if not (isinstance(got, str) and got.startswith(want["prefix"])):
                return False
        elif isinstance(want, dict) and "in" in want:
            if got not in want["in"]:
                return False
        elif got != want:
            return False
    return True


def _matchers(lists: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """For each shared list, whether an entry is OpenProgram's: a TOML list
    of tables then changes only those entries' blocks (magpie SetTOMLArrayTables)."""
    return {key: (lambda item, match=match: matches(item, match)) for key, match in lists.items()}


def _load(fmt: str, text: str, where: str) -> Any:
    """A config file's contents: an object, or the list a JSON file may be."""
    if fmt in ("json", "jsonc") and text.lstrip().startswith("["):
        from ..edit.jsonc import to_json
        import json
        try:
            data = json.loads(to_json(text) or "")
        except ValueError as exc:
            raise ConfigUnreadable(f"{where} is not valid JSON: {exc}") from exc
        if isinstance(data, list):
            return data
    return edit.load(fmt, text, where)


def _get(doc: Any, key: str, where: str = "config") -> tuple[bool, Any]:
    """``get_path``, with :data:`ROOT` the file's own list; a key that holds
    anything but a list is a file OpenProgram cannot share a list in."""
    if key == ROOT:
        if isinstance(doc, list):
            return True, doc
        if doc == {}:
            return False, None
        raise ConfigUnreadable(f"{where} is not a JSON list")
    if isinstance(doc, list):
        return False, None
    present, current = get_path(doc, key)
    if present and not isinstance(current, list):
        raise ConfigUnreadable(f"{where}: {key} is not a list")
    return present, current


def _dump_list(items: list[Any]) -> str:
    import json
    return json.dumps(items, indent=2, ensure_ascii=False) + "\n"


def _value(doc: Any, key: str, written: dict[str, Any]) -> tuple[bool, Any]:
    """What ``key`` holds now, as :func:`_ours` compares it to ``written``."""
    if "members" in (written or {}):
        try:
            return _get(doc, key)
        except ConfigUnreadable:
            return True, None
    if isinstance(doc, list):
        return False, None
    return get_path(doc, key)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


#: The largest file whose text the stash keeps to put back byte for byte.
TEXT_KEPT = 256 * 1024


def _copy_path(path: Path) -> Path:
    return path.with_name(path.name + ".before-openprogram")


def _merged_wrote(plan: _Plan, previous: dict[str, Any]) -> dict[str, Any]:
    """What OpenProgram holds in the file after ``plan``: its own writes, and
    every earlier one the file still holds as written (a re-apply that sets
    fewer keys never forgets one it left in place)."""
    wrote = dict(plan.wrote)
    if plan.write.format == "file":
        return wrote
    try:
        doc = _load(plan.write.format, plan.new, str(plan.text.path))
    except ConfigUnreadable:
        doc = {}
    for key, value in previous.items():
        if key not in wrote and key != "" and _ours(value, *_value(doc, key, value)):
            wrote[key] = value
    return wrote


def _apply(agent: Agent, plans: list[_Plan], *, stamp: bool = True) -> bool:
    """Stash, then write every plan; whether any file changed. A failure puts
    back the files written so far and the stash records this call added."""
    before = copy.deepcopy(stash.entry(agent.id))
    copies: list[Path] = []
    written: list[tuple[ConfigText, str, bool]] = []
    reclaimed: list[str] = []
    try:
        for plan in plans:
            path = str(plan.text.path)
            kept = ((stash.entry(agent.id) or {}).get("files") or {}).get(path)
            absent = not plan.text.exists
            # another agent wrote a shared file first: what was there before it is the original
            first = _shared_record(agent.id, path) if plan.write.shared and kept is None else None
            if first is None and plan.write.shared and kept is None:
                # OpenProgram's own file, left by an earlier last disconnect
                # while an agent still named it: its record says what was
                # there before OpenProgram, not the file as it is now
                first = stash.left(path)
                if first is not None:
                    reclaimed.append(path)
            if first is not None:
                # only for the keys it wrote: one this agent alone writes
                # (OpenCode's model) is as the file has it now, which is the
                # user's once an earlier disconnect put it back
                theirs = set(first.get("wrote") or {})
                stash.remember(agent.id, path, {k: (bool(v.get("had")), v.get("value"))
                                                for k, v in (first.get("originals") or {}).items() if k in theirs},
                               fmt=plan.write.format, absent=bool(first.get("absent")), copy=first.get("copy"),
                               parents=list(first.get("parents") or []))
                absent = bool(first.get("absent"))
                if first.get("made"):
                    stash.update_file(agent.id, path, made=list(first["made"]))
                if isinstance(first.get("text"), str):
                    stash.update_file(agent.id, path, text=first["text"])
            copy_to = None
            if kept is None and first is None and plan.text.exists and (
                    agent.before_copy or plan.write.format == "file" or ROOT in plan.write.owns):
                copy_to = str(_copy_path(plan.text.path))
                edit.write_atomic(Path(copy_to), plan.text.text)
                copies.append(Path(copy_to))
            # a table OpenProgram added and now takes out again was never the user's
            added = set((kept or {}).get("parents") or [])
            originals = {key: ((False, None) if key in added else value) for key, value in plan.originals.items()}
            stash.remember(agent.id, path, originals, fmt=plan.write.format, absent=absent,
                           copy=copy_to, parents=plan.parents, keep=list(plan.write.keep))
            if plan.write.shared:
                stash.update_file(agent.id, path, shared=True)
            if (kept is None and first is None and copy_to is None and plan.text.exists
                    and plan.write.format != "file" and not isinstance(plan.text, edit.SqliteRowText)
                    and len(plan.text.text) <= TEXT_KEPT):
                # the file as the user had it, put back byte for byte where
                # disconnect leaves the same settings (beforeMagpie, for every file)
                stash.update_file(agent.id, path, text=plan.text.text)
            if kept is None and not plan.text.exists:
                # the folders this write makes, gone again with the file (magpie desktopMade)
                made = [str(p) for p in plan.text.path.parents if not p.exists()]
                if made:
                    stash.update_file(agent.id, path, made=made)
        for plan in plans:
            if plan.new != plan.text.text or not plan.text.exists:
                was, existed = plan.text.text, plan.text.exists
                plan.text.write(plan.new)
                written.append((plan.text, was, existed))
        for plan in plans:
            path = str(plan.text.path)
            files = ((stash.entry(agent.id) or {}).get("files") or {})
            previous = (files.get(path) or {})
            stash.update_file(agent.id, path, wrote=_merged_wrote(plan, previous.get("wrote") or {}),
                              kept_set=plan.kept)
        for path in reclaimed:
            stash.forget_left(path)  # an agent's stash holds it again
    except (OSError, stash.StashUnreadable) as exc:
        for text, was, existed in reversed(written):
            try:
                if existed:
                    text.write(was)
                else:
                    _remove(text)
            except OSError:
                pass
        for made in copies:
            made.unlink(missing_ok=True)
        try:
            stash.put(agent.id, before)
        except (OSError, stash.StashUnreadable):
            pass
        raise AgentConfigError(f"{exc}; nothing was changed") from exc
    if written and stamp:
        stash.update(agent.id, at=time.time())
    return bool(written)


def _live_kept(record: Optional[dict[str, Any]]) -> dict[str, Any]:
    return copy.deepcopy(dict((record or {}).get("live") or {}))


def _keep_live(agent: Agent, kept: dict[str, Any]) -> None:
    """Store what a live hook keeps, making the record if there is none yet."""
    if stash.entry(agent.id) is None:
        stash.put(agent.id, {"files": {}, "values": {}, "live": kept})
    else:
        stash.update(agent.id, live=kept)


def connect(agent_id: str, values: dict[str, str], *, options: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Point the agent's own config at the gateway with ``values`` (field → model ref)."""
    agent = _find(agent_id)
    picks = {key: strip_ref(value) for key, value in (values or {}).items() if strip_ref(value or "")}
    if agent.link is not None:
        raise AgentConfigError(f"{agent.name} takes OpenProgram only through its own import link")
    if agent.field("model") is not None and not picks.get("model"):
        raise AgentConfigError("pick a model first")
    kept_keys = [f.key for f in agent.fields if f.kept]
    if kept_keys:
        # the user's own choices outlive disconnecting (magpie Field.Own)
        from ..kept_picks import keep
        keep(agent.id, {key: picks.get(key, "") for key in kept_keys})
    try:
        previous = stash.entry(agent.id)
        was = previous is not None
        context = wiring(agent, picks, options)
        plans = _plans(agent.wire(context), agent.id)
        changed = False
        if agent.live is not None:
            kept = agent.live.connect(context, _live_kept(previous))
            if previous is None or kept != _live_kept(previous):
                _keep_live(agent, kept)
                changed = True
        try:
            changed = _apply(agent, plans, stamp=False) or changed
        except AgentConfigError:
            if agent.live is not None and not was:
                # the files could not be written: the live part is taken back too
                try:
                    agent.live.disconnect(_live_kept(stash.entry(agent.id)))
                finally:
                    stash.put(agent.id, None)
            raise
        if changed:
            stash.update(agent.id, at=time.time())
        stash.update(agent.id, values=picks, address=context.worker, key=key_mark(context.key), **context.record)
        stash.keep_take_out(agent.id, None)  # on OpenProgram again: nothing left to take out
    except (ConfigUnreadable, stash.StashUnreadable) as exc:
        raise AgentConfigError(f"{exc}; it was left unchanged") from exc
    if not was and agent.on_route is not None:
        try:
            agent.on_route(agent.path().parent, True, stash.entry(agent.id) or {})
        except AgentConfigError:
            # the route could not be taken (Claude Science's daemon would not
            # start): what was written goes again, the agent stays off
            disconnect(agent.id)
            raise
    # a choice its files don't carry (agy's fast tools) is a change too
    repicked = was and dict(previous.get("values") or {}) != picks
    if (changed or not was or repicked) and agent.after is not None:
        agent.after(True, stash.entry(agent.id) or {})
    return status(agent.id)


def set_kept(agent_id: str, picks: dict[str, str]) -> dict[str, Any]:
    """Set the agent's kept choices (``Field.kept``) among ``picks``, connected
    or not, and carry them out (magpie ``Field.Own``): fast tools turned off
    after agy is disconnected goes off."""
    agent = _find(agent_id)
    kept_keys = [f.key for f in agent.fields if f.kept]
    given = {key: str(picks[key] or "") for key in kept_keys if key in picks}
    if given:
        from ..kept_picks import keep, kept
        keep(agent.id, given)
        if agent.apply_kept is not None:
            agent.apply_kept(kept(agent.id))
    return status(agent.id)


def sync(agent_id: str, *, options: Optional[dict[str, Any]] = None) -> bool:
    """Rewrite the model list a connected agent keeps (magpie ``Sync``); whether it did."""
    agent = _find(agent_id)
    try:
        record = stash.entry(agent.id)
        if record is None:
            return False
        context = wiring(agent, dict(record.get("values") or {}), options, syncing=True)
        changed = False
        if agent.live is not None and agent.live.sync is not None:
            kept = agent.live.sync(context, _live_kept(record))
            if kept != _live_kept(record):
                _keep_live(agent, kept)
                changed = True
        # files holding an older key are written whole, the new key with them
        stale = key_stale(agent, record) and bool((record.get("values") or {}).get("model"))
        plans = _plans([write for write in agent.wire(context) if write.listing or stale], agent.id)
        changed = (bool(plans) and _apply(agent, plans)) or changed
        if stale:
            stash.update(agent.id, key=key_mark(context.key))
        if context.record and plans:
            # what the agent keeps between writes follows the list, written
            # or not (Pi's entries on models no longer given, LiveAgent's
            # models it has had once it restarts)
            stash.update(agent.id, **context.record)
    except (ConfigUnreadable, stash.StashUnreadable) as exc:
        raise AgentConfigError(f"{exc}; it was left unchanged") from exc
    if changed and agent.after is not None:
        agent.after(True, stash.entry(agent.id) or {})
    return changed


def owned(agent_id: str) -> bool:
    """The agent's files still point at this instance's gateway as it wrote
    them: every key it set and every key it keeps (address, key) holds its
    value. Another OpenProgram (the stable and the dev instance share
    ``~/.codex`` and ``~/.claude``) that wired the agent since owns it now,
    and this one leaves its files alone."""
    agent = _find(agent_id)
    try:
        record = stash.entry(agent.id)
    except stash.StashUnreadable:
        return False
    if record is None:
        return False
    for path, kept in (record.get("files") or {}).items():
        wrote = kept.get("wrote") or {}
        if "sha" in (wrote.get("") or {}):
            continue
        try:
            doc = _load(kept.get("format") or "json", ConfigText.read(Path(path)).text, path)
        except ConfigUnreadable:
            return False
        if not all(_ours(value, *_value(doc, key, value)) for key, value in wrote.items() if key and key not in agent.loose):
            return False
        if not all(get_path(doc, key) == (True, value) for key, value in (kept.get("kept_set") or {}).items()):
            return False
    return True


def _ours(written: dict[str, Any] | None, present: bool, current: Any) -> bool:
    if not written:
        return False
    if "pick" in written:
        # the agent's own picker rewrites it: what it holds is no sign of anything
        return True
    if "members" in written:
        members = written["members"]
        if members.get("checked") is False:
            return True
        if not isinstance(current, list):
            return not members["items"] and not present
        loose = members.get("loose") or []
        found = [item for item in current if matches(item, members["match"])]
        if len(found) != len(members["items"]):
            return False
        # each entry holds what OpenProgram wrote in it; fields the user added are theirs
        return all(_holds(item, wrote, loose) for item, wrote in zip(found, members["items"]))
    if written.get("owns"):
        want = written.get("set")
        if not isinstance(want, dict):
            return present and current == want
        if not (present and isinstance(current, dict)):
            return False
        loose = [edit.split_path(path) for path in written.get("loose") or []]
        return _holds_in(_loosened(current, loose), _loosened(want, loose))
    if written.get("loose") is True:
        return present
    if written.get("deleted"):
        return not present
    if present and isinstance(current, str) and current.lstrip().startswith(SEALED) and isinstance(written.get("set"), str):
        return True
    return present and current == written.get("set")


def _holds_in(current: Any, want: Any) -> bool:
    """Whether ``current`` still says each thing ``want`` says: objects alike,
    field by field, with what the user added at any depth theirs; anything
    else equal."""
    if isinstance(want, dict) and isinstance(current, dict):
        return all(k in current and _holds_in(current[k], v) for k, v in want.items())
    return current == want


def _loosened(value: Any, paths: list[list[str]]) -> Any:
    """``value`` without the fields at ``paths`` (split dotted paths inside an
    owned object, ``*`` any key): what is left is what must still read as written."""
    if not isinstance(value, dict) or not paths:
        return value
    out = {}
    for key, item in value.items():
        inner = [path[1:] for path in paths if path and path[0] in (key, "*")]
        if any(not path for path in inner):
            continue
        out[key] = _loosened(item, inner)
    return out


#: A value an agent's app seals at rest in place of the one written (Claude
#: Desktop's configLibrary secrets, magpie ``desktopseal.go``): what it holds
#: can't be read here, which is not the same as changed.
SEALED = ("__sealed__:v1:",)


def remove_made(kept: dict[str, Any]) -> None:
    """The folders a file's first write made, deepest first, each while empty."""
    for folder in kept.get("made") or []:
        try:
            Path(folder).rmdir()
        except OSError:
            pass


def _remove(text: ConfigText) -> None:
    """The file gone, or for a database's row (``edit.row_path``) the row."""
    if isinstance(text, edit.SqliteRowText):
        if text.exists:
            text.delete()
        return
    text.path.unlink(missing_ok=True)


def _empty(value: Any) -> bool:
    """An object holding nothing but empty objects (magpie ``emptyJSON``)."""
    return isinstance(value, dict) and all(_empty(v) for v in value.values())


def _restore_file(path: Path, kept: dict[str, Any], fmt: str, held: frozenset[str] = frozenset()) -> None:
    """Put one file back as :func:`disconnect` says. ``held`` are keys of a
    shared file another connected agent wrote too: they stay, and so does the
    file and the copy of it taken whole, which are that agent's to put back."""
    text = ConfigText.read(path)
    wrote = kept.get("wrote") or {}
    if "" in wrote:  # a file written whole, or one that is OpenProgram's whole
        if text.exists and (wrote[""].get("owns") or _sha(text.text) == wrote[""].get("sha")):
            copy_path = kept.get("copy")
            if copy_path and Path(copy_path).exists():
                text.write(Path(copy_path).read_text(encoding="utf-8"))
            else:
                path.unlink()
                remove_made(kept)
        if kept.get("copy"):
            Path(kept["copy"]).unlink(missing_ok=True)
        return
    if not text.exists:
        return
    where = str(path)
    doc = _load(fmt, text.text, where)
    if ROOT in wrote:  # a file that is one list
        if isinstance(doc, list):
            rest = [item for item in doc if not matches(item, wrote[ROOT]["members"]["match"])]
            if not rest and kept.get("absent"):
                path.unlink(missing_ok=True)
            elif rest != doc:
                text.write(_dump_list(rest))
        if kept.get("copy"):
            Path(kept["copy"]).unlink(missing_ok=True)
        return
    sets: dict[str, Any] = {}
    deletes: list[str] = []
    owned_keys = {key for key, written in wrote.items() if written.get("owns")}
    for key in owned_keys - held:
        # OpenProgram's own object goes whole, edited inside or not
        present, _current = get_path(doc, key)
        original = (kept.get("originals") or {}).get(key) or {}
        if original.get("had"):
            sets[key] = original.get("value")
        elif present:
            deletes.append(key)
    for key, written in wrote.items():
        if "members" not in written or key in held:
            continue
        present, current = get_path(doc, key)
        if not present or not isinstance(current, list):
            continue
        rest = [item for item in current if not matches(item, written["members"]["match"])]
        if not rest and not ((kept.get("originals") or {}).get(key) or {}).get("had"):
            deletes.append(key)  # a list OpenProgram added goes with its last entry
        elif rest != current:
            sets[key] = rest  # one that was there stays, empty or not (magpie)
    for key, original in (kept.get("originals") or {}).items():
        if key in owned_keys or key in held or "members" in (wrote.get(key) or {}):
            continue
        present, current = get_path(doc, key)
        if "pick" in (wrote.get(key) or {}):
            # a pick goes back whole while it still says OpenProgram's, and
            # stays whole once the user picked their own
            if not _picked_ours(doc, wrote[key]["pick"]):
                continue
        elif not _ours(wrote.get(key), present, current):
            continue
        if original.get("had"):
            sets[key] = original.get("value")
        elif present:
            deletes.append(key)
    after = copy.deepcopy(doc)
    for key, value in sets.items():
        edit.set_path(after, key, value)
    for key in deletes:
        edit.delete_path(after, key)
    for parent in sorted(kept.get("parents") or [], key=len, reverse=True):
        present, current = get_path(after, parent)
        if present and _empty(current):
            deletes.append(parent)
            edit.delete_path(after, parent)
    lists = {key: written["members"]["match"] for key, written in wrote.items() if "members" in written}
    new = edit.edit(fmt, text.text, sets=sets, deletes=deletes, where=where,
                    members=_matchers(lists)) if sets or deletes else text.text
    if new != text.text:
        text.write(new)
    if held:
        return
    if kept.get("absent") and _empty(after):
        _remove(text)
        remove_made(kept)
        return
    copy_path = kept.get("copy")
    was = kept.get("text") if isinstance(kept.get("text"), str) else None
    if copy_path:
        try:
            was = Path(copy_path).read_text(encoding="utf-8")
        except OSError:
            was = None
    if was is not None and was != new:
        # the same settings as before OpenProgram: the user's own bytes,
        # their order and layout
        try:
            if _load(fmt, was, where) == after:
                text.write(was)
        except ConfigUnreadable:
            pass
    if copy_path:
        Path(copy_path).unlink(missing_ok=True)


def _picked_ours(doc: Any, pick: dict[str, Any]) -> bool:
    """Whether a pick's key ``on`` still says OpenProgram's (``Pick.want``)."""
    present, value = get_path(doc, pick.get("on") or "") if isinstance(doc, dict) else (False, None)
    return present and matches(value, {"": pick.get("want")})


def disconnect(agent_id: str) -> dict[str, Any]:
    """Put back the values OpenProgram replaced, except ones changed since."""
    agent = _find(agent_id)
    try:
        record = stash.entry(agent.id)
        if record is None:
            return status(agent.id)
        formats = _formats(agent, record)
        if agent.live is not None:
            try:
                agent.live.disconnect(_live_kept(record))
            except AppGone:
                # the app is uninstalled, its data kept, which only it can
                # change: what is OpenProgram's comes out once it is back
                stash.keep_take_out(agent.id, _live_kept(record))
            stash.update(agent.id, live={})
        if agent.unwire is not None:
            # the agent's own entries out first, however its app has changed
            # them since (magpie ``Unwire``): what is stashed goes back after
            agent.unwire(record)
        # a shared file last: the agent's own files are back first, so a scan
        # for agents still naming it sees this one's put back (magpie unwire)
        files = sorted((record.get("files") or {}).items(), key=lambda pair: bool(pair[1].get("shared")))
        for path, kept in files:
            if kept.get("shared") and _shared_record(agent.id, path) is None and agent.shared_used is not None \
                    and agent.shared_used(Path(path)):
                # no connected agent's stash names it, but one of the agent's
                # own still uses it (a QwenPaw agent switched to OpenProgram's
                # provider in QwenPaw, magpie qwenpawNamesMagpie): it stays,
                # and the gateway state keeps that it is OpenProgram's and
                # what was there before, for the next connect to take up
                stash.leave(path, kept)
            elif not (kept.get("shared") and _shared_record(agent.id, path) is not None):
                _restore_file(Path(path), kept, formats.get(path, "json"))
            elif not ({"", ROOT} & set(kept.get("wrote") or {})):
                # another connected agent still uses the shared file: what it
                # wrote too stays, and it puts the file back last; keys only
                # this agent wrote go now (OpenCode's model, beside the
                # provider OpenChamber also names)
                _restore_file(Path(path), kept, formats.get(path, "json"), _held(agent.id, path))
                if kept.get("made"):
                    _hand_made(agent.id, path, list(kept["made"]))
            stash.forget_file(agent.id, path)
        if agent.unwired is not None:
            agent.unwired(record)
        stash.forget(agent.id)
    except (ConfigUnreadable, stash.StashUnreadable) as exc:
        raise AgentConfigError(f"{exc}; the original settings are kept to restore later") from exc
    if agent.on_route is not None:
        agent.on_route(agent.path().parent, False, record)
    if agent.after is not None:
        agent.after(False, record)
    return status(agent.id)


def _shared_record(agent_id: str, path: str) -> Optional[dict[str, Any]]:
    """Another agent's record of a shared file, while one's stash names it."""
    for other, record in stash.load().items():
        kept = ((record or {}).get("files") or {}).get(path)
        if other != agent_id and kept and kept.get("shared"):
            return kept
    return None


def _hand_made(agent_id: str, path: str, made: list[str]) -> None:
    """The folders a shared file's first write made, to each agent still
    keeping the file that doesn't know them: the last to go takes them out."""
    for other, record in stash.load().items():
        kept = ((record or {}).get("files") or {}).get(path)
        if other != agent_id and kept and kept.get("shared") and not kept.get("made"):
            stash.update_file(other, path, made=made)


def _held(agent_id: str, path: str) -> frozenset[str]:
    """The keys of a shared file other connected agents wrote."""
    held: set[str] = set()
    for other, record in stash.load().items():
        kept = ((record or {}).get("files") or {}).get(path)
        if other != agent_id and kept and kept.get("shared"):
            held.update(key for key in (kept.get("wrote") or {}) if key)
    return frozenset(held)


def _formats(agent: Agent, record: dict[str, Any]) -> dict[str, str]:
    """Each recorded file's format."""
    return {path: kept.get("format") or "json" for path, kept in (record.get("files") or {}).items()}


def take_out(agent_id: str) -> bool:
    """Take out of an app that was uninstalled when its agent was turned off
    what is still OpenProgram's there, once it answers again; whether that
    is done (or nothing is left to do)."""
    agent = _find(agent_id)
    kept = stash.take_outs().get(agent.id)
    if kept is None:
        return True
    if agent.live is None or stash.entry(agent.id) is not None:
        stash.keep_take_out(agent.id, None)  # on OpenProgram again: nothing to take out
        return True
    try:
        agent.live.disconnect(copy.deepcopy(kept))
    except (AppGone, AgentConfigError):
        return False
    stash.keep_take_out(agent.id, None)
    return True


def disconnect_all() -> list[str]:
    """Disconnect every agent; the messages of those that were left alone."""
    errors = []
    try:
        ids = stash.agents()
    except stash.StashUnreadable as exc:
        return [str(exc)]
    for agent_id in ids:
        try:
            disconnect(agent_id)
        except AgentConfigError as exc:
            errors.append(f"{agent_id}: {exc}")
    return errors


def drift(agent: Agent, record: dict[str, Any]) -> tuple[list[str], Optional[str]]:
    """The keys of the agent's files no longer as OpenProgram wrote them, and an error."""
    found: list[str] = []
    formats = _formats(agent, record)
    for path, kept in (record.get("files") or {}).items():
        wrote = kept.get("wrote") or {}
        if "sha" in (wrote.get("") or {}):
            continue
        try:
            doc = _load(formats.get(path, "json"), ConfigText.read(Path(path)).text, path)
        except ConfigUnreadable as exc:
            return found, str(exc)
        for key, written in wrote.items():
            if not key:
                continue
            present, current = _value(doc, key, written)
            if key not in agent.loose and not _ours(written, present, current):
                found.append(key)
        for key in kept.get("keep") or []:
            if not get_path(doc, key)[0]:
                owner = (_ancestors(key) or [key])[-1]
                if owner not in found:
                    found.append(owner)
    return found, None


def _field_row(f: Any) -> dict[str, Any]:
    row = {"key": f.key, "label": f.label, "quiet": f.quiet, "follows": f.follows, "kept": f.kept}
    if f.multi:
        row["multi"] = True
    if f.with_catalog:
        row["withCatalog"] = True
    if f.options is not None:
        try:
            row["options"] = [{"value": value, "label": label} for value, label in f.options()]
        except Exception:  # noqa: BLE001 - a choice that cannot be listed now offers none
            row["options"] = []
    return row


def status(agent_id: str) -> dict[str, Any]:
    """The agent as its row shows it: here or not, connected, its picks, drift."""
    from .running import app_behind
    agent = _find(agent_id)
    if agent.link is not None:
        return _link_status(agent)
    unreadable = None
    try:
        record = stash.entry(agent.id)
    except stash.StashUnreadable as exc:
        record, unreadable = None, str(exc)
    try:
        path = str(agent.path())
    except Exception:  # noqa: BLE001 - a path that cannot be worked out is shown empty
        path = ""
    result: dict[str, Any] = {
        "id": agent.id, "label": agent.name, "icon": agent.icon, "kind": agent.kind,
        "protocol": agent.protocol, "harness": agent.harness,
        # a choice with nothing to choose now (no https address for Desktop) is not shown
        "fields": [row for row in map(_field_row, agent.fields) if row.get("options", True) != [] or row.get("withCatalog")],
        "installed": agent.installed(), "cliGone": agent.cli_gone(),
        "config": {"path": path, "applied": record is not None, "drift": [], "error": unreadable},
        "values": dict((record or {}).get("values") or {}),
        "notice": agent.notice() if agent.notice and record is not None else "",
        "restart": False, "gateway": agent.gateway, "launch": "",
        "switch": agent.switch, "link": False,
    }
    kept_keys = [f.key for f in agent.fields if f.kept]
    if kept_keys:
        # kept choices show, and go to the next connect, while it is off too
        from ..kept_picks import kept
        saved = kept(agent.id)
        for key in kept_keys:
            result["values"].pop(key, None)
            if saved.get(key):
                result["values"][key] = saved[key]
    if record is None:
        return result
    result["config"]["drift"], result["config"]["error"] = drift(agent, record)
    if agent.check is not None and not result["config"]["error"]:
        result["config"]["error"] = agent.check() or None
    if agent.live is not None and agent.live.check is not None and not result["config"]["error"]:
        try:
            gone = agent.live.check(wiring(agent, dict(record.get("values") or {})), _live_kept(record))
        except AgentConfigError as exc:
            gone, result["config"]["error"] = [], str(exc)
        result["config"]["drift"] += [item for item in gone if item not in result["config"]["drift"]]
    result["restart"] = app_behind(agent, record.get("at")) is not None
    if agent.launch is not None and result["values"].get("model"):
        result["launch"] = agent.launch(wiring(agent, result["values"]))
    return result


def _link_status(agent: Agent) -> dict[str, Any]:
    """An import-link app's row (magpie ``Import``/``Added``): whether it has
    the gateway, ``None`` when that is unknown or cannot be read."""
    added: Optional[bool] = None
    if agent.added is not None:
        try:
            has, known = agent.added(wiring(agent, {}))
        except Exception:  # noqa: BLE001 - unknown, never "not added"
            has, known = False, False
        added = has if known else None
    try:
        path = str(agent.path())
    except Exception:  # noqa: BLE001 - a path that cannot be worked out is shown empty
        path = ""
    return {
        "id": agent.id, "label": agent.name, "icon": agent.icon, "kind": agent.kind,
        "protocol": agent.protocol, "harness": False, "fields": [],
        "installed": agent.installed(), "cliGone": False,
        "config": {"path": path, "applied": bool(added), "drift": [], "error": None},
        "values": {}, "notice": agent.notice() if agent.notice else "", "restart": False,
        "switch": False, "link": True, "added": added, "gateway": True, "launch": "",
    }


def import_link(agent_id: str) -> str:
    """The import link of an app that takes the gateway only through one."""
    agent = _find(agent_id)
    if agent.link is None:
        raise AgentConfigError(f"{agent.name} has no import link")
    link = agent.link(wiring(agent, {}))
    if not link:
        raise AgentConfigError(f"{agent.name} takes only a loopback gateway address")
    return link
