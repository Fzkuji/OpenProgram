"""The code index, the paper parts and the SVG layout, in plain code."""

from auto_research.tasks.codewiki import code, graph, paper

from .repo import make


def test_index_reads_modules_calls_entries_and_configs(tmp_path):
    root = make(tmp_path)
    idx = code.index(root)
    files = {m["file"]: m for m in idx["modules"]}
    assert "drafts/old/junk.py" not in files                       # drafts are not code
    assert files["tests/test_model.py"]["test"] and not files["gated/model.py"]["test"]
    model = files["gated/model.py"]
    assert model["doc"].startswith("Gated attention") and [c["name"] for c in model["classes"]] == ["GatedAttention", "Block"]
    assert {f["qualname"] for f in model["functions"]} >= {"GatedAttention.forward", "Block.forward", "softmax", "train_step"}
    assert model["internal"] == ["gated.loss"]
    edges = {(e["from"], e["to"]) for e in idx["edges"]}
    assert ("gated/model.py::Block.forward", "gated/model.py::GatedAttention.forward") in edges   # self.attn(...)
    assert ("gated/model.py::Block.__init__", "gated/model.py::GatedAttention.__init__") in edges
    assert ("gated/model.py::train_step", "gated/loss.py::margin_loss") in edges                 # relative import
    assert ("train.py::main", "gated/model.py::train_step") in edges                             # absolute import
    assert ("train.py::<module>", "train.py::main") in edges
    entries = {(e["kind"], e["name"]): e for e in idx["entries"]}
    assert "__main__ block" in entries[("script", "train.py")]["why"]
    assert "parses command-line arguments" in entries[("script", "train.py")]["why"]
    assert entries[("console_script", "gated-train")]["file"] == "train.py"
    assert {c["file"]: c["keys"] for c in idx["configs"]}["config.yaml"] == ["heads", "lr"]


def test_other_languages_by_regex(tmp_path):
    idx = code.index(make(tmp_path))
    files = {m["file"]: m for m in idx["modules"]}
    assert {f["name"] for f in files["web/app.ts"]["functions"]} == {"tokenize", "helper"}
    assert [c["name"] for c in files["web/app.ts"]["classes"]] == ["Viewer"]
    assert {f["name"] for f in files["web/server.go"]["functions"]} == {"Handle", "main"}
    assert [c["name"] for c in files["web/cache.rs"]["classes"]] == ["Cache"]
    assert [f["name"] for f in files["web/cache.rs"]["functions"]] == ["lookup"]
    assert ("web/server.go::main", "web/server.go::Handle") in {(e["from"], e["to"]) for e in idx["edges"]}


def test_find_line_and_enclosing(tmp_path):
    root = make(tmp_path)
    idx = code.index(root)
    nodes = code.nodes(idx)
    lines = (root / "gated" / "model.py").read_text().splitlines()
    at = code.find_line(lines, "gated = [s / (1 +   math.exp(-self.gate[0]))  for s in scores]", 1)
    assert lines[at - 1].strip().startswith("gated =")
    assert code.enclosing(nodes, "gated/model.py", at) == "gated/model.py::GatedAttention.forward"
    assert code.find_line(lines, "return softmax(gated)\n\n\nclass Block:", 1) == at + 1     # a quote over lines
    assert code.find_line(lines, "not in the file", 1) == 0


def test_paper_parts(tmp_path):
    doc = paper.read(make(tmp_path))
    parts = {u["id"]: u for u in paper.units(doc)}
    assert list(parts) == ["sec:2.1", "sec:2.2", "sec:2.3", "eq:1", "alg:1"]   # not the Experiments equation
    assert parts["eq:1"]["label"] == "eq:gate" and "sigma(g)" in parts["eq:1"]["latex"][0]
    assert parts["alg:1"]["title"] == "Algorithm 1: Training loop" and "margin loss" in parts["alg:1"]["latex"][0]
    assert "hinge loss" in parts["sec:2.2"]["text"]


def test_method_sections_without_subsections():
    sections = [{"id": "S1", "title": "Introduction", "level": 1}, {"id": "S2", "title": "Our Approach", "level": 1},
                {"id": "S3", "title": "Related Work", "level": 1}, {"id": "S4", "title": "Model", "level": 1},
                {"id": "S5", "title": "Encoder", "level": 2}, {"id": "S6", "title": "Experimental Results", "level": 1}]
    assert paper.method_sections(sections) == {"S2", "S4", "S5"}


def test_graph_layers_break_cycles_and_draw():
    col, back = graph.layers(["a", "b", "c"], [("a", "b"), ("b", "c"), ("c", "a")])
    assert back == {("c", "a")} and col == {"a": 0, "b": 1, "c": 2}
    svg = graph.svg([{"id": "a", "label": "a.py", "href": "modules/a.html", "cls": "entry"},
                     {"id": "b", "label": "b.py"}, {"id": "c", "label": "c.py"}],
                    [("a", "b"), ("b", "c"), ("c", "a")])
    assert svg.startswith("<svg") and svg.count("<rect") == 3 and svg.count("<path class=\"e") == 3
    assert "e back" in svg and 'href="modules/a.html"' in svg and "<script" not in svg
    assert "no calls" in graph.svg([], [])
