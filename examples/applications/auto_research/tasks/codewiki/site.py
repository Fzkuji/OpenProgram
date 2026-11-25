"""The wiki as a static site and as Markdown, in plain code. Pages: index.html (overview,
entry points, module tree, module call graph, paper-to-code table, config files), one
page per module (purpose, paper parts, key functions, data flow, function call graph,
every class and function), and the source of every module with line anchors. Plain HTML
and CSS, no script; every page stands on its own."""

from html import escape
from pathlib import Path

from . import graph
from .code import MODULE, first_line

CSS = """
:root{--bg:#fbfbfe;--fg:#1c1d2b;--muted:#5d6079;--line:#e1e2ee;--card:#fff;--accent:#4b4fc4;--ok:#0a7d55;
--warn:#a15c00;--bad:#b3261e;--code:#f2f3f9;--hl:#fff4c2}
@media (prefers-color-scheme: dark){:root{--bg:#14151f;--fg:#e8e9f5;--muted:#a2a5c2;--line:#2c2e44;--card:#1b1d2b;
--accent:#9fa3ff;--ok:#3ccf95;--warn:#f0b35a;--bad:#ff8a80;--code:#20223a;--hl:#4a4320}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:24px;margin:8px 0 4px}h2{font-size:18px;margin:32px 0 10px;border-bottom:1px solid var(--line);padding-bottom:4px}
h3{font-size:15px;margin:18px 0 6px}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.muted{color:var(--muted)}.crumb{font-size:13px;color:var(--muted)}
code,pre{font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace}code{background:var(--code);padding:1px 4px;border-radius:4px}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{border-bottom:1px solid var(--line);padding:6px 8px;
text-align:left;vertical-align:top}th{color:var(--muted);font-weight:600}
.tag{display:inline-block;font-size:12px;padding:0 7px;border-radius:9px;border:1px solid currentColor;white-space:nowrap}
.implemented{color:var(--ok)}.partial{color:var(--warn)}.not_found{color:var(--bad)}
.PASS{color:var(--ok)}.PASS_WITH_NOTES{color:var(--warn)}.FAIL{color:var(--bad)}
.graph{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--card);padding:8px}
.tree ul{list-style:none;padding-left:18px;margin:2px 0}.tree>ul{padding-left:0}
.q{color:var(--muted);font-size:13px}.stats{display:flex;flex-wrap:wrap;gap:10px;margin:12px 0}
.stat{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 12px}
.stat b{display:block;font-size:20px}
table.src{width:auto;border:0}table.src td{border:0;padding:0 8px;white-space:pre}
table.src td.n{color:var(--muted);text-align:right;user-select:none}table.src tr:target{background:var(--hl)}
.wrap{overflow-x:auto}
"""


def slug(rel: str) -> str:
    return rel.replace("/", "__").replace("\\", "__") + ".html"


def page(title: str, body: str) -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{escape(title)}</title><style>{CSS}</style></head><body><main>{body}</main></body></html>")


def src_link(file: str, line: int, prefix: str, text: str = "") -> str:
    label = text or f"{file}:{line}"
    return f'<a href="{prefix}src/{escape(slug(file))}#L{line}"><code>{escape(label)}</code></a>'


def fn_link(fid: str, prefix: str) -> str:
    file, qual = fid.split("::", 1)
    return f'<a href="{prefix}modules/{escape(slug(file))}#{escape(anchor(qual))}"><code>{escape(file)}::{escape(qual)}</code></a>'


def anchor(qual: str) -> str:
    return "f-" + "".join(ch if ch.isalnum() else "-" for ch in qual)


def short(fid: str) -> str:
    file, qual = fid.split("::", 1)
    return f"{Path(file).stem}.{qual}" if qual != MODULE else f"{Path(file).name} (top level)"


def build(out: Path, r: dict, sources: dict) -> list[Path]:
    """Write the site to out; the files written."""
    (out / "modules").mkdir(parents=True, exist_ok=True)
    (out / "src").mkdir(parents=True, exist_ok=True)
    written = []
    svg = module_graph(r)
    (out / "callgraph.svg").write_text(svg, encoding="utf-8")
    written.append(out / "callgraph.svg")
    (out / "index.html").write_text(index_page(r, svg), encoding="utf-8")
    written.append(out / "index.html")
    for m in r["index"]["modules"]:
        (out / "modules" / slug(m["file"])).write_text(module_page(r, m), encoding="utf-8")
        (out / "src" / slug(m["file"])).write_text(source_page(m["file"], sources[m["file"]]), encoding="utf-8")
        written += [out / "modules" / slug(m["file"]), out / "src" / slug(m["file"])]
    return written


