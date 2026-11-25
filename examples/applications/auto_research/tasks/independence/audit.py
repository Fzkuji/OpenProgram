"""The independence audit of each claim's supporting works. Plain code, no model calls.

claim_support counts "two or more supporting works", but two papers of one group, or
a paper and its own follow-up, are one line of evidence. Each supporting work is
resolved in OpenAlex to its authors, institutions, references and abstract; works
that share an author (directly or through a chain of works) fall into one
independence group, and so do works that name the same dataset when by_dataset is yes.

Every request goes through citations.sources.fetch, so a source that fails is named
in UNAVAILABLE and tests replace fetch alone.
"""

import csv
import io
import json
import re
from datetime import datetime
from pathlib import Path

from openprogram.runtime.agent.call_state import check_cancelled

from auto_research import checks, stages
from auto_research.trail import Trail

from ..citations import sources
from ..citations.refs import AUTHENTIC_TITLE, title_sim
from ..library import resolve as library
from ..reading import reader
from ..revision.steps.prepare import source as tex_source

OA_SELECT = "id,doi,title,publication_year,cited_by_count,authorships,referenced_works,abstract_inverted_index"
MIN_GROUPS = 2
MATCH_SHARE = 0.5         # of a claim's terms a sentence must contain to state the claim
COLUMNS = ("claim", "work", "title", "openalex", "group", "claim_groups", "linked_to", "reason",
           "institutions", "datasets", "resolved")
STOP = set("""this that with from have been were their there which these those than then into over under
about after before between while where when what does more most much such each every only also both
other same show shows shown using used make makes made our your they them very much less lower higher
""".split())
# Datasets a paper often names; only those the paper itself names are looked for in abstracts.
KNOWN_DATASETS = (
    "ImageNet", "ImageNet-21k", "ImageNet-1k", "JFT-300M", "CIFAR-10", "CIFAR-100", "MNIST", "Fashion-MNIST",
    "SVHN", "COCO", "MS COCO", "Pascal VOC", "ADE20K", "Cityscapes", "Kinetics", "LAION", "Places365",
    "GLUE", "SuperGLUE", "SQuAD", "MNLI", "SST-2", "MMLU", "GSM8K", "MATH", "HumanEval", "MBPP", "BIG-bench",
    "WikiText-103", "Penn Treebank", "BookCorpus", "Common Crawl", "C4", "The Pile", "LibriSpeech",
    "Natural Questions", "TriviaQA", "HotpotQA", "MS MARCO", "SWE-bench", "WebArena", "OSWorld", "ARC",
)
DATASET_NEAR = re.compile(r"\b([A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)\s+(?:dataset|benchmark|corpus|suite)s?\b")
REASON_ZH = {"same work": "同一篇工作", "self-citation": "共同作者且互相引用", "shared author": "共同作者",
             "shared dataset": "同一数据集"}


