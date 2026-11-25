"""source_independence with OpenAlex replaced by a fake fetch: no network."""

import csv
import json
from pathlib import Path

import pytest

from auto_research.tasks.citations import sources
from auto_research.tasks.independence import source_independence
from auto_research.tasks.independence.audit import cluster, paper_datasets, stating_cites

BIB = r"""@inproceedings{he2016resnet, title={Deep Residual Learning for Image Recognition},
  author={He, Kaiming and Zhang, Xiangyu}, doi={10.1109/cvpr.2016.90}, year={2016}}
@inproceedings{he2016identity, title={Identity Mappings in Deep Residual Networks},
  author={He, Kaiming and Zhang, Xiangyu}, eprint={1603.05027}, archivePrefix={arXiv}, year={2016}}
@inproceedings{vit, title={An Image is Worth 16x16 Words}, author={Dosovitskiy, Alexey}, year={2021}}
@inproceedings{densenet, title={Densely Connected Convolutional Networks}, author={Huang, Gao}, year={2017}}
@article{lost, title={A Work Nobody Indexed}, author={Nobody, A.}, year={2020}}
"""
TEX = r"""\documentclass{article}
\begin{document}
\begin{abstract}
Residual connections ease the training of very deep networks on ImageNet.
\end{abstract}
\section{Introduction}
Residual connections make very deep convolutional networks trainable \cite{he2016resnet,he2016identity}.
Attention models need large data \cite{vit}. We train on the CIFAR-10 dataset and the FooBench-2 benchmark.
\bibliography{refs}
\end{document}
"""
CLAIMS = """# Claims
Approved: yes

C1: Residual connections make very deep convolutional networks trainable.
RQ: RQ1
C2: Deeper residual networks keep improving accuracy.
RQ: RQ2
"""


def author(n, name, inst="I1"):
    return {"author": {"id": f"https://openalex.org/A{n}", "display_name": name},
            "institutions": [{"id": f"https://openalex.org/{inst}", "display_name": f"Lab {inst}"}]}


def work(n, title, authors, refs=(), text="", cited=10):
    words = {}
    for i, w in enumerate(text.split()):
        words.setdefault(w, []).append(i)
    return {"id": f"https://openalex.org/W{n}", "title": title, "cited_by_count": cited,
            "authorships": authors, "referenced_works": [f"https://openalex.org/W{r}" for r in refs],
            "abstract_inverted_index": words or None}


HE, ZHANG = author(1, "Kaiming He", "I9"), author(2, "Xiangyu Zhang", "I9")
RESNET = work(1, "Deep Residual Learning for Image Recognition", [HE, ZHANG], text="We train on ImageNet and CIFAR-10.")
IDENTITY = work(2, "Identity Mappings in Deep Residual Networks", [HE, ZHANG], refs=[1], text="Results on CIFAR-10.")
VIT = work(3, "An Image is Worth 16x16 Words", [author(3, "Alexey Dosovitskiy", "I2")], text="Pretrained on JFT-300M.")
DENSE = work(4, "Densely Connected Convolutional Networks", [author(4, "Gao Huang", "I3")], text="On CIFAR-10 and SVHN.")
BY_DOI = {"10.1109/cvpr.2016.90": RESNET}
BY_TITLE = {w["title"]: w for w in (IDENTITY, VIT, DENSE)}


def offline(monkeypatch, fail=False):
    calls = []

    def fetch(url, params=None, as_json=True, tries=3, label="", headers=None):
        params = params or {}
        calls.append((url, params.get("search")))
        if fail:
            sources.UNAVAILABLE.add(label)
            return None
        if url.startswith("https://api.openalex.org/works/doi:"):
            return BY_DOI.get(url.split("doi:", 1)[1])
        if url == "https://api.openalex.org/works" and "search" in params:
            hit = BY_TITLE.get(params["search"])
            return {"results": [hit] if hit else []}
        raise AssertionError(url)

    monkeypatch.setattr(sources, "fetch", fetch)
    return calls


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    (repo / "main.tex").write_text(TEX)
    (repo / "refs.bib").write_text(BIB)
    (repo / "workflow" / "claims.md").write_text(CLAIMS)
    return repo


