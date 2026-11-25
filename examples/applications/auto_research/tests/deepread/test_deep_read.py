"""deep_read with the downloads replaced by an in-memory arXiv source and the roles scripted."""

import gzip
import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.citations import sources
from auto_research.tasks.deepread import history, source, structure
from auto_research.tasks.deepread import workflow as wf

MAIN = r"""\documentclass{article}
\newcommand{\ours}{SparseNet}
\newcommand{\vect}[1]{\mathbf{#1}}
\title{\ours: Sparse Attention for Images}
\begin{document}
\maketitle
\begin{abstract}
We present \ours, a sparse attention model that keeps accuracy at a tenth of the compute.
\end{abstract}
\input{intro}
\section{Method}\label{sec:method}
Each layer computes attention as in Transformers \citep{vaswani2017,bit2020}. The scores are
\begin{equation}
a = \vect{q}^\top \vect{k} \label{eq:score}
\end{equation}
and the output is a weighted sum of the values.
\begin{align}
s &= \mathrm{softmax}(a) \label{eq:soft} \\
o &= s \vect{v} \nonumber \\
o' &= \mathrm{softmax}(\vect{q}^\top \vect{k}) \vect{v} \label{eq:out}
\end{align}
Substituting Eq.~\eqref{eq:score} into Eq.~\eqref{eq:soft} gives Eq.~\ref{eq:out} \cite{bit2020}.

\begin{figure}[t]
\includegraphics{x.pdf}
\caption{The \ours{} layer, adapted from \citet{bit2020}.}\label{fig:layer}
\end{figure}

\paragraph{Cost} The sparse pattern keeps only 10\% of the pairs, as Figure~\ref{fig:layer} shows \cite{newwork}.
\bibliography{refs}
\appendix
\section{Proofs}
\begin{equation*}
x = y
\end{equation*}
The appendix restates Section~\ref{sec:method} briefly for completeness here.
\end{document}
"""

INTRO = r"""\section{Introduction}
Attention scales quadratically with the number of patches \cite{bit2020,newwork}.
We ask whether most pairs can be skipped without losing accuracy.

Our answer is yes for natural images.
"""

BIB = r"""@inproceedings{vaswani2017, title={Attention is All you Need}, author={Vaswani, Ashish}, booktitle={NIPS}, year={2017}}
@inproceedings{bit2020, title={Big Transfer (BiT): General Visual Representation Learning}, author={Kolesnikov, A.}, booktitle={ECCV}, year={2020}}
@article{newwork, title={A Paper Nobody Here Has Seen}, author={Doe, Jane}, journal={arXiv}, year={2021}, eprint={2101.00001}}
"""


def tarball(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


FETCHED: list[str] = []
ARCHIVE = {"main.tex": MAIN, "intro.tex": INTRO, "paper.bib": BIB, "image.png": "binary"}


@pytest.fixture
def project(tmp_path, monkeypatch):
    repo = tmp_path / "project"
    (repo / "workflow" / "surveys" / "attention" / "20260101-000000").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "main.tex").write_text("\\documentclass{article}\\begin{document}Probe.\\end{document}\n")
    (repo / "refs.bib").write_text("@inproceedings{vas17, title={Attention Is All You Need}, author={Vaswani, A.}, year={2017}}\n")
    (repo / "workflow" / "surveys" / "attention" / "20260101-000000" / "survey.json").write_text(json.dumps(
        {"direction": "attention", "papers": [
            {"title": "Big Transfer (BiT): General Visual Representation Learning", "arxiv": "1912.11370",
             "doi": "", "decision": "core"},
            {"title": "A Paper Nobody Here Has Seen", "arxiv": "", "doi": "", "decision": "exclude"}]}))
    FETCHED.clear()
    calls = FETCHED

    def fake_bytes(url, label="", tries=3):
        calls.append(url)
        return tarball(ARCHIVE) if "e-print/2401.00001" in url else None
    monkeypatch.setattr(source, "fetch_bytes", fake_bytes)
    monkeypatch.setattr(sources, "fetch", lambda *a, **k: None)
    return repo


def document():
    files, _ = source.unpack(tarball(ARCHIVE))
    return structure.document({"kind": "latex", "files": files, "main": "main.tex",
                               "tex": source.flatten(files, "main.tex")})


