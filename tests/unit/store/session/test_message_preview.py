"""A chat's preview line shows the words of its latest user message,
without the wrapping some messages carry (session_store/preview.py)."""

from openprogram.state.store.session.session_store.preview import message_preview

ENVELOPE = '{"speaker_kind":"owner","speaker_id":"owner/local","speaker_display":"Owner","content":'


def test_pasted_content_tags_are_dropped():
    assert message_preview('<pasted_content id="32e9"> \\paragraph{Intervention control.} The') == "\\paragraph{Intervention control.} The"


def test_owner_envelope_gives_its_words_even_cut_short():
    assert message_preview(ENVELOPE + '"hello there"}') == "hello there"
    assert message_preview(ENVELOPE + '"hello the') == "hello the"


def test_one_line_cut_at_eighty_and_stable():
    line = message_preview("word\n" * 40)
    assert "\n" not in line and len(line) == 78 and line.endswith("…")
    assert message_preview(line) == line
