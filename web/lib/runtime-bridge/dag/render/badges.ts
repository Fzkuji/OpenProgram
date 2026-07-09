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
): void {
  const rows =
    (sessionId && HGW._branchesByConv && HGW._branchesByConv[sessionId]) || [];
  if (!rows.length) return;
  const tagG = _svg("g", { class: "history-branch-tags" });
  rows.forEach((b) => {
    const hid = b.head_msg_id as string | undefined;
    if (!hid) return;
    const entry = tree.byId[hid];
    if (!entry) return;
    // Anchor the chip on the branch's LAST (deepest) *visible* node, not
    // its entry node. When the branch is collapsed that's the collapsed
    // group representative; expanded it's the bottom-most node. Same lane
    // as the entry, largest y among visible nodes in that lane.
    const lane = entry._lane || 0;
    let node = entry;
    let bottomY = pos(entry).y;
    Object.keys(tree.byId).forEach((id) => {
      const n = tree.byId[id];
      if ((n._lane || 0) !== lane) return;
      const y = pos(n).y;
      if (y > bottomY) {
        bottomY = y;
        node = n;
      }
    });
    const p = pos(node);
    const label = (b.name as string) || hid.slice(0, 8);
    const isActive = !!b.active;
    const dy = 28;
    const color = _branchColor(node, stableLeafOfNode);
    const tg = _svg("g", {
      class: "history-branch-tag" + (isActive ? " active" : ""),
      transform: "translate(" + p.x + "," + (p.y + dy) + ")",
      "data-head": hid,
    });
    (tg as SVGGraphicsElement).style.cursor = isActive ? "default" : "pointer";
    // 背景宽 = 实测文字宽 + 左右各 6px 内边距，下限 40。
    const bw = Math.max(Math.ceil(_textWidth(label)) + 12, 40);
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