# Graphs ------------------------------------------------------------------------------------

def module_graph(r: dict) -> str:
    """Modules as nodes, an edge where a function of one calls a function of the other."""
    idx = r["index"]
    of = {f"{m['file']}::{f['qualname']}": m["file"] for m in idx["modules"] for f in m["functions"]}
    entries = {e["file"] for e in idx["entries"]}
    linked = {l["file"] for mp in r["maps"].values() for l in mp["links"]}
    edges = {(of[e["from"]], of[e["to"]]) for e in idx["edges"] if of.get(e["from"]) != of.get(e["to"])}
    used = {a for e in edges for a in e}
    mods = [m for m in idx["modules"] if m["file"] in used or (not m["test"] and m["functions"])]
    nodes = [{"id": m["file"], "label": m["file"], "href": f"modules/{slug(m['file'])}",
              "cls": "entry" if m["file"] in entries else "paper" if m["file"] in linked else "",
              "tip": first_line(r["pages"].get(m["file"], {}).get("purpose", "") or m["doc"]) or m["file"]} for m in mods]
    return graph.svg(nodes, sorted(edges), "module call graph")


def function_graph(r: dict, m: dict) -> str:
    """The module's functions, and the functions of other modules they call or are called by."""
    idx = r["index"]
    mine = [f"{m['file']}::{f['qualname']}" for f in m["functions"]]
    own = set(mine)
    edges = [(e["from"], e["to"]) for e in idx["edges"] if e["from"] in own or e["to"] in own]
    linked = {l["function"] for mp in r["maps"].values() for l in mp["links"]}
    outside = sorted({x for e in edges for x in e if x not in own})
    nodes = [{"id": f, "label": short(f), "href": f"#{anchor(f.split('::', 1)[1])}",
              "cls": "paper" if f in linked else ""} for f in mine if any(f in e for e in edges)]
    nodes += [{"id": f, "label": short(f), "href": f"{slug(f.split('::')[0])}#{anchor(f.split('::', 1)[1])}",
               "cls": "ext"} for f in outside]
    return graph.svg(nodes, edges, f"calls of {m['file']}")


# Pages -------------------------------------------------------------------------------------

def tree(modules: list[dict], pages: dict) -> str:
    root: dict = {}
    for m in modules:
        node = root
        for part in m["file"].split("/")[:-1]:
            node = node.setdefault(part + "/", {})
        node[m["file"].split("/")[-1]] = m

    def render(node: dict) -> str:
        items = []
        for name in sorted(node, key=lambda n: (not n.endswith("/"), n)):
            v = node[name]
            if name.endswith("/"):
                items.append(f"<li><b>{escape(name)}</b>{render(v)}</li>")
            else:
                purpose = first_line(pages.get(v["file"], {}).get("purpose", "") or v["doc"])
                tag = ' <span class="tag muted">test</span>' if v["test"] else ""
                items.append(f'<li><a href="modules/{escape(slug(v["file"]))}"><code>{escape(name)}</code></a>{tag}'
                             f'{" — " + escape(sentence(purpose)) if purpose else ""}</li>')
        return "<ul>" + "".join(items) + "</ul>"
    return f'<div class="tree">{render(root)}</div>'


