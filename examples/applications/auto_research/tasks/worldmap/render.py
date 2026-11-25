"""Draw the argument map and the world model as one self-contained HTML page (inline
SVG, no script, a <title> tooltip on every node and edge), and write the Chinese report.
Plain code."""

import html

from ..library.render import HEIGHT, WIDTH, layout

KINDS = ("experiment", "finding", "evidence", "claim", "section")       # left to right
KIND_LABEL = {"experiment": "实验", "finding": "发现", "evidence": "证据", "claim": "主张", "section": "章节"}
EDGE_LABEL = {"supports": "支持", "planned_for": "计划验证", "argued_in": "论证于", "refutes": "否定"}
TYPE_LABEL = {"concept": "概念", "method": "方法", "dataset": "数据集", "metric": "指标", "result": "结果",
              "open_question": "开放问题"}
RELATION_LABEL = {"improves": "提升", "hurts": "损害", "measured_by": "用…衡量", "evaluated_on": "在…上评测",
                  "explains": "解释", "contradicts": "矛盾", "depends_on": "依赖"}
ROW, COL_W, TOP = 36, WIDTH / len(KINDS), 44


def esc(text) -> str:
    return html.escape(str(text))


def short(text: str, n: int = 26) -> str:
    """Cut to about n half-width characters; a CJK character counts as two."""
    text = " ".join(str(text).split())
    out, used = "", 0
    for ch in text:
        used += 2 if ord(ch) > 0x2E7F else 1
        if used > n:
            return out.rstrip() + "…"
        out += ch
    return out


def argument_svg(amap: dict) -> str:
    flagged = {g["id"] for g in amap["gaps"]}
    columns = {k: [n for n in amap["nodes"] if n["kind"] == k] for k in KINDS}
    height = TOP + ROW * max([len(c) for c in columns.values()] + [1]) + 12
    pos = {}
    for c, kind in enumerate(KINDS):
        for r, n in enumerate(columns[kind]):
            pos[n["id"]] = (COL_W * c + 18, TOP + ROW * r + ROW / 2)
    heads = "".join(f'<text class="head" x="{COL_W * c + 18:.0f}" y="22">{KIND_LABEL[k]}（{len(columns[k])}）</text>'
                    for c, k in enumerate(KINDS))
    paths = []
    for e in amap["edges"]:
        if e["source"] in pos and e["target"] in pos:
            (x1, y1), (x2, y2) = pos[e["source"]], pos[e["target"]]
            mid = (x1 + x2) / 2
            paths.append(f'<path class="e {e["type"]}" d="M{x1:.1f},{y1:.1f} C{mid:.1f},{y1:.1f} {mid:.1f},{y2:.1f} '
                         f'{x2:.1f},{y2:.1f}"><title>{esc(e["source"])} {EDGE_LABEL[e["type"]]} {esc(e["target"])}'
                         f'</title></path>')
    dots = []
    for n in amap["nodes"]:
        x, y = pos[n["id"]]
        tip = f"{n['id']}：{n['label']}" + "".join(f" · {k} {n[k]}" for k in ("status", "verdict", "source") if n.get(k))
        cls = n["kind"] + (" gap" if n["id"] in flagged else "")
        dots.append(f'<g class="n {cls}"><circle cx="{x:.1f}" cy="{y:.1f}" r="7"/>'
                    f'<text x="{x + 12:.1f}" y="{y + 4:.1f}">{esc(n["id"])} {esc(short(n["label"], 22))}</text>'
                    f"<title>{esc(tip)}</title></g>")
    return (f'<svg viewBox="0 0 {WIDTH} {height:.0f}" role="img" aria-label="论证图">{heads}'
            f'<g>{"".join(paths)}</g><g>{"".join(dots)}</g></svg>')


