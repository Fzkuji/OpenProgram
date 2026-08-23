"""Unit tests for the docs_question agentic function
(``openprogram/programs/workflow/docs_question/``): the entry point's
input validation, the page listing it builds its prompt from, source
path normalization, and the shape it returns once the spawned agent has
replied.

The single LLM round goes through the module-level ``_run_docs_turn``
seam (the same shape ``goal`` and ``agentic_workflow`` use), so these tests
stub it and never reach a provider."""
from __future__ import annotations

import json

import pytest

import openprogram.programs.workflow.docs_question as DQ


@pytest.fixture
def fake_docs(tmp_path, monkeypatch):
    """A miniature docs/ tree, standing in for the repository's."""
    root = tmp_path / "docs"
    (root / "capabilities").mkdir(parents=True)
    (root / "capabilities" / "goal.md").write_text("# Session goals\n\nbody")
    (root / "capabilities" / "goal.zh.md").write_text("# 会话目标\n\n正文")
    (root / "README.md").write_text("# OpenProgram\n")
    (root / "_site").mkdir()
    (root / "_site" / "built.md").write_text("# build output\n")
    monkeypatch.setattr(DQ, "docs_root", lambda: root)
    return root


def _reply(answer="It can.", sources=("capabilities/goal.md",), covered=True):
    return json.dumps({"answer": answer, "sources": list(sources),
                       "covered": covered})


# ---------------------------------------------------------------------------
# Entry point — argument validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("question", ["", "   ", "\n\t "])
def test_empty_question_is_rejected(monkeypatch, fake_docs, question) -> None:
    monkeypatch.setattr(
        DQ, "_run_docs_turn",
        lambda *a, **k: pytest.fail("must not spawn an agent for an empty question"))
    with pytest.raises(ValueError, match="question"):
        DQ.run_docs_question(question=question, session_id="s1")


def test_question_is_trimmed_into_the_prompt(monkeypatch, fake_docs) -> None:
    prompts = []
    monkeypatch.setattr(
        DQ, "_run_docs_turn",
        lambda sid, p, **k: prompts.append(p) or _reply())
    DQ.run_docs_question(question="  can it set a goal?  ", session_id="s1")
    assert "<question>\ncan it set a goal?\n</question>" in prompts[0]


# ---------------------------------------------------------------------------
# The prompt: page listing and the rules that matter
# ---------------------------------------------------------------------------

def test_prompt_lists_english_pages_with_titles(fake_docs) -> None:
    prompt = DQ._prompt("q", fake_docs)
    assert "capabilities/goal.md — Session goals" in prompt
    assert "README.md — OpenProgram" in prompt
    assert "capabilities/goal.zh.md" not in prompt      # translations not listed
    assert "_site/built.md" not in prompt               # build output not listed
    assert str(fake_docs) in prompt                     # the scope it works in


def test_prompt_states_the_three_outcomes_and_the_no_fabrication_rule(
    fake_docs,
) -> None:
    prompt = DQ._prompt("q", fake_docs)
    assert "covered=false" in prompt and "does not cover it" in prompt
    assert "NOT supported" in prompt          # documented-no is not the same
    assert "fabrication" in prompt
    assert "authoritative" in prompt          # English page wins


def test_missing_docs_tree_lists_nothing_rather_than_erroring(tmp_path) -> None:
    assert DQ.list_pages(tmp_path / "nope") == []
    assert "(no pages found)" in DQ._prompt("q", tmp_path / "nope")


def test_generated_reference_pages_are_listed_only_when_present(
    fake_docs,
) -> None:
    # docs/reference/cli/ is gitignored build output — absent on a fresh
    # clone, and its absence is not an error.
    assert not any(rel.startswith("reference/")
                   for rel, _ in DQ.list_pages(fake_docs))
    generated = fake_docs / "reference" / "cli"
    generated.mkdir(parents=True)
    (generated / "run.md").write_text("# openprogram run\n")
    assert ("reference/cli/run.md", "openprogram run") in DQ.list_pages(fake_docs)


# ---------------------------------------------------------------------------
# The spawned agent is read-only and scoped
# ---------------------------------------------------------------------------

def test_tools_are_read_only(monkeypatch) -> None:
    from openprogram.agent import sub_agent_run

    seen = []

    class Result:
        failed = False
        final_text = _reply()
        error = None

    monkeypatch.setattr(sub_agent_run, "run_agent_turn",
                        lambda *a, **k: seen.append(k) or Result())
    DQ._run_docs_turn("s1", "prompt", agent_id="main", spawn_caller=None)

    tools = seen[0]["tools_override"]
    assert set(tools) == set(DQ.DOCS_TOOLS)
    for forbidden in ("write", "edit", "apply_patch", "bash", "task"):
        assert forbidden not in tools
    # The question is the whole brief — no session history pulled in.
    assert seen[0]["render_range"] == {"callers": 0, "subcalls": 0}


