"""Forest plot and funnel plot of a meta-analysis as inline SVG in one self-contained
HTML page (forest.html). Plain code, no model calls, no external files."""

import html
import math

ROW = 26
LEFT = 300          # label and numbers column
PLOT = 360          # width of the effect axis
RIGHT = 150         # effect (CI) and weight text


def esc(text) -> str:
    return html.escape(str(text), quote=True)


def fmt(x, nd=2) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def ticks(lo: float, hi: float, count: int = 5) -> list[float]:
    span = hi - lo or 1.0
    step = 10 ** math.floor(math.log10(span / count))
    for m in (1, 2, 2.5, 5, 10):
        if span / (step * m) <= count:
            step *= m
            break
    first = math.ceil(lo / step) * step
    out, x = [], first
    while x <= hi + 1e-9:
        out.append(round(x, 10))
        x += step
    return out


def forest(rows: list[dict], result: dict, unit: str) -> str:
    """Each study's effect and 95% CI (square sized by weight), the pooled diamond and
    the prediction interval, on one axis with the line of no effect."""
    k = len(rows)
    cis = [(r["y"] - 1.96 * math.sqrt(r["v"]), r["y"] + 1.96 * math.sqrt(r["v"])) if r.get("v") else (r["y"], r["y"])
           for r in rows]
    values = [0.0] + [v for c in cis for v in c]
    for pair in (result.get("ci") or [], result.get("pi") or []):
        values += [v for v in pair if v is not None]
    lo, hi = min(values), max(values)
    pad = (hi - lo) * 0.08 or 1.0
    lo, hi = lo - pad, hi + pad
    x = lambda v: LEFT + (v - lo) / (hi - lo) * PLOT
    height = (k + 4) * ROW + 40
    width = LEFT + PLOT + RIGHT
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="Forest plot" '
           f'xmlns="http://www.w3.org/2000/svg" font-size="12">']
    out.append(f'<text x="8" y="16" class="head">研究</text><text x="{LEFT + PLOT + 8}" y="16" class="head">'
               f'差值 [95% CI]　权重</text>')
    weights = result.get("weights") or [100 / k] * k
    biggest = max(weights) if weights else 1
    for i, (r, (a, b), w) in enumerate(zip(rows, cis, weights)):
        y = 34 + i * ROW
        label = r["label"] if len(r["label"]) <= 44 else r["label"][:43] + "…"
        out.append(f'<text x="8" y="{y + 4}">{esc(label)}</text>')
        out.append(f'<line x1="{x(a):.1f}" x2="{x(b):.1f}" y1="{y}" y2="{y}" class="ci"/>')
        side = 4 + 8 * math.sqrt(w / biggest)
        out.append(f'<rect x="{x(r["y"]) - side / 2:.1f}" y="{y - side / 2:.1f}" width="{side:.1f}" '
                   f'height="{side:.1f}" class="{"imputed" if r.get("variance") != "reported" else "box"}"/>')
        out.append(f'<text x="{LEFT + PLOT + 8}" y="{y + 4}">{r["y"]:.2f} [{a:.2f}, {b:.2f}]　{w:.1f}%</text>')
    y = 34 + k * ROW + 8
    if result.get("mu") is not None and result.get("ci", [None])[0] is not None:
        mu, (a, b) = result["mu"], result["ci"]
        out.append(f'<text x="8" y="{y + 4}" class="head">合并（{"随机效应 DL" if result["method"] != "unweighted" else "未加权"}）</text>')
        out.append(f'<polygon points="{x(a):.1f},{y} {x(mu):.1f},{y - 7} {x(b):.1f},{y} {x(mu):.1f},{y + 7}" class="diamond"/>')
        out.append(f'<text x="{LEFT + PLOT + 8}" y="{y + 4}" class="head">{mu:.2f} [{a:.2f}, {b:.2f}]</text>')
        pi = result.get("pi") or [None, None]
        if pi[0] is not None:
            y2 = y + ROW
            out.append(f'<text x="8" y="{y2 + 4}">预测区间</text>')
            out.append(f'<line x1="{x(pi[0]):.1f}" x2="{x(pi[1]):.1f}" y1="{y2}" y2="{y2}" class="pi"/>')
            out.append(f'<text x="{LEFT + PLOT + 8}" y="{y2 + 4}">[{pi[0]:.2f}, {pi[1]:.2f}]</text>')
    axis = height - 24
    out.append(f'<line x1="{LEFT}" x2="{LEFT + PLOT}" y1="{axis}" y2="{axis}" class="axis"/>')
    out.append(f'<line x1="{x(0):.1f}" x2="{x(0):.1f}" y1="22" y2="{axis}" class="zero"/>')
    for t in ticks(lo, hi):
        out.append(f'<line x1="{x(t):.1f}" x2="{x(t):.1f}" y1="{axis}" y2="{axis + 4}" class="axis"/>'
                   f'<text x="{x(t):.1f}" y="{axis + 16}" text-anchor="middle">{t:g}</text>')
    out.append(f'<text x="{LEFT}" y="{axis - 4}" class="note">← 对照更好</text>'
               f'<text x="{LEFT + PLOT}" y="{axis - 4}" text-anchor="end" class="note">方法更好 →（{esc(unit)}）</text>')
    out.append("</svg>")
    return "\n".join(out)


