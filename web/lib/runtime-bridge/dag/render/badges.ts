/**
 * Renderer: branch badge buttons below branch tip nodes.
 *
 * Each branch tip gets a small rounded-rect badge showing the branch
 * name (or short id). Non-active badges are clickable — clicking
 * sends ``checkout_branch`` + ``load_session`` via WS.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

import { type GNode, HGW } from "../types";
import { _branchColor, _svg } from "../shapes";

// 量文字实际像素宽（复用一个 canvas）。标签字号 9px。原来按
// label.length*6 估，对中文（字宽≈字号）严重低估，导致背景比文字短、
// 文字溢出。实测才能中英文都贴合。
let _measureCtx: CanvasRenderingContext2D | null = null;
function _textWidth(s: string): number {
  if (!_measureCtx) {
    const c = document.createElement("canvas");
    _measureCtx = c.getContext("2d");
    if (_measureCtx) {
      _measureCtx.font =
        "500 9px var(--font-sans, -apple-system, sans-serif)";
    }
  }
  if (!_measureCtx) return s.length * 8; // 拿不到 canvas 时保守按 8px/字
  return _measureCtx.measureText(s).width;
}

export function drawBadges(
  svg: SVGElement,
  tree: { byId: Record<string, GNode> },
  pos: (n: GNode) => { x: number; y: number },
  stableLeafOfNode: Record<string, string>,
  sessionId: string | null,
  fullById: Record<string, GNode> = Object.create(null),
): void {
  const rows =
    (sessionId && HGW._branchesByConv && HGW._branchesByConv[sessionId]) || [];
  // Badges come ONLY from list_branches' active rows. Merging erases the
  // badge (git semantics) — a merged branch's name lives on in the merge
  // node's tooltip, not as a lane pill (dag-rendering.md §5).
  if (!rows.length) return;
  const tagG = _svg("g", { class: "history-branch-tags" });
  // 已放置的 badge 像素盒——碰撞按实测盒判定（dag-rendering.md §5）。
  const placed: Array<{ x1: number; x2: number; y1: number; y2: number }> = [];
  const ROW_STEP = 32; // = layout ROW_H：碰撞下移一行
  rows.forEach((b) => {
    const hid = b.head_msg_id as string | undefined;
    if (!hid) return;
    // 锚定＝分支链**最后一个对话层节点**的正下方（不看执行层节点，执行
    // 层展开收起不挪 badge）。从 head 沿 predecessor/caller 链上溯，取第
    // 一个可见的对话层节点。
    const isConvLayer = (n: GNode): boolean =>
      (n.role === "user" || n.role === "assistant")
      && n.display !== "runtime" && n.display !== "root" && !n._runNode;
    let node: GNode | null = null;
    let cur: string | undefined = hid;
    let hops = 0;
    const seen: Record<string, boolean> = Object.create(null);
    while (cur && !seen[cur] && hops < 200) {
      seen[cur] = true;
      hops++;
      const n: GNode | undefined = tree.byId[cur];
      if (n && isConvLayer(n)) { node = n; break; }
      // 不可见/非对话层（折叠的执行节点、被过滤的 attach 尾指针）→ 用
      // 全量图继续沿链上溯。
      const raw: GNode | undefined = n || fullById[cur];
      cur = raw ? ((raw.predecessor as string) || (raw.caller as string) || undefined) : undefined;
    }
    if (!node) return;
    const p = pos(node);
    let bx = p.x;
    let by = p.y + 28;
    // 避让：锚位正下方有竖线穿过（对话延续 / 展开的执行子树在同一列往
    // 下走）时左偏半格——徽标永不压边。
    const hasLineBelow = Object.keys(tree.byId).some((id) => {
      const n = tree.byId[id];
      const np = pos(n);
      return np.x === p.x && np.y > p.y;
    });
    if (hasLineBelow) bx -= 16;
    const label = (b.name as string) || hid.slice(0, 8);
    const isActive = !!b.active;
    const color = _branchColor(node, stableLeafOfNode);
    // 碰撞：与已放置盒重叠 → 下移一行，直至无碰撞。
    const bwPre = Math.max(Math.ceil(_textWidth(label)) + 12, 40);
    const overlaps = (): boolean =>
      placed.some((r) =>
        bx - bwPre / 2 < r.x2 && bx + bwPre / 2 > r.x1
        && by - 10 < r.y2 && by + 10 > r.y1);
    let guard = 0;
    while (overlaps() && guard < 50) { by += ROW_STEP; guard++; }
    placed.push({ x1: bx - bwPre / 2, x2: bx + bwPre / 2, y1: by - 10, y2: by + 10 });
    const tg = _svg("g", {
      class: "history-branch-tag" + (isActive ? " active" : ""),
      transform: "translate(" + bx + "," + by + ")",
      "data-head": hid,
    });
    (tg as SVGGraphicsElement).style.cursor = isActive ? "default" : "pointer";
    // 背景宽 = 实测文字宽 + 左右各 6px 内边距，下限 40（碰撞判定同款盒）。
    const bw = bwPre;
    const bh = 18;
    const rect = _svg("rect", {
      x: String(-bw / 2),
      y: String(-bh / 2),
      width: String(bw),
      height: String(bh),
      rx: "6",
      ry: "6",
      fill: "var(--bg-hover, #2e2e2c)",
      opacity: "0.85",
    });
    tg.appendChild(rect);
    const text = _svg("text", {
      x: "0",
      y: "0",
      "text-anchor": "middle",
      "dominant-baseline": "central",
      "font-size": "9",
      "font-family": "var(--font-sans, sans-serif)",
      "font-weight": "500",
      fill: isActive
        ? "var(--text-bright, #f8f8f6)"
        : "var(--text-muted, #6b6a63)",
      "pointer-events": "none",
    });
    text.textContent = label;
    tg.appendChild(text);
    if (!isActive) {
      tg.addEventListener("click", (ev) => {
        ev.stopPropagation();
        if (HGW.ws && HGW.ws.readyState === WebSocket.OPEN) {
          HGW.ws.send(
            JSON.stringify({
              action: "checkout_branch",
              session_id: sessionId,
              head_msg_id: hid,
            }),
          );
          HGW.ws.send(
            JSON.stringify({
              action: "load_session",
              session_id: sessionId,
            }),
          );
        }
      });
    }
    tagG.appendChild(tg);
  });
  svg.appendChild(tagG);
}
