"""Lay out the citation graph in Python and draw it as one self-contained HTML page:
inline SVG, no script, a <title> tooltip on every node. Plain code."""

import html
import math
import random

WIDTH, HEIGHT, PAD = 960, 720, 40


def layout(nodes: list[dict], edges: list[dict], rounds: int = 200) -> dict[str, tuple[float, float]]:
    """A small Fruchterman-Reingold layout from a fixed start, so the same graph is
    always drawn the same way."""
    n = len(nodes)
    if not n:
        return {}
    ids = [node["id"] for node in nodes]
    pos = {i: [math.cos(2 * math.pi * k / n), math.sin(2 * math.pi * k / n)] for k, i in enumerate(ids)}
    rng = random.Random(7)
    for p in pos.values():
        p[0] += rng.uniform(-0.05, 0.05)
        p[1] += rng.uniform(-0.05, 0.05)
    k = math.sqrt(4.0 / n)
    links = [(e["source"], e["target"]) for e in edges if e["source"] in pos and e["target"] in pos]
    temp = 0.2
    for _ in range(rounds):
        move = {i: [0.0, 0.0] for i in ids}
        for a_i, a in enumerate(ids):
            for b in ids[a_i + 1:]:
                dx, dy = pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]
                d = math.hypot(dx, dy) or 1e-3
                f = k * k / d
                move[a][0] += dx / d * f
                move[a][1] += dy / d * f
                move[b][0] -= dx / d * f
                move[b][1] -= dy / d * f
        for a, b in links:
            dx, dy = pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]
            d = math.hypot(dx, dy) or 1e-3
            f = d * d / k
            move[a][0] -= dx / d * f
            move[a][1] -= dy / d * f
            move[b][0] += dx / d * f
            move[b][1] += dy / d * f
        for i in ids:      # a weak pull to the centre keeps loose nodes on the page
            move[i][0] -= pos[i][0] * 0.05
            move[i][1] -= pos[i][1] * 0.05
            d = math.hypot(*move[i]) or 1e-9
            step = min(d, temp)
            pos[i][0] += move[i][0] / d * step
            pos[i][1] += move[i][1] / d * step
        temp = max(temp * 0.97, 0.005)
    xs, ys = [p[0] for p in pos.values()], [p[1] for p in pos.values()]
    span_x, span_y = (max(xs) - min(xs)) or 1, (max(ys) - min(ys)) or 1
    return {i: (PAD + (p[0] - min(xs)) / span_x * (WIDTH - 2 * PAD),
                PAD + (p[1] - min(ys)) / span_y * (HEIGHT - 2 * PAD)) for i, p in pos.items()}


def radius(citations) -> float:
    return 4 + 2.2 * math.sqrt(math.log1p(max(int(citations or 0), 0)) * 4)


def page(title: str, nodes: list[dict], edges: list[dict]) -> str:
    pos = layout(nodes, edges)
    lines = []
    for e in edges:
        if e["source"] in pos and e["target"] in pos:
            (x1, y1), (x2, y2) = pos[e["source"]], pos[e["target"]]
            lines.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>')
    circles = []
    for node in sorted(nodes, key=lambda n: n["kind"] == "yours"):      # your works drawn on top
        x, y = pos[node["id"]]
        tip = node["label"] + (f" ({node['year']})" if node.get("year") else "") + f" · 被引 {node.get('citations') or 0}"
        if node["kind"] == "candidate":
            tip += f" · 你的 {node.get('shared', 0)} 篇参考文献引用了它"
        if node.get("flag"):
            tip += f" · {node['flag']}"
        cls = node["kind"] + (" flagged" if node.get("flag") else "")
        circles.append(f'<circle class="{cls}" cx="{x:.1f}" cy="{y:.1f}" r="{radius(node.get("citations")):.1f}">'
                       f"<title>{html.escape(tip)}</title></circle>")
    return f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{ --bg:#fff; --fg:#1d1d1f; --muted:#6e6e73; --edge:#c7c7cc; --yours:#4f46e5; --cand:#f59e0b; --bad:#dc2626; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#111114; --fg:#f2f2f7; --muted:#a1a1aa; --edge:#3f3f46;
  --yours:#818cf8; --cand:#fbbf24; --bad:#f87171; }} }}
body {{ margin:0; padding:16px; background:var(--bg); color:var(--fg); font:14px/1.5 -apple-system, system-ui, sans-serif; }}
h1 {{ font-size:18px; margin:0 0 4px; }} p {{ color:var(--muted); margin:0 0 12px; }}
svg {{ width:100%; height:auto; max-width:{WIDTH}px; display:block; }}
line {{ stroke:var(--edge); stroke-width:.8; }}
circle {{ stroke:var(--bg); stroke-width:1; }} circle.yours {{ fill:var(--yours); }} circle.candidate {{ fill:var(--cand); }}
circle.flagged {{ stroke:var(--bad); stroke-width:3; }}
.key i {{ display:inline-block; width:10px; height:10px; border-radius:50%; margin:0 4px 0 12px; }}
</style></head><body>
<h1>{html.escape(title)}</h1>
<p class="key"><i style="background:var(--yours);margin-left:0"></i>你的参考文献
<i style="background:var(--cand)"></i>可能遗漏的工作
<i style="border:2px solid var(--bad)"></i>撤稿或更正 · 圆的大小表示被引次数，悬停看详情</p>
<svg viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="{html.escape(title)}">
<g>{''.join(lines)}</g><g>{''.join(circles)}</g></svg>
</body></html>
"""


__all__ = ["layout", "page", "radius"]