def funnel(rows: list[dict], result: dict) -> str:
    """Effect against standard error (axis inverted), with the pooled effect and its
    pseudo 95% limits."""
    pts = [(r["y"], math.sqrt(r["v"])) for r in rows if r.get("v")]
    if len(pts) < 2 or result.get("mu") is None:
        return '<p class="note">研究太少或没有方差，不画漏斗图。</p>'
    mu = result["mu"]
    top = max(s for _, s in pts) * 1.1
    xs = [y for y, _ in pts] + [mu - 1.96 * top, mu + 1.96 * top]
    lo, hi = min(xs), max(xs)
    w, h, m = 420, 280, 40
    X = lambda v: m + (v - lo) / (hi - lo) * (w - 2 * m)
    Y = lambda s: m / 2 + s / top * (h - m - m / 2)
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px" role="img" aria-label="Funnel plot" '
           f'xmlns="http://www.w3.org/2000/svg" font-size="11">',
           f'<polyline points="{X(mu - 1.96 * top):.1f},{Y(top):.1f} {X(mu):.1f},{Y(0):.1f} '
           f'{X(mu + 1.96 * top):.1f},{Y(top):.1f}" class="limits"/>',
           f'<line x1="{X(mu):.1f}" x2="{X(mu):.1f}" y1="{Y(0):.1f}" y2="{Y(top):.1f}" class="zero"/>']
    for y, s in pts:
        out.append(f'<circle cx="{X(y):.1f}" cy="{Y(s):.1f}" r="4" class="dot"/>')
    out.append(f'<line x1="{m}" x2="{w - m}" y1="{h - m / 2}" y2="{h - m / 2}" class="axis"/>')
    for t in ticks(lo, hi):
        out.append(f'<text x="{X(t):.1f}" y="{h - 4}" text-anchor="middle">{t:g}</text>')
    out.append(f'<text x="4" y="{Y(0) + 4:.1f}">SE 0</text><text x="4" y="{Y(top):.1f}">{top:.2f}</text>')
    out.append("</svg>")
    return "\n".join(out)


STYLE = """
:root{--bg:#fff;--fg:#1d1d1f;--muted:#6b6b70;--line:#c8c8cc;--accent:#3f51b5;--imputed:#9aa0c8;--soft:#e8eaf6}
@media (prefers-color-scheme: dark){:root{--bg:#161618;--fg:#ececee;--muted:#a0a0a6;--line:#4a4a50;
--accent:#8c9eff;--imputed:#5c6390;--soft:#262a40}}
body{background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,"PingFang SC",system-ui,sans-serif;
margin:0 auto;max-width:900px;padding:16px}
h1{font-size:20px;margin:8px 0}h2{font-size:16px;margin:24px 0 8px}
p.note,.note{color:var(--muted);fill:var(--muted);font-size:12px}
svg text{fill:var(--fg)} svg .head{font-weight:600}
.ci,.axis{stroke:var(--fg);stroke-width:1}.zero{stroke:var(--muted);stroke-dasharray:3 3}
.box{fill:var(--accent)}.imputed{fill:var(--imputed)}.diamond{fill:var(--accent)}
.pi{stroke:var(--accent);stroke-width:3;opacity:.5}.limits{fill:none;stroke:var(--line)}
.dot{fill:var(--accent);opacity:.85}
table{border-collapse:collapse;font-size:13px}td,th{border-bottom:1px solid var(--line);padding:4px 8px;text-align:left}
"""


def page(question: str, rows: list[dict], result: dict, certainty: str, unit: str) -> str:
    summary = (f"合并差值 {fmt(result.get('mu'))} [{fmt((result.get('ci') or [None])[0])}, "
               f"{fmt((result.get('ci') or [None, None])[1])}]，{result.get('k', 0)} 项研究，"
               f"I² {fmt(result.get('i2'), 0)}%，tau² {fmt(result.get('tau2'), 3)}，证据确定性：{certainty}。")
    return "\n".join([
        "<!doctype html>", '<html lang="zh"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">', "<title>Forest plot</title>",
        f"<style>{STYLE}</style></head><body>",
        f"<h1>{esc(question)}</h1>", f"<p>{esc(summary)}</p>",
        "<h2>森林图</h2>", forest(rows, result, unit),
        '<p class="note">方块大小表示权重；浅色方块的方差是按其他研究的标准差插补的。差值为正表示方法优于对照。</p>',
        "<h2>漏斗图</h2>", funnel(rows, result),
        '<p class="note">纵轴是标准误（越往上越精确），斜线是合并效应的 95% 伪置信界。</p>',
        "</body></html>"])


__all__ = ["forest", "funnel", "page"]
