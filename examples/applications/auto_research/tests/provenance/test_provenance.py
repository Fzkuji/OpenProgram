"""writing_provenance on small Git repositories built with real git commands: an
author's commits, a commit with a Claude Co-Authored-By trailer, an Auto Research run's
commit, a commit applying a proofread edit, a paragraph pasted from a cached cited
work, a large insert that repeats nothing, an \\input file and uncommitted changes."""

import importlib
import json
import subprocess

import pytest

prov = importlib.import_module("auto_research.tasks.provenance")
workflow = importlib.import_module("auto_research.tasks.provenance.workflow")
paragraphs = importlib.import_module("auto_research.tasks.provenance.paragraphs")

AUTHOR = ("-c", "user.name=Ada Author", "-c", "user.email=ada@example.edu")
CLAUDE = "\n\nCo-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
AUTO = ("-c", "user.name=auto_research", "-c", "user.email=auto_research@localhost")

SOURCE = ("When deeper networks are able to start converging, a degradation problem has been exposed: with the "
          "network depth increasing, accuracy gets saturated and then degrades rapidly. Unexpectedly, such "
          "degradation is not caused by overfitting, and adding more layers to a suitably deep model leads to "
          "higher training error, as reported in earlier studies and thoroughly verified by our experiments. "
          "The degradation of training accuracy indicates that not all systems are similarly easy to optimize. "
          "Let us consider a shallower architecture and its deeper counterpart that adds more layers onto it. "
          "There exists a solution by construction to the deeper model: the added layers are identity mapping, "
          "and the other layers are copied from the learned shallower model. The existence of this constructed "
          "solution indicates that a deeper model should produce no higher training error than its shallower "
          "counterpart, yet current solvers fail to find it.")
OWN = ("Our ablation repeats every configuration with five random seeds and reports the mean and the standard "
       "deviation, because a single run hides how much of the difference between two schedules is noise. ")
BULK = ("We see three limits of this study. " + OWN +
        "The benchmark is synthetic, so the absolute numbers do not transfer to real features, and the "
        "comparison covers a single probe architecture only. A larger study would repeat the sweep on frozen "
        "features of several pretrained backbones, track how often the ranking between two backbones flips once "
        "each probe is tuned, and publish the full grid of runs so that others can check which settings were "
        "tried before the final schedule was chosen for the paper and how long each run took on our hardware.")

MAIN = r"""\documentclass{article}
\title{Schedules for Linear Probes}
\begin{document}
\maketitle
\begin{abstract}
Linear probes are cheap. The probe is evaluated by mean squared error on a held-out split.
\end{abstract}

\section{Introduction}
Linear probing measures representation quality \cite{he2016resnet}. We study its schedule.

\input{sections/method}

\begin{table}[t]
\begin{tabular}{lc}
Model & Error \\
ResNet & 19.38 \\
\end{tabular}
\caption{Numbers from prior work.}
\end{table}

\bibliographystyle{plain}
\bibliography{refs}
\end{document}
"""
METHOD = r"""\section{Method}
We train a linear model with SGD at a learning rate of $0.05$ % a comment
for ten epochs.
"""
RELATED = ("Prior work fixes one optimization recipe for the probe and reports its accuracy, treating the recipe as "
           "a detail of the protocol rather than a source of variance.")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def commit(repo, message, who=AUTHOR):
    git(repo, "add", "-A")
    git(repo, *who, "commit", "-q", "-m", message)


