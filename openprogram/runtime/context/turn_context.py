"""What a turn tells the model beside its message, stored on its user node.

One storage form: the user node's ``turn_context`` list, each entry
``{"kind": "section" | "attachment", "id", "text"}``.

- An **attachment** is a fact about this turn: the date it was sent on
  (``date``, only when it differs from the last date the history shows),
  that the reply before it was stopped (``interrupted``,
  :func:`interruption_note`), a rewind the user made before it
  (``rewind``), the plan-mode note (``plan_mode``), the Goal note
  (``goal``) and its todo reminder (``todo_reminder``), the memory it
  recalled (``memory_prefetch``), a scheduled run's note (``run_note``).
  Whether the date, the plan-mode note, the Goal note and the todo reminder
  go is decided from what the request's rendered history shows of earlier
  ones (:func:`turn_view`), so a compaction that drops them brings them
  back in full.
- A **section** is the current text of a part of the system prompt that
  changed since the conversation's prompt was recorded (``memory``,
  ``environment``, ``open_work`` …), or of a part that never goes in the
  prompt (``pages``). The conversation's recorded prompt is sent again byte
  for byte and the section's whole current text goes beside the message,
  replacing the prompt's section and any earlier note of it. A note is never
  a patch: the history a request renders may have lost the turn an earlier
  note sat on (compacted, cut from a bounded tail), so the model is always
  given the whole section.

Entries are written when the event happens, on the turn's user node, and
every later request renders them from there, so the bytes never change.
One rule says which entries a render shows (:func:`block`): it takes the
request's ``audience`` explicitly from the one place that builds the request.

A node written before this list existed carries the old keys
(``current_date``, ``memory_map``, ``memory_prefetch``, ``run_note``);
:func:`entries` reads them in the order they were sent. New nodes are written
with both forms for one promotion cycle, so a build that predates this list
renders them the same (:func:`node_metadata`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

#: A request built on a system prompt of its own (``runtime.exec``, an Agent
#: method, a workflow, a summary, a token estimate): section notes are left
#: out, since its prompt already holds the current sections they would
#: contradict.
OWN_PROMPT = "own_prompt"
#: A conversation turn's own request, built on the conversation's recorded
#: prompt: section notes are shown.
CONVERSATION = "conversation"
#: The same, for a turn whose prompt holds no memory section (memory off or
#: unreadable): notes of the memory section are left out with it.
CONVERSATION_WITHOUT_MEMORY = "conversation_without_memory"

#: Suffix of an audience whose render leaves recalled memory out
#: (:func:`without_recall`).
WITHOUT_RECALL = ":without_recall"

SECTION = "section"
ATTACHMENT = "attachment"

#: Sections that may change while the conversation's recorded system prompt
#: is kept: their new text goes beside the next message instead. Order is the
#: order their notes render in.
MOVABLE_SECTIONS = ("environment", "agents_md", "memory", "open_work", "capabilities")
#: Sections that never go in the main turn's system prompt. The tools this
#: turn cannot use (``tool_availability``: they stay in the tools array and
#: their calls are refused) come after the Pages. The deferred-tool catalog
#: (``deferred_catalog``) comes last, after the memory section, so a change
#: of one never moves the other.
BESIDE_ONLY_SECTIONS = ("pages", "tool_availability", "deferred_catalog")
#: The section that lists the tools this turn cannot use.
TOOL_AVAILABILITY = "tool_availability"
#: The deferred-tool catalog section. Its note is the whole catalog on a
#: turn with no earlier note in view, and only what was added and removed
#: afterwards (``programs.catalog_note``); its entry keeps the catalog it
#: describes under ``catalog``, the baseline of the next turn's note.
CATALOG = "deferred_catalog"

# Where an entry renders, by id: the date first, then what happened before
# the turn, then the sections, then what the turn recalled and its run note.
# This is the order the old keys were sent in, so an old node renders the
# same bytes.
_ATTACHMENT_RANK = {"date": 0, "interrupted": 5, "rewind": 10, "plan_mode": 60, "goal": 65,
                   "todo_reminder": 70, "memory_prefetch": 80, "run_note": 90}
_SECTION_RANK = 50
_SECTION_ORDER = (*MOVABLE_SECTIONS, *BESIDE_ONLY_SECTIONS)

# Old key on the node → (kind, id).
_LEGACY = (
    ("current_date", ATTACHMENT, "date"),
    ("memory_map", SECTION, "memory"),
    ("memory_prefetch", ATTACHMENT, "memory_prefetch"),
    ("run_note", ATTACHMENT, "run_note"),
)


def _rank(entry: Mapping) -> tuple:
    if entry.get("kind") == SECTION:
        ident = entry.get("id")
        order = _SECTION_ORDER.index(ident) if ident in _SECTION_ORDER else len(_SECTION_ORDER)
        return (_SECTION_RANK, order)
    return (_ATTACHMENT_RANK.get(str(entry.get("id")), _SECTION_RANK), 0)


def _valid(entry: Any) -> bool:
    return (isinstance(entry, Mapping) and entry.get("kind") in (SECTION, ATTACHMENT)
            and isinstance(entry.get("id"), str) and entry["id"]
            and isinstance(entry.get("text"), str) and bool(entry["text"]))


def entries(metadata: Optional[Mapping]) -> list[dict]:
    """The node's entries, in the order they render.

    ``metadata`` is a node's metadata or a stored message row (whose
    metadata keys sit at its top level).
    """
    md = metadata or {}
    stored = md.get("turn_context")
    if isinstance(stored, list):
        return [dict(entry) for entry in stored if _valid(entry)]
    out = []
    for key, kind, ident in _LEGACY:
        text = md.get(key)
        if isinstance(text, str) and text:
            out.append({"kind": kind, "id": ident, "text": text})
    return out


def put(current: Iterable[Mapping], kind: str, ident: str, text: Optional[str],
        **extra: Any) -> list[dict]:
    """``current`` with the entry ``ident`` set to ``text`` (removed when
    empty), in render order. Entries keep their relative order otherwise.
    ``extra`` fields are stored on the entry beside its text."""
    kept = [dict(entry) for entry in current if entry.get("id") != ident]
    if isinstance(text, str) and text:
        kept.append({"kind": kind, "id": ident, "text": text, **extra})
    return sorted(kept, key=_rank)


def entry_text(items: Iterable[Mapping], ident: str) -> str:
    return next((str(entry.get("text") or "") for entry in items if entry.get("id") == ident), "")


def node_metadata(items: Iterable[Mapping]) -> dict:
    """The metadata to store for ``items``: the list, and the old keys a
    build that predates it reads (empty where the entry is absent)."""
    items = sorted((dict(entry) for entry in items if _valid(entry)), key=_rank)
    out: dict[str, Any] = {"turn_context": items}
    for key, _kind, ident in _LEGACY:
        out[key] = entry_text(items, ident)
    return out


def shown(entry: Mapping, audience: str) -> bool:
    """Whether a render for ``audience`` shows ``entry``.

    Recalled memory (``memory_prefetch``) is history like any other
    attachment: turning memory off stops new recalls and the memory tools,
    and a compaction leaves recalled memory out of its summary
    (``compaction.view``), but what an earlier turn was sent is never taken
    back from it (Codex, Claude Code and Hermes keep it too; taking it back
    rewrites history the provider has cached, and on Anthropic's
    preserved-thinking models a rewritten prefix is refused)."""
    recall = not audience.endswith(WITHOUT_RECALL)
    audience = audience.removesuffix(WITHOUT_RECALL)
    if entry.get("kind") == SECTION:
        if audience == CONVERSATION:
            return True
        return audience == CONVERSATION_WITHOUT_MEMORY and entry.get("id") != "memory"
    if entry.get("id") == "memory_prefetch":
        return recall
    return True


def without_recall(audience: str) -> str:
    """``audience`` for a compaction's input while memory is off: recalled
    memory is left out of the summary, so after the compaction the history
    no longer holds it."""
    return audience if audience.endswith(WITHOUT_RECALL) else audience + WITHOUT_RECALL


def block(metadata: Optional[Mapping], audience: str = OWN_PROMPT) -> str:
    """The text a past turn sent in front of its message, for ``audience``."""
    return "\n\n".join(entry["text"] for entry in entries(metadata) if shown(entry, audience))


def notes_text(items: Iterable[Mapping], audience: str = CONVERSATION) -> str:
    """What goes between the date and the recalled memory: the rewind note
    and the section notes, joined as :func:`block` joins them. The loop puts
    it in front of the live message (``AgentContext.turn_notes``)."""
    middle = [entry for entry in sorted(items, key=_rank)
              if entry.get("id") not in ("date", "memory_prefetch", "run_note")
              and shown(entry, audience)]
    return "\n\n".join(entry["text"] for entry in middle)


def conversation_audience(memory_notes: bool) -> str:
    return CONVERSATION if memory_notes else CONVERSATION_WITHOUT_MEMORY


def last_sections(graph: Any, read_ids: list[str], audience: str) -> dict[str, str]:
    """``{section id: text}`` of the last note of each section a render of
    ``read_ids`` for ``audience`` shows."""
    found: dict[str, str] = {}
    if audience.removesuffix(WITHOUT_RECALL) == OWN_PROMPT:
        return found
    for nid in reversed(read_ids):
        node = graph.nodes.get(nid)
        if node is None or not node.is_user() or node.id == "ROOT":
            continue
        output = node.output
        if not (output if isinstance(output, str) else str(output or "")).strip():
            continue
        for entry in entries(node.metadata):
            if entry["kind"] == SECTION and entry["id"] not in found and shown(entry, audience):
                found[entry["id"]] = _catalog_json(entry) if entry["id"] == CATALOG else entry["text"]
    return found


def _catalog_json(entry: Mapping) -> str:
    import json

    catalog = entry.get("catalog")
    return json.dumps(catalog if isinstance(catalog, dict) else {}, sort_keys=True, ensure_ascii=False)


def catalog_shown(section_notes: Optional[Mapping]) -> Optional[dict]:
    """The deferred-tool catalog the last note in view described, or None
    when no note of it is in view (:func:`last_sections`)."""
    import json

    raw = (section_notes or {}).get(CATALOG)
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else None


def catalog_stored(history: Any, *, exclude: Optional[str] = None) -> Optional[dict]:
    """The catalog the last stored note of it on ``history`` (message rows)
    described, for an engine that does not report what it shows."""
    for message in reversed(history or []):
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        if exclude and message.get("id") == exclude:
            continue
        entry = next((entry for entry in entries(message)
                      if entry["kind"] == SECTION and entry["id"] == CATALOG), None)
        if entry is not None:
            return catalog_shown({CATALOG: _catalog_json(entry)})
    return None


# The reply before this turn was stopped

_INTERRUPTION_NOTES = {
    "cancel.user": "The user stopped your previous reply before it finished; its last part is incomplete.",
    "cancel.parent": ("Your previous reply was stopped because the task that started it was stopped; "
                      "its last part is incomplete."),
    "cancel.session": ("Your previous reply was stopped when the whole conversation was stopped; "
                       "its last part is incomplete."),
}
_TIME_LIMIT_NOTE = "Your previous reply was stopped by its time limit; its last part is incomplete."
_RESTART_NOTE = ("Your previous reply was cut off by a service restart, not by the user; "
                 "continue from where it left off if the next message asks for it.")
_OTHER_NOTE = "Your previous reply was stopped before it finished."
_CUT_TOOLS_NOTE = " A tool call that was stopped may have partly run."


def interruption_note(interruption: Any) -> str:
    """What the turn after a stopped reply is told, beside its message.

    A fact and, when a tool call was stopped or never ran, that it may have
    partly run (Codex ``<turn_aborted>``), put in front of the next user
    message (Grok Build) rather than in the assistant's place, where a
    model repeats it (Hermes). Written once, on that message's node.
    """
    if not isinstance(interruption, Mapping):
        return ""
    from openprogram.runtime.execution.run_control.stop_reasons import (
        RESTART_REASONS, TIME_LIMIT_REASONS,
    )

    reason = interruption.get("reason_code")
    if reason in RESTART_REASONS:
        note = _RESTART_NOTE
    elif reason in TIME_LIMIT_REASONS:
        note = _TIME_LIMIT_NOTE
    else:
        note = _INTERRUPTION_NOTES.get(str(reason), _OTHER_NOTE)
    if interruption.get("cut_tools"):
        note += _CUT_TOOLS_NOTE
    return note


def reply_interruption(row: Optional[Mapping]) -> Optional[dict]:
    """The interruption a stored reply records, or None when it ended.

    A reply written before replies recorded it says so by its status: a
    stopped one (``cancelled``, which a stop before any output leaves) or
    one a restart cut off (``interrupted``).
    """
    if not isinstance(row, Mapping) or row.get("role") != "assistant":
        return None
    recorded = row.get("interruption")
    if isinstance(recorded, Mapping):
        return dict(recorded)
    status = row.get("status")
    if status == "cancelled":
        return {"reason_code": row.get("reason_code") or "cancel.user"}
    if status == "interrupted":
        return {"reason_code": "interrupted"}
    return None


# What the rendered history shows of earlier per-turn notes

def _code_name(node: Any) -> str:
    try:
        return str(node.function_name or "")
    except Exception:
        return str(getattr(node, "name", "") or "")


def turn_view(graph: Any, read_ids: list[str]) -> dict:
    """What a render of ``read_ids`` shows of the notes decided per view.

    Turns are the user messages in view; the turn being prepared is the
    next one, so a note on the last turn in view is 1 turn back.

    - ``date``: the text of the last date note, or None;
    - ``goal``: the disclosure of the last full Goal note, or None;
    - ``plan_mode``: ``last`` (kind of the last plan-mode note, or None),
      ``turns_since`` and ``sent_since_exit`` (notes after the last exit);
    - ``todo``: ``turns_since_use`` (turns since a ``todo_*`` call, counting
      every turn in view when there was none) and ``turns_since_reminder``
      (None when no reminder is in view).
    """
    turns = 0
    date = None
    goal_full = None
    plan_last, plan_turn, plan_sent = None, 0, 0
    todo_turn, reminder_turn = 0, None
    for nid in read_ids:
        node = graph.nodes.get(nid)
        if node is None or node.id == "ROOT":
            continue
        if node.is_code():
            if _code_name(node).startswith("todo_"):
                todo_turn = turns
            continue
        if not node.is_user():
            continue
        output = node.output
        if not (output if isinstance(output, str) else str(output or "")).strip():
            continue
        turns += 1
        for entry in entries(node.metadata):
            ident = entry.get("id")
            if ident == "date":
                date = entry["text"]
            elif ident == "goal" and entry.get("full"):
                disclosure = entry.get("disclosure")
                goal_full = dict(disclosure) if isinstance(disclosure, Mapping) else None
            elif ident == "plan_mode":
                plan_last, plan_turn = str(entry.get("mode") or "full"), turns
                plan_sent = 0 if plan_last == "exit" else plan_sent + 1
            elif ident == "todo_reminder":
                reminder_turn = turns
    now = turns + 1
    return {
        "date": date,
        "goal": goal_full,
        "plan_mode": {"last": plan_last, "turns_since": now - plan_turn if plan_last else None,
                      "sent_since_exit": plan_sent},
        "todo": {"turns_since_use": now - todo_turn,
                 "turns_since_reminder": None if reminder_turn is None else now - reminder_turn},
    }


def date_to_send(date: str, view: Optional[Mapping]) -> str:
    """The date note this turn sends: "" when the history in view already
    ends on the same date (Claude Code, Codex, Kimi Code and OpenCode all
    send the date only when it changed). Without a view, the date goes."""
    if view is not None and view.get("date") == date:
        return ""
    return date


# Section notes

@dataclass(frozen=True)
class Section:
    """A section's text now, and its text in the system prompt sent."""

    current: str
    in_prompt: str


