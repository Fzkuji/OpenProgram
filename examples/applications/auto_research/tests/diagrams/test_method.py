"""method_diagram with the extractor and inspector scripted; pdflatex runs when installed."""

import json
import subprocess
from pathlib import Path

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.diagrams import common
from auto_research.tasks.diagrams import method as wf

TEX = r"""\documentclass{article}
\begin{document}
\begin{abstract}
Linear probes on frozen features measure representation quality.
\end{abstract}
\section{Introduction}
A linear classifier is trained on frozen features from a pretrained encoder.
\section{Our Method}\label{sec:method}
The pretrained encoder maps each image to frozen features. A linear probe reads the frozen
features and outputs class scores, trained with mini-batch stochastic gradient descent on
the cross-entropy loss with weight decay of $1\times10^{-4}$.
\section{Results}
The probe reaches 73.5 points.
\end{document}
"""

GRAPH = {"nodes": [
    {"id": "encoder", "label": "pretrained encoder", "kind": "process"},
    {"id": "features", "label": "frozen features", "kind": "store"},
    {"id": "probe", "label": "linear probe", "kind": "process"},
    {"id": "scores", "label": "class scores", "kind": "output"},
    {"id": "sgd", "label": "mini-batch stochastic gradient descent", "kind": "process"},
    {"id": "loss", "label": "cross-entropy loss", "kind": "output"},
    {"id": "magic", "label": "attention router", "kind": "process"},          # not in the paper
    {"id": "wd", "label": "weight decay of $1\\times10^{-4}$", "kind": "input"},  # isolated
], "edges": [
    {"source": "encoder", "target": "features", "label": "each image"},
    {"source": "features", "target": "probe", "label": "frozen features"},
    {"source": "probe", "target": "scores", "label": ""},
    {"source": "scores", "target": "loss", "label": "logits and labels"},     # label not in the paper
    {"source": "loss", "target": "sgd", "label": ""},
    {"source": "sgd", "target": "probe", "label": "updates"},                  # closes a cycle
    {"source": "magic", "target": "probe", "label": ""},                       # end dropped
    {"source": "encoder", "target": "scores", "label": ""},                    # long edge
]}

TEXLIVE = bool(common.which("pdflatex")) and bool(common.which("pdftoppm"))


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "main.tex").write_text(TEX)
    return repo


def run(paper, inspector=(), **kw):
    with script_roles({wf.extractor: [GRAPH], wf.inspector: list(inspector)}) as calls:
        out = json.loads(wf.method_diagram(str(paper), **kw))
    return out, calls


def test_graph_is_validated_against_the_paper(paper):
    nodes, edges, dropped = wf.check_graph(GRAPH, TEX)
    assert [n["id"] for n in nodes] == ["encoder", "features", "probe", "scores", "sgd", "loss"]
    reasons = {d["item"].split(":")[0]: d["reason"] for d in dropped}
    assert reasons["node magic"] == "label not found in the paper"
    assert reasons["node wd"] == "isolated: no kept edge"
    assert reasons["edge magic -> probe"] == "an end is not a kept node"
    assert reasons["edge label scores -> loss"] == "label not found in the paper"
    assert next(e for e in edges if e["target"] == "loss")["label"] == ""
    assert next(e for e in edges if e["source"] == "features")["label"] == ""     # repeats the box's label
    assert next(e for e in edges if e["target"] == "features")["label"] == "each image"
    with pytest.raises(ValueError, match="not a graph"):
        wf.check_graph({"nodes": []}, TEX)


def test_layout_breaks_cycles_and_routes_long_edges():
    nodes, edges, _ = wf.check_graph(GRAPH, TEX)
    plan = wf.layout(nodes, edges)
    layer = {i: p["layer"] for i, p in plan["nodes"].items()}
    assert layer["encoder"] == 0 and layer["features"] == 1 and layer["probe"] == 2 and layer["scores"] == 3
    routes = {(r["from"], r["to"]): r for r in plan["routes"]}
    assert len(routes[("encoder", "scores")]["points"]) == 4          # two columns passed, two points each
    assert sum(r["reverse"] for r in plan["routes"]) == 1              # one edge closes the cycle
    xs = {p["x"] for p in plan["nodes"].values()}
    assert len(xs) == max(layer.values()) + 1                          # one x per column
    wider = wf.layout(nodes, edges, scale=1.35)
    assert wider["gap_x"] > plan["gap_x"] and wider["row_gap"] > plan["row_gap"]
    tex = wf.tikz(nodes, plan, "Our Method")
    assert "\\node[output] (n_scores)" in tex and "flowback" in tex and tex.count("\\draw[") == len(edges)
    assert wf.as_tex("a {broken", safe=False) == "a broken"
    assert wf.wrap("one two three four five", 9) == "one two\\\\three\\\\four five"


