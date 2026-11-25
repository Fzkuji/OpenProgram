"""The project's brief: what kind of work the paper is, and its claim in one sentence.

Kept in workflow/project.md, which the author sets from the app and every task's agent
reads before it works. The kind decides which tasks the app lists (a task names the
kinds it serves) and what a section plan may cite as evidence. Plain code.
"""

import re
from pathlib import Path

from . import stages

# Each kind: its name in the app and the work it describes.
KINDS = {
    "method": ("方法", "提出新方法，用对比和消融证明它更好"),
    "analysis": ("实证分析", "不提新方法，测量、分析现象并得出发现"),
    "survey": ("综述", "梳理一个领域的工作、分类和未解问题"),
    "benchmark": ("基准 / 数据集", "发布评测集或数据，给出基线结果"),
    "position": ("立场 / 展望", "提出观点或路线，用文献和例子支撑"),
}
# Kinds whose section plans cite works of the bibliography as evidence, beside evidence.csv rows.
CITES_WORKS = ("survey", "position")
FILE = "project.md"
LINE = re.compile(r"^(Kind|Claim):[ \t]*(.*)$", re.M)


def path(paper: Path) -> Path:
    return stages.folder(paper) / FILE


def read(paper: Path) -> dict:
    """The brief: kind ("" until the author sets it) and claim."""
    file = path(paper)
    text = file.read_text(encoding="utf-8") if file.is_file() else ""
    found = {k.lower(): v.strip() for k, v in LINE.findall(text)}
    kind = found.get("kind", "")
    return {"kind": kind if kind in KINDS else "", "claim": found.get("claim", "")}


def write(paper: Path, kind: str | None = None, claim: str | None = None) -> dict:
    """Set the kind and/or the claim, keeping the rest of the file as the author left it."""
    if kind is not None and kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    file = path(paper)
    text = file.read_text(encoding="utf-8") if file.is_file() else stages.template(FILE)
    for key, value in (("Kind", kind), ("Claim", claim)):
        if value is None:
            continue
        value = " ".join(str(value).split())
        line = re.compile(rf"^{key}:[ \t]*.*$", re.M)
        text = line.sub(lambda _: f"{key}: {value}", text, count=1) if line.search(text) else f"{key}: {value}\n" + text
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, encoding="utf-8")
    return read(paper)


__all__ = ["CITES_WORKS", "KINDS", "path", "read", "write"]