def test_failed_turn_raises(monkeypatch) -> None:
    from openprogram.agent import sub_agent_run

    class Result:
        failed = True
        final_text = ""
        error = "provider down"

    monkeypatch.setattr(sub_agent_run, "run_agent_turn", lambda *a, **k: Result())
    with pytest.raises(RuntimeError, match="provider down"):
        DQ._run_docs_turn("s1", "p", agent_id="main", spawn_caller=None)


# ---------------------------------------------------------------------------
# Return shape
# ---------------------------------------------------------------------------

def test_returns_answer_sources_covered(monkeypatch, fake_docs) -> None:
    monkeypatch.setattr(
        DQ, "_run_docs_turn",
        lambda *a, **k: _reply("Yes, with /goal.", ["capabilities/goal.md"]))
    out = DQ.run_docs_question(question="can it set a goal?", session_id="s1")
    assert out == {"answer": "Yes, with /goal.",
                   "sources": ["capabilities/goal.md"],
                   "covered": True}


def test_uncovered_answer_keeps_its_nearest_pages(monkeypatch, fake_docs) -> None:
    monkeypatch.setattr(
        DQ, "_run_docs_turn",
        lambda *a, **k: _reply("The documentation does not cover this.",
                               ["capabilities/goal.md"], covered=False))
    out = DQ.run_docs_question(question="does it fly?", session_id="s1")
    assert out["covered"] is False
    assert out["sources"] == ["capabilities/goal.md"]   # closest related page


def test_fenced_json_reply_is_accepted(fake_docs) -> None:
    out = DQ._parse_answer(
        '```json\n{"answer": "a", "sources": ["README.md"], "covered": true}\n```',
        fake_docs)
    assert out["answer"] == "a" and out["sources"] == ["README.md"]


@pytest.mark.parametrize("reply", [
    "no json at all",
    '{"answer": "a", "sources": []}',            # covered missing
    '{"answer": "a", "covered": "yes"}',         # covered not a bool
    '{"answer": "   ", "covered": true}',        # no answer text
])
def test_malformed_replies_are_rejected(reply, fake_docs) -> None:
    with pytest.raises(ValueError):
        DQ._parse_answer(reply, fake_docs)


# ---------------------------------------------------------------------------
# Source path normalization
# ---------------------------------------------------------------------------

def test_absolute_and_prefixed_paths_fold_to_one_form(fake_docs) -> None:
    want = "capabilities/goal.md"
    for raw in (
        "capabilities/goal.md",
        "docs/capabilities/goal.md",
        "/capabilities/goal.md",
        "`capabilities/goal.md`",
        "  capabilities/goal.md  ",
        "capabilities/goal.md#how-a-goal-is-judged",
        str(fake_docs / "capabilities" / "goal.md"),
    ):
        assert DQ.normalize_source(raw, fake_docs) == want


def test_paths_outside_docs_are_dropped(fake_docs) -> None:
    for raw in ("", "   ", "../secrets.md", "a/../../b.md", "/etc/passwd"):
        assert DQ.normalize_source(raw, fake_docs) == ""


def test_a_cited_page_that_does_not_exist_is_dropped(fake_docs) -> None:
    # A path the agent invented is not a source, however plausible it looks.
    assert DQ.normalize_source("capabilities/imaginary.md", fake_docs) == ""
    assert DQ._parse_answer(json.dumps({
        "answer": "a", "covered": True,
        "sources": ["capabilities/imaginary.md", "README.md"],
    }), fake_docs)["sources"] == ["README.md"]


def test_sources_are_deduplicated_in_order(fake_docs) -> None:
    out = DQ._parse_answer(json.dumps({
        "answer": "a", "covered": True,
        "sources": ["docs/README.md", "README.md", 7, None,
                    "/etc/passwd", "capabilities/goal.md"],
    }), fake_docs)
    assert out["sources"] == ["README.md", "capabilities/goal.md"]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_module_is_registered() -> None:
    from openprogram.programs._registry import AGENTIC_MODULES
    assert "docs_question" in AGENTIC_MODULES


def test_docs_root_points_at_the_repository_docs_tree() -> None:
    root = DQ.docs_root()
    assert root.name == "docs"
    assert (root / "capabilities").is_dir()
