"""baseline_code with GitHub, arXiv, Hugging Face and Semantic Scholar replaced by fakes
and the chooser scripted."""

import csv
import json
import subprocess

import pytest
from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.baselinecode import apply_edit, baseline_code
from auto_research.tasks.baselinecode import find
from auto_research.tasks.baselinecode import workflow as wf
from auto_research.tasks.citations import sources

TEX = r"""\documentclass{article}
\title{Probes}
\begin{document}
\section{Introduction}
Features transfer \cite{he2016resnet,dosovitskiy2021vit}, and so does \cite{related2020}.
\section{Experimental Results}
\begin{tabular}{lcc}
Model & error & GLUE \\
ResNet-152 \cite{he2016resnet} & 19.38 & -- \\
ViT-H/14 \cite{dosovitskiy2021vit} & 12.10 & -- \\
BERT-Large~\cite{devlin2019bert} & -- & 80.5 \\
\end{tabular}
\bibliography{refs}
\end{document}
"""
BIB = r"""@inproceedings{he2016resnet, title={Deep Residual Learning for Image Recognition},
  author={He, Kaiming and Zhang, Xiangyu}, year={2016}, eprint={1512.03385}}
@inproceedings{dosovitskiy2021vit, title={An Image is Worth 16x16 Words},
  author={Dosovitskiy, Alexey and others}, year={2021}, eprint={2010.11929}}
@inproceedings{devlin2019bert, title={BERT: Pre-training of Deep Bidirectional Transformers},
  author={Devlin, Jacob and Chang, Ming-Wei}, year={2019}, eprint={1810.04805}}
@article{related2020, title={Some Related Work}, author={Doe, Jane}, year={2020}}
@article{slowfast, title={A Method Without Official Code At All}, author={Roe, Rick}, year={2020}}
"""
ABS = '<blockquote class="abstract mathjax">Code is at <a href="https://github.com/{repo}">x</a>.</blockquote>'
BERT_HTML = ('<div class="ltx_document"><p>based on the original implementation described in Vaswani et al. '
             'and released in the tensor2tensor library<a href="https://github.com/tensorflow/tensor2tensor">'
             'https://github.com/tensorflow/tensor2tensor</a>. The code and pre-trained models are available at '
             '<a href="https://github.com/google-research/bert">https://github.com/google-research/bert</a>. '
             'Rendered by <a href="https://github.com/dginev/ar5iv">ar5iv</a></p></div>')
REPOS = {
    "KaimingHe/deep-residual-networks": ("User", 6775, "MIT", False),
    "someone/resnet": ("User", 87, "", False),
    "google-research/vision_transformer": ("Organization", 12000, "Apache-2.0", False),
    "google-research/bert": ("Organization", 40000, "Apache-2.0", True),
    "tensorflow/tensor2tensor": ("Organization", 16000, "Apache-2.0", True),
    "alice/slowfast-impl": ("User", 300, "MIT", False),
    "bob/slowfast": ("User", 250, "BSD-3-Clause", False),
    "carol/slowfast-old": ("User", 20, "", False),
}
SEARCH = {"Deep Residual Learning for Image Recognition": ["KaimingHe/deep-residual-networks", "someone/resnet"],
          "A Method Without Official Code At All": ["alice/slowfast-impl", "bob/slowfast", "carol/slowfast-old"]}
READMES = {"alice/slowfast-impl": "# SlowFast\nA partial port of the model definition only.",
           "bob/slowfast": "# slowfast\nReproduces the paper's Table 2 training and evaluation on Kinetics-400."}


def fake_fetch(url, params=None, as_json=True, tries=3, label="", headers=None):
    if "huggingface.co/api/papers/" in url:
        return {"authors": [{"name": "Kaiming He"}, {"name": "Xiangyu Zhang"}]} if "1512" in url else {}
    if "arxiv.org/abs/2010.11929" in url:
        return ABS.format(repo="google-research/vision_transformer")
    if "arxiv.org/abs/" in url:
        return '<blockquote class="abstract mathjax">No code here.</blockquote>'
    if "1810.04805" in url and "ar5iv" in url:
        return BERT_HTML
    if "semanticscholar" in url:
        return {"data": []}
    return None


def fake_github(path, params=None, raw=False):
    if path == "search/repositories":
        return {"items": [{"full_name": f} for f in SEARCH.get(params["q"].strip('"'), [])]}
    if path.startswith("users/"):
        return {"name": ""}
    if path.endswith("/readme"):
        return READMES.get(path[len("repos/"):-len("/readme")], "")
    full = path[len("repos/"):]
    if full not in REPOS:
        return None
    kind, stars, lic, archived = REPOS[full]
    return {"full_name": full, "html_url": f"https://github.com/{full}", "pushed_at": "2025-01-02T00:00:00Z",
            "stargazers_count": stars, "archived": archived, "fork": False,
            "license": {"spdx_id": lic, "name": lic} if lic else None,
            "owner": {"login": full.split("/")[0], "type": kind}, "description": ""}