def world_svg(world: dict) -> str:
    nodes = [{"id": e["id"]} for e in world["entities"]]
    edges = [{"source": r["from"], "target": r["to"]} for r in world["relations"]]
    pos = layout(nodes, edges)
    lines = []
    for r in world["relations"]:
        if r["from"] in pos and r["to"] in pos:
            (x1, y1), (x2, y2) = pos[r["from"]], pos[r["to"]]
            tip = f"{r['from']} {RELATION_LABEL[r['type']]} {r['to']} · 依据 {', '.join(r['evidence'])}"
            if r.get("note"):
                tip += f" · {r['note']}"
            lines.append(f'<line class="r {r["type"]}" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                         f'marker-end="url(#arrow)"><title>{esc(tip)}</title></line>')
    dots = []
    for e in world["entities"]:
        x, y = pos[e["id"]]
        tip = (f"{e['id']} · {TYPE_LABEL.get(e['type'], e['type'])} · {e['name']}" + (f" · {e['note']}" if e.get("note") else "")
               + (" · 待确认" if e.get("approved") is False else ""))
        dots.append(f'<g class="n {e["type"]}"><circle cx="{x:.1f}" cy="{y:.1f}" r="8"/>'
                    f'<text x="{x + 11:.1f}" y="{y + 4:.1f}">{esc(short(e["name"], 18))}</text>'
                    f"<title>{esc(tip)}</title></g>")
    return (f'<svg viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="世界模型"><defs>'
            '<marker id="arrow" viewBox="0 0 10 10" refX="18" refY="5" markerWidth="6" markerHeight="6" '
            'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--edge)"/></marker></defs>'
            f'<g>{"".join(lines)}</g><g>{"".join(dots)}</g></svg>')


def key(labels: dict, prefix: str) -> str:
    return "".join(f'<span><i class="{prefix} {k}"></i>{v}</span>' for k, v in labels.items())


def page(name: str, amap: dict, world: dict) -> str:
    gaps = "".join(f"<li>{esc(g['text'])}</li>" for g in amap["gaps"]) or "<li>没有缺口</li>"
    questions = "".join(f"<li><b>{esc(q['id'])}</b> {esc(q['question'])}<br><small>为什么重要：{esc(q['why'])}；"
                        f"最便宜的下一步：{esc(q['next_experiment'])}</small></li>"
                        for q in world["open_questions"]) or "<li>没有</li>"
    return f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>研究地图</title>
<style>
:root {{ --bg:#fff; --fg:#1d1d1f; --muted:#6e6e73; --edge:#c7c7cc; --line:#e5e5ea; --bad:#dc2626; --good:#16a34a;
  --c1:#4f46e5; --c2:#0891b2; --c3:#f59e0b; --c4:#db2777; --c5:#64748b; --c6:#7c3aed; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#111114; --fg:#f2f2f7; --muted:#a1a1aa; --edge:#52525b;
  --line:#27272a; --bad:#f87171; --good:#4ade80; --c1:#818cf8; --c2:#22d3ee; --c3:#fbbf24; --c4:#f472b6;
  --c5:#94a3b8; --c6:#a78bfa; }} }}
body {{ margin:0; padding:16px; background:var(--bg); color:var(--fg); font:14px/1.5 -apple-system, system-ui, sans-serif; }}
main {{ max-width:{WIDTH}px; margin:0 auto; }}
h1 {{ font-size:20px; margin:0 0 4px; }} h2 {{ font-size:16px; margin:28px 0 6px; }}
p, small {{ color:var(--muted); }} section {{ border-top:1px solid var(--line); }}
svg {{ width:100%; height:auto; display:block; }}
.head {{ fill:var(--muted); font-size:12px; font-weight:600; }}
.n text {{ fill:var(--fg); font-size:11px; }} .n circle {{ stroke:var(--bg); stroke-width:1.5; }}
.e, .r {{ fill:none; stroke:var(--edge); stroke-width:1.2; }}
.e.refutes, .r.contradicts, .r.hurts {{ stroke:var(--bad); }} .r.improves {{ stroke:var(--good); }}
.e.argued_in {{ stroke-dasharray:3 3; }}
.claim circle, i.claim, .concept circle, i.concept {{ fill:var(--c1); background:var(--c1); }}
.evidence circle, i.evidence, .method circle, i.method {{ fill:var(--c2); background:var(--c2); }}
.experiment circle, i.experiment, .dataset circle, i.dataset {{ fill:var(--c3); background:var(--c3); }}
.finding circle, i.finding, .metric circle, i.metric {{ fill:var(--c4); background:var(--c4); }}
.section circle, i.section, .result circle, i.result {{ fill:var(--c5); background:var(--c5); }}
.open_question circle, i.open_question {{ fill:var(--c6); background:var(--c6); }}
.n.gap circle {{ stroke:var(--bad); stroke-width:3; }}
.key span {{ margin-right:12px; white-space:nowrap; }} .key i {{ display:inline-block; width:10px; height:10px;
  border-radius:50%; margin-right:4px; }}
li {{ margin:4px 0; }}
</style></head><body><main>
<h1>研究地图 · {esc(name)}</h1>
<p>论证图由代码从 workflow/ 读出；世界模型由 cartographer 整理，每条关系都有可查的依据。悬停看详情。</p>
<section><h2>论证图</h2>
<p class="key">{key(KIND_LABEL, "")}<span><i style="border:2px solid var(--bad)"></i>有缺口</span></p>
{argument_svg(amap)}
<h2>缺口</h2><ul>{gaps}</ul></section>
<section><h2>世界模型</h2>
<p class="key">{key(TYPE_LABEL, "")}<span>红线：矛盾或损害；绿线：提升</span></p>
{world_svg(world)}
<h2>开放问题</h2><ul>{questions}</ul></section>
</main></body></html>
"""


def report(name: str, status: str, amap: dict, world: dict, dropped: list[dict], shared: list[dict]) -> str:
    lines = [f"# 研究地图：{name}", "",
             f"结论：{status}　主张 {len(amap['claims'])} 条　实体 {len(world['entities'])} 个　"
             f"关系 {len(world['relations'])} 条　开放问题 {len(world['open_questions'])} 个", "", "## 缺口", ""]
    lines += [f"- {g['text']}" for g in amap["gaps"]] or ["没有缺口。"]
    if dropped:
        lines += ["", f"另有 {len(dropped)} 条关系因依据查不到被丢弃：", ""]
        lines += [f"- {d['relation'].get('from')} {d['relation'].get('type')} {d['relation'].get('to')}：{d['reason']}"
                  for d in dropped]
    pending = [e for e in world["entities"] if e.get("approved") is False] + \
              [r for r in world["relations"] if r.get("approved") is False]
    if pending:
        lines += ["", f"## 待确认（来自你的笔记，{len(pending)} 条）", "",
                  "逐条看过后，在 workflow/world.json 把对的改成 \"approved\": true，不对的删掉。", ""]
        lines += [f"- {x.get('id') or (str(x.get('from')) + ' ' + str(x.get('type')) + ' ' + str(x.get('to')))}"
                  f" {x.get('name', '')}：「{x.get('quote', '')}」（{x.get('source', '')}）" for x in pending]
    lines += ["", "## 开放问题", ""]
    lines += [f"- **{q['id']}** {q['question']}\n  - 为什么重要：{q['why']}\n  - 最便宜的下一步：{q['next_experiment']}"
              for q in world["open_questions"]] or ["没有。"]
    lines += ["", "## 建议的下一步", ""]
    lines += [f"{k}. `{p['task']}`：{p['why']}" for k, p in enumerate(world["proposals"], 1)] or ["没有。"]
    if shared:
        lines += ["", "## 其他项目的发现", ""]
        lines += [f"- [{f['project']}] {f['id']} {f['title']}（{f['verdict'] or '未定'}）" for f in shared]
    lines += ["", "世界模型保存在 workflow/world.json，下次运行在此基础上更新。", ""]
    return "\n".join(lines)


__all__ = ["KINDS", "argument_svg", "page", "report", "world_svg"]
