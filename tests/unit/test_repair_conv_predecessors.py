"""The one-time conv-predecessor repair must re-link continuation
turns without touching legitimately-rootless nodes (agent_spawn roots,
forks, the session ROOT)."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.repair_conv_predecessors import repair_session


def _node(seq, nid, role, *, caller="", pred=None, source=None, display=None):
    meta = {}
    if pred is not None:
        meta["predecessor"] = pred
    if source is not None:
        meta["source"] = source
    if display is not None:
        meta["display"] = display
    return {"id": nid, "seq": seq, "role": role, "caller": caller,
            "metadata": meta}


def _write_history(tmp_path: Path, nodes: list[dict]) -> Path:
    sdir = tmp_path / "local_test"
    hdir = sdir / "history"
    hdir.mkdir(parents=True)
    for n in nodes:
        (hdir / f"{n['seq']:04d}-{n['id']}.json").write_text(
            json.dumps(n), encoding="utf-8")
    return sdir


def _read(sdir: Path, nid: str) -> dict:
    for f in (sdir / "history").iterdir():
        n = json.loads(f.read_text(encoding="utf-8"))
        if n["id"] == nid:
            return n
    raise AssertionError(nid)


def test_relinks_continuation_turn(tmp_path):
    sdir = _write_history(tmp_path, [
        _node(0, "ROOT", "user", display="root"),
        _node(1, "u1", "user", caller="ROOT", source="web"),
        _node(2, "u1_reply", "llm", pred="u1", source="web"),
        _node(3, "u2", "user", caller="", pred="", source=None),  # broken
        _node(4, "u2_reply", "llm", pred="u2", source="web"),
    ])
    fixes = repair_session(sdir, apply=True)
    assert len(fixes) == 1
    assert _read(sdir, "u2")["metadata"]["predecessor"] == "u1_reply"


def test_chains_multiple_broken_turns(tmp_path):
    sdir = _write_history(tmp_path, [
        _node(0, "ROOT", "user", display="root"),
        _node(1, "u1", "user", caller="ROOT", source="web"),
        _node(2, "u1_reply", "llm", pred="u1", source="web"),
        _node(3, "u2", "user", caller="", pred=""),   # broken
        _node(4, "u2_reply", "llm", pred="u2", source="web"),
        _node(5, "u3", "user", caller="", pred=""),   # broken
        _node(6, "u3_reply", "llm", pred="u3", source="web"),
    ])
    repair_session(sdir, apply=True)
    assert _read(sdir, "u2")["metadata"]["predecessor"] == "u1_reply"
    assert _read(sdir, "u3")["metadata"]["predecessor"] == "u2_reply"


def test_leaves_agent_spawn_untouched(tmp_path):
    sdir = _write_history(tmp_path, [
        _node(0, "ROOT", "user", display="root"),
        _node(1, "u1", "user", caller="ROOT", source="web"),
        _node(2, "u1_reply", "llm", pred="u1", source="web"),
        # a sub-agent's own root — legitimately has no conv predecessor
        _node(3, "spawn1", "user", caller="", pred=None, source="agent_spawn"),
        _node(4, "spawn1_reply", "llm", pred="spawn1", source="agent_spawn"),
    ])
    fixes = repair_session(sdir, apply=True)
    assert fixes == []
    assert not _read(sdir, "spawn1")["metadata"].get("predecessor")


def test_leaves_fork_root_untouched(tmp_path):
    """A fork root's caller points at the spawning node, not ROOT — skip."""
    sdir = _write_history(tmp_path, [
        _node(0, "ROOT", "user", display="root"),
        _node(1, "u1", "user", caller="ROOT", source="web"),
        _node(2, "u1_reply", "llm", pred="u1", source="web"),
        _node(3, "fork", "user", caller="u1_reply", pred=None),  # spawn fork
    ])
    fixes = repair_session(sdir, apply=True)
    assert fixes == []


def test_idempotent(tmp_path):
    sdir = _write_history(tmp_path, [
        _node(0, "ROOT", "user", display="root"),
        _node(1, "u1", "user", caller="ROOT", source="web"),
        _node(2, "u1_reply", "llm", pred="u1", source="web"),
        _node(3, "u2", "user", caller="", pred=""),
        _node(4, "u2_reply", "llm", pred="u2", source="web"),
    ])
    repair_session(sdir, apply=True)
    second = repair_session(sdir, apply=True)
    assert second == []  # already linked
