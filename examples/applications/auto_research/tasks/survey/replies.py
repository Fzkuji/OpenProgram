"""Schemas for every role reply, built from the ids of the call, and the checks that
read each reply again. A check raises ValueError on an unknown id; nothing is repaired.
Plain code, no model calls.
"""

import json
import re

CITE = re.compile(r"\[(p\d+(?:\s*[,，;；]\s*p\d+)*)\]")
DECISIONS = ("core", "adjacent", "survey", "exclude")
KINDS = ("method", "benchmark", "dataset", "analysis", "survey", "system", "other")
PROBLEMS = ("no_mechanism", "no_evidence", "unsupported", "over_explained", "template", "wrong_cite", "process")


def _json(reply):
    return json.loads(reply) if isinstance(reply, str) else reply


def _obj(props: dict, required=None) -> dict:
    return {"type": "object", "properties": props, "required": list(required or props), "additionalProperties": False}


def _str(max_len: int = 0, min_len: int = 1) -> dict:
    s = {"type": "string", "minLength": min_len}
    if max_len:
        s["maxLength"] = max_len
    return s


def _arr(items: dict, lo: int = 0, hi: int = 0, unique: bool = False) -> dict:
    a = {"type": "array", "items": items, "minItems": lo}
    if hi:
        a["maxItems"] = hi
    if unique:
        a["uniqueItems"] = True
    return a


def _enum(values) -> dict:
    return {"type": "string", "enum": list(values)}


def cites(text: str) -> list[str]:
    return [p.strip() for group in CITE.findall(text) for p in re.split(r"[,，;；]", group)]


# Scope

def scope_schema(with_lines: bool = True) -> dict:
    """The scope; without lines when a preset fixes them."""
    line = _obj({"name": _str(40), "definition": _str(400), "boundary": _str(300),
                 "queries": _arr(_str(120), 2, 5)})
    props = {"title": _str(60), "prerequisites": _str(200), "boundary": _str(400),
             "questions": _arr(_str(200), 2, 6), "lines": _arr(line, 3, 8),
             "survey_queries": _arr(_str(120), 1, 3)}
    if not with_lines:
        del props["lines"]
    return _obj(props)


def check_scope(reply, lines: list[dict] | None = None) -> dict:
    """The planner's scope; a preset's lines replace any the reply holds."""
    plan = _json(reply)
    if lines is not None:
        if "lines" in plan:
            raise ValueError("scope: the preset fixes the research lines; the reply wrote its own")
        plan["lines"] = [dict(l) for l in lines]
        return plan
    names = [l["name"] for l in plan["lines"]]
    if len(set(names)) != len(names):
        raise ValueError("scope: two research lines share a name")
    plan["lines"] = [dict(l, id=f"L{i}") for i, l in enumerate(plan["lines"], 1)]
    return plan


# Screening

def screen_schema(ids: list[str], lines: list[str]) -> dict:
    item = _obj({"id": _enum(ids), "decision": _enum(DECISIONS), "line": _enum(lines + ["none"]),
                 "kind": _enum(KINDS), "reason": _str(160)})
    return _obj({"decisions": _arr(item, len(ids), len(ids), unique=True)})


def check_screen(reply, ids: list[str], lines: list[str]) -> dict[str, dict]:
    out = {}
    for d in _json(reply)["decisions"]:
        if d["id"] not in ids or d["id"] in out:
            raise ValueError(f"screen: unknown or repeated id {d['id']}")
        if d["decision"] in ("core", "adjacent") and d["line"] not in lines:
            raise ValueError(f"screen: {d['id']} is {d['decision']} but names no line")
        out[d["id"]] = d
    missing = [i for i in ids if i not in out]
    if missing:
        raise ValueError(f"screen: no decision for {', '.join(missing)}")
    return out


# Baseline surveys

