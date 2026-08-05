/**
 * Renderer: branch badge buttons below branch tip nodes.
 *
 * Each branch tip gets a small rounded-rect badge showing the branch
 * name (or short id). Non-active badges are clickable — clicking
 * sends ``checkout_branch`` + ``load_session`` via WS.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

import type { GNode } from "../types";
import { getSocket, runtimeState } from "../../state";
import { translateText } from "@/lib/i18n";
import { _branchColor, _svg, _textWidth } from "../shapes";
import { isSpawnRoot } from "../passes/thread";

// canvas 量宽不认 var()（见 shapes.ts SPAWN_FONT 注释），写死同一字体栈。
const BADGE_FONT =
  '500 9px "Inter Variable", "Inter", -apple-system, "PingFang SC", sans-serif';

export function drawBadges(
  svg: SVGElement,
  tree: { byId: Record<string, GNode> },
  pos: (n: GNode) => { x: number; y: number },
  stableLeafOfNode: Record<string, string>,
  sessionId: string | null,
  fullById: Record<string, GNode> = Object.create(null),
): void {
  const rows: GNode[] =
    ((sessionId && runtimeState._branchesByConv[sessionId]) as GNode[]) || [];
  // Badges come ONLY from list_branches' active rows. Merging erases the
  // badge (git semantics) — a merged branch's name lives on in the merge
  // node's tooltip, not as a lane pill (rendering.md §5).
  if (!rows.length) return;
  const tagG = _svg("g", { class: "history-branch-tags" });
  // 已放置的 badge 像素盒——碰撞按实测盒判定（rendering.md §5）。
  const placed: Array<{ x1: number; x2: number; y1: number; y2: number }> = [];
  const ROW_STEP = 32; // = layout ROW_H：碰撞下移一行
  // 一趟预扫代替每行分支各自两遍全图扫描（流式期间每帧重画徽章）：
  // 每条 lane 的最深可见节点（锚点），和每一列的最大 y（判"锚位正下方
  // 有竖线穿过"）。比较序与原逐行扫描一致：先比 y，再比 x。
  const deepestByLane: Record<number, GNode> = Object.create(null);
  const maxYByX: Record<number, number> = Object.create(null);
  Object.keys(tree.byId).forEach((id) => {
    const n = tree.byId[id];
    const np = pos(n);
    if (maxYByX[np.x] === undefined || np.y > maxYByX[np.x]) {
      maxYByX[np.x] = np.y;
    }
    if (n.display === "root" || n.display === "runtime") return;
    const lane = (n as any)._lane ?? 0;
    const d = deepestByLane[lane];
    if (!d) {
      deepestByLane[lane] = n;
      return;
    }
    const dp = pos(d);
    if (np.y > dp.y || (np.y === dp.y && np.x > dp.x)) deepestByLane[lane] = n;
  });
  rows.forEach((b) => {
    const hid = b.head_msg_id as string | undefined;
    if (!hid) return;
    // 第一步：从 head 沿 predecessor/caller 链上溯，取第一个可见的对话
    // 层节点，确定分支归属（lane）；最终锚点在下面按"lane 内最深可见
    // 节点"再算。
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
      const raw: GNode | undefined = n || fullById[cur];
      // 分支属于一个 spawn 的 agent：它在画布上就是那个三角形，分支
      // 药丸会是同一事实的第二种画法——不立（rendering.md §12）。
      // 先于对话层判定：spawn 根按 role 是 user，线程展开后它可见，
      // 后判就会命中 isConvLayer、把 agent 的药丸立在三角形底下。
      if (raw && isSpawnRoot(raw)) return;
      if (n && isConvLayer(n)) { node = n; break; }
      // 不可见/非对话层（折叠的执行节点、被过滤的 attach 尾指针）→ 用
      // 全量图继续沿链上溯。
      cur = raw ? ((raw.predecessor as string) || (raw.caller as string) || undefined) : undefined;
    }
    if (!node) return;
    // 锚定＝该分支**当前可见的最深节点**正下方（2026-07-31 裁定，
    // rendering.md §5）：执行子树展开时徽章跟到最底下的节点，
    // 收起时自动回到会话层节点。分支归属按 lane——展开的执行节点
    // 与所属轮次同 lane。查预扫表，不再逐行全图扫。
    const lane = (node as any)._lane ?? 0;
    node = deepestByLane[lane] ?? node;
    const p = pos(node);
    let bx = p.x;
    let by = p.y + 28;
    // 避让：锚位正下方有竖线穿过（对话延续 / 展开的执行子树在同一列往
    // 下走）时左偏半格——徽标永不压边。
    const hasLineBelow = maxYByX[p.x] !== undefined && maxYByX[p.x] > p.y;
    if (hasLineBelow) bx -= 16;
    const label = (b.name as string) || hid.slice(0, 8);
    const isActive = !!b.active;
    const color = _branchColor(node, stableLeafOfNode);
    // 碰撞：与已放置盒重叠 → 下移一行，直至无碰撞。
    // 顶部的分支条已删（分支切换只在这里），标签按按钮规格画：
    // 更大的字号/内边距 + 描边，hover 态在 right-dock.css。
    const bwPre = Math.max(Math.ceil(_textWidth(label, BADGE_FONT)) + 20, 52);
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
    const bh = 22;
    const rect = _svg("rect", {
      class: "history-branch-tag-bg",
      x: String(-bw / 2),
      y: String(-bh / 2),
      width: String(bw),
      height: String(bh),
      rx: "11",
      ry: "11",
      fill: isActive
        ? "color-mix(in srgb, " + color + " 14%, var(--bg-primary, #fff))"
        : "var(--bg-tertiary, #f2f0ea)",
      stroke: isActive ? color : "var(--border, #e4e2da)",
      "stroke-width": isActive ? "1.5" : "1",
    });
    tg.appendChild(rect);
    const tip = _svg("title", {});
    tip.textContent = isActive
      ? translateText("Current branch", "当前分支")
      : translateText("Click to switch to this branch", "点击切换到此分支");
    tg.appendChild(tip);
    const text = _svg("text", {
      x: "0",
      y: "0",
      "text-anchor": "middle",
      "dominant-baseline": "central",
      "font-size": "10.5",
      "font-family": "var(--font-sans, sans-serif)",
      "font-weight": isActive ? "600" : "500",
      fill: isActive
        ? "var(--text-bright, #1a1a17)"
        : "var(--text-secondary, #6b6a63)",
      "pointer-events": "none",
    });
    text.textContent = label;
    tg.appendChild(text);
    if (!isActive) {
      tg.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const sock = getSocket();
        if (sock && sock.readyState === WebSocket.OPEN) {
          sock.send(
            JSON.stringify({
              action: "checkout_branch",
              session_id: sessionId,
              head_msg_id: hid,
            }),
          );
          sock.send(
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