def test_structure_of_the_latex_source():
    doc = document()
    assert doc["title"] == "SparseNet: Sparse Attention for Images"
    titles = [(s["number"], s["title"]) for s in doc["sections"]]
    assert titles == [("", "Abstract"), ("1", "Introduction"), ("2", "Method"), ("A", "Proofs")]
    texts = [p["text"] for p in doc["paragraphs"]]
    assert texts[0].startswith("We present SparseNet, a sparse attention model")
    method = next(t for t in texts if t.startswith("Each layer"))
    assert "[cite: vaswani2017, bit2020]" in method
    assert any("Substituting Eq. (1) into Eq. (2) gives Eq. 3" in t for t in texts)
    assert any(t.startswith("Figure 1: The SparseNet layer") for t in texts)
    assert any(t.startswith("Cost. The sparse pattern keeps only 10% of the pairs, as Figure 1 shows") for t in texts)
    assert any("restates Section 2 briefly" in t for t in texts)
    rows = [[(r["number"], r["label"]) for r in d["rows"]] for d in doc["displays"]]
    assert rows == [[("1", "eq:score")], [("2", "eq:soft"), ("", ""), ("3", "eq:out")], [("", "")]]
    refs = {r["key"]: r for r in doc["references"]}
    assert refs["bit2020"]["count"] == 4 and refs["vaswani2017"]["count"] == 1 and refs["newwork"]["count"] == 2
    assert refs["newwork"]["arxiv"] == "2101.00001" and refs["vaswani2017"]["n"] == 3


def test_equation_numbers_tags_and_macros():
    tex = (r"\begin{document}\section{A}Text one two three." "\n\n"
           r"\begin{align*}a&=b\\c&=d\end{align*}\begin{gather}x=1\tag{T}\\y=2\end{gather}"
           r"\begin{equation}\begin{split}u&=v\\&=w\end{split}\end{equation}\end{document}")
    doc = structure.document({"kind": "latex", "files": {}, "main": "m.tex", "tex": tex})
    assert [[r["number"] for r in d["rows"]] for d in doc["displays"]] == [["", ""], ["T", "1"], ["2"]]
    defs = structure.macros(r"\newcommand\eg{e.g.} \def\x#1{<#1>} \renewcommand{\y}[2]{skip}")
    assert structure.expand(r"say \eg{} and \x{z}", defs) == "say e.g. and <z>" and "y" not in defs


def test_parse_work_and_unpack():
    assert source.parse_work("2010.11929") == ("arxiv", "2010.11929")
    assert source.parse_work("https://arxiv.org/abs/2010.11929v2") == ("arxiv", "2010.11929")
    assert source.parse_work("arXiv:hep-th/9901001") == ("arxiv", "hep-th/9901001")
    assert source.parse_work("10.48550/arXiv.2010.11929") == ("arxiv", "2010.11929")
    assert source.parse_work("https://doi.org/10.1109/CVPR.2016.90") == ("doi", "10.1109/cvpr.2016.90")
    with pytest.raises(ValueError, match="not an arXiv id"):
        source.parse_work("attention paper")
    with pytest.raises(ValueError, match="does not exist"):
        source.parse_work("/nowhere/paper.pdf")
    files, pdf = source.unpack(gzip.compress(MAIN.encode()))
    assert list(files) == ["main.tex"] and pdf is None
    assert source.unpack(gzip.compress(b"%PDF-1.4 ...")) == ({}, b"%PDF-1.4 ...")
    files, _ = source.unpack(tarball(ARCHIVE))
    assert sorted(files) == ["intro.tex", "main.tex", "paper.bib"] and source.main_file(files) == "main.tex"