def gaps_schema(headings: list[str], lines: list[str], new: bool = True) -> dict:
    """Headings mapped onto our lines, and the gaps; new=False (a preset) allows no new line."""
    extra = ["new"] if new else []
    mapping = _obj({"heading": _enum(headings), "line": _enum(lines + extra + ["out_of_scope"])})
    gap = _obj({"heading": _enum(headings), "line": _enum(lines + extra), "name": _str(40, 0),
                "definition": _str(400, 0), "queries": _arr(_str(120), 1, 3)})
    return _obj({"mapping": _arr(mapping, 0, len(headings)), "gaps": _arr(gap, 0, 6)})


def check_gaps(reply, headings: list[str], lines: list[str], new: bool = True) -> dict:
    data = _json(reply)
    for g in data["gaps"]:
        if g["heading"] not in headings:
            raise ValueError(f"gaps: unknown heading {g['heading']}")
        if g["line"] == "new" and not new:
            raise ValueError(f"gaps: the preset fixes the research lines; {g['heading']} cannot open a new one")
        if g["line"] == "new" and not (g["name"].strip() and g["definition"].strip()):
            raise ValueError(f"gaps: new line for {g['heading']} needs a name and definition")
    return data


# Reading

def read_schema(papers: dict[str, list[str]], known: list[str]) -> dict:
    """papers: id -> its locators. Locators are written "<id> <locator>", so each paper
    can point only into its own sections."""
    locs = [f"{pid} {loc}" for pid, found in papers.items() for loc in found]
    point = _obj({"text": _str(300), "locator": _enum(locs)})
    note = _obj({"id": _enum(papers), "problem": _str(300), "mechanism": _str(500),
                 "builds_on": _arr(_enum(known), 0, 4, unique=True), "difference": _str(300, 0),
                 "findings": _arr(point, 1, 4), "limitations": _arr(_obj({**point["properties"],
                                                                         "stated": {"type": "boolean"}}), 0, 4),
                 "benchmarks": _arr(_str(60), 0, 6)})
    return _obj({"notes": _arr(note, len(papers), len(papers), unique=True)})


def check_read(reply, papers: dict[str, list[str]], known: list[str]) -> dict[str, dict]:
    out = {}
    for n in _json(reply)["notes"]:
        pid = n["id"]
        if pid not in papers or pid in out:
            raise ValueError(f"read: unknown or repeated id {pid}")
        for point in n["findings"] + n["limitations"]:
            if not point["locator"].startswith(pid + " "):
                raise ValueError(f"read: {pid} points into another paper ({point['locator']})")
            point["locator"] = point["locator"][len(pid) + 1:]
        bad = [b for b in n["builds_on"] if b not in known or b == pid]
        if bad:
            raise ValueError(f"read: {pid} builds on unknown paper {', '.join(bad)}")
        if n["builds_on"] and not n["difference"].strip():
            raise ValueError(f"read: {pid} names earlier work but not how it differs")
        out[pid] = n
    if set(out) != set(papers):
        raise ValueError("read: a note is missing")
    return out


# Writing

def chapter_schema(papers: list[str]) -> dict:
    row = _obj({"paper": _enum(papers), "cells": _arr(_str(120), 1, 5)})
    table = _obj({"caption": _str(200, 0), "columns": _arr(_str(30), 0, 5), "rows": _arr(row, 0, 12)})
    return _obj({"title": _str(30), "paragraphs": _arr(_str(1200), 2, 10), "table": table})


def check_chapter(reply, papers: list[str]) -> dict:
    ch = _json(reply)
    for i, text in enumerate(ch["paragraphs"], 1):
        found = cites(text)
        if not found:
            raise ValueError(f"chapter: paragraph {i} cites no paper")
        bad = [p for p in found if p not in papers]
        if bad:
            raise ValueError(f"chapter: paragraph {i} cites {', '.join(bad)}, which are not in this line")
    t = ch["table"]
    if t["rows"]:
        if len(t["columns"]) < 1:
            raise ValueError("chapter: the table has rows but no columns")
        for r in t["rows"]:
            if len(r["cells"]) != len(t["columns"]):
                raise ValueError(f"chapter: table row {r['paper']} has {len(r['cells'])} cells for {len(t['columns'])} columns")
    return ch


