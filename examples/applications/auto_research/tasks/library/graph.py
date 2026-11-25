"""The citation graph of a paper's bibliography: retracted and corrected references,
key works the references share that the paper does not cite, and a drawing of both.

Plain code, no model calls. OpenAlex resolves each entry and lists what it cites;
Crossref names the retraction and correction notices of each DOI.
"""

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from openprogram.runtime.agent.call_state import check_cancelled

from auto_research.trail import Trail

from ..citations import sources
from ..survey import search
from . import render, resolve

TOP = 20            # missing works kept
FETCHED = 60        # candidates whose records are fetched before ranking
KIND_ZH = {"retraction": "撤稿", "correction": "更正", "erratum": "勘误", "expression_of_concern": "关注声明"}
HOW_ZH = {"doi": "DOI", "arxiv": "arXiv 编号", "title": "标题检索"}


def citation_graph(paper_dir: str, min_shared: str = "3", out_dir: str = "") -> str:
    """Check the paper's references for retractions and corrections and find key works it misses.

    paper_dir is the project's Git repository. Its bibliographies are the bib_files of
    workflow/rules.json that exist, else every *.bib in the repository root. Each entry is
    resolved to an OpenAlex work (DOI, arXiv id, then title); OpenAlex's is_retracted and
    Crossref's update notices flag retractions, corrections, errata and expressions of
    concern. A work cited by at least min_shared of the resolved references and absent
    from the bibliography is a missing-work candidate; the top 20 by shared count, then
    citations, are kept. FAIL when a reference is retracted. Files go to
    out_dir/<timestamp>/ (default <paper_dir>/drafts/citation_graph).
    """
    paper = Path(paper_dir).expanduser().resolve()
    if not paper.is_dir():
        raise ValueError(f"paper_dir is not a folder: {paper}")
    try:
        need = int(str(min_shared).strip())
    except ValueError:
        raise ValueError("min_shared must be a whole number") from None
    if need < 1:
        raise ValueError("min_shared must be at least 1")
    base = Path(out_dir).expanduser() if out_dir else paper / "drafts" / "citation_graph"
    trail = Trail("citation_graph", {"paper_dir": str(paper), "min_shared": need, "out_dir": out_dir}, {})
    refs = resolve.entries(paper)
    if not refs:
        raise ValueError(f"no bibliography entry found in {paper} (looked for the rules' bib_files and *.bib)")
    trail.step("read", files=[p.name for p in resolve.bib_files(paper)], entries=len(refs))

    sources.UNAVAILABLE.clear()
    works, unresolved = {}, []
    for ref in refs:
        check_cancelled()
        work, how = resolve.resolve(ref)
        if work:
            works[ref["key"]] = dict(work, how=how)
        else:
            unresolved.append(ref["key"])
    trail.step("resolve", resolved=len(works), unresolved=unresolved, unavailable=sorted(sources.UNAVAILABLE))

    problems = []
    for ref in refs:
        check_cancelled()
        work = works.get(ref["key"])
        found = resolve.notices(ref["doi"] or resolve.work_doi(work))
        if work and work.get("is_retracted") and not any(n["kind"] == "retraction" for n in found):
            found.append({"kind": "retraction", "relation": "openalex", "notice_doi": "",
                          "notice_url": work["id"]})
        seen = set()
        for n in found:
            if (n["kind"], n["notice_doi"]) not in seen:
                seen.add((n["kind"], n["notice_doi"]))
                problems.append({"key": ref["key"], "title": ref["title"] or (work or {}).get("title") or "", **n})
    trail.step("notices", problems=[f"{p['key']}: {p['kind']}" for p in problems])

    missing = missing_works(refs, works, need)
    trail.step("missing", min_shared=need, candidates=[m["title"] for m in missing])

    nodes, edges = graph(refs, works, missing, problems)
    retracted = [p for p in problems if p["kind"] == "retraction"]
    unavailable = sorted(sources.UNAVAILABLE)
    status = ("FAIL" if retracted else
              "PASS_WITH_NOTES" if problems or missing or unresolved or unavailable else "PASS")
    data = {"status": status, "paper_dir": str(paper), "min_shared": need, "entries": len(refs),
            "resolved": {k: {"openalex": w["id"], "how": w["how"], "doi": resolve.work_doi(w),
                             "cited_by_count": w.get("cited_by_count"),
                             "references": len(w.get("referenced_works") or [])} for k, w in works.items()},
            "problems": problems, "missing": missing, "unresolved": unresolved, "unavailable_sources": unavailable}

    out = base / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "citation_graph.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "graph.json").write_text(json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    (out / "graph.html").write_text(render.page(f"{paper.name} 的引用图", nodes, edges), encoding="utf-8")
    (out / "report.md").write_text(report(data, refs), encoding="utf-8")
    trail.step("report", status=status, nodes=len(nodes), edges=len(edges))
    run = trail.save(out, report=out / "report.md", json=out / "citation_graph.json",
                     graph=out / "graph.json", html=out / "graph.html")
    return json.dumps({"status": status, "report": str(out / "report.md"), "json": str(out / "citation_graph.json"),
                       "graph": str(out / "graph.json"), "html": str(out / "graph.html"), "run": str(run),
                       "entries": len(refs), "resolved": len(works), "problems": problems, "missing": missing,
                       "unresolved": unresolved, "unavailable_sources": unavailable}, ensure_ascii=False)