_LEADS = {
    "pages": ("These are the browser Pages open now. This replaces any earlier list of open Pages.",
              "No browser Pages are open now. Any earlier list of open Pages no longer applies."),
    TOOL_AVAILABILITY: (
        "These tools stay listed but cannot be used now; a call of one is refused. "
        "This replaces any earlier list of unavailable tools.",
        "Every listed tool can be used again. Any earlier list of unavailable tools no longer applies."),
}


def availability_text(unavailable: Mapping[str, str]) -> str:
    """The ``tool_availability`` section's text: one line per tool this
    turn cannot use, in name order, "" when every tool can be used."""
    return "\n".join(f"- {name}: {reason}" for name, reason in sorted(unavailable.items()))


def section_note(ident: str, text: str) -> str:
    """The note that tells the model a section's current text."""
    if ident == "memory" and text:
        from openprogram.runtime.context.memory_map import note_for

        return note_for(text)
    changed, removed = _LEADS.get(ident, (
        f'The "{ident}" section of the system prompt has changed since this conversation\'s '
        "system prompt was written. This is its current text; it replaces that section and "
        "any earlier update of it.",
        f'The "{ident}" section of the system prompt no longer applies; disregard it and any '
        "earlier update of it.",
    ))
    tag = f'<system-prompt-update section="{ident}">'
    if text:
        return f"{tag}\n{changed}\n\n{text}\n</system-prompt-update>"
    return f"{tag}\n{removed}\n</system-prompt-update>"