def overview_schema(lines: list[str], papers: list[str]) -> dict:
    edge = _obj({"from": _enum(lines), "to": _enum(lines), "label": _str(40)})
    return _obj({"paragraphs": _arr(_str(1200), 1, 4), "edges": _arr(edge, 0, 12)})


def check_overview(reply, lines: list[str], papers: list[str]) -> dict:
    ov = _json(reply)
    for text in ov["paragraphs"]:
        bad = [p for p in cites(text) if p not in papers]
        if bad:
            raise ValueError(f"overview: unknown paper {', '.join(bad)}")
    for e in ov["edges"]:
        if e["from"] == e["to"]:
            raise ValueError(f"overview: edge from {e['from']} to itself")
    return ov


def trends_schema(lines: list[str], papers: list[str]) -> dict:
    refs = _arr(_enum(papers), 1, 8, unique=True)
    return _obj({
        "summary": _str(800),
        "rising": _arr(_obj({"line": _enum(lines), "text": _str(500), "papers": refs}), 0, 6),
        "new": _arr(_obj({"kind": _enum(("setting", "benchmark", "method", "finding")), "name": _str(60),
                          "text": _str(500), "papers": refs}), 0, 10),
        "saturated": _arr(_obj({"name": _str(60), "text": _str(500), "papers": refs}), 0, 5)})


def check_refs(reply, papers: list[str], what: str) -> dict:
    data = _json(reply)
    for group in data.values():
        for item in group if isinstance(group, list) else []:
            bad = [p for p in item.get("papers", []) + cites(item.get("text", "")) if p not in papers]
            if bad:
                raise ValueError(f"{what}: unknown paper {', '.join(bad)}")
    return data


def directions_schema(limitations: list[str], papers: list[str]) -> dict:
    card = _obj({"title": _str(40), "problem": _str(600), "evidence": _arr(_enum(limitations), 2, 6, unique=True),
                 "active": _arr(_enum(papers), 0, 8, unique=True), "cost": _str(300), "first_step": _str(400)})
    return _obj({"cards": _arr(card, 0, 6)})


def check_directions(reply, limitations: list[str], papers: list[str]) -> dict:
    data = _json(reply)
    for c in data["cards"]:
        if len({e.split(".")[0] for e in c["evidence"]}) < 2:
            raise ValueError(f"directions: {c['title']} rests on one paper only")
        bad = [p for p in c["active"] + cites(c["problem"]) if p not in papers]
        if bad:
            raise ValueError(f"directions: {c['title']} names unknown paper {', '.join(bad)}")
    return data


def audit_schema(paragraphs: list[str]) -> dict:
    issue = _obj({"paragraph": _enum(paragraphs), "quote": _str(200), "problem": _enum(PROBLEMS), "note": _str(300)})
    return _obj({"issues": _arr(issue, 0, 20)})


def check_audit(reply, paragraphs: list[str]) -> list[dict]:
    issues = _json(reply)["issues"]
    for i in issues:
        if i["paragraph"] not in paragraphs:
            raise ValueError(f"audit: unknown paragraph {i['paragraph']}")
    return issues


def related_schema(lines: list[str]) -> dict:
    return _obj({"paragraphs": _arr(_obj({"line": _enum(lines), "text": _str(1500)}), 1, 8)})


def check_related(reply, papers: list[str]) -> dict:
    data = _json(reply)
    for p in data["paragraphs"]:
        found = cites(p["text"])
        if not found or any(c not in papers for c in found):
            raise ValueError(f"related work: paragraph on {p['line']} cites no known paper")
    return data