def edit(path, old, new):
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new, 1))


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "sections").mkdir(parents=True)
    git(repo, "init", "-q")
    (repo / "main.tex").write_text(MAIN)
    (repo / "sections" / "method.tex").write_text(METHOD)
    (repo / ".gitignore").write_text("drafts/\n")
    commit(repo, "First draft")
    main = repo / "main.tex"
    # An AI-co-authored paragraph, then the author changes two words of it.
    edit(main, r"\input{sections/method}", "\\section{Related Work}\n" + RELATED + "\n\n\\input{sections/method}")
    commit(repo, "Draft related work" + CLAUDE)
    edit(main, "treating the recipe", "taking the recipe")
    commit(repo, "Word choice")
    # An Auto Research run's commit (ablation identity) adds a sentence to the method.
    edit(repo / "sections" / "method.tex", "for ten epochs.", "for ten epochs.\nWithout weight decay the loss rises.")
    commit(repo, "ablation: weight_decay", who=AUTO)
    # A proofread run proposes an edit; the author applies it in a commit.
    write_json(repo / "drafts" / "paper_proofread" / "20260101-000000" / "edits.json",
               {"root": str(repo), "edits": [{"id": "E1", "file": "main.tex", "section": "Abstract",
                                               "kind": "active_voice",
                                               "quote": "The probe is evaluated by mean squared error",
                                               "replacement": "We evaluate the probe by mean squared error",
                                               "problem": "主动语态"}]})
    write_json(repo / "drafts" / "paper_proofread" / "20260101-000000" / "run.json", {"workflow": "paper_proofread"})
    edit(main, "The probe is evaluated by mean squared error", "We evaluate the probe by mean squared error")
    commit(repo, "Apply proofread edit E1")
    # One commit pastes 120+ words that text_overlap's cache holds for a cited work.
    cache = repo / "drafts" / "text_overlap" / "cache"
    cache.mkdir(parents=True)
    (cache / "1512.03385.txt").write_text("Introduction. " + SOURCE + " More text of the cited work follows here.")
    write_json(repo / "drafts" / "text_overlap" / "20260101-000000" / "overlap.json",
               {"sources": [{"id": "arXiv:1512.03385", "kind": "reference", "title": "Deep Residual Learning",
                             "file": "he2016resnet"}]})
    edit(main, r"\section{Related Work}", "\\section{Background}\n" + SOURCE + "\n\n\\section{Related Work}")
    commit(repo, "Background")
    # One commit adds 120+ words that repeat nothing on disk.
    edit(main, r"\bibliographystyle{plain}", "\\section{Discussion}\n" + BULK + "\n\n\\bibliographystyle{plain}")
    commit(repo, "Discussion")
    return repo


def run(repo, **kw):
    return json.loads(prov.writing_provenance(str(repo), **kw))


def record(result):
    return json.loads(open(result["json"], encoding="utf-8").read())


def para(rec, start_words):
    return next(p for p in rec["paragraphs"] if " ".join(s[1] for s in p["segments"]).startswith(start_words))


def test_each_kind_of_history_gets_its_class(paper):
    result = run(paper)
    assert result["status"] == "PASS"
    rec = record(result)
    related = para(rec, "Prior work fixes")
    assert related["counts"]["ai"] == len(paragraphs.words(RELATED)) - 1     # "taking" is the author's word
    assert related["counts"]["human"] == 1
    assert ["human", "taking"] in related["segments"]
    method = para(rec, "We train a linear model")
    assert method["file"] == "sections/method.tex" and method["section"] == "Method"
    assert method["counts"]["ai"] == len("Without weight decay the loss rises.".split())
    assert "Auto Research" in {t for c in method["commits"] for t in c["tools"]}
    abstract = para(rec, "Linear probes are cheap.")
    assert abstract["counts"]["ai_edit"] == 3                               # "We evaluate the" replaced "The probe is evaluated"
    assert rec["edits_applied"][0]["workflow"] == "paper_proofread" and rec["edits_applied"][0]["paragraphs"]
    background = para(rec, "When deeper networks")
    assert set(background["counts"]) == {"pasted"}
    item = background["inserts"][0]
    assert item["kind"] == "pasted" and item["source"] == "arXiv:1512.03385" and item["key"] == "he2016resnet"
    assert item["cited"] is False
    discussion = para(rec, "We see three limits")
    assert set(discussion["counts"]) == {"bulk"} and discussion["inserts"][0]["kind"] == "bulk"
    intro = para(rec, "Linear probing measures")
    assert set(intro["counts"]) == {"human"}


def test_tables_math_citations_and_comments_are_not_counted(paper):
    rec = record(run(paper))
    texts = [" ".join(s[1] for s in p["segments"]) for p in rec["paragraphs"]]
    assert not any("19.38" in t for t in texts)
    assert any(t.startswith("Numbers from prior work.") for t in texts)          # the caption stays
    method = para(rec, "We train a linear model")
    assert method["words"] == len("We train a linear model with SGD at a learning rate of for ten epochs.".split()) + 6
    assert "$0.05$" in " ".join(s[1] for s in method["segments"])
    assert not any("comment" in t for t in texts)
    intro = para(rec, "Linear probing measures")
    assert intro["words"] == 9 and "[he2016resnet]" in intro["segments"][0][1]


def test_shares_sections_and_disclosure_agree(paper):
    rec = record(run(paper))
    assert sum(rec["counts"].values()) == rec["words"] == sum(p["words"] for p in rec["paragraphs"])
    assert sum(s["words"] for s in rec["sections"]) == rec["words"]
    assert abs(sum(rec["shares"].values()) - 100) < 0.5
    en = rec["disclosure"]["en"]
    assert f"{rec['shares']['ai']}%" in en and f"{rec['shares']['ai_edit']}%" in en
    assert "Claude Opus 4.5" in rec["ai_tools"] and "Auto Research" in rec["ai_tools"]