@pytest.fixture
def fakes(monkeypatch):
    monkeypatch.setattr(sources, "fetch", fake_fetch)
    monkeypatch.setattr(find, "github", fake_github)
    monkeypatch.setattr(find.search, "arxiv_search", lambda q, n=3: [])
    sources.UNAVAILABLE.clear()


def make(tmp_path, baselines=None):
    paper = tmp_path / "paper"
    (paper / "workflow").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(paper)], check=True)
    (paper / "main.tex").write_text(TEX)
    (paper / "refs.bib").write_text(BIB)
    if baselines is not None:
        (paper / "workflow" / "baselines.csv").write_text(baselines)
    return paper


def table(paper):
    with open(paper / "workflow" / "baseline_code.csv", encoding="utf-8") as f:
        return {r["baseline"]: r for r in csv.DictReader(f)}


def test_paper_baselines_resolve_to_their_official_repositories(tmp_path, fakes):
    paper = make(tmp_path)
    with script_roles({wf.chooser: []}) as calls:
        out = json.loads(baseline_code(str(paper)))
    assert not calls[wf.chooser]
    assert out["origin"] == "paper" and out["status"] == "PASS" and out["official"] == 3
    rows = table(paper)
    assert list(rows) == ["ResNet-152", "ViT-H/14", "BERT-Large"]           # Some Related Work is not a baseline
    assert rows["ResNet-152"]["repo"] == "https://github.com/KaimingHe/deep-residual-networks"
    assert "所有者 KaimingHe 是作者 Kaiming He" in rows["ResNet-152"]["evidence"]
    assert rows["ViT-H/14"]["repo"].endswith("google-research/vision_transformer")
    assert "摘要链接" in rows["ViT-H/14"]["evidence"]
    bert = rows["BERT-Large"]
    assert bert["repo"].endswith("google-research/bert") and bert["official"] == "yes"
    assert "The code and pre-trained models are available at" in bert["evidence"]
    assert bert["archived"] == "yes" and bert["license"] == "Apache-2.0" and bert["stars"] == "40000"
    report = open(out["report"], encoding="utf-8").read()
    assert "基线代码" in report and "仓库已归档" in report
    edits = json.load(open(out["edits"], encoding="utf-8"))["edits"]
    assert len(edits) == 1 and edits[0]["kind"] == "create"
    assert not (paper / "workflow" / "baselines.csv").exists()
    assert apply_edit(str(paper), edits[0]) and not apply_edit(str(paper), edits[0])
    made = list(csv.DictReader(open(paper / "workflow" / "baselines.csv", encoding="utf-8")))
    assert made[0]["source"] == "he2016resnet" and made[2]["repo"].endswith("google-research/bert")
    run = json.load(open(out["run"], encoding="utf-8"))
    assert [s["stage"] for s in run["stages"]] == ["baselines", "find ResNet-152", "find ViT-H/14",
                                                   "find BERT-Large", "report"]


CSV = """name,source,setting,reported,reproduced,tolerance,status,log
baseline,bibkey or repository URL,dataset and configuration,0.0,,0.5,planned,
ResNet,he2016resnet,ImageNet,19.38,,0.5,planned,
SlowFast,slowfast,Kinetics,77.0,,0.5,planned,
Mine,https://github.com/someone/resnet,ImageNet,1.0,,0.5,planned,
Ghost,A title nobody wrote code for,x,1.0,,0.5,planned,
"""