def note_to_send(ident: str, section: Section, last_note: Optional[str],
                 *, stored: Optional[str] = None) -> str:
    """The note this turn sends for a section, or "" when the model has it.

    ``last_note`` is the last note of the section the request's rendered
    history shows, None when it shows none: the system prompt's section is
    then what the model has. ``stored`` is for an engine that does not
    report what its render shows: the last note stored on the turns it was
    given, which it may or may not show, so the note is sent unless both
    that and the prompt's section are the current text.
    """
    note = section_note(ident, section.current)
    if last_note is not None:
        return "" if last_note == note else note
    if stored is not None and stored != note:
        return note
    return "" if section.current == section.in_prompt else note


def last_stored(history: Any, ident: str, *, exclude: Optional[str] = None) -> Optional[str]:
    """The last note of section ``ident`` stored on the user messages of
    ``history`` (message rows), leaving out the message ``exclude`` names."""
    for message in reversed(history or []):
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        if exclude and message.get("id") == exclude:
            continue
        text = next((entry["text"] for entry in entries(message)
                     if entry["kind"] == SECTION and entry["id"] == ident), None)
        if text:
            return text
    return None


# The recorded prompt (the conversation's runtime contract)

def _blocks(text: str, sections: Any) -> Optional[list[tuple[str, str]]]:
    if not isinstance(sections, list) or not sections:
        return None
    from openprogram.runtime.context.components import split_sections

    return split_sections(text, sections)