def source_independence(paper_dir: str, by_dataset: str = "no", out_dir: str = "") -> str:
    """Count the independent lines of evidence behind each claim of the paper.

    paper_dir is the project's Git repository. A claim's supporting works are the rows of
    workflow/claim_support.csv with stance supports; a claim with no such row takes the
    \\cite keys of the paper's sentences that state it (sharing half or more of the
    claim's content words and numbers). Each bib entry is resolved in OpenAlex (DOI, then
    a title search, then the arXiv DOI). Works sharing an OpenAlex author id are one
    group, transitively; a shared author plus a citation between them is labelled a
    self-citation chain. With by_dataset yes, works whose abstracts name the same dataset
    of the paper are also one group; otherwise shared datasets are only listed. PASS when
    every claim has two or more independent groups, else PASS_WITH_NOTES (also when a
    source fails). Files go to out_dir/<timestamp>/ (default
    <paper_dir>/drafts/source_independence).
    """
    paper = Path(paper_dir).expanduser().resolve()
    if not paper.is_dir():
        raise ValueError(f"paper_dir is not a folder: {paper}")
    merge_datasets = str(by_dataset).strip().lower() in ("yes", "true", "1", "on")
    claims = own_claims(paper)
    if not claims:
        raise ValueError(f"no claims in {paper / 'workflow' / 'claims.md'}")
    base = Path(out_dir).expanduser() if out_dir else paper / "drafts" / "source_independence"
    trail = Trail("source_independence", {"paper_dir": str(paper), "by_dataset": merge_datasets,
                                          "out_dir": out_dir}, {})

    full = paper_text(paper)
    support, origin = supporting_works(paper, claims, full)
    refs = {r["key"]: r for r in library.entries(paper)}
    datasets = paper_datasets(full)
    trail.step("read", claims=list(claims), origin=origin, works=sorted({k for ks in support.values() for k in ks}),
               datasets=datasets)

    sources.UNAVAILABLE.clear()
    works, unresolved = {}, {}
    for key in sorted({k for ks in support.values() for k in ks}):
        check_cancelled()
        if key not in refs:
            unresolved[key] = "not in the .bib files"
            continue
        work, how = resolve(refs[key])
        if work:
            works[key] = describe(work, how, datasets)
        else:
            unresolved[key] = "not found in OpenAlex"
    trail.step("resolve", resolved=len(works), unresolved=unresolved, unavailable=sorted(sources.UNAVAILABLE))

    groups, links = cluster(works, merge_datasets)
    claims_out = summarize(claims, support, works, unresolved, groups, links)
    trail.step("cluster", groups=len(set(groups.values())), links=[f"{a}~{b}: {r}" for a, b, r, _ in links])

    unavailable = sorted(sources.UNAVAILABLE)
    weak = [c for c in claims_out if c["groups"] < MIN_GROUPS]
    status = "PASS_WITH_NOTES" if weak or unavailable else "PASS"
    data = {"status": status, "paper_dir": str(paper), "origin": origin, "by_dataset": merge_datasets,
            "datasets": datasets, "claims": claims_out,
            "works": {k: {x: w[x] for x in ("openalex", "title", "how", "authors", "institutions", "datasets")}
                      for k, w in works.items()},
            "links": [{"a": a, "b": b, "reason": r, "shared": s} for a, b, r, s in links],
            "unresolved": unresolved, "unavailable_sources": unavailable}

    out = base / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "independence.csv").write_text(table(claims_out, works, unresolved, groups, links), encoding="utf-8")
    (out / "independence.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(report(data), encoding="utf-8")
    trail.step("report", status=status, weak=[c["claim"] for c in weak])
    run = trail.save(out, report=out / "report.md", csv=out / "independence.csv", json=out / "independence.json")
    return json.dumps({"status": status, "report": str(out / "report.md"), "csv": str(out / "independence.csv"),
                       "json": str(out / "independence.json"), "run": str(run), "origin": origin,
                       "claims": [{k: c[k] for k in ("claim", "works", "groups", "flag")} for c in claims_out],
                       "single_group": [c["claim"] for c in claims_out if c["flag"] == "one group"],
                       "unresolved": unresolved, "unavailable_sources": unavailable}, ensure_ascii=False)


# Reading the project

def own_claims(paper: Path) -> dict[str, str]:
    claims = checks.claim_ids(paper)
    template = dict(stages.CLAIM.findall(stages.template("claims.md")))
    return {} if claims == template else claims


def paper_text(paper: Path) -> str:
    """The main file flattened, or "" when the project has no LaTeX source yet."""
    try:
        return reader.open_paper(str(paper))[2]
    except (ValueError, OSError):
        return ""


def supporting_works(paper: Path, claims: dict[str, str], full: str) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Each claim's supporting bib keys and where they came from (claim_support or paper)."""
    path = stages.folder(paper) / "claim_support.csv"
    rows = []
    if path.is_file():
        problems, rows = checks.table(path.read_text(encoding="utf-8"), ("claim", "work", "stance"),
                                      "claim_support.csv")
        rows = [] if problems else checks.own_rows(rows, "claim_support.csv")
    support, origin = {}, {}
    sentences = paper_sentences(full)
    for cid, text in claims.items():
        keys = [r["work"] for r in rows if r["claim"] == cid and r["stance"] == "supports" and r["work"]]
        if keys:
            support[cid], origin[cid] = list(dict.fromkeys(keys)), "claim_support"
        else:
            support[cid], origin[cid] = stating_cites(text, sentences), "paper"
    return support, origin


def terms(text: str) -> set[str]:
    words = {w for w in re.findall(r"[a-z][a-z-]{3,}", text.lower()) if w not in STOP}
    numbers = set(re.findall(r"\d+(?:\.\d+)?", text))
    return words | numbers


def paper_sentences(full: str) -> list[str]:
    if not full:
        return []
    return [s for p in tex_source.paragraphs(full) for s in tex_source.sentences(p)]


def stating_cites(claim: str, sentences: list[str]) -> list[str]:
    """The cite keys of the sentences that state the claim."""
    want = terms(claim)
    keys = []
    for s in sentences:
        cites = reader.cite_keys(s)
        if not cites or not want:
            continue
        shared = want & terms(reader.plain(s))
        if len(shared) >= 2 and len(shared) >= MATCH_SHARE * len(want):
            keys += [k for k in cites if k not in keys]
    return keys


def paper_datasets(full: str) -> list[str]:
    """Dataset names the paper itself uses: known names it mentions, and capitalized names
    written before dataset, benchmark or corpus that carry a digit or an inner capital."""
    text = reader.plain(tex_source.body(full)) if full else ""
    found = [d for d in KNOWN_DATASETS if re.search(rf"(?<![\w-]){re.escape(d)}(?![\w-])", text)]
    for name in DATASET_NEAR.findall(text):
        if re.search(r"\d|.[A-Z]", name) and name not in found:
            found.append(name)
    return found


# OpenAlex

def fetch_work(path: str) -> dict | None:
    d = sources.fetch("https://api.openalex.org/works/" + path,
                      {"select": OA_SELECT, **sources._env("OPENALEX_API_KEY", "api_key")}, label="OpenAlex")
    return d if isinstance(d, dict) and d.get("id") else None


def resolve(ref: dict) -> tuple[dict | None, str]:
    """The OpenAlex work of one entry: by DOI, else a title search (the closest title,
    then the most cited), else the arXiv DOI."""
    if ref["doi"]:
        w = fetch_work("doi:" + ref["doi"])
        if w:
            return w, "doi"
    if ref["title"]:
        d = sources.fetch("https://api.openalex.org/works",
                          {"search": ref["title"], "per-page": 5, "select": OA_SELECT,
                           **sources._env("OPENALEX_API_KEY", "api_key")}, label="OpenAlex")
        hits = [w for w in (d or {}).get("results") or [] if w.get("id")
                and title_sim(ref["title"], w.get("title") or "") >= AUTHENTIC_TITLE]
        if hits:
            return max(hits, key=lambda w: (round(title_sim(ref["title"], w.get("title") or ""), 2),
                                            w.get("cited_by_count") or 0)), "title"
    if ref["arxiv"]:
        w = fetch_work("doi:10.48550/arxiv." + ref["arxiv"])
        if w:
            return w, "arxiv"
    return None, ""


def abstract(work: dict) -> str:
    index = work.get("abstract_inverted_index") or {}
    places = sorted((i, word) for word, spots in index.items() for i in spots)
    return " ".join(word for _, word in places)


def describe(work: dict, how: str, datasets: list[str]) -> dict:
    authors, institutions = {}, {}
    for a in work.get("authorships") or []:
        author = a.get("author") or {}
        if author.get("id"):
            authors[library.short(author["id"])] = author.get("display_name") or ""
        for inst in a.get("institutions") or []:
            if inst.get("id"):
                institutions[library.short(inst["id"])] = inst.get("display_name") or ""
    text = abstract(work)
    named = [d for d in datasets if re.search(rf"(?<![\w-]){re.escape(d)}(?![\w-])", text, re.I)]
    return {"openalex": library.short(work["id"]), "title": work.get("title") or "", "how": how,
            "authors": authors, "institutions": institutions, "datasets": named,
            "refs": {library.short(r) for r in work.get("referenced_works") or []}}


# Grouping

def cluster(works: dict[str, dict], merge_datasets: bool) -> tuple[dict[str, str], list[tuple]]:
    """Union-find over the resolved works. Returns each key's group (G1, G2, … in key
    order) and the links (a, b, reason, shared names)."""
    parent = {k: k for k in works}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    links = []
    keys = sorted(works)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            wa, wb = works[a], works[b]
            shared = [x for x in wa["authors"] if x in wb["authors"]]
            if wa["openalex"] == wb["openalex"]:
                reason, names = "same work", [wa["title"]]
            elif shared:
                cites = wa["openalex"] in wb["refs"] or wb["openalex"] in wa["refs"]
                reason, names = ("self-citation" if cites else "shared author"), [wa["authors"][s] for s in shared]
            elif set(wa["datasets"]) & set(wb["datasets"]):
                reason, names = "shared dataset", sorted(set(wa["datasets"]) & set(wb["datasets"]))
                if not merge_datasets:
                    links.append((a, b, reason, names))
                    continue
            else:
                continue
            links.append((a, b, reason, names))
            parent[find(a)] = find(b)
    roots, groups = {}, {}
    for k in keys:
        groups[k] = roots.setdefault(find(k), f"G{len(roots) + 1}")
    return groups, links


def summarize(claims, support, works, unresolved, groups, links) -> list[dict]:
    out = []
    for cid, text in claims.items():
        keys = support.get(cid, [])
        resolved = [k for k in keys if k in works]
        found = sorted({groups[k] for k in resolved}, key=lambda g: int(g[1:]))
        n = len(found)
        flag = ("" if n >= MIN_GROUPS else "no works" if not keys else
                "one group" if len(resolved) >= 2 else "too few works")
        inside = [lk for lk in links if lk[0] in resolved and lk[1] in resolved]
        out.append({"claim": cid, "text": text, "works": len(keys), "resolved": len(resolved), "groups": n,
                    "group_ids": found, "keys": keys, "unresolved": [k for k in keys if k in unresolved],
                    "flag": flag, "links": [{"a": a, "b": b, "reason": r, "shared": s} for a, b, r, s in inside]})
    return out


# Output

def table(claims_out, works, unresolved, groups, links) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    for c in claims_out:
        for key in c["keys"]:
            w = works.get(key)
            mine = [lk for lk in links if key in lk[:2] and lk[0] in c["keys"] and lk[1] in c["keys"]]
            writer.writerow({
                "claim": c["claim"], "work": key, "title": (w or {}).get("title", ""),
                "openalex": (w or {}).get("openalex", ""), "group": groups.get(key, ""),
                "claim_groups": c["groups"],
                "linked_to": "; ".join(lk[1] if lk[0] == key else lk[0] for lk in mine),
                "reason": "; ".join(f"{lk[2]}: {', '.join(lk[3])}" for lk in mine),
                "institutions": "; ".join((w or {}).get("institutions", {}).values()),
                "datasets": "; ".join((w or {}).get("datasets", [])),
                "resolved": "yes" if w else unresolved.get(key, "no")})
    return buf.getvalue()


def report(data: dict) -> str:
    verdict = {"PASS": "通过：每条主张都有至少两组彼此独立的支持文献",
               "PASS_WITH_NOTES": "通过，但有主张的独立支持不足两组，或有查询没完成"}
    flag_zh = {"one group": "支持文献都来自同一组", "too few works": "能确认的支持文献不足两篇",
               "no works": "没有支持文献"}
    lines = ["# 支持文献独立性", "", f"结论：**{data['status']}**（{verdict[data['status']]}）", "",
             "同一批作者的几篇论文、互相引用的自引链条算一组证据，不算多篇独立支持。", "",
             "| 主张 | 支持文献 | 独立组数 | 来源 | 问题 |", "|---|---|---|---|---|"]
    for c in data["claims"]:
        src = "claim_support.csv" if data["origin"][c["claim"]] == "claim_support" else "论文中陈述该主张的句子"
        lines.append(f"| {c['claim']} | {c['works']} | {c['groups']} | {src} | {flag_zh.get(c['flag'], '—')} |")
    for c in data["claims"]:
        if not c["links"] and not c["unresolved"]:
            continue
        lines += ["", f"## {c['claim']}", "", f"> {c['text']}", ""]
        for lk in c["links"]:
            merged = lk["reason"] != "shared dataset" or data["by_dataset"]
            lines.append(f"- {lk['a']} 与 {lk['b']}：{REASON_ZH[lk['reason']]}（{'、'.join(lk['shared'])}）"
                         + ("，算作一组" if merged else "，只提示，不合并"))
        if c["unresolved"]:
            lines.append(f"- 没能核对：{'、'.join(c['unresolved'])}")
    weak = [c for c in data["claims"] if c["flag"]]
    if weak:
        lines += ["", "## 怎么补", "",
                  "给这些主张找一篇其他团队、用不同数据的工作做支持，写进 claim_support.csv："
                  + "、".join(c["claim"] for c in weak) + "。"]
    if data["unresolved"]:
        lines += ["", "## 没有核对的文献", ""]
        lines += [f"- {k}：{'不在 .bib 里' if v == 'not in the .bib files' else 'OpenAlex 里没找到'}"
                  for k, v in data["unresolved"].items()]
        lines.append("")
        lines.append("没核对的文献不计入独立组数；补上 DOI 或 arXiv 编号能让下次找到。")
    if data["unavailable_sources"]:
        lines += ["", "## 没有完成的查询", "", "这些来源请求失败，结果可能不全：" + "、".join(data["unavailable_sources"]) + "。"]
    lines += ["", "逐篇的作者组、机构和数据集见同目录的 independence.csv。", ""]
    return "\n".join(lines)


__all__ = ["cluster", "source_independence"]
