"""User message text: recordings, titles and pasted-span records."""
from __future__ import annotations


def _with_recording(text: str, recording_id) -> tuple[str, dict, list]:
    """Append a recording's step block to the message text.

    Returns the new text, the summary kept in the message's metadata and
    the screenshots that go to the model as image blocks. A recording
    still running is finished first, as if Done had been pressed. The
    recorder is the ``workflow_recording`` application's service; while it
    is disabled a message naming a recording is refused.
    """
    import re
    from openprogram.programs._host import services as _services
    recording = _services.lookup("workflow_recording")
    if recording is None:
        raise ValueError("Workflow recording is not enabled")
    if not isinstance(recording_id, str):
        raise ValueError("Invalid recording id")
    live = recording.get(recording_id)
    if live is not None:
        live.finish("done")
    try:
        directory = recording.directory_of(recording_id)
    except recording.RecordingError:
        raise ValueError("The recording no longer exists") from None
    block, images = recording.compose(directory)
    # The composer's placeholder for the optimistic bubble, and anything
    # else shaped like a recording block, gives way to the real one.
    text = re.sub(rf"<{recording.TAG}[^>]*>.*?</{recording.TAG}>", "", text or "", flags=re.S).strip()
    info = recording.summary(directory)
    meta = {key: info[key] for key in ("id", "purpose", "steps", "duration_ms", "images")}
    return (f"{text}\n\n{block}" if text else block), meta, images


def _title_from_text(text: str) -> str:
    """Conversation title from the first user message, with attachment
    markers + legacy inline blocks stripped so a truncated
    a long path-bearing attachment marker never leaks into the sidebar.

    Mirrors the web parser (``user-attachments.tsx``) on the backend so
    the stored title is already clean — the frontend strips markers for
    display too, but only when the closing bracket survives; truncating
    at 50 chars can sever it, so we clean first, then truncate.
    """
    import re
    from openprogram.state import attachments as _attachments
    t = re.sub(r"<attachment-preview[^>]*>.*?</attachment-preview>", "", text, flags=re.S)
    recorded = "<recorded-demonstration" in t
    t = re.sub(r"<recorded-demonstration[^>]*>.*?</recorded-demonstration>", "", t, flags=re.S)
    t = _attachments.strip_markers(t)
    t = re.sub(r"\[attachment:[^\]]*\]", "", t)
    t = re.sub(r"\[attached(?: file)?:[^\]]*\]", "", t)
    t = re.sub(r"<file [^>]*>.*?</file>", "", t, flags=re.S)
    t = t.strip()
    if not t and recorded:
        return "Recorded demonstration"
    return t[:50] + ("..." if len(t) > 50 else "")


def _pasted_text_elements(raw, text_length: int) -> list[dict]:
    """Display-only records of pasted spans (Codex ``text_elements``).

    The model still receives the full text; these only let the web bubble
    show each span collapsed. Offsets count from the end of the message
    (see apps/web/lib/chat/send/paste-elements.ts). Malformed records are
    dropped rather than rejecting the turn.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw[:20]:
        if not isinstance(item, dict):
            continue
        start, end = item.get("tail_start"), item.get("tail_end")
        label, head = item.get("label"), item.get("head")
        if (type(start) is int and type(end) is int and 0 <= end < start <= text_length
                and isinstance(label, str) and isinstance(head, str)):
            out.append({"tail_start": start, "tail_end": end,
                        "label": label[:80], "head": head[:32]})
    return out