def test_pdf_document_counts_numeric_citations():
    text = ("Sparse Attention\n\nAbstract\n\nWe skip most pairs of patches [1, 2].\n\n1 Introduction\n\n"
            "Attention is quadratic [2-3] and costly [2].\n\n7\n\f References\n\n"
            "[1] A. Vaswani. Attention is all you need. In NIPS, 2017. [2] J. Doe. A paper nobody has seen. "
            "arXiv:2101.00001, 2021. [3] K. He. Deep residual learning for image recognition. In CVPR, 2016.")
    doc = structure.pdf_document({"text": text})
    assert [s["title"] for s in doc["sections"]] == ["Abstract", "1 Introduction"]
    refs = {r["n"]: r for r in doc["references"]}
    assert refs[2]["count"] == 3 and refs[1]["count"] == 1 and refs[3]["count"] == 1
    assert refs[2]["arxiv"] == "2101.00001" and refs[1]["title"] == "Attention is all you need"
    assert doc["displays"] == []


def replies(doc):
    by_section = {}
    for p in doc["paragraphs"]:
        by_section.setdefault(p["section"], []).append(p)
    sections, points = [], []
    for s in doc["sections"]:
        paras = by_section.get(s["id"], [])
        first = paras[0]
        sections.append({"id": s["id"], "summary": [
            {"text": f"{s['title']} 的要点", "paragraph": first["id"], "quote": " ".join(first["text"].split()[:6])}]})
        for p in paras:
            words = structure.strip_marks(p["text"]).split()
            points.append({"paragraph": p["id"], "point": "要点", "quote": " ".join(words[:5])})
    by_id_section = {p["id"]: p["section"] for p in doc["paragraphs"]}
    method = next(p for p in doc["paragraphs"] if p["text"].startswith("Each layer"))
    sections[2]["summary"].append({"text": "编造的说法", "paragraph": method["id"], "quote": "a quote the paper never had"})
    sections[2]["summary"].append({"text": "引文在别的段落", "paragraph": method["id"],
                                   "quote": "Our answer is yes for natural images"})
    for s in sections:
        s["points"] = [pt for pt in points if by_id_section[pt["paragraph"]] == s["id"]]
    read = {"sections": sections}
    overview = {"sentence": {"text": "稀疏注意力省算力", "paragraph": "P1",
                             "quote": "a sparse attention model that keeps accuracy"},
                "overview": [{"text": "问题", "paragraph": "P2", "quote": "Attention scales quadratically with the number"},
                             {"text": "方法", "paragraph": method["id"], "quote": "Each layer computes attention as in Transformers"},
                             {"text": "编造", "paragraph": "P1", "quote": "ten times faster on every benchmark"}]}
    derive = {"derivations": [
        {"display": "D2", "target": "(3)", "steps": [{"explain": "把式 1 代入式 2", "uses": ["1", "(2)"]},
                                                     {"explain": "再乘以 v", "uses": ["D1"]}]},
        {"display": "D2", "target": "2", "steps": [{"explain": "用了不存在的式子", "uses": ["9"]}]},
        {"display": "D1", "target": "1", "steps": [{"explain": "用了后面的式子", "uses": ["3"]}]}]}
    return read, overview, derive


def run(project, script=None, work="2401.00001"):
    doc = document()
    read, overview, derive = replies(doc)
    script = script or {wf.reader: [read], wf.condenser: [overview], wf.deriver: [derive]}
    with script_roles(script) as calls:
        out = json.loads(wf.deep_read(str(project), work))
    return out, calls, doc


