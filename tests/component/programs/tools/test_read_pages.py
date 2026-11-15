"""A read longer than one result pages: contiguous from its offset, ending on
a whole line, with the offset to continue from — never a head and tail with
the middle cut out by the agent loop's bound. The AGENTS.md a read reaches
is sent beside the page, bounded on its own, so it never shrinks the page."""

import asyncio
import re
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from openprogram.programs.tools.files.read import read
from openprogram.runtime.agent.loop.tool_dispatch import _as_stored
from openprogram.runtime.agent.runtime import _adapt_tools
from openprogram.state.store.session import spill

# The bound, in characters of the ASCII text these tests read (a weight of
# CAP // 4), set as a model's ``tool_output_weight`` would set it.
CAP = 8_000


@pytest.fixture(autouse=True)
def _small_cap(monkeypatch, tmp_path):
    saved = tmp_path / "tool_results"
    saved.mkdir()
    monkeypatch.setattr("openprogram.programs._host.runtime._tool_results_dir", lambda: saved)
    token = spill.set_window(None, CAP // 4)
    try:
        yield saved
    finally:
        spill.reset_window(token)


def _read(arguments):
    """The text the model reads: the tool's result in its stored form."""
    tool = _adapt_tools([read])[0]
    result = asyncio.run(tool.execute("read-page", arguments, None, None))
    assert not result.is_error
    stored = _as_stored(SimpleNamespace(name="read", id="read-page", arguments=arguments), result)
    texts = [block.text for block in stored.content if hasattr(block, "text")]
    assert len(texts) == 1
    return texts[0]


def test_a_long_read_pages_from_its_offset_and_the_pages_rebuild_the_file(tmp_path, _small_cap):
    lines = [f"def f{i}(): return {i}  # {'x' * (i % 50)}" for i in range(1, 1_201)]
    path = tmp_path / "big.py"
    path.write_text("\n".join(lines) + "\n")

    seen: list[str] = []
    offset, pages = 1, 0
    while True:
        text = _read({"file_path": str(path), "offset": offset, "limit": 2_000})
        pages += 1
        assert len(text) <= CAP
        # The stored text is the page itself: nothing elided, nothing saved aside.
        assert "elided" not in text and not list(_small_cap.iterdir())
        rows = re.findall(r"^ *(\d+)\t(.*)$", text, flags=re.M)
        assert [int(n) for n, _ in rows] == list(range(offset, offset + len(rows)))
        seen.extend(body for _, body in rows)
        last = offset + len(rows) - 1
        assert f"(lines {offset}-{last} of 1200)" in text
        if last == 1_200:
            assert "to continue" not in text
            break
        assert f"offset={last + 1} to continue" in text
        assert f'read("{path}", offset={last + 1}, limit={1_200 - last})' in text
        offset = last + 1
    assert pages > 3
    assert seen == lines


def test_a_read_that_fits_is_unchanged(tmp_path):
    path = tmp_path / "small.txt"
    path.write_text("a\nb\nc\n")
    assert _read({"file_path": str(path)}) == f"# {path} (lines 1-3 of 3)\n     1\ta\n     2\tb\n     3\tc"


def test_a_pdf_read_stops_before_the_page_that_would_pass_the_bound(tmp_path):
    writer = PdfWriter()
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    for index in range(12):
        page = writer.add_blank_page(width=600, height=800)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        body = " ".join(f"(page {index + 1} line {n} {'y' * 60}) Tj T*" for n in range(20))
        stream.set_data(f"BT /F1 8 Tf 10 TL 10 780 Td {body} ET".encode())
        page[NameObject("/Contents")] = stream
    path = tmp_path / "doc.pdf"
    writer.write(path)

    text = _read({"file_path": str(path)})
    assert len(text) <= CAP and "elided" not in text
    shown = re.findall(r"^\[page (\d+)\]$", text, flags=re.M)
    last = int(shown[-1])
    assert 1 < last < 12
    assert f"use offset={last + 1} to continue" in text


def test_a_remote_read_pages_the_same_way(monkeypatch):
    from openprogram.host import environments as envs
    from openprogram.programs.tools.files import read as local
    from openprogram.state.worktree.context import reset_worktree, set_worktree

    class _FS:
        env = envs.Environment(id="devbox", name="devbox", kind="ssh", ssh_target="me@devbox")

        def read_bytes(self, path):
            return "".join(f"row {i} {'z' * 40}\n" for i in range(1, 801)).encode()

    monkeypatch.setattr("openprogram.programs.tools.files._remote.remote_target", lambda: _FS())
    monkeypatch.setattr("openprogram.programs.tools.files._remote.mark_seen", lambda *a: None)
    token = set_worktree("/srv/app")
    try:
        out = local.execute("data.txt")
    finally:
        reset_worktree(token)
    last = int(re.findall(r"^ *(\d+)\t", out, flags=re.M)[-1])
    assert len(out) <= CAP and last < 800
    assert out.startswith(f"# me@devbox:/srv/app/data.txt (lines 1-{last} of 800)")
    assert f'read("/srv/app/data.txt", offset={last + 1}, limit={800 - last})' in out


def _project_read(tmp_path, monkeypatch, agents_chars, context_window=None):
    """Read a long file in another project whose AGENTS.md the system prompt
    does not carry, through the agent loop's dispatch, as a turn does; the
    page and the attached AGENTS.md text."""
    import time
    from openprogram.models.providers.shared.tool_attachments import record
    from openprogram.models.providers.types import AssistantMessage, ToolCall
    from openprogram.models.providers.utils.event_stream import EventStream
    from openprogram.runtime.agent.loop import _execute_tool_calls
    from openprogram.runtime.context import agents_md

    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    rule = "- keep things tidy and test everything you touch.\n"
    (project / "AGENTS.md").write_text("# Rules\n" + rule * (agents_chars // len(rule)))
    lines = [f"def f{i}(): return {i}  # {'x' * (i % 50)}" for i in range(1, 1_201)]
    path = project / "big.py"
    path.write_text("\n".join(lines) + "\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setattr(agents_md, "working_dir", lambda: elsewhere)
    monkeypatch.setattr("openprogram.runtime.events.bus._context_metadata", lambda: {"session": "pages"})

    message = AssistantMessage(
        content=[ToolCall(id="read-agents", name="read", arguments={"file_path": str(path)})],
        api="openai-codex-responses", provider="openai-codex", model="test",
        stop_reason="toolUse", timestamp=int(time.time() * 1000))
    out = asyncio.run(_execute_tool_calls(_adapt_tools([read]), message, None, EventStream(),
                                          context_window=context_window,
                                          tool_output_weight=CAP // 4))
    result = out["tool_results"][0]
    assert not result.is_error
    texts = [block.text for block in result.content if hasattr(block, "text")]
    assert len(texts) == 1
    entry = record(result.details)
    return texts[0], (entry["text"] if entry else ""), lines


def _whole_page(page):
    rows = re.findall(r"^ *(\d+)\t(.*)$", page, flags=re.M)
    assert rows and [int(n) for n, _ in rows] == list(range(1, len(rows) + 1))
    assert f"offset={len(rows) + 1} to continue" in page
    assert "elided" not in page
    return rows


def test_the_agents_md_a_read_reached_goes_beside_the_page(tmp_path, monkeypatch, _small_cap):
    page, attached, lines = _project_read(tmp_path, monkeypatch, agents_chars=3_000)
    assert "keep things tidy" not in page and len(page) <= CAP
    assert "keep things tidy" in attached and attached.endswith("</agents_md>")
    rows = _whole_page(page)
    # The page uses the whole bound: the AGENTS.md takes nothing from it.
    assert len(page) > CAP - 200 and all(body == lines[int(n) - 1] for n, body in rows)
    assert not list(_small_cap.iterdir())


def test_an_agents_md_longer_than_the_bound_is_cut_and_the_page_kept(tmp_path, monkeypatch, _small_cap):
    page, attached, lines = _project_read(tmp_path, monkeypatch, agents_chars=12_000)
    assert len(page) <= CAP and len(attached) <= CAP
    _whole_page(page)
    # Only the attached AGENTS.md lost its middle, kept whole in a file.
    assert "elided" in attached and attached.endswith("</agents_md>")
    [saved] = list(_small_cap.iterdir())
    assert "keep things tidy" in saved.read_text()


@pytest.mark.parametrize("window", [4_096, 6_000])
def test_a_small_window_pages_a_read_within_its_share(tmp_path, monkeypatch, _small_cap, window):
    # 30% of the window, in weight, is the bound, under CAP.
    room = int(window * spill.WINDOW_SHARE) * 4
    assert room < CAP
    page, attached, lines = _project_read(tmp_path, monkeypatch, agents_chars=8_000,
                                          context_window=window)
    assert len(page) <= room and len(attached) <= room
    rows = _whole_page(page)
    assert all(body == lines[int(n) - 1] for n, body in rows)
    assert "keep things tidy" in attached and attached.endswith("</agents_md>")
    [saved] = list(_small_cap.iterdir())
    assert "keep things tidy" in saved.read_text()


def test_a_line_longer_than_a_small_windows_page_is_cut_to_fit(tmp_path, monkeypatch, _small_cap):
    path = tmp_path / "wide.txt"
    path.write_text("\n".join("y" * 3_000 for _ in range(5)) + "\n")
    token = spill.set_window(2_000)
    try:
        text = _read({"file_path": str(path)})
    finally:
        spill.reset_window(token)
    assert len(text) <= int(2_000 * spill.WINDOW_SHARE) * 4
    assert "elided" not in text and not list(_small_cap.iterdir())
    assert "     1\ty" in text and "…[truncated]" in text
    assert re.search(r"offset=\d+ to continue", text)
