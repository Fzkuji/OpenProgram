"""code_wiki on the fixture repository with the mapper and writer scripted."""

import json

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.codewiki import workflow as wf

from .repo import make

GATE = {"function": "gated/model.py::GatedAttention.forward", "line": 15,
        "code_quote": "gated = [s / (1 + math.exp(-self.gate[0])) for s in scores]",
        "paper_quote": "gates the scores with a sigmoid before the softmax", "how": "Divides each score by 1 + e^-g."}


def mapping():
    gate = dict(GATE)
    return {"maps": [
        {"unit": "sec:2.1", "status": "implemented", "note": "", "links": [
            gate,
            # right quote, wrong function and line: re-attributed to the function holding it
            {"function": "gated/model.py::softmax", "line": 3, "code_quote": "return self.attn(q, k)",
             "paper_quote": "Each block scores queries against keys", "how": "Blocks call the attention."},
            # the paper quote is from another part: dropped
            {"function": "gated/model.py::softmax", "line": 28, "code_quote": "e = [math.exp(x - m) for x in xs]",
             "paper_quote": "hinge loss with margin one", "how": "softmax"}]},
        {"unit": "sec:2.2", "status": "implemented", "note": "", "links": [
            {"function": "gated/loss.py:margin_loss", "line": 6,
             "code_quote": "return sum(max(0.0, margin - logits[label] + x) for i, x in enumerate(logits) if i != label)",
             "paper_quote": "We train with a hinge loss with margin one", "how": "Sums the hinge over wrong classes."}]
         },
        {"unit": "sec:2.3", "status": "not_found", "note": "The code has no data collection.", "links": []},
        {"unit": "eq:1", "status": "partial", "note": "One gate for all heads.", "links": [
            dict(GATE, paper_quote=r"\mathrm{softmax}(\sigma(g) \cdot q k^\top)")]},
        {"unit": "alg:1", "status": "implemented", "note": "", "links": [
            {"function": "train.py::main", "line": 12, "code_quote": "for step in range(10):",
             "paper_quote": "Training loop", "how": "Ten steps."},
            {"function": "nope.py::main", "line": 1, "code_quote": "for step in range(10):",
             "paper_quote": "Training loop", "how": "a file that does not exist"}]}]}


def page(units):
    return {"purpose": "Gated attention and the block that stacks it.",
            "key_functions": [
                {"function": "GatedAttention.forward", "role": "Scores, gates and normalizes.", "line": 99,
                 "quote": "scores = [qi * ki for qi, ki in zip(q, k)]"},
                {"function": "Block.forward", "role": "wrong quote", "line": 24, "quote": "return softmax(gated)"},
                {"function": "Missing.fn", "role": "not there", "line": 1, "quote": "import math"}],
            "data_flow": [{"step": "Queries and keys are multiplied.", "line": 14,
                           "quote": "scores = [qi * ki for qi, ki in zip(q, k)]"},
                          {"step": "made up", "line": 1, "quote": "torch.load(checkpoint)"}],
            "paper": [{"unit": u, "how": "Implements it."} for u in units]}


def plain(units):
    return {"purpose": "A module.", "key_functions": [], "data_flow": [], "paper": [{"unit": u, "how": "x"} for u in units]}