def _of(blocks: list[tuple[str, str]], ident: str) -> str:
    return next((block for name, block in blocks if name == ident), "")


def _fixed(blocks: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(name, block) for name, block in blocks if name not in MOVABLE_SECTIONS]


def _summarised_since(store: Any, session_id: str, record: Any) -> bool:
    """Whether a compaction summary was written after the prompt ``record``."""
    from openprogram.runtime.context.system_prompt_node import _metadata_of, _seq_of

    seq = _seq_of(record)
    try:
        nodes = store.get_nodes(session_id) or []
    except Exception:
        return True
    for node in nodes:
        covers = _metadata_of(node).get("covers_ids")
        if _seq_of(node) > seq and isinstance(covers, list) and covers:
            return True
    return False


def kept_sections(store: Any, session_id: str, built: str) -> tuple[str, dict[str, Section]]:
    """The system prompt a conversation's turn sends, and its movable sections.

    ``built`` is the prompt just assembled. When the prompt recorded on this
    branch differs from it only in movable sections, the recorded prompt is
    sent again, byte for byte, and each changed section goes beside the
    turn's message. Anything else that changed (the tools, the agent, the
    output style) rebuilds the prompt whole.

    Memory turned off is a movable change too: the recorded prompt keeps
    its memory section and the next message says it no longer applies, as
    recalled memory stays in the history (Codex, Claude Code and Hermes take
    nothing back). The memory leaves the prompt at the next compaction: a
    recorded prompt with memory is not kept once a summary was written after
    it while memory is off.
    """
    from openprogram.runtime.context.components import prompt_sections

    built_blocks = _blocks(built, prompt_sections(built))
    if built_blocks is None:
        # Not assembled from sections (the inline fallback).
        return built, {}
    unchanged = {ident: Section(_of(built_blocks, ident), _of(built_blocks, ident))
                 for ident in MOVABLE_SECTIONS}
    if not session_id:
        return built, unchanged
    from openprogram.runtime.context.system_prompt_node import (
        _latest_prompt_node,
        _metadata_of,
        _output_of,
    )

    node = _latest_prompt_node(store, session_id)
    recorded = None if node is None else _output_of(node)
    if not recorded or recorded == built:
        return built, unchanged
    recorded_blocks = _blocks(recorded, _metadata_of(node).get("sections"))
    if recorded_blocks is None or _fixed(recorded_blocks) != _fixed(built_blocks):
        return built, unchanged
    if (_of(recorded_blocks, "memory") and not _of(built_blocks, "memory")
            and _summarised_since(store, session_id, node)):
        return built, unchanged
    return recorded, {
        ident: Section(_of(built_blocks, ident), _of(recorded_blocks, ident))
        for ident in MOVABLE_SECTIONS
    }


__all__ = [
    "ATTACHMENT",
    "BESIDE_ONLY_SECTIONS",
    "CATALOG",
    "CONVERSATION",
    "CONVERSATION_WITHOUT_MEMORY",
    "MOVABLE_SECTIONS",
    "OWN_PROMPT",
    "SECTION",
    "Section",
    "TOOL_AVAILABILITY",
    "availability_text",
    "date_to_send",
    "block",
    "catalog_shown",
    "catalog_stored",
    "conversation_audience",
    "entries",
    "entry_text",
    "interruption_note",
    "kept_sections",
    "last_sections",
    "last_stored",
    "node_metadata",
    "note_to_send",
    "notes_text",
    "put",
    "reply_interruption",
    "section_note",
    "shown",
    "turn_view",
    "without_recall",
]
