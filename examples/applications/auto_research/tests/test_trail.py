"""A trail records roles, code stages, model stages with their context, and saves."""

import json

import pytest

from openprogram import Agent, Context

from auto_research.trail import Trail

judge = Agent(instructions="You pick among records.", tools=[], effort="high")


def pick(ctx, trail):
    """Pick the record."""
    with trail.call("judge", judge, pick, ctx, schema="verdicts") as rec:
        rec.output = {"choice": "c1"}
    return rec.output


def test_roles_stages_and_outputs_are_saved(tmp_path):
    trail = Trail("demo", {"source": "refs.bib", "n": 2}, {"judge": judge})
    trail.step("lookup", references=2, unavailable=[])
    pick(Context({"records": [{"title": "A"}], "note": "x" * 10}), trail)
    path = trail.save(tmp_path, report=tmp_path / "report.md")

    run = json.loads(path.read_text())
    assert run["schema"] == 1 and run["workflow"] == "demo" and run["args"] == {"source": "refs.bib", "n": 2}
    assert run["roles"]["judge"] == {"name": "judge", "instructions": "You pick among records.",
                                     "effort": "high", "model": "caller's model", "tools": []}
    code, model = run["stages"]
    assert code == {"stage": "lookup", "kind": "code", "at": code["at"], "references": 2, "unavailable": []}
    assert model["role"] == "judge" and model["function"] == "pick" and model["instructions"] == "Pick the record."
    assert model["context"] == {"records": [{"title": "A"}], "note": "x" * 10}
    assert model["schema"] == "verdicts" and model["output"] == {"choice": "c1"} and "error" not in model
    assert run["outputs"] == {"report": str(tmp_path / "report.md")}
    md = (tmp_path / "run.md").read_text()
    assert "| judge |" in md and "| lookup |" in md and "schema `verdicts`" in md


def test_error_inside_a_model_stage_is_recorded_and_raised(tmp_path):
    trail = Trail("demo", {}, {"judge": judge})
    with pytest.raises(ValueError):
        with trail.call("judge", judge, pick, Context({"a": 1})):
            raise ValueError("bad reply")
    assert trail.stages[0]["error"] == "ValueError('bad reply')" and trail.stages[0]["output"] is None


def test_long_text_is_cut_and_marked():
    trail = Trail("demo", {"text": "y" * 300_000}, {})
    assert trail.args["text"].endswith("[cut, 300000 chars]") and len(trail.args["text"]) < 300_000
    assert trail.roles == {}


def test_listeners_see_every_stage_live():
    from auto_research.trail import listen
    seen = []
    trail = Trail("demo", {}, {})
    with listen(seen.append):
        trail.step("read", refs=[1, 2, 3], note="x")
    trail.step("after")
    assert seen == [{"workflow": "demo", "stage": "read", "kind": "code", "event": "done", "refs": 3, "note": "x"}]
