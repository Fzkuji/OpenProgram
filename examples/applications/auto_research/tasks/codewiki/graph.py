"""A call graph as SVG, laid out in plain code: nodes in columns by longest path from the
callers (cycles broken at the edge that closes them), ordered inside a column by the mean
position of their callers, edges as curves with arrowheads. No JavaScript, no library."""

from html import escape

CHAR = 7.0          # width of one character of the 12px label font
PAD = 12
HEIGHT = 28
GAP_Y = 14
GAP_X = 70
MAX_NODES = 60
LABEL = 34          # characters of a label before it is cut

STYLE = """
.n rect{fill:#f4f5fb;stroke:#5b5fc7;stroke-width:1.2}
.n.entry rect{fill:#e3e6ff;stroke:#3b3fa8;stroke-width:1.8}
.n.ext rect{fill:#fff;stroke:#9a9cb8;stroke-dasharray:3 2}
.n.paper rect{stroke:#0a7d55;stroke-width:2}
.n text{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;fill:#1c1d2b}
.e{fill:none;stroke:#8f92b8;stroke-width:1.1}
.e.back{stroke-dasharray:4 3}
.head{fill:#8f92b8}
a:hover rect{fill:#dfe2ff}
@media (prefers-color-scheme: dark){
.n rect{fill:#23253a;stroke:#8d91f0}.n.entry rect{fill:#2f3260;stroke:#b4b7ff}
.n.ext rect{fill:#1a1b26;stroke:#6b6e8f}.n.paper rect{stroke:#3ccf95}
.n text{fill:#e8e9f5}.e{stroke:#6b6e8f}.head{fill:#6b6e8f}a:hover rect{fill:#383b6a}}
"""


def layers(ids: list[str], edges: list[tuple[str, str]]) -> tuple[dict[str, int], set[tuple[str, str]]]:
    """Column of each node by longest path, and the edges that close a cycle (drawn back)."""
    out = {i: [] for i in ids}
    for a, b in edges:
        if a in out and b in out and a != b:
            out[a].append(b)
    state, back = {}, set()

    def visit(n):
        stack = [(n, iter(out[n]))]
        state[n] = 1
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
            elif state.get(nxt) == 1:
                back.add((node, nxt))
            elif nxt not in state:
                state[nxt] = 1
                stack.append((nxt, iter(out[nxt])))
    for i in ids:
        if i not in state:
            visit(i)
    forward = [(a, b) for a, b in edges if a in out and b in out and a != b and (a, b) not in back]
    col = {i: 0 for i in ids}
    for _ in range(len(ids)):
        changed = False
        for a, b in forward:
            if col[b] < col[a] + 1:
                col[b] = col[a] + 1
                changed = True
        if not changed:
            break
    return col, back


def cut(label: str) -> str:
    return label if len(label) <= LABEL else "…" + label[-(LABEL - 1):]


def svg(nodes: list[dict], edges: list[tuple[str, str]], title: str = "call graph") -> str:
    """nodes: {id, label, href?, cls?} (cls: entry, ext, paper); edges: (from id, to id)."""
    nodes = nodes[:MAX_NODES]
    ids = [n["id"] for n in nodes]
    known = set(ids)
    edges = sorted({(a, b) for a, b in edges if a in known and b in known and a != b})
    if not nodes:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="40" role="img" aria-label="{escape(title)}">'
                f'<text x="8" y="24" font-size="12">no calls between the project\'s functions</text></svg>')
    col, back = layers(ids, edges)
    columns: dict[int, list[str]] = {}
    for i in ids:
        columns.setdefault(col[i], []).append(i)
    preds = {i: [a for a, b in edges if b == i and (a, b) not in back] for i in ids}
    succs = {i: [b for a, b in edges if a == i and (a, b) not in back] for i in ids}
    rank = {}
    for c in sorted(columns):
        for k, i in enumerate(columns[c]):
            rank[i] = k
    for sweep in range(4):                  # barycenter ordering, down and up
        order = sorted(columns) if sweep % 2 == 0 else sorted(columns, reverse=True)
        for c in order:
            near = preds if sweep % 2 == 0 else succs

            def key(i):
                ns = [rank[p] for p in near[i]]
                return (sum(ns) / len(ns)) if ns else rank[i]
            columns[c].sort(key=key)
            for k, i in enumerate(columns[c]):
                rank[i] = k
    label = {n["id"]: cut(n["label"]) for n in nodes}
    width = {c: max(len(label[i]) for i in columns[c]) * CHAR + 2 * PAD for c in columns}
    x0, xs = {}, 10.0
    for c in sorted(columns):
        x0[c] = xs
        xs += width[c] + GAP_X
    tallest = max(len(v) for v in columns.values())
    total_h = tallest * (HEIGHT + GAP_Y) + 20
    pos = {}
    for c, members in columns.items():
        offset = (total_h - len(members) * (HEIGHT + GAP_Y)) / 2
        for k, i in enumerate(members):
            pos[i] = (x0[c], offset + k * (HEIGHT + GAP_Y), width[c])
    W, H = xs - GAP_X + 10, total_h + 30
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" height="{H:.0f}" '
             f'role="img" aria-label="{escape(title)}"><title>{escape(title)}</title><style>{STYLE}</style>',
             '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
             'orient="auto-start-reverse"><path class="head" d="M0,0L10,5L0,10z"/></marker></defs>']
    for a, b in edges:
        ax, ay, aw = pos[a]
        bx, by, bw = pos[b]
        if (a, b) in back or col[b] <= col[a]:
            sx, sy = ax + aw / 2, ay + HEIGHT
            ex, ey = bx + bw / 2, by + HEIGHT
            dip = max(sy, ey) + 40
            d = f"M{sx:.1f},{sy:.1f} C{sx:.1f},{dip:.1f} {ex:.1f},{dip:.1f} {ex:.1f},{ey + 2:.1f}"
            cls = "e back"
        else:
            sx, sy = ax + aw, ay + HEIGHT / 2
            ex, ey = bx - 2, by + HEIGHT / 2
            mid = (sx + ex) / 2
            d = f"M{sx:.1f},{sy:.1f} C{mid:.1f},{sy:.1f} {mid:.1f},{ey:.1f} {ex:.1f},{ey:.1f}"
            cls = "e"
        parts.append(f'<path class="{cls}" d="{d}" marker-end="url(#arrow)"><title>{escape(a)} → {escape(b)}</title></path>')
    for n in nodes:
        x, y, w = pos[n["id"]]
        box = (f'<g class="n {escape(n.get("cls", ""))}"><rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{HEIGHT}" rx="6"/>'
               f'<text x="{x + PAD:.1f}" y="{y + HEIGHT / 2 + 4:.1f}">{escape(label[n["id"]])}</text>'
               f'<title>{escape(n.get("tip") or n["label"])}</title></g>')
        parts.append(f'<a href="{escape(n["href"])}">{box}</a>' if n.get("href") else box)
    parts.append("</svg>")
    return "".join(parts)


__all__ = ["layers", "svg"]