def sentence(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    cut = text.find(". ")
    text = text[:cut + 1] if 0 < cut < limit else text
    return text if len(text) <= limit else text[:limit - 1] + "…"


def mapping_rows(r: dict, prefix: str, only_file: str = "") -> str:
    rows = []
    for u in r["parts"]:
        mp = r["maps"][u["id"]]
        links = [l for l in mp["links"] if not only_file or l["file"] == only_file]
        if only_file and not links:
            continue
        where = "".join(f'<div>{fn_link(l["function"], prefix)} {src_link(l["file"], l["line"], prefix, "line " + str(l["line"]))}'
                        f'<div class="q">code: <code>{escape(l["code_quote"])}</code></div>'
                        f'<div class="q">paper: “{escape(l["paper_quote"])}”</div>'
                        f'<div>{escape(l["how"])}</div></div>' for l in links) or f'<span class="muted">{escape(mp["note"])}</span>'
        rows.append(f'<tr><td>{escape(u["title"])}<div class="q">{escape(u["section"]) if u["kind"] != "subsection" else ""}</div></td>'
                    f'<td>{escape(u["kind"])}</td><td><span class="tag {mp["status"]}">{escape(mp["status"].replace("_", " "))}</span>'
                    f'{"<div class=q>" + escape(mp["note"]) + "</div>" if mp["note"] and links else ""}</td><td>{where}</td></tr>')
    if not rows:
        return ""
    return ('<div class="wrap"><table><tr><th>Paper part</th><th>Kind</th><th>Status</th><th>Implemented in</th></tr>'
            + "".join(rows) + "</table></div>")


def index_page(r: dict, svg: str) -> str:
    idx, maps = r["index"], r["maps"]
    mapped = sum(1 for m in maps.values() if m["links"])
    stats = [("modules", len(idx["modules"])), ("functions", sum(len(m["functions"]) for m in idx["modules"])),
             ("calls", len(idx["edges"])), ("paper parts", len(r["parts"])), ("mapped", mapped)]
    body = [f'<p class="crumb">Code wiki</p><h1>{escape(r["title"] or "Code wiki")}</h1>',
            f'<p class="muted">Code: <code>{escape(r["code"])}</code> · Paper: <code>{escape(r["main"])}</code> · '
            f'<span class="tag {r["status"]}">{escape(r["status"])}</span></p>',
            '<div class="stats">' + "".join(f'<div class="stat"><b>{v}</b>{escape(k)}</div>' for k, v in stats) + "</div>"]
    if idx["entries"]:
        body.append("<h2>Entry points</h2><ul>")
        for e in idx["entries"]:
            target = f'<a href="modules/{escape(slug(e["file"]))}"><code>{escape(e["name"])}</code></a>' if e["file"] else \
                f'<code>{escape(e["name"])}</code>'
            extra = f' → <code>{escape(e["target"])}</code>' if e["target"] else ""
            body.append(f'<li>{target}{extra} <span class="muted">({escape(e["why"])})</span></li>')
        body.append("</ul>")
    body.append("<h2>Modules</h2>" + tree(idx["modules"], r["pages"]))
    body.append('<h2>Call graph</h2><p class="muted">Modules linked where a function of one calls a function of '
                'the other. Bold: entry points; green: implements a part of the paper. '
                '<a href="callgraph.svg">SVG</a></p>' f'<div class="graph">{svg}</div>')
    if r["parts"]:
        body.append("<h2>Paper ↔ code</h2>" + mapping_rows(r, ""))
    if idx["configs"]:
        body.append("<h2>Config files</h2><ul>" + "".join(
            f'<li><code>{escape(c["file"])}</code>{" — keys: " + escape(", ".join(c["keys"])) if c["keys"] else ""}</li>'
            for c in idx["configs"]) + "</ul>")
    return page(f"{r['title'] or 'Code'} wiki", "".join(body))


def module_page(r: dict, m: dict) -> str:
    p = r["pages"].get(m["file"], {})
    pre = "../"
    body = [f'<p class="crumb"><a href="../index.html">Code wiki</a> / {escape(m["file"])}</p>',
            f'<h1><code>{escape(m["file"])}</code></h1>',
            f'<p>{escape(p.get("purpose") or first_line(m["doc"]) or "No description.")}</p>',
            f'<p class="muted">{escape(m["language"])} · {m["lines"]} lines · {src_link(m["file"], 1, pre, "source")}'
            + (f' · imports {", ".join(f"<code>{escape(x)}</code>" for x in m.get("internal", []))}' if m.get("internal") else "")
            + (" · test" if m["test"] else "") + "</p>"]
    if not m["parsed"]:
        body.append(f'<p class="not_found">Could not parse: {escape(m["error"])}</p>')
    rows = mapping_rows(r, pre, m["file"])
    if rows or p.get("paper"):
        body.append("<h2>Paper parts implemented here</h2>")
        titles = {u["id"]: u["title"] for u in r["parts"]}
        if p.get("paper"):
            body.append("<ul>" + "".join(f'<li><b>{escape(titles.get(x["unit"], x["unit"]))}</b>: {escape(x["how"])}</li>'
                                         for x in p["paper"]) + "</ul>")
        body.append(rows)
    if p.get("key_functions"):
        body.append("<h2>Key functions</h2><ul>" + "".join(
            f'<li>{fn_link(k["function"], pre)} {src_link(m["file"], k["line"], pre, "line " + str(k["line"]))}: '
            f'{escape(k["role"])}<div class="q"><code>{escape(k["quote"])}</code></div></li>' for k in p["key_functions"])
                    + "</ul>")
    if p.get("data_flow"):
        body.append("<h2>How data flows</h2><ol>" + "".join(
            f'<li>{escape(s["step"])} {src_link(m["file"], s["line"], pre, "line " + str(s["line"]))}'
            f'<div class="q"><code>{escape(s["quote"])}</code></div></li>' for s in p["data_flow"]) + "</ol>")
    if any(True for e in r["index"]["edges"] if e["from"].startswith(m["file"] + "::") or e["to"].startswith(m["file"] + "::")):
        body.append('<h2>Calls</h2><p class="muted">Dashed boxes are functions of other modules.</p>'
                    f'<div class="graph">{function_graph(r, m)}</div>')
    if m["classes"] or m["functions"]:
        body.append("<h2>Classes and functions</h2><div class='wrap'><table><tr><th>Name</th><th>Lines</th><th>Description</th></tr>")
        for c in m["classes"]:
            body.append(f'<tr id="{escape(anchor(c["name"]))}"><td><b>class {escape(c["name"])}</b>'
                        f'{"(" + escape(", ".join(c["bases"])) + ")" if c["bases"] else ""}</td>'
                        f'<td>{src_link(m["file"], c["line"], pre, str(c["line"]) + "–" + str(c["end"]))}</td>'
                        f'<td>{escape(first_line(c["doc"]))}</td></tr>')
        for f in m["functions"]:
            body.append(f'<tr id="{escape(anchor(f["qualname"]))}"><td><code>{escape(f["signature"] if f["qualname"] != MODULE else "top-level code")}</code>'
                        f'{"<div class=q>in " + escape(f["class"]) + "</div>" if f["class"] else ""}</td>'
                        f'<td>{src_link(m["file"], f["line"], pre, str(f["line"]) + "–" + str(f["end"]))}</td>'
                        f'<td>{escape(first_line(f["doc"]))}</td></tr>')
        body.append("</table></div>")
    return page(f"{m['file']} — code wiki", "".join(body))


def source_page(file: str, lines: list[str]) -> str:
    rows = "".join(f'<tr id="L{i}"><td class="n"><a href="#L{i}">{i}</a></td><td>{escape(line)}</td></tr>'
                   for i, line in enumerate(lines, 1))
    body = (f'<p class="crumb"><a href="../index.html">Code wiki</a> / <a href="../modules/{escape(slug(file))}">'
            f'{escape(file)}</a> / source</p><h1><code>{escape(file)}</code></h1>'
            f'<div class="wrap"><pre><table class="src">{rows}</table></pre></div>')
    return page(f"{file} — source", body)


# Markdown ----------------------------------------------------------------------------------

def md_cell(s) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def markdown(r: dict) -> str:
    idx, maps = r["index"], r["maps"]
    out = [f"# Code wiki: {r['title'] or r['main']}", "",
           f"Code: `{r['code']}` · paper: `{r['main']}` · status: **{r['status']}**", ""]
    if idx["entries"]:
        out += ["## Entry points", ""] + [f"- `{e['name']}`" + (f" → `{e['target']}`" if e["target"] else "")
                                          + f" ({e['why']})" for e in idx["entries"]] + [""]
    if r["parts"]:
        out += ["## Paper ↔ code", "", "| Paper part | Kind | Status | Implemented in |", "|---|---|---|---|"]
        for u in r["parts"]:
            mp = maps[u["id"]]
            where = "<br>".join(f"`{l['file']}:{l['line']}` `{short(l['function'])}`: {l['how']}"
                                for l in mp["links"]) or mp["note"] or "—"
            out.append(f"| {md_cell(u['title'])} | {u['kind']} | {mp['status']} | {md_cell(where)} |")
        out.append("")
    out += ["## Modules", ""]
    for m in idx["modules"]:
        p = r["pages"].get(m["file"], {})
        if not m["functions"] and not p:
            continue
        out += [f"### `{m['file']}`", "", p.get("purpose") or first_line(m["doc"]) or "No description.", ""]
        if p.get("key_functions"):
            out += ["Key functions:", ""] + [f"- `{short(k['function'])}` (`{m['file']}:{k['line']}`): {k['role']}"
                                             for k in p["key_functions"]] + [""]
        if p.get("data_flow"):
            out += ["Data flow:", ""] + [f"{i}. {s['step']} (`{m['file']}:{s['line']}`)"
                                         for i, s in enumerate(p["data_flow"], 1)] + [""]
        if p.get("paper"):
            titles = {u["id"]: u["title"] for u in r["parts"]}
            out += ["Implements:", ""] + [f"- {titles.get(x['unit'], x['unit'])}: {x['how']}" for x in p["paper"]] + [""]
    if idx["configs"]:
        out += ["## Config files", ""] + [f"- `{c['file']}`" + (f": {', '.join(c['keys'])}" if c["keys"] else "")
                                          for c in idx["configs"]] + [""]
    return "\n".join(out)


__all__ = ["build", "markdown", "slug"]
