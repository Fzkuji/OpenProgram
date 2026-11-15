"""Memory is read by one lenient reader, and a bad file spoils only itself.

Both indexes build on the lenient reader. Every paragraph is admitted:
one that cites nothing is one event under its own ID, its origin saying
where it came from.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _cited(fact: str, block: str, source: str = "/s/s1#m1") -> str:
    return (
        f"{fact}[^e1] ^{block}\n"
        "\n"
        f"[^e1]: Time: `2026-01-01`; Sources: [said]({source})\n"
    )


def _write(root: Path, relative: str, text: str, **kwargs) -> Path:
    path = root / "topics" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8", **kwargs)
    return path


class _Encoder:
    """Bag of words over a fixed vocabulary: enough to rank by overlap."""

    vocabulary = ("kyoto", "tea", "grpc", "transport", "pencils")

    def encode(self, values):
        import numpy as np

        return np.asarray([
            [float(word in value.lower()) for word in self.vocabulary]
            for value in values
        ])


def _bm25(root: Path, query: str) -> list[str]:
    from openprogram.state.memory.retrieval.bm25 import MemoryBM25Index

    return [hit["path"] for hit in MemoryBM25Index(root).search(query)]


def _embedding(root: Path, query: str) -> list[str]:
    from openprogram.state.memory.retrieval.embedding import MemoryEmbeddingIndex

    return [
        hit["path"]
        for hit in MemoryEmbeddingIndex(root, encoder=_Encoder()).search(query)
    ]


def test_a_footnote_citing_a_web_page_spoils_neither_index(tmp_path):
    _write(tmp_path, "home.md", "# Home\n\n" + _cited("The owner lives in Kyoto.", "abc11111"))
    _write(
        tmp_path, "links.md",
        "# Links\n\n"
        + _cited("Pencils come from a shop.", "def22222", "https://example.com/foo"),
    )
    assert _bm25(tmp_path, "Kyoto")[0] == "topics/home.md"
    assert _embedding(tmp_path, "Kyoto")[0] == "topics/home.md"


def test_a_copied_topic_file_still_reads(tmp_path):
    from openprogram.state.memory.markdown import parse_topic_tree, read_topic_tree

    text = "# Home\n\n" + _cited("The owner lives in Kyoto.", "abc11111")
    _write(tmp_path, "home.md", text)
    _write(tmp_path, "home copy.md", text)
    tree = read_topic_tree(tmp_path / "topics")
    owned = [unit for unit in tree.units if unit.memory_id == "abc11111"]
    assert [unit.topic_path for unit in owned] == ["home copy.md"]
    assert "duplicate memory_id: abc11111" in tree.problems["home.md"]
    assert len(parse_topic_tree(tmp_path / "topics", strict=False)) == 2
    # The copy's paragraph keeps no ID it can be linked by, but it is
    # still memory and is found, under a derived ID with no citation.
    assert sorted(_bm25(tmp_path, "Kyoto")) == ["topics/home copy.md", "topics/home.md"]


def test_byte_order_mark_and_crlf_still_give_the_heading(tmp_path):
    from openprogram.state.memory.markdown import read_topic_tree
    from openprogram.state.memory.retrieval.bm25 import MemoryBM25Index

    text = "# Home\n\n" + _cited("The owner lives in Kyoto.", "abc11111")
    _write(tmp_path, "home.md", b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode())
    [unit] = read_topic_tree(tmp_path / "topics").units
    assert unit.headings == ("Home",)
    assert unit.memory_id == "abc11111"
    [event] = MemoryBM25Index(tmp_path).events
    assert event.headings == ["Home"]
    assert event.content == "[2026-01-01] The owner lives in Kyoto."


def test_front_matter_upper_case_suffix_and_plain_prose(tmp_path):
    from openprogram.state.memory.markdown import read_topic_tree

    _write(
        tmp_path, "career/NOTES.MD",
        "---\nname: Notes\ndescription: job search\n---\n# Notes\n\n"
        "Applied to three places.\n\n"
        + _cited("The owner prefers tea.", "abc11111"),
    )
    _write(tmp_path, "binary.md", b"\xff\xfe\x00bad")
    tree = read_topic_tree(tmp_path / "topics")
    plain, cited = tree.units
    assert plain.derived_id and plain.content == "Applied to three places."
    assert plain.memory_id.startswith("~") and plain.line == 7
    assert not cited.derived_id and cited.memory_id == "abc11111"
    assert cited.topic_path == "career/NOTES.MD"
    assert tree.problems == {"binary.md": "not text"}
    # Uncited prose is found like the rest.
    from openprogram.state.memory.retrieval.bm25 import MemoryBM25Index

    hits = {hit["content"]: hit["origin"] for hit in MemoryBM25Index(tmp_path).search("Applied tea")}
    assert hits == {
        "Applied to three places.": "outside",
        "[2026-01-01] The owner prefers tea.": "cited",
    }


def test_one_claim_per_run_of_citations(tmp_path):
    from openprogram.state.memory.retrieval.bm25 import MemoryBM25Index

    _write(
        tmp_path, "home.md",
        "# Home\n\nThe owner lives in Kyoto.[^e1] They like tea.[^e2] ^abc11111\n\n"
        "[^e1]: Time: `2026-01-01`; Sources: [said](/s/s1#m1)\n"
        "[^e2]: Time: `undated`; Sources: [said](/s/s1#m2)\n",
    )
    events = MemoryBM25Index(tmp_path).events
    assert [(e.event_id, e.content, e.refs) for e in events] == [
        ("abc11111:1", "[2026-01-01] The owner lives in Kyoto.", ["openprogram/s1/m1"]),
        ("abc11111:2", "They like tea.", ["openprogram/s1/m2"]),
    ]


@pytest.mark.parametrize("strict", [True])
def test_the_strict_reading_still_refuses_what_a_write_may_not_leave(tmp_path, strict):
    from openprogram.state.memory.markdown import TopicFormatError, parse_topic_tree

    _write(tmp_path, "notes.md", "# Notes\n\nApplied to three places.\n")
    with pytest.raises(TopicFormatError, match="memory block ID required"):
        parse_topic_tree(tmp_path / "topics", strict=strict)
