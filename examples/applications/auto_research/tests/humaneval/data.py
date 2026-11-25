"""A project with two summarizers' outputs, and raters who answer the survey page the
way the browser would (same CSV columns), each with their own behaviour."""

import csv
import io
import json
import random
import re
import subprocess
from pathlib import Path

from auto_research.tasks.humaneval.page import CSV_COLUMNS

CLAIMS = """# Claims
Approved: yes

C1: Summaries from terse are preferred by human raters over verbose summaries.
RQ: RQ1
C2: The probe lowers validation loss.
RQ: RQ1
"""
EVIDENCE = "id,claim,statement,source,script\nE1,C2,Validation loss 0.0102,workflow/results.tsv,train.py\n"
TEX = r"""\documentclass{article}
\begin{document}
\begin{abstract}
We compare a terse summarizer with a verbose one and ask people which summaries they prefer.
\end{abstract}
\end{document}
"""


def outputs(items: int = 12) -> list[dict]:
    rows = []
    for i in range(1, items + 1):
        prompt = f"Summarize report number {i} about river flooding in the northern valley."
        rows.append({"id": f"q{i}", "prompt": prompt, "system": "terse",
                     "output": f"Report {i}: the river flooded twice and farms lost their spring harvest."})
        rows.append({"id": f"q{i}", "prompt": prompt, "system": "verbose",
                     "output": f"In report {i}, which is long, it is said that there was a flood, and many "
                               f"things happened, and some farms might have been affected in some way."})
    return rows


def make(tmp_path: Path, items: int = 12, claims: str = CLAIMS, evidence: str | None = EVIDENCE) -> Path:
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "main.tex").write_text(TEX)
    (repo / "workflow" / "claims.md").write_text(claims)
    if evidence is not None:
        (repo / "workflow" / "evidence.csv").write_text(evidence)
    (repo / "outputs.jsonl").write_text("\n".join(json.dumps(r) for r in outputs(items)) + "\n")
    return repo


def survey_data(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="survey">(.*?)</script>', html, re.S)
    return json.loads(m.group(1).replace("<\\/", "</"))


def answer(data: dict, key: dict, rater: str, choose, attentive: bool = True, seed: int = 0) -> str:
    """The CSV a rater downloads. choose(task, criterion, shown_codes) returns "1", "2",
    "tie" (pairwise) or a rating; attentive raters follow the attention checks."""
    rng = random.Random(f"{rater}:{seed}")
    tasks = data["tasks"][:data["max_tasks"]]
    seq = [{"task": t, "kind": "main", "flip": rng.random() < 0.5, "repeat_of": ""} for t in tasks]
    for t in rng.sample(seq[:-3], data["repeats"]):
        seq.append({"task": t["task"], "kind": "repeat", "flip": not t["flip"], "repeat_of": t["task"]["id"]})
    for a in data["attention"]:
        seq.insert(rng.randrange(1, len(seq)), {"task": a, "kind": "attention", "flip": False, "repeat_of": ""})
    rows = []
    for pos, s in enumerate(seq, 1):
        t = s["task"]
        if data["mode"] == "pairwise":
            sides = [t["right"], t["left"]] if s["flip"] else [t["left"], t["right"]]
            shown = [sides[0]["code"], sides[1]["code"]]
        else:
            shown = [t["code"], ""]
        for c in data["criteria"]:
            if s["kind"] == "attention":
                a = key["attention"][t["id"]] if attentive else ("2" if key["attention"][t["id"]] == "1" else "1")
            else:
                a = choose(t, c["id"], shown, rng)
            win = ""
            if data["mode"] == "pairwise":
                win = "tie" if a == "tie" else shown[0] if a == "1" else shown[1]
            rows.append({"survey_id": data["survey_id"], "rater": rater, "position": pos, "task": t["id"],
                         "kind": s["kind"], "repeat_of": s["repeat_of"], "item": t["item"], "criterion": c["id"],
                         "shown_1": shown[0], "shown_2": shown[1], "answer": a, "winner": win, "seconds": "9.5",
                         "finished_at": "2026-11-24T00:00:00Z"})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def truth(key: dict, p: float):
    """A rater who sees which output is better on each item (terse on four items of five,
    verbose on the fifth) and says so with probability p."""
    codes = key["systems"]

    def choose(task, criterion, shown, rng):
        n = int("".join(ch for ch in task["item"] if ch.isdigit()) or 0)
        best = codes["verbose"] if n % 5 == 0 else codes["terse"]
        good = "1" if shown[0] == best else "2"
        return good if rng.random() < p else ("2" if good == "1" else "1")
    return choose


def coin(task, criterion, shown, rng):
    return rng.choice(["1", "2"])
