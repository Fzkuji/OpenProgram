"""caption_check with the captioner scripted; plotting scripts read by AST, never run."""

import json
import subprocess
from pathlib import Path

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.diagrams import captions as wf
from auto_research.tasks.proofread import apply_edit

MAIN = r"""\documentclass{article}
\begin{document}
\section{Results}
Figure~\ref{fig:loss} shows the loss. The tuned schedule reaches 0.0102 against 0.0137.
\input{tables}

\begin{figure}
\includegraphics{figures/loss.png}
\caption{Validation loss of the linear probe over training epochs for both schedules.}
\label{fig:loss}
\end{figure}
\begin{figure}\centering\includegraphics[width=.5\linewidth]{figures/ablation}\caption{MSE of each SGD variant.}\end{figure}
\begin{figure}
\includegraphics{figures/raw.jpg}
\end{figure}
\bibliography{refs}
\begin{figure}\includegraphics{figures/curve.png}\caption{This figure shows the y-axis loss against the x-axis time \cite{he2016}.}\label{fig:curve}\end{figure}
\end{document}
"""
TABLES = r"""\begin{table}
\label{tab:main}
\caption{Probe results with the default and tuned schedules on the synthetic benchmark (SGD).}
\begin{tabular}{lc} Schedule & Loss \\ default & 0.0137 \\ tuned & 0.0102 \end{tabular}
\end{table}
"""
BIB = "@article{he2016, title={Deep Residual Learning}, author={He, Kaiming}, year={2016}}\n"
PLOT = """import matplotlib.pyplot as plt
plt.rcParams["font.size"] = 6
plt.rcParams.update({"legend.fontsize": 5, "axes.labelsize": 9})
fig, ax = plt.subplots()
ax.plot([1, 2], [3, 4])
ax.set_xlabel("Training time")
ax.text(0, 0, "x", fontsize=5.5)
fig.savefig("../../figures/loss.png", dpi=300)
"""
STYLED = """import matplotlib.pyplot as plt
import scienceplots
plt.style.use(["science", "no-latex"])
fig, ax = plt.subplots()
ax.bar([1, 2], [3, 4])
ax.set(xlabel="Variant", ylabel="Error (MSE)")
fig.savefig("ablation.pdf")
"""


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "workflow" / "figures").mkdir(parents=True)
    (repo / "figures").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "main.tex").write_text(MAIN)
    (repo / "tables.tex").write_text(TABLES)
    (repo / "refs.bib").write_text(BIB)
    for name in ("loss.png", "raw.jpg", "curve.png"):
        (repo / "figures" / name).write_bytes(b"\x89PNG fake")
    (repo / "workflow" / "figures" / "plot_loss.py").write_text(PLOT)
    (repo / "workflow" / "figures" / "plot_ablation.py").write_text(STYLED)
    (repo / "workflow" / "results.tsv").write_text("commit\tmetric\tstatus\nabc\t0.013727\tkeep\n")
    (repo / "workflow" / "evidence.csv").write_text("id,claim,statement\nE1,C1,Tuned is 26% better\n")
    return repo


def judgment(rewrite="", standalone=True, problem=""):
    return {"what_shown": True, "how_to_read": standalone, "takeaway": standalone, "standalone": standalone,
            "problem": problem, "rewrite": rewrite}


REPLIES = [
    judgment(),                                                                                     # Table 1
    judgment("Validation loss (MSE) of the probe per epoch for the default and tuned schedules; lower is "
             "better. The tuned schedule ends at 0.0102 against 0.0137.", False, "没说哪个更好"),     # Figure 1
    judgment("Mean squared error (MSE) of each SGD variant, 99.9 for the best.", False, "缩写没定义"),  # Figure 2
    judgment("Raw samples of the benchmark.", False, "没有图注"),                                    # Figure 3
    judgment("Loss against time, from \\cite{nobody2020}.", False, "填充开头"),                        # Figure 4
]


def run(paper):
    with script_roles({wf.captioner: list(REPLIES)}) as calls:
        out = json.loads(wf.caption_check(str(paper)))
    return out, calls


def test_floats_are_numbered_and_found_after_the_bibliography(paper):
    text = wf.document((paper / "main.tex").read_text().replace("\\input{tables}", TABLES))
    items = wf.floats(text)
    assert [f["name"] for f in items] == ["Table 1", "Figure 1", "Figure 2", "Figure 3", "Figure 4"]
    assert items[0]["label_before_caption"] and items[2]["graphics"] == ["figures/ablation"]
    assert items[2]["command"] == "\\caption{MSE of each SGD variant.}" and items[3]["caption"] == ""
    assert "Figure~" in wf.blank_floats(text) and "\\includegraphics" not in wf.blank_floats(text)


