"""The benchmarks of the paper's results tables. Plain code, no model calls.

A benchmark is one numeric column of a results table that holds a cited row: its
dataset and metric (from the column headers, else the caption), whether higher is
better, the paper's own best number (rows without a \\cite) and the cited baselines'
numbers (rows with one; the value baseline_numbers verified in workflow/sota.csv wins
over the cell). The same dataset and metric in two tables is one benchmark.
"""

import csv
import io
import re
from pathlib import Path

from auto_research import checks, stages

from ..reading import reader, sota
from ..revision.steps.prepare.source import body, caption

LOWER = re.compile(r"\b(error|err|loss|perplexity|ppl|latency|wer|cer|fid|mse|mae|rmse|nll|bpc|bpd|time|cost"
                   r"|distance|regret|eer|ece|kid)\b|↓|\\downarrow|lower is better", re.I)
HIGHER = re.compile(r"↑|\\uparrow|higher is better", re.I)
METRIC_TOKENS = re.compile(r"^(?:top-?\d+|acc\.?|accuracy|error|err\.?|f1|bleu|rouge[\w-]*|score|em|map|ap\w*|iou|miou"
                           r"|loss|ppl|perplexity|auc|auroc|wer|cer|fid|success|recall|precision|r@\d+|ndcg\w*|mrr"
                           r"|pass@\d+|mse|mae|rmse|exact|match|avg\.?|average|mean|win|rate|reward|latency|psnr|ssim"
                           r"|lpips|is|kid|bpc|bpd|nll|\(%\)|%)$", re.I)
CAPTION_DATASET = re.compile(r"\b(?:on|for)\s+(?:the\s+)?([A-Z][\w\-.]*(?:\s+[A-Z0-9][\w\-.]*)?)")
VERIFIED = ("match", "mismatch", "better_reported")


def split_header(header: str) -> tuple[str, str]:
    """A single header that names both: "ImageNet top-1 error" is (ImageNet, top-1
    error). The dataset is the leading run of words that start with a capital or a digit
    and are not metric words; ("", header) when there is none."""
    words = header.split()
    n = 0
    while n < len(words) - 1 and (words[n][0].isupper() or words[n][0].isdigit()) \
            and not METRIC_TOKENS.match(words[n]):
        n += 1
    return (" ".join(words[:n]), " ".join(words[n:])) if n else ("", header)


def direction(metric: str, raw: str = "", experiment: dict | None = None) -> bool:
    """Whether higher is better: arrows in the header first, then the experiment's
    Direction when its Metric is this metric, then the metric's words."""
    text = f"{raw} {metric}"
    if HIGHER.search(text):
        return True
    if re.search(r"↓|\\downarrow|lower is better", text, re.I):
        return False
    exp = experiment or {}
    name = (exp.get("Metric") or "").strip().lower()
    if name and name in metric.lower() and exp.get("Direction"):
        return exp["Direction"].strip().lower() != "lower"
    return not LOWER.search(metric)


