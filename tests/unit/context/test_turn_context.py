"""What a turn tells the model beside its message: one storage form, one
replay rule (``runtime/context/turn_context.py``)."""
from __future__ import annotations

from openprogram.runtime.context import turn_context as tc
from openprogram.runtime.context.render import turn_block

DATE = "Today is Friday, November 13, 2026."
MAP = "<memory-map-update>\nmap\n</memory-map-update>"
RECALLED = "<memory-context>dog: Pip</memory-context>"
RUN = "Last run: ok."


def test_a_node_from_before_the_list_renders_as_it_was_sent():
    old = {"current_date": DATE, "memory_map": MAP, "memory_prefetch": RECALLED, "run_note": RUN}
    assert tc.block(old, tc.CONVERSATION) == turn_block(RECALLED, RUN, DATE, MAP)
    # A call with a prompt of its own never showed the memory map.
    assert tc.block(old, tc.OWN_PROMPT) == turn_block(RECALLED, RUN, DATE, None)


def test_a_new_node_is_written_in_both_forms_and_renders_the_same():
    items = tc.put([], tc.ATTACHMENT, "run_note", RUN)
    items = tc.put(items, tc.ATTACHMENT, "memory_prefetch", RECALLED)
    items = tc.put(items, tc.SECTION, "memory", MAP)
    items = tc.put(items, tc.ATTACHMENT, "date", DATE)
    stored = tc.node_metadata(items)

    assert [entry["id"] for entry in stored["turn_context"]] == ["date", "memory", "memory_prefetch", "run_note"]
    assert (stored["current_date"], stored["memory_map"], stored["memory_prefetch"], stored["run_note"]) == (
        DATE, MAP, RECALLED, RUN)
    legacy = {key: value for key, value in stored.items() if key != "turn_context"}
    assert tc.block(stored, tc.CONVERSATION) == tc.block(legacy, tc.CONVERSATION)


def test_the_live_message_gets_the_bytes_later_renders_show():
    items = tc.put([], tc.ATTACHMENT, "date", DATE)
    items = tc.put(items, tc.ATTACHMENT, "rewind", "The user rewound the conversation.")
    items = tc.put(items, tc.SECTION, "open_work", tc.section_note("open_work", "<open_work>x</open_work>"))
    items = tc.put(items, tc.ATTACHMENT, "memory_prefetch", RECALLED)
    live = turn_block(RECALLED, None, DATE, tc.notes_text(items, tc.CONVERSATION))
    assert live == tc.block(tc.node_metadata(items), tc.CONVERSATION)


def test_section_notes_show_only_to_the_conversation():
    items = tc.put([], tc.SECTION, "memory", MAP)
    items = tc.put(items, tc.SECTION, "environment", tc.section_note("environment", "<environment/>"))
    items = tc.put(items, tc.ATTACHMENT, "rewind", "rewound")
    md = tc.node_metadata(items)
    assert "environment" in tc.block(md, tc.CONVERSATION) and MAP in tc.block(md, tc.CONVERSATION)
    assert MAP not in tc.block(md, tc.CONVERSATION_WITHOUT_MEMORY)
    assert "environment" in tc.block(md, tc.CONVERSATION_WITHOUT_MEMORY)
    assert tc.block(md, tc.OWN_PROMPT) == "rewound"


def test_a_section_is_sent_whole_and_only_when_the_model_lacks_it():
    changed = tc.Section(current="<open_work>b</open_work>", in_prompt="<open_work>a</open_work>")
    note = tc.section_note("open_work", changed.current)
    assert "<open_work>b</open_work>" in tc.note_to_send("open_work", changed, None)
    assert tc.note_to_send("open_work", changed, note) == ""
    back = tc.Section(current="<open_work>a</open_work>", in_prompt="<open_work>a</open_work>")
    # The history shows a later text than the prompt: the prompt's text comes again.
    assert tc.note_to_send("open_work", back, note) == tc.section_note("open_work", back.current)
    assert tc.note_to_send("open_work", back, None) == ""
    gone = tc.Section(current="", in_prompt="<open_work>a</open_work>")
    assert "no longer applies" in tc.note_to_send("open_work", gone, None)
    # Pages never sit in the prompt: nothing open, nothing said.
    assert tc.note_to_send("pages", tc.Section(current="", in_prompt=""), None) == ""


def test_an_engine_that_does_not_report_what_it_shows_is_given_the_note():
    back = tc.Section(current="a", in_prompt="a")
    stored = tc.section_note("open_work", "b")
    assert tc.note_to_send("open_work", back, None, stored=stored) == tc.section_note("open_work", "a")