def missing_works(refs: list[dict], works: dict, need: int) -> list[dict]:
    """Works cited by at least `need` of the resolved references and not in the
    bibliography (by OpenAlex id, DOI or normalized title), ranked by how many
    references cite them, then by citations."""
    yours = {resolve.short(w["id"]) for w in works.values()}
    dois = {r["doi"] for r in refs if r["doi"]} | {resolve.work_doi(w) for w in works.values()} - {""}
    titles = {search.norm_title(r["title"]) for r in refs if r["title"]}
    citing = defaultdict(set)
    for key, w in works.items():
        for ref_id in w.get("referenced_works") or []:
            citing[resolve.short(ref_id)].add(key)
    shared = {i: keys for i, keys in citing.items() if len(keys) >= need and i not in yours}
    if not shared:
        return []
    top = sorted(shared, key=lambda i: (-len(shared[i]), i))[:FETCHED]
    out = []
    for c in search.openalex_works(top):
        i = resolve.short(c.get("openalex"))
        if i not in shared or c["doi"] in dois or search.norm_title(c["title"]) in titles:
            continue
        out.append({"openalex": i, "title": c["title"], "year": c["year"], "venue": c["venue"], "doi": c["doi"],
                    "url": c["url"] or f"https://openalex.org/{i}", "citations": c["citations"] or 0,
                    "shared": len(shared[i]), "cited_by": sorted(shared[i])})
    out.sort(key=lambda m: (-m["shared"], -m["citations"], m["title"]))
    return out[:TOP]


def graph(refs: list[dict], works: dict, missing: list[dict], problems: list[dict]) -> tuple[list[dict], list[dict]]:
    """Nodes for the resolved references and the missing works; an edge for every
    citation among them."""
    flags = defaultdict(list)
    for p in problems:
        flags[p["key"]].append(KIND_ZH[p["kind"]])
    titles = {r["key"]: r["title"] for r in refs}
    nodes, by_id = [], {}
    for key, w in works.items():
        i = resolve.short(w["id"])
        by_id[i] = key
        nodes.append({"id": i, "kind": "yours", "key": key, "label": f"[{key}] {titles.get(key) or w.get('title') or ''}",
                      "year": w.get("publication_year"), "citations": w.get("cited_by_count") or 0,
                      "flag": "、".join(dict.fromkeys(flags.get(key, [])))})
    for m in missing:
        nodes.append({"id": m["openalex"], "kind": "candidate", "label": m["title"], "year": m["year"],
                      "citations": m["citations"], "shared": m["shared"], "url": m["url"]})
    ids = {n["id"] for n in nodes}
    edges = []
    for key, w in works.items():
        source = resolve.short(w["id"])
        for ref_id in dict.fromkeys(resolve.short(r) for r in w.get("referenced_works") or []):
            if ref_id in ids and ref_id != source:
                edges.append({"source": source, "target": ref_id})
    return nodes, edges