def table_columns(full: str) -> list[dict]:
    """Every numeric column of every table that has a cited row, with its header words,
    caption, cited cells and own cells."""
    out = []
    for n, m in enumerate(sota.TABLE_ENV.finditer(body(full)), 1):
        block = m.group(2)
        label = re.search(r"\\label\{([^}]*)\}", block)
        where = f"Table {n}" + (f" ({label.group(1)})" if label else "")
        cap = reader.plain(caption(block))
        for tab in sota.tabulars(block):
            rows = [sota.split_cells(sota.RULES.sub("", r)) for r in sota.ROW_END.split(tab)]
            rows = [r for r in rows if any(reader.plain(c) for c in r)]
            header = []
            for r in rows:
                if any(sota.cell_number(c) for c in r[1:]) or reader.CITE.search(" ".join(r)):
                    break
                header.append(r)
            body_rows = rows[len(header):]
            if not any(reader.cite_keys(" ".join(r)) for r in body_rows):
                continue
            width = max(len(r) for r in body_rows)
            for j in range(1, width):
                raw = [h[j] if j < len(h) else "" for h in header]
                heads = [reader.plain(h) for h in raw if reader.plain(h)]
                cited, own = [], []
                for r in body_rows:
                    if j >= len(r) or reader.CITE.search(r[j]):
                        continue
                    value = sota.cell_number(r[j])
                    if not value:
                        continue
                    name = reader.plain(r[0]) or "?"
                    keys = reader.cite_keys(" ".join(r))
                    (cited if keys else own).append({"name": name, "value": value, "keys": keys})
                if not cited and not own:
                    continue
                header_text = heads[-1] if heads else ""
                metric, dataset = header_text, heads[-2] if len(heads) > 1 else ""
                if not dataset:
                    dataset, metric = split_header(header_text)
                if not dataset:
                    c = CAPTION_DATASET.search(cap)
                    dataset = c.group(1).rstrip(".") if c else ""
                out.append({"header": header_text, "raw": " ".join(raw), "dataset": dataset, "metric": metric,
                            "caption": cap, "location": where, "cited": cited, "own": own})
    return out


def sota_values(paper: Path) -> dict[tuple[str, str], str]:
    """(key, header) -> the value baseline_numbers read in the cited work itself."""
    path = stages.folder(paper) / sota.FILE
    if not path.is_file():
        return {}
    with io.StringIO(path.read_text(encoding="utf-8"), newline="") as f:
        rows = list(csv.DictReader(f))
    return {((r.get("key") or "").strip(), (r.get("metric") or "").strip()): (r.get("theirs") or "").strip()
            for r in rows if (r.get("status") or "").strip() in VERIFIED and sota.number(r.get("theirs") or "") is not None}


def best(values: list[dict], higher: bool) -> dict | None:
    known = [v for v in values if sota.number(v["value"]) is not None]
    if not known:
        return None
    pick = max if higher else min
    return pick(known, key=lambda v: sota.number(v["value"]))


def benchmarks(paper: Path, full: str) -> list[dict]:
    """The benchmarks, B1, B2, ...: dataset, metric, higher, ours (the best own cell or
    None), baselines (every cited cell, with the sota.csv value where it has one),
    strongest (the best of them), keys, caption, location."""
    experiment = checks.fields((stages.folder(paper) / "experiment.md").read_text(encoding="utf-8")) \
        if (stages.folder(paper) / "experiment.md").is_file() else {}
    verified = sota_values(paper)
    merged: dict[tuple[str, str], dict] = {}
    for col in table_columns(full):
        if not col["dataset"]:
            continue
        higher = direction(col["metric"], col["raw"], experiment)
        ident = (col["dataset"].lower(), col["metric"].lower())
        b = merged.setdefault(ident, {"dataset": col["dataset"], "metric": col["metric"], "header": col["header"],
                                      "higher": higher, "captions": [], "locations": [], "own": [], "baselines": []})
        b["captions"] += [col["caption"]] if col["caption"] and col["caption"] not in b["captions"] else []
        b["locations"] += [col["location"]] if col["location"] not in b["locations"] else []
        b["own"] += col["own"]
        for c in col["cited"]:
            checked = next((verified[(k, col["header"])] for k in c["keys"] if (k, col["header"]) in verified), "")
            b["baselines"].append(dict(c, cited=c["value"], value=checked or c["value"], checked=bool(checked)))
    out = []
    for n, b in enumerate(merged.values(), 1):
        b["id"] = f"B{n}"
        b["ours"] = best(b["own"], b["higher"])
        b["strongest"] = best(b["baselines"], b["higher"])
        b["keys"] = list(dict.fromkeys(k for c in b["baselines"] for k in c["keys"]))
        out.append(b)
    return out


__all__ = ["benchmarks", "direction", "split_header", "table_columns"]