def test_the_wiki_maps_checks_and_writes_the_site(tmp_path):
    root = make(tmp_path)
    pages = [page(["sec:2.1", "eq:1"])] + [plain([])] * 10
    with script_roles({wf.mapper: [mapping()], wf.writer: pages}) as calls:
        out = json.loads(wf.code_wiki(str(root)))
    assert out["code"] == str(root)                     # code_dir defaults to the project
    assert out["status"] == "PASS_WITH_NOTES" and out["unmapped"] == ["sec:2.3"]
    assert out["parts"] == 5 and out["mapped"] == 4
    first = calls[wf.mapper][0].text
    assert "### gated/model.py" in first and "   15|         gated = [s / (1 + math.exp(-self.gate[0])) for s in scores]" in first
    assert "### config.yaml" in first and "lr: 0.1" in first                        # config values
    assert "tests/test_model.py" not in first.split("outline of the rest")[0]      # tests only in the outline
    record = json.load(open(out["md"].replace("code_wiki.md", "wiki.json"), encoding="utf-8"))
    maps = record["maps"]
    a = maps["sec:2.1"]["links"]
    assert [(l["function"], l["line"]) for l in a] == [("gated/model.py::GatedAttention.forward", 15),
                                                      ("gated/model.py::Block.forward", 24)]       # re-attributed
    assert maps["sec:2.2"]["links"][0]["function"] == "gated/loss.py::margin_loss"               # "file:fn" accepted
    assert maps["eq:1"]["status"] == "partial" and maps["sec:2.3"]["status"] == "not_found"
    assert [l["function"] for l in maps["alg:1"]["links"]] == ["train.py::main"]
    why = {d["why"] for d in record["dropped"] if d["where"] == "map"}
    assert why == {"paper quote not found in this part of the paper", "no file nope.py in the code"}
    model_page = record["pages"]["gated/model.py"]
    assert [k["function"] for k in model_page["key_functions"]] == ["gated/model.py::GatedAttention.forward"]
    assert model_page["key_functions"][0]["line"] == 14                   # the quoted line, not the claimed 99
    assert [s["line"] for s in model_page["data_flow"]] == [14]
    assert [p["unit"] for p in model_page["paper"]] == ["sec:2.1", "eq:1"]
    writer_first = calls[wf.writer][0].text
    assert "gated/model.py" in writer_first and "sec:2.1" in writer_first
    assert len(calls[wf.writer]) == len(record["pages"])
    assert "tests/test_model.py" not in record["pages"]

    site = tmp_path / "paper" / "drafts" / "code_wiki"
    index = open(out["site"], encoding="utf-8").read()
    assert "<script" not in index and "<svg" in index and "Paper ↔ code" in index
    assert 'href="modules/gated__model.py.html"' in index and "Entry points" in index and "gated-train" in index
    assert 'src/gated__model.py.html#L15' in index
    folder = list(site.iterdir())[0] / "docs" / "code_wiki"
    module = (folder / "modules" / "gated__model.py.html").read_text(encoding="utf-8")
    assert "Key functions" in module and "How data flows" in module and 'id="f-GatedAttention-forward"' in module
    assert "<svg" in module and "Gated Attention" in module
    src = (folder / "src" / "gated__model.py.html").read_text(encoding="utf-8")
    assert 'id="L15"' in src
    assert (folder / "callgraph.svg").read_text().startswith("<svg")
    md = open(out["md"], encoding="utf-8").read()
    assert "`gated/model.py:15`" in md and "| 2.3 Data Collection | subsection | not_found |" in md
    report = open(out["report"], encoding="utf-8").read()
    assert "代码 Wiki" in report and "2.3 Data Collection" in report and "cp -R" in report
    assert "The code has no data collection." in report
    run = json.load(open(out["run"], encoding="utf-8"))
    stages = [s["stage"] for s in run["stages"]]
    assert stages[:4] == ["index", "paper", "map sec:2.1..alg:1", "check"] and stages[-2:] == ["write", "site"]
    assert not (root / "docs").exists()                 # the repository is not touched


def test_every_part_mapped_passes_and_none_fails(tmp_path):
    root = make(tmp_path)
    full = mapping()
    full["maps"][2] = {"unit": "sec:2.3", "status": "partial", "note": "", "links": [
        {"function": "train.py::<module>", "line": 6, "code_quote": 'p.add_argument("--heads", type=int, default=2)',
         "paper_quote": "filtered them by hand", "how": "pretend"}]}
    with script_roles({wf.mapper: [full], wf.writer: [plain([])] * 10}):
        out = json.loads(wf.code_wiki(str(root), out_dir=str(tmp_path / "o1")))
    assert out["status"] == "PASS" and out["unmapped"] == []
    broken = mapping()
    for item in broken["maps"]:
        for link in item["links"]:
            link["code_quote"] = "this line is not in the code"
    with script_roles({wf.mapper: [broken], wf.writer: [plain([])] * 10}):
        out = json.loads(wf.code_wiki(str(root), out_dir=str(tmp_path / "o2")))
    assert out["status"] == "FAIL" and out["mapped"] == 0
    assert out["report"].startswith(str(tmp_path / "o2"))


def test_code_dir_and_errors(tmp_path):
    root = make(tmp_path)
    code = tmp_path / "elsewhere"
    code.mkdir()
    (code / "solo.py").write_text("def gate(x):\n    return x * 2  # gates the scores\n")
    reply = {"maps": [{"unit": u, "status": "not_found", "note": "", "links": []}
                      for u in ("sec:2.1", "sec:2.2", "sec:2.3", "eq:1", "alg:1")]}
    with script_roles({wf.mapper: [reply], wf.writer: [plain([])]}):
        out = json.loads(wf.code_wiki(str(root), code_dir=str(code)))
    assert out["code"] == str(code.resolve()) and out["modules"] == 1 and out["status"] == "FAIL"
    with pytest.raises(ValueError, match="not a folder"):
        wf.code_wiki(str(root), code_dir=str(tmp_path / "missing"))
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="no source file"):
        wf.code_wiki(str(root), code_dir=str(empty))
    with pytest.raises(ValueError, match="not a Git repository"):
        wf.code_wiki(str(code))
    (root / "main.tex").unlink()
    with pytest.raises(ValueError, match="no main .tex"):
        wf.code_wiki(str(root))


def test_check_maps_rejects_a_malformed_reply():
    with pytest.raises(ValueError):
        wf.check_maps({"oops": []}, [], {}, {}, [])
    assert wf.paper_found("q k^\\top)", {"check": [r"\sigma(g) \cdot q k^\top"]}) is False    # too short
    assert wf.paper_found(r"\sigma(g)\cdot q k^\top", {"check": [r"\sigma(g) \cdot q k^\top"]})   # spaces ignored
