#!/usr/bin/env python3
"""Aggregate run.py's JSONL results into markdown tables.

    python summarize.py                      # everything under results/
    python summarize.py results/*.jsonl
    python summarize.py --by task            # per-task instead of per-variant
    python summarize.py --by variant,task    # cross-tab

Medians (not means) for token counts and wall time: agent runs have a long
right tail and a couple of runaway trials would swamp a mean.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

COLS = [
    ("n", "n"),
    ("success", "success"),
    ("input_tokens", "input (med)"),
    ("output_tokens", "output (med)"),
    ("cache_read_tokens", "cache_read (med)"),
    ("cache_write_tokens", "cache_write (med)"),
    ("total_tokens", "total (med)"),
    ("wall_s", "wall s (med)"),
    ("cost_total", "cost $ (sum)"),
]


def load(paths: list[str]) -> list[dict]:
    rows = []
    for p in paths:
        with open(p) as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def med(xs: list[float]) -> float:
    return statistics.median(xs) if xs else 0.0


def aggregate(rows: list[dict], keys: list[str]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault(tuple(str(r.get(k)) for k in keys), []).append(r)
    out = []
    for gk, rs in sorted(groups.items()):
        u = [r.get("usage") or {} for r in rs]
        rec = dict(zip(keys, gk))
        rec["n"] = len(rs)
        rec["success"] = f"{sum(1 for r in rs if r.get('score', 0) >= 1.0) / len(rs):.0%}"
        for field in ("input_tokens", "output_tokens", "cache_read_tokens",
                      "cache_write_tokens", "total_tokens"):
            rec[field] = f"{med([x.get(field, 0) for x in u]):,.0f}"
        rec["wall_s"] = f"{med([r.get('wall_s', 0) or 0 for r in rs]):.1f}"
        rec["cost_total"] = f"{sum(x.get('cost_total', 0) or 0 for x in u):.4f}"
        rec["_unsupported"] = sorted(
            {v for r in rs for v in (r.get("unsupported_env") or [])})
        rec["_mode"] = ",".join(sorted({str(r.get("mode")) for r in rs}))
        out.append(rec)
    return out


def table(recs: list[dict], keys: list[str]) -> str:
    headers = keys + [label for _, label in COLS] + ["mode"]
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in recs:
        cells = [r[k] for k in keys] + [str(r[f]) for f, _ in COLS] + [r["_mode"]]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="jsonl files (default: results/*.jsonl)")
    ap.add_argument("--by", default="variant",
                    help="comma-separated group keys (default: variant)")
    args = ap.parse_args(argv)

    paths = args.paths or sorted(glob.glob(os.path.join(HERE, "results", "*.jsonl")))
    if not paths:
        print("no result files found", file=sys.stderr)
        return 1
    rows = load(paths)
    if not rows:
        print("result files are empty", file=sys.stderr)
        return 1

    keys = [k.strip() for k in args.by.split(",") if k.strip()]
    recs = aggregate(rows, keys)
    print(f"# DAG ablation — {len(rows)} trials from {len(paths)} file(s)\n")
    print(table(recs, keys))

    warn = sorted({v for r in recs for v in r["_unsupported"]})
    if warn:
        print("\n> **Caveat**: these rows were produced with variant switches "
              "the product does not implement yet, so the conditions are not "
              "faithful:\n>\n" + "\n".join(f"> - `{v}`" for v in warn))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