def test_deep_read_levels_quotes_derivations_and_history(project):
    out, calls, doc = run(project)
    assert any("e-print/2401.00001" in u for u in FETCHED)
    assert "exactly one point per paragraph" in calls[wf.reader][0].text
    assert "[cite: vaswani2017, bit2020]" in calls[wf.reader][0].text
    assert "voids the whole derivation" in calls[wf.deriver][0].text
    folder = Path(out["report"]).parent
    assert folder.parent == project / "drafts" / "deep_read"
    record = json.loads((folder / "read.json").read_text())
    assert out["status"] == "PASS_WITH_NOTES" and out["source"] == "latex"
    assert out["points"] == out["paragraphs"] == len(doc["paragraphs"])
    method_section = doc["sections"][2]["id"]
    texts = [s["text"] for s in record["level3"][method_section]]
    assert "编造的说法" not in texts and "引文在别的段落" in texts
    moved = next(s for s in record["level3"][method_section] if s["text"] == "引文在别的段落")
    intro = next(p for p in doc["paragraphs"] if p["text"].startswith("Our answer"))
    assert moved["paragraph"] == intro["id"]                       # re-attributed to the paragraph holding it
    assert record["level1"]["paragraph"] == "P1" and [s["text"] for s in record["level2"]] == ["问题", "方法"]
    whys = [d["why"] for d in record["dropped"]]
    assert any("do not exist: 9" in w for w in whys) and any("do not come before 1: 3" in w for w in whys)
    assert [(d["display"], d["target"]) for d in record["derivations"]] == [("D2", "3")]
    assert record["derivations"][0]["steps"][0]["uses"] == ["1", "2"]
    refs = record["references"]
    assert [(r["key"], r["status"]) for r in refs] == [("newwork", "new"), ("bit2020", "read"), ("vaswani2017", "cited")]
    assert refs[2]["bib_key"] == "vas17" and refs[1]["read_in"] == ["survey attention"]
    page = (folder / "read.html").read_text()
    assert "<script src" not in page and "<link" not in page and page.count("<details") >= 8
    assert f'href="#{intro["id"]}"' in page and f'id="{intro["id"]}"' in page
    assert 'class="cite new" href="#ref-2"' in page and 'class="cite cited"' in page
    md = (folder / "read.md").read_text()
    assert "## 第 1 层：一句话" in md and "| 2 | 新 | 2 |" in md and "```latex" in md
    report = (folder / "report.md").read_text()
    assert "最值得补读的新文献" in report and "A Paper Nobody Here Has Seen" in report
    trail = json.loads(Path(out["run"]).read_text())
    assert [s["stage"] for s in trail["stages"]] == ["fetch", "structure", "history", "read", "condense", "derive",
                                                     "check", "report"]


def test_doi_goes_through_semantic_scholar_to_the_arxiv_source(project, monkeypatch):
    seen = []

    def fake_fetch(url, params=None, **kw):
        seen.append(url)
        return {"externalIds": {"ArXiv": "2401.00001"}} if "semanticscholar" in url else None
    monkeypatch.setattr(sources, "fetch", fake_fetch)
    out, _, _ = run(project, work="10.1000/sparse.2024")
    assert "DOI:10.1000/sparse.2024" in seen[0] and out["source"] == "latex"


def test_pdf_fallback_and_nothing_to_read(project, monkeypatch, tmp_path):
    def no_source(url, label="", tries=3):
        if label:
            sources.UNAVAILABLE.add(label)
        return b"%PDF-1.4 fake" if "/pdf/" in url else None
    monkeypatch.setattr(source, "fetch_bytes", no_source)
    monkeypatch.setattr(source, "pdf_text", lambda path: (
        "Sparse\n\nAbstract\n\nWe skip most attention pairs in images [1].\n\nReferences\n\n"
        "[1] A. Vaswani. Attention is all you need. In NIPS, 2017."))
    doc = structure.pdf_document({"text": source.pdf_text(None)})
    read = {"sections": [{"id": "S1", "summary": [{"text": "跳过", "paragraph": "P1", "quote": "We skip most attention pairs"}],
                          "points": [{"paragraph": "P1", "point": "跳过", "quote": "most attention pairs in images"}]}]}
    overview = {"sentence": {"text": "跳过", "paragraph": "P1", "quote": "We skip most attention pairs"},
                "overview": [{"text": "跳过", "paragraph": "P1", "quote": "We skip most attention pairs"}] * 3}
    with script_roles({wf.reader: [read], wf.condenser: [overview]}):
        out = json.loads(wf.deep_read(str(project), "2401.00002"))
    assert out["source"] == "pdf" and out["status"] == "PASS_WITH_NOTES" and out["derivations"] == 0
    assert out["references"] == {"new": 0, "read": 0, "cited": 1} and doc["paragraphs"][0]["cites"] == ["R1"]
    report = Path(out["report"]).read_text()
    assert "PDF" in report and "arXiv source" in report
    assert (Path(out["report"]).parent / "source.pdf").exists()
    monkeypatch.setattr(source, "fetch_bytes", lambda *a, **k: None)
    out = json.loads(wf.deep_read(str(project), "2401.00003"))
    assert out["status"] == "FAIL" and "could not be downloaded" in Path(out["report"]).read_text()