def test_close_unofficial_candidates_go_to_the_chooser_and_edits_fill_the_repo_column(tmp_path, fakes):
    paper = make(tmp_path, CSV)
    pick = {"choice": "bob/slowfast", "quote": "Reproduces the paper's Table 2 training and evaluation",
            "reason": "完整复现训练和评估"}
    with script_roles({wf.chooser: [pick]}) as calls:
        out = json.loads(baseline_code(str(paper)))
    assert len(calls[wf.chooser]) == 1
    sent = calls[wf.chooser][0].text
    assert "alice/slowfast-impl" in sent and "bob/slowfast" in sent and "carol/slowfast-old" not in sent
    assert out["origin"] == "baselines.csv" and out["status"] == "PASS_WITH_NOTES"
    rows = table(paper)
    assert list(rows) == ["ResNet", "SlowFast", "Mine", "Ghost"]             # the template example is skipped
    assert rows["ResNet"]["official"] == "yes"
    assert rows["SlowFast"]["repo"].endswith("bob/slowfast") and rows["SlowFast"]["official"] == "no"
    assert "选择者读 README 后选定" in rows["SlowFast"]["evidence"]
    assert rows["Mine"]["repo"].endswith("someone/resnet") and "baselines.csv 写明" in rows["Mine"]["evidence"]
    assert rows["Ghost"]["repo"] == "" and out["missing"] == ["Ghost"]
    before = (paper / "workflow" / "baselines.csv").read_text()
    edits = json.load(open(out["edits"], encoding="utf-8"))["edits"]
    assert [e["section"] for e in edits] == ["header", "ResNet", "SlowFast", "Mine"]
    assert (paper / "workflow" / "baselines.csv").read_text() == before        # nothing written silently
    assert all(apply_edit(str(paper), e) for e in edits)
    after = {r["name"]: r for r in csv.DictReader(open(paper / "workflow" / "baselines.csv", encoding="utf-8"))}
    assert after["ResNet"]["repo"].endswith("KaimingHe/deep-residual-networks")
    assert after["ResNet"]["reported"] == "19.38" and after["Ghost"]["repo"] in ("", None)
    with script_roles({wf.chooser: [pick]}):
        again = json.loads(baseline_code(str(paper)))
    assert json.load(open(again["edits"], encoding="utf-8"))["edits"] == []   # the column already agrees


def test_a_choice_outside_the_candidates_or_without_its_quote_falls_back_to_stars(tmp_path, fakes):
    paper = make(tmp_path, "name,source\nSlowFast,slowfast\n")
    for reply in ({"choice": "evil/repo", "quote": "x" * 20, "reason": "?"},
                  {"choice": "bob/slowfast", "quote": "this sentence is not in the readme", "reason": "?"}):
        with script_roles({wf.chooser: [reply]}):
            json.loads(baseline_code(str(paper)))
        row = table(paper)["SlowFast"]
        assert row["repo"].endswith("alice/slowfast-impl") and "选择者的回答不成立" in row["evidence"]
    with script_roles({wf.chooser: [{"choice": "", "quote": "", "reason": "都只是模型定义"}]}):
        out = json.loads(baseline_code(str(paper)))
    assert table(paper)["SlowFast"]["repo"] == "" and out["missing"] == ["SlowFast"]


def test_github_down_is_a_note_not_a_crash(tmp_path, monkeypatch, fakes):
    def down(path, params=None, raw=False):
        sources.UNAVAILABLE.add("GitHub")
        return None
    monkeypatch.setattr(find, "github", down)
    paper = make(tmp_path)
    with script_roles({wf.chooser: []}):
        out = json.loads(baseline_code(str(paper)))
    assert out["status"] == "PASS_WITH_NOTES" and out["unavailable_sources"] == ["GitHub"]
    rows = table(paper)
    assert rows["ViT-H/14"]["repo"].endswith("google-research/vision_transformer")   # the abstract still links it
    assert rows["ViT-H/14"]["stars"] == "" and rows["ResNet-152"]["repo"] == ""
    assert "GitHub 这次没有查到" in open(out["report"], encoding="utf-8").read()


def test_no_baselines_raises(tmp_path, fakes):
    paper = make(tmp_path)
    (paper / "main.tex").write_text(r"\documentclass{article}\begin{document}\section{Intro}x\end{document}")
    with pytest.raises(ValueError, match="no baseline"):
        baseline_code(str(paper))


def test_links_owners_and_release_sentences():
    assert find.links('see https://github.com/a-b/c_d.git, and github.com/dginev/ar5iv and '
                      'github.com/a-b/c_d/tree/main') == ["a-b/c_d"]
    assert find.owner_match("KaimingHe", "", ["Kaiming He"], []) == "Kaiming He"
    assert find.owner_match("giffmana", "", ["Lucas Beyer"], ["giffmana"]) == "giffmana"
    assert find.owner_match("lb123", "Lucas Beyer", ["Lucas Beyer"], []) == "Lucas Beyer"
    assert find.owner_match("google-research", "", ["Jacob Devlin"], []) == ""
    assert find.RELEASE.search("Our code is publicly available at")
    assert find.RELEASE.search("We release our implementation")
    assert find.RELEASE.search("The source-code necessary to replicate our CIFAR-10 experiments is available at:")
    assert not find.RELEASE.search("the original implementation described in Vaswani et al. and released in")
    assert find.repo_url("https://github.com/x/y") == "x/y" and find.repo_url("1512.03385") == ""
    assert find.arxiv_url("https://arxiv.org/abs/2010.11929v2") == "2010.11929"
