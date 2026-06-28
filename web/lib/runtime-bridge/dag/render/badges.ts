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
    const node = tree.byId[hid];
    if (!node) return;
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
    const bw = Math.max(label.length * 6 + 12, 40);
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