def report(data: dict, refs: list[dict]) -> str:
    verdict = {"FAIL": "不通过：有参考文献已被撤稿", "PASS_WITH_NOTES": "通过，但有需要看的地方", "PASS": "通过"}
    lines = [f"# 引用图检查", "", f"结论：**{data['status']}**（{verdict[data['status']]}）", "",
             f"共 {data['entries']} 条参考文献，{len(data['resolved'])} 条在 OpenAlex 找到，"
             f"{len(data['unresolved'])} 条没找到。", ""]
    lines += ["## 撤稿与更正", ""]
    if data["problems"]:
        lines += ["| 条目 | 标题 | 类型 | 通知 |", "|---|---|---|---|"]
        for p in data["problems"]:
            notice = p["notice_url"] or "—"
            if p["relation"] == "update-to":
                notice += "（这条 DOI 本身就是通知）"
            lines.append(f"| {p['key']} | {cell(p['title'])} | {KIND_ZH[p['kind']]} | {notice} |")
        lines += ["", "撤稿的文献要删掉或换成可靠来源；更正和勘误要核对引用的内容是否受影响。"]
    else:
        lines.append("没有发现撤稿、更正、勘误或关注声明。")
    lines += ["", f"## 可能遗漏的关键工作（至少被 {data['min_shared']} 篇参考文献引用）", ""]
    if data["missing"]:
        lines += ["| # | 标题 | 年份 | 发表处 | 被引 | 共同引用 | 引用它的条目 |", "|---|---|---|---|---|---|---|"]
        for n, m in enumerate(data["missing"], 1):
            lines.append(f"| {n} | [{cell(m['title'])}]({m['url']}) | {m['year'] or '—'} | {cell(m['venue']) or '—'} | "
                         f"{m['citations']} | {m['shared']} | {', '.join(m['cited_by'])} |")
        lines += ["", "这些是你的参考文献共同依赖的工作。逐条判断：该引用就加进 .bib，不相关就忽略。"]
    else:
        lines.append("没有找到达到门槛的遗漏工作。")
    if data["unresolved"]:
        lines += ["", "## 没有找到的条目", "", "这些条目没有参与撤稿检查和遗漏分析；补上 DOI 或 arXiv 编号能让下次找到。", ""]
        titles = {r["key"]: r["title"] for r in refs}
        lines += [f"- {k}：{titles.get(k) or '（没有标题）'}" for k in data["unresolved"]]
    if data["unavailable_sources"]:
        lines += ["", "## 没有完成的查询", "", "这些来源请求失败，结果可能不全：" + "、".join(data["unavailable_sources"]) + "。"]
    if data["resolved"]:
        lines += ["", "## 找到的条目", "", "| 条目 | 方式 | OpenAlex | 被引 | 参考文献数 |", "|---|---|---|---|---|"]
        lines += [f"| {k} | {HOW_ZH[v['how']]} | {v['openalex']} | {v['cited_by_count'] or 0} | {v['references']} |"
                  for k, v in data["resolved"].items()]
    lines += ["", "引用图见同目录的 graph.html（数据在 graph.json）。", ""]
    return "\n".join(lines)


def cell(text: str, limit: int = 100) -> str:
    text = " ".join(str(text or "").split()).replace("|", "\\|")
    return text if len(text) <= limit else text[:limit - 1] + "…"


__all__ = ["citation_graph", "graph", "missing_works"]