def test_reader_must_stay_in_its_part(project):
    doc = document()
    read, _, derive = replies(doc)
    chunk = wf.chunks(doc)[0]
    by_id = {p["id"]: p for p in doc["paragraphs"]}
    read["sections"][0]["points"][0]["paragraph"] = "P99"
    with pytest.raises(ValueError, match="not given"):
        wf.check_read(read, chunk, by_id, [])
    derive["derivations"][0]["display"] = "D9"
    with pytest.raises(ValueError, match="display it was not given"):
        wf.check_derivations(derive, ["D1", "D2"], wf.equation_positions(doc["displays"]), doc, [])
    with pytest.raises(ValueError, match="Git repository"):
        wf.deep_read(str(project / "workflow"), "2401.00001")


def test_history_reads_every_reading_file(project):
    (project / "refs.bib").write_text(
        "@article{a1, title={Alpha Paper on Probes}, year={2020}, eprint={2001.00001}}\n"
        "@article{b2, title={Beta Paper on Schedules}, year={2021}, doi={10.1/beta}}\n")
    wfolder = project / "workflow"
    (wfolder / "annotations.csv").write_text("key,page,quote,comment,color\na1,3,q,,\n")
    (wfolder / "watch.csv").write_text("id,title,url,date,claim,verdict,reason,handled\n"
                                       "2301.00009,Gamma Watched Paper,,2023-01-01,C1,related,,read it\n"
                                       "2301.00010,Delta Unhandled,,2023-01-01,C1,related,,\n")
    refs = [{"key": "x", "n": 1, "count": 1, "title": "Something else", "arxiv": "2001.00001", "doi": ""},
            {"key": "y", "n": 2, "count": 5, "title": "beta paper on schedules", "arxiv": "", "doi": ""},
            {"key": "z", "n": 3, "count": 2, "title": "", "arxiv": "2301.00009", "doi": ""},
            {"key": "w", "n": 4, "count": 9, "title": "Delta Unhandled", "arxiv": "", "doi": ""}]
    marked = {r["key"]: r for r in history.mark(refs, project)}
    assert marked["x"]["status"] == "cited" and marked["x"]["read_in"] == ["annotations.csv"]
    assert marked["y"]["bib_key"] == "b2" and marked["z"]["status"] == "read" and marked["w"]["status"] == "new"
    assert [r["key"] for r in history.ordered(list(marked.values()))] == ["w", "z", "y", "x"]


def test_a_source_that_only_wraps_the_pdf_is_read_as_the_pdf():
    wrapper = b"\\documentclass{article}\\usepackage{pdfpages}\\begin{document}\\includepdf[pages=1-last]{0_main.pdf}\\end{document}"
    pdf = b"%PDF-1.5\n% the paper\n"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in (("arxiv.tex", wrapper), ("0_main.pdf", pdf)):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    data = buf.getvalue()
    files, _ = source.unpack(data)
    tex = source.flatten(files, source.main_file(files))
    assert source.prose_words(tex) < source.MIN_PROSE
    assert source.included_pdf(data, tex) == pdf
    assert source.prose_words("\\begin{document}" + " word" * 50 + " $x+y$ \\cite{a}") == 50


def test_small_caps_headings_and_running_heads_from_pdftotext():
    assert structure.small_caps("A DAM : A M ETHOD FOR S TOCHASTIC O PTIMIZATION") == "ADAM : A METHOD FOR STOCHASTIC OPTIMIZATION"
    assert structure.small_caps("R EFERENCES") == "REFERENCES"
    assert structure.small_caps("We use A NEW idea") == "We use A NEW idea"
    text = ("Published as a conference paper at ICLR 2015\n\nA DAM : A M ETHOD FOR S TOCHASTIC O PTIMIZATION\n\n"
            "1 I NTRODUCTION\n\n" + "Stochastic gradient methods are used widely in practice today. " * 3 + "\n\n"
            "R EFERENCES\n\nBa, J. Something about optimisation. In ICML, 2014.\n")
    doc = structure.pdf_document({"text": text})
    assert doc["title"] == "ADAM : A METHOD FOR STOCHASTIC OPTIMIZATION"
    assert len(doc["references"]) == 1 and doc["paragraphs"]