def test_report_and_page(paper):
    result = run(paper)
    report = open(result["report"], encoding="utf-8").read()
    assert "# 写作溯源" in report and "AI 使用声明草稿" in report and "arXiv:1512.03385" in report
    assert "段内没有引用" in report and "不代表来源有问题" in report
    page = open(result["html"], encoding="utf-8").read()
    assert "<script src" not in page and "<link" not in page
    data = page.split('<script type="application/json" id="data">', 1)[1].split("</script>", 1)[0]
    assert json.loads(data)["words"] == record(result)["words"]
    run_json = json.loads(open(result["run"], encoding="utf-8").read())
    assert run_json["workflow"] == "writing_provenance"


def test_replay_shows_each_version_with_its_diff(paper):
    rec = record(run(paper))
    versions = para(rec, "Prior work fixes")["replay"]["versions"]
    assert [v["subject"] for v in versions] == ["Draft related work", "Word choice"]
    assert versions[0]["class"] == "ai" and versions[0]["diff"][0][0] == "ins"
    assert ["del", "treating"] in versions[1]["diff"] and ["ins", "taking"] in versions[1]["diff"]


def test_replay_keeps_thirty_versions(paper):
    method = paper / "sections" / "method.tex"
    for k in range(35):
        edit(method, "for ten epochs.", f"for ten epochs (v{k}).") if k == 0 else \
            edit(method, f"(v{k - 1})", f"(v{k})")
        commit(paper, f"tweak {k}")
    rec = record(run(paper))
    replay = para(rec, "We train a linear model")["replay"]
    assert len(replay["versions"]) == 30 and replay["omitted"] > 0
    assert replay["versions"][0]["subject"] == "First draft" and replay["versions"][-1]["subject"] == "tweak 34"


def test_uncommitted_changes_give_notes(paper):
    edit(paper / "main.tex", "We study its schedule.", "We study its schedule in detail.")
    result = run(paper)
    assert result["status"] == "PASS_WITH_NOTES" and result["uncommitted_files"] == ["main.tex"]
    rec = record(result)
    intro = para(rec, "Linear probing measures")
    assert intro["counts"]["uncommitted"] == 3 and intro["uncommitted"]          # "schedule in detail." is new
    assert intro["replay"]["versions"][-1]["class"] == "uncommitted"
    assert "未提交" in open(result["report"], encoding="utf-8").read()


def test_an_untracked_input_file_is_uncommitted(paper):
    edit(paper / "main.tex", r"\input{sections/method}", "\\input{sections/method}\n\\input{sections/extra}")
    (paper / "sections" / "extra.tex").write_text("A new paragraph nobody committed yet.\n")
    rec = record(run(paper))
    extra = para(rec, "A new paragraph")
    assert set(extra["counts"]) == {"uncommitted"} and rec["status"] == "PASS_WITH_NOTES"


def test_a_repository_without_ai_gets_a_plain_disclosure(tmp_path):
    repo = tmp_path / "p"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "main.tex").write_text("\\documentclass{article}\n\\begin{document}\nOnly the author wrote this.\n"
                                   "\\end{document}\n")
    commit(repo, "init")
    result = run(repo, out_dir=str(tmp_path / "out"))
    assert result["status"] == "PASS" and result["shares"]["human"] == 100.0
    assert "no part of the manuscript text was generated or edited" in result["disclosure"]
    assert result["report"].startswith(str(tmp_path / "out"))


def test_a_folder_that_is_not_a_repository_is_refused(tmp_path):
    with pytest.raises(ValueError):
        prov.writing_provenance(str(tmp_path))


def test_origins_follow_the_word_diff():
    versions = [("a", ["x", "y", "z"]), ("b", ["x", "w", "z"]), ("c", ["x", "w", "z", "q"])]
    assert workflow.origins(versions) == [None, "b", None, "c"]


def test_a_paragraph_moved_into_an_input_file_keeps_its_history(paper):
    main = paper / "main.tex"
    text = main.read_text()
    block = "\\section{Related Work}\n" + RELATED.replace("treating", "taking") + "\n\n"
    assert block in text
    main.write_text(text.replace(block, "\\input{sections/related}\n\n"))
    (paper / "sections" / "related.tex").write_text(block)
    commit(paper, "Move related work to its own file")
    rec = record(run(paper))
    related = para(rec, "Prior work fixes")
    assert related["file"] == "sections/related.tex"
    assert related["counts"] == {"ai": len(paragraphs.words(RELATED)) - 1, "human": 1}
    assert [v["subject"] for v in related["replay"]["versions"]] == ["Draft related work", "Word choice"]