def test_section_by_title_label_or_default(paper):
    sections = wf.split_sections(wf.body(TEX))
    assert wf.pick_section(sections, "")["title"] == "Our Method"
    assert wf.pick_section(sections, "sec:method")["title"] == "Our Method"
    assert wf.pick_section(sections, "results")["title"] == "Results"
    with pytest.raises(ValueError, match="no section"):
        wf.pick_section(sections, "Appendix")


@pytest.mark.skipif(not TEXLIVE, reason="pdflatex and pdftoppm are needed")
def test_render_inspect_and_widen(paper):
    problem = {"kind": "overlap", "labels": ["frozen features"], "detail": "edge label covers an arrow"}
    out, calls = run(paper, inspector=[{"problems": [problem]}, {"problems": []}])
    assert out["status"] == "PASS_WITH_NOTES"                          # drops are notes
    assert out["nodes"] == 6 and out["edges"] == 7 and out["dropped"] == 5
    folder = Path(out["report"]).parent
    assert folder.parent == paper / "drafts" / "method_diagram"
    for name in ("diagram.tex", "standalone.tex", "diagram.pdf", "diagram.png", "graph.json"):
        assert (folder / name).is_file()
    assert not (folder / "build").exists()
    graph = json.loads((folder / "graph.json").read_text())
    assert [r["scale"] for r in graph["rounds"]] == [1.0, 1.35] and graph["vision"] == "clean"
    look = calls[wf.inspector][0]
    assert any(b.get("type") == "image" for b in look.blocks) and "frozen features" in look.text
    extract = calls[wf.extractor][0]
    assert "lower_snake_case" in extract.text and "mini-batch stochastic" in extract.text
    report = (folder / "report.md").read_text()
    assert "# 方法示意图" in report and "attention router" in report and "×1.35" in report
    trail = json.loads(Path(out["run"]).read_text())
    assert [s["stage"] for s in trail["stages"]] == ["read", "extract", "validate", "render", "inspect 1",
                                                      "widen 1", "inspect 2", "report"]


@pytest.mark.skipif(not TEXLIVE, reason="pdflatex and pdftoppm are needed")
def test_unbalanced_label_falls_back_to_plain_text(paper, monkeypatch):
    monkeypatch.setattr(wf, "as_tex", lambda label, safe: wf.escape(label) if safe else label + "\\undefinedcmd")
    monkeypatch.setattr(wf, "image_input", lambda role: False)
    out, _ = run(paper)
    record = json.loads((Path(out["report"]).parent / "graph.json").read_text())
    assert out["pdf"] and record["fallback"] and "plain text" in " ".join(record["notes"])
    assert out["vision"].startswith("skipped") and "image input" in Path(out["report"]).read_text()


def test_without_pdflatex_writes_the_tikz_only(paper, monkeypatch):
    monkeypatch.setattr(common, "which", lambda tool: None)
    out, calls = run(paper)
    assert out["status"] == "PASS_WITH_NOTES" and out["diagram"] and not out["pdf"]
    assert "pdflatex is not installed" in Path(out["report"]).read_text()
    assert calls[wf.inspector] == []


def test_too_few_nodes_fails_without_a_figure(paper):
    bad = {"nodes": [{"id": "a", "label": "nothing like this", "kind": "input"},
                     {"id": "b", "label": "linear probe", "kind": "process"}],
           "edges": [{"source": "a", "target": "b", "label": ""}]}
    with script_roles({wf.extractor: [bad]}):
        out = json.loads(wf.method_diagram(str(paper)))
    assert out["status"] == "FAIL" and not out["diagram"]