def support(repo, rows):
    lines = ["claim,work,stance,study,quote,note"] + [f"{c},{k},{s},empirical,quoted text," for c, k, s in rows]
    (repo / "workflow" / "claim_support.csv").write_text("\n".join(lines) + "\n")


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_same_group_counts_once(paper, monkeypatch):
    support(paper, [("C1", "he2016resnet", "supports"), ("C1", "he2016identity", "supports"),
                    ("C1", "densenet", "supports"), ("C1", "lost", "supports"), ("C1", "vit", "related"),
                    ("C2", "he2016resnet", "supports"), ("C2", "he2016identity", "supports"),
                    ("C2", "ghost", "supports")])
    offline(monkeypatch)
    out = json.loads(source_independence(str(paper), out_dir=str(paper / "out")))

    assert out["status"] == "PASS_WITH_NOTES"
    by = {c["claim"]: c for c in out["claims"]}
    assert (by["C1"]["works"], by["C1"]["groups"], by["C1"]["flag"]) == (4, 2, "")
    assert (by["C2"]["works"], by["C2"]["groups"], by["C2"]["flag"]) == (3, 1, "one group")
    assert out["single_group"] == ["C2"]
    assert out["unresolved"] == {"ghost": "not in the .bib files", "lost": "not found in OpenAlex"}
    assert out["origin"] == {"C1": "claim_support", "C2": "claim_support"}

    rows = read_csv(out["csv"])
    he = [r for r in rows if r["claim"] == "C2" and r["work"] == "he2016identity"][0]
    assert he["group"] == "G2" and he["linked_to"] == "he2016resnet"
    assert he["reason"] == "self-citation: Kaiming He, Xiangyu Zhang"
    assert he["institutions"] == "Lab I9" and he["datasets"] == "CIFAR-10"
    dense = [r for r in rows if r["work"] == "densenet"][0]
    # CIFAR-10 is named by all three abstracts; without by_dataset it is listed, not merged
    assert dense["group"] == "G1" and "shared dataset: CIFAR-10" in dense["reason"]
    assert [r["resolved"] for r in rows if r["work"] == "ghost"] == ["not in the .bib files"]

    text = Path(out["report"]).read_text(encoding="utf-8")
    assert "共同作者且互相引用" in text and "支持文献都来自同一组" in text and "只提示，不合并" in text
    run = json.loads(Path(out["run"]).read_text())
    assert [s["stage"] for s in run["stages"]] == ["read", "resolve", "cluster", "report"]


def test_shared_dataset_merges_when_asked(paper, monkeypatch):
    support(paper, [("C1", "he2016resnet", "supports"), ("C1", "densenet", "supports"),
                    ("C2", "he2016resnet", "supports"), ("C2", "vit", "supports")])
    offline(monkeypatch)
    assert json.loads(source_independence(str(paper), out_dir=str(paper / "out")))["status"] == "PASS"
    out = json.loads(source_independence(str(paper), by_dataset="yes", out_dir=str(paper / "out")))
    assert out["status"] == "PASS_WITH_NOTES"
    by = {c["claim"]: c["groups"] for c in out["claims"]}
    assert by == {"C1": 1, "C2": 2}     # CIFAR-10 joins ResNet and DenseNet; ViT names no dataset of the paper


def test_falls_back_to_the_sentences_that_state_the_claim(paper, monkeypatch):
    calls = offline(monkeypatch)
    out = json.loads(source_independence(str(paper), out_dir=str(paper / "out")))
    assert out["origin"] == {"C1": "paper", "C2": "paper"}
    by = {c["claim"]: c for c in out["claims"]}
    assert (by["C1"]["works"], by["C1"]["groups"], by["C1"]["flag"]) == (2, 1, "one group")
    assert (by["C2"]["works"], by["C2"]["flag"]) == (0, "no works")
    # arXiv entries are found by title search, DOIs directly
    assert ("https://api.openalex.org/works", "Identity Mappings in Deep Residual Networks") in calls
    assert ("https://api.openalex.org/works/doi:10.1109/cvpr.2016.90", None) in calls


def test_a_failed_source_is_named_not_raised(paper, monkeypatch):
    support(paper, [("C1", "he2016resnet", "supports"), ("C1", "vit", "supports")])
    offline(monkeypatch, fail=True)
    out = json.loads(source_independence(str(paper), out_dir=str(paper / "out")))
    assert out["status"] == "PASS_WITH_NOTES"
    assert out["unavailable_sources"] == ["OpenAlex"]
    assert set(out["unresolved"]) == {"he2016resnet", "vit"}


def test_chains_join_transitively():
    def w(oid, authors, refs=()):
        return {"openalex": oid, "title": oid, "authors": authors, "refs": set(refs), "datasets": [],
                "institutions": {}}
    works = {"a": w("W1", {"A1": "X"}), "b": w("W2", {"A1": "X", "A2": "Y"}), "c": w("W3", {"A2": "Y"}),
             "d": w("W4", {"A9": "Z"}), "e": w("W1", {"A1": "X"})}
    groups, links = cluster(works, False)
    assert groups["a"] == groups["b"] == groups["c"] == groups["e"] != groups["d"]
    assert ("a", "e", "same work", ["W1"]) in links


def test_sentence_matching_and_paper_datasets():
    sentences = [r"Residual connections make very deep convolutional networks trainable \cite{x,y}.",
                 r"Convolutional networks are popular \cite{z}."]
    assert stating_cites("Residual connections make very deep convolutional networks trainable.", sentences) == ["x", "y"]
    assert paper_datasets(TEX) == ["ImageNet", "CIFAR-10", "FooBench-2"]


def test_no_claims_raise(tmp_path):
    (tmp_path / "workflow").mkdir()
    with pytest.raises(ValueError):
        source_independence(str(tmp_path))
