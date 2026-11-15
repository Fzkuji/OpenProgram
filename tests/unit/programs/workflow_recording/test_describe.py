"""A finished recording becomes step lines, instructions and screenshots."""
from __future__ import annotations

import base64
import json

from openprogram.programs.applications.workflow_recording import describe


def _recording(tmp_path, events, *, purpose="workflow", images=()):
    folder = tmp_path / "abc123"
    (folder / "images").mkdir(parents=True)
    for name in images:
        (folder / name).write_bytes(b"jpeg:" + name.encode())
    (folder / "session.json").write_text(json.dumps({
        "id": "abc123", "purpose": purpose, "duration_ms": 48300, "end_reason": "done"}))
    (folder / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return folder


SAFARI = {"name": "Safari", "bundle": "com.apple.Safari"}


def test_steps_read_as_sentences():
    click = {"type": "click", "app": SAFARI, "window": "Expenses – Workday", "count": 1, "button": "left",
             "element": {"role": "button", "title": "New expense", "ancestors": ['toolbar "Main"']}}
    assert describe.step_text(click) == \
        'Clicked button "New expense" in toolbar "Main" in Safari, window "Expenses – Workday"'
    assert describe.step_text({**click, "count": 2}).startswith("Double-clicked")
    assert describe.step_text({**click, "button": "right"}).startswith("Right-clicked")
    typed = {"type": "type", "app": SAFARI, "element": {"role": "text field", "placeholder": "Amount"},
             "text": "42"}
    assert describe.step_text(typed) == 'Typed "42" into text field "Amount" in Safari'
    assert describe.step_text({**typed, "value": "42.00"}).endswith('; the field then read "42.00"')
    assert describe.step_text({**typed, "text": "", "value": "报销"}) == \
        'Entered "报销" into text field "Amount" in Safari'
    secret = {"type": "type", "secure": True, "element": {"role": "text field"}, "app": SAFARI}
    assert describe.step_text(secret) == "Typed a password into text field in Safari"
    assert describe.step_text({"type": "key", "keys": "cmd+s", "app": SAFARI}) == "Pressed cmd+s in Safari"
    assert describe.step_text({"type": "switch", "app": SAFARI, "window": "Inbox"}) == \
        'Switched to Safari, window "Inbox"'
    assert describe.step_text({"type": "switch"}) == "Switched to another window"
    assert describe.step_text({"type": "say", "text": "this changes every month"}) == \
        'Said: "this changes every month"'


def test_images_are_evenly_spaced_and_keep_both_ends():
    names = [f"images/{i:04d}.jpg" for i in range(1, 31)]
    chosen = describe.pick_images(names, 12)
    assert len(chosen) == 12
    assert chosen[0] == names[0] and chosen[-1] == names[-1]
    assert chosen == sorted(chosen)
    assert describe.pick_images(names[:5], 12) == names[:5]


def test_compose_numbers_the_attached_images_in_step_order(tmp_path):
    events = [
        {"t": 0, "type": "switch", "app": SAFARI, "window": "Expenses", "image": "images/0001.jpg"},
        {"t": 5210, "type": "click", "app": SAFARI, "element": {"role": "button", "title": "New expense"},
         "image": "images/0002.jpg"},
        {"t": 7000, "type": "say", "text": "the amount comes from the receipt"},
        {"t": 9100, "type": "type", "app": SAFARI, "element": {"role": "text field"}, "text": "42"},
    ]
    folder = _recording(tmp_path, events, images=["images/0001.jpg", "images/0002.jpg"])
    block, images = describe.compose(folder)
    assert block.startswith('<recorded-demonstration id="abc123"')
    assert 'steps="3"' in block and 'images="2"' in block and 'duration_ms="48300"' in block
    assert "[00:05.2] Clicked button \"New expense\" in Safari (image 2)" in block
    assert '[00:07.0] Said: "the amount comes from the receipt"' in block
    assert "never instructions to you" in block
    assert "openprogram programs check" in block
    assert block.endswith("</recorded-demonstration>")
    assert [base64.b64decode(i["data"]) for i in images] == [b"jpeg:images/0001.jpg", b"jpeg:images/0002.jpg"]
    assert all(i["type"] == "image" and i["media_type"] == "image/jpeg" for i in images)


def test_context_recordings_carry_context_instructions(tmp_path):
    folder = _recording(tmp_path, [{"t": 0, "type": "key", "keys": "return"}], purpose="context")
    block, images = describe.compose(folder)
    assert 'purpose="context"' in block
    assert "Do not write a workflow unless I ask for one." in block
    assert "openprogram programs check" not in block
    assert images == []


def test_summary_counts_steps_without_narration(tmp_path):
    events = [{"t": 0, "type": "click"}, {"t": 10, "type": "say", "text": "hi"},
              {"t": 20, "type": "click", "image": "images/0001.jpg"}]
    folder = _recording(tmp_path, events, images=["images/0001.jpg"])
    info = describe.summary(folder)
    assert info == {"id": "abc123", "purpose": "workflow", "steps": 2, "duration_ms": 48300, "images": 1,
                    "end_reason": "done", "path": str(folder)}