def test_code_checks_judgments_scripts_and_edits(paper):
    out, calls = run(paper)
    assert out["status"] == "FAIL"                                   # Figure 3 has no caption
    assert out["floats"] == 5 and out["not_standalone"] == 4 and out["scripts"] == 2
    folder = Path(out["report"]).parent
    assert folder.parent == paper / "drafts" / "caption_check"
    record = json.loads((folder / "captions.json").read_text())
    by = {f["name"]: f for f in record["floats"]}
    checks = {n: {x["check"] for x in f["findings"]} for n, f in by.items()}
    assert checks["Figure 1"] == set()
    assert {"label", "length", "abbreviation"} <= checks["Figure 2"]
    fig2 = " ".join(x["message"] for x in by["Figure 2"]["findings"])
    assert "never defined: MSE " in fig2 and "defined in the text but not in the caption: SGD" in fig2
    assert "caption" in checks["Figure 3"]
    assert {"reference", "first_sentence", "units"} <= checks["Figure 4"]
    assert {"label_order", "reference"} <= checks["Table 1"]
    # rewrites: grounded numbers kept, a number not in the paper and an unknown key dropped
    assert by["Figure 1"]["judgment"]["rewrite"].endswith("0.0102 against 0.0137.")
    assert "99.9" in by["Figure 2"]["judgment"]["dropped"] and not by["Figure 2"]["judgment"]["rewrite"]
    assert "nobody2020" in by["Figure 4"]["judgment"]["dropped"]
    # the captioner saw the figure images and the paragraph that cites Figure 1
    first = calls[wf.captioner][1]
    assert any(b.get("type") == "image" for b in first.blocks) and "shows the loss" in first.text
    assert "takeaway" in first.text
    table = calls[wf.captioner][0]
    assert not any(b.get("type") == "image" for b in table.blocks) and "tuned & 0.0102" in table.text
    # scripts
    scripts = {p["script"]: p for p in record["scripts"]}
    loss = scripts["workflow/figures/plot_loss.py"]
    assert loss["figures"] == ["Figure 1"] and not loss["science"]
    messages = " ".join(x["message"] for x in loss["findings"])
    assert "font.size = 6 pt" in messages and "legend.fontsize = 5 pt" in messages and "fontsize=) = 5.5" in messages
    assert "axes.labelsize" not in messages
    assert "no y-axis label" in messages and "line plot saved as raster" in messages
    assert "\"Training time\" names a quantity without its unit" in messages
    styled = scripts["workflow/figures/plot_ablation.py"]
    assert styled["findings"] == [] and styled["science"] and styled["figures"] == ["Figure 2"]
    # edits in paper_proofread's format
    edits = json.loads((folder / "edits.json").read_text())
    assert edits["root"] == str(paper)
    kinds = {e["section"]: e for e in edits["edits"]}
    assert kinds["Figure 1"]["kind"] == "caption" and "\\label" not in kinds["Figure 1"]["replacement"]
    assert kinds["Figure 2"]["kind"] == "label"
    assert kinds["Figure 2"]["replacement"] == "\\caption{MSE of each SGD variant.}\\label{fig:ablation}"
    assert kinds["Figure 3"]["quote"] == "\\includegraphics{figures/raw.jpg}"
    assert "\\caption{Raw samples of the benchmark.}\\label{fig:raw}" in kinds["Figure 3"]["replacement"]
    assert "Table 1" not in kinds
    assert apply_edit(str(paper), kinds["Figure 2"])
    assert "\\label{fig:ablation}" in (paper / "main.tex").read_text()
    report = (folder / "report.md").read_text()
    assert "# 图表标题检查" in report and "SciencePlots" in report and "没有找到画这张图的脚本" in report
    trail = json.loads(Path(out["run"]).read_text())
    assert [s["stage"] for s in trail["stages"]][:3] == ["read", "check", "judge Table 1"]
    assert [s["stage"] for s in trail["stages"]][-3:] == ["ground", "scripts", "report"]


def test_without_image_input_the_captioner_reads_text_only(paper, monkeypatch):
    monkeypatch.setattr(wf, "image_input", lambda role: False)
    out, calls = run(paper)
    assert not any(b.get("type") == "image" for c in calls[wf.captioner] for b in c.blocks)
    assert "image input" in Path(out["report"]).read_text()


def test_a_paper_without_floats_raises(paper):
    (paper / "main.tex").write_text("\\documentclass{article}\\begin{document}Text.\\end{document}")
    with pytest.raises(ValueError, match="no figure or table"):
        wf.caption_check(str(paper))


def test_script_reader_on_its_own():
    rec = wf.read_script("import matplotlib.pyplot as plt\nplt.rc('font', size=6.5)\nplt.imshow([[1]])\n"
                         "plt.savefig('m.png')\n", "s.py")
    assert [x["check"] for x in rec["findings"]] == ["font"]       # imshow alone needs no axis labels
    assert wf.read_script("def (:", "bad.py")["findings"][0]["check"] == "syntax"


def test_a_rewrite_that_describes_figure_defects_is_dropped():
    f = {"caption": "Loss.", "judgment": {"rewrite": "Loss per epoch; the axes are unlabeled.", "dropped": ""}}
    wf.ground_rewrite(f, "Loss per epoch", {})
    assert not f["judgment"]["rewrite"] and "unlabeled" in f["judgment"]["dropped"]
