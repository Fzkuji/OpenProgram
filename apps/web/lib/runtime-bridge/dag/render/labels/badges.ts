import { requestSessionLoad, sendRuntimeCommand } from "@/lib/runtime-bridge/session/session-load";
/**
 * Renderer: branch badge buttons below branch tip nodes.
 *
 * Each branch tip gets a small rounded-rect badge showing the branch
 * name (or short id). Non-active badges are clickable — clicking
 * sends ``checkout_branch`` + ``load_session`` via WS.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

import type { GNode } from "../../types";
import { getSocket, runtimeState } from "../../../state";
import { translateText } from "@/lib/i18n";
import { _branchColor, _svg, _textWidth } from "../shapes";
import { TILE_R, taskLabel } from "../tiles";
import { isSpawnRoot, type ThreadModel } from "../../passes/thread";
import { _currentHead } from "../../store/globals";

// canvas 量宽不认 var()（见 shapes.ts SPAWN_FONT 注释），写死同一字体栈。
// 字号/字重必须跟下面 <text> 实际画的一致（取 active 的 600 偏保守），
// 否则量出来的宽度偏窄，文字贴边。
const BADGE_FONT =
  '600 10.5px "Inter Variable", "Inter", -apple-system, "PingFang SC", sans-serif';
// agent 名牌用小一号：它是字形旁的标注，不是分支级按钮。
const BADGE_FONT_SMALL =
  '600 9px "Inter Variable", "Inter", -apple-system, "PingFang SC", sans-serif';

interface PlacedBox { x1: number; x2: number; y1: number; y2: number }

/** One badge pill: measured bg, tooltip, label, optional click. Shared
 *  by branch badges and open-agent badges so the two can never drift
 *  apart visually. Collision resolution against ``placed`` mutates it. */
function _drawPill(
  tagG: SVGElement,
  placed: PlacedBox[],
  bx: number,
  by: number,
  label: string,
  color: string,
  isActive: boolean,
  tipText: string,
  onClick: (() => void) | null,
  headAttr: string,
  small = false,
  /** "start": the pill begins at ``bx`` and runs right, a label for the
   *  tile above; "middle": centred on ``bx``. */
  align: "middle" | "start" = "middle",
  /** The tile the name sits beside; an agent's bubble there hides it. */
  anchorId = "",
): void {
  const ROW_STEP = 32;
  const font = small ? BADGE_FONT_SMALL : BADGE_FONT;
  const bw = Math.max(
    Math.ceil(_textWidth(label, font)) + (small ? 18 : 28), small ? 40 : 56);
  const PAD = 4;
  const x1 = align === "start" ? bx - PAD : bx - bw / 2;
  const x2 = align === "start" ? bx + bw - PAD : bx + bw / 2;
  const overlaps = (): boolean =>
    placed.some((r) =>
      x1 < r.x2 && x2 > r.x1
      && by - 10 < r.y2 && by + 10 > r.y1);
  let guard = 0;
  while (overlaps() && guard < 50) { by += ROW_STEP; guard++; }
  placed.push({ x1, x2, y1: by - 10, y2: by + 10 });
  const tg = _svg("g", {
    class: "history-branch-tag" + (isActive ? " active" : ""),
    transform: "translate(" + bx + "," + by + ")",
    "data-head": headAttr,
    ...(anchorId ? { "data-for": anchorId } : {}),
  });
  (tg as SVGGraphicsElement).style.cursor = onClick ? "pointer" : "default";
  const bh = small ? 17 : 22;
  tg.appendChild(_svg("rect", {
    class: "history-branch-tag-bg",
    x: String(x1 - bx),
    y: String(-bh / 2),
    width: String(bw),
    height: String(bh),
    rx: String(bh / 2),
    ry: String(bh / 2),
    // No background: the rect is only the click target; the name itself
    // carries the branch colour (app/styles/dag/graph/badges.css).
    fill: "transparent",
  }));
  const tip = _svg("title", {});
  tip.textContent = tipText;
  tg.appendChild(tip);
  const text = _svg("text", {
    x: "0",
    y: "0",
    "text-anchor": align,
    "dominant-baseline": "central",
    "font-size": small ? "9" : "10.5",
    "font-family": "var(--font-sans)",
    "font-weight": isActive ? "600" : "500",
    fill: isActive ? color : "var(--text-secondary)",
    "pointer-events": "none",
  });
  text.textContent = label;
  tg.appendChild(text);
  if (onClick) {
    tg.addEventListener("click", (ev) => {
      ev.stopPropagation();
      onClick();
    });
  }
  tagG.appendChild(tg);
}

function _sendCheckout(sessionId: string | null, headMsgId: string): void {
  const sock = getSocket();
  if (sock && sock.readyState === WebSocket.OPEN) {
    sendRuntimeCommand({
      action: "checkout_branch",
      session_id: sessionId,
      head_msg_id: headMsgId,
    });
    if (sessionId) requestSessionLoad({
      action: "load_session",
      session_id: sessionId,
    }, true);
  }
}

export function drawBadges(
  svg: SVGElement,
  tree: { byId: Record<string, GNode> },
  pos: (n: GNode) => { x: number; y: number },
  stableLeafOfNode: Record<string, string>,
  sessionId: string | null,
  fullById: Record<string, GNode> = Object.create(null),
  thread: ThreadModel | null = null,
): void {
  const rows: GNode[] =
    ((sessionId && runtimeState._branchesByConv[sessionId]) as GNode[]) || [];
  // Branch badges come ONLY from list_branches' active rows. Merging
  // erases the badge (git semantics) — a merged branch's name lives on
  // in the merge node's tooltip, not as a lane pill (rendering.md §5).
  // Open sub-agent badges below need no rows, so no early return here.
  const tagG = _svg("g", { class: "history-branch-tags" });
  // 已放置的 badge 像素盒——碰撞按实测盒判定（rendering.md §5）。
  const placed: PlacedBox[] = [];
  // 一趟预扫代替每行分支各自两遍全图扫描（流式期间每帧重画徽章）：
  // 每条 lane 的最深可见节点（锚点），和每一列的最大 y（判"锚位正下方
  // 有竖线穿过"）。比较序与原逐行扫描一致：先比 y，再比 x。
  const deepestByLane: Record<number, GNode> = Object.create(null);
  const maxYByX: Record<number, number> = Object.create(null);
  Object.keys(tree.byId).forEach((id) => {
    const n = tree.byId[id];
    const np = pos(n);
    // Every visible glyph is an obstacle: a badge that lands on a node
    // (an opened thread inserts items right where "below the anchor"
    // used to be empty) hides it, so the collision loop pushes the
    // badge down past it instead.
    placed.push({
      x1: np.x - 12, x2: np.x + 12, y1: np.y - 12, y2: np.y + 12,
    });
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
    // Only into the branch's OWN descendants: a sibling branch sharing
    // the lane (a scheduled job's reply threaded off a turn) would
    // otherwise drag this pill down under the other branch's tip.
    const lane = (node as any)._lane ?? 0;
    const deepest = deepestByLane[lane];
    if (deepest && deepest !== node) {
      let up: string | undefined = deepest.id;
      for (let i = 0; up && i < 10000; i++) {
        if (up === node.id) { node = deepest; break; }
        const m: GNode | undefined = fullById[up] || tree.byId[up];
        up = m ? ((m.predecessor as string) || (m.caller as string) || undefined) : undefined;
      }
    }
    const p = pos(node);
    // The name sits on the tip's own row, just right of the tile, the
    // way a message's caption sits beside it: "this tile's branch is X".
    // Under the tile it landed on the next row's line, and centred, a long
    // name reached over the neighbouring lane until the collision loop
    // pushed it below another branch's tip.
    const bx = p.x + TILE_R + 8;
    const by = p.y;
    // The server names an unnamed branch by its head id; the canvas says
    // what you asked on it instead — your words, never a reply's.
    let asked: GNode | undefined;
    for (let id: string | undefined = hid, i = 0; id && i < 10000 && !asked; i++) {
      const m: GNode | undefined = fullById[id];
      if (m?.role === "user" && taskLabel(m)) asked = m;
      id = m ? ((m.predecessor as string) || (m.caller as string) || undefined) : undefined;
    }
    const label = (b.is_named ? (b.name as string) : taskLabel(asked))
      || (b.name as string) || hid.slice(0, 8);
    const isActive = !!b.active;
    const color = _branchColor(node, stableLeafOfNode);
    _drawPill(
      tagG, placed, bx, by, label, color, isActive,
      isActive
        ? translateText("Current branch", "当前分支")
        : translateText("Click to switch to this branch", "点击切换到此分支"),
      isActive ? null : () => _sendCheckout(sessionId, hid),
      hid,
      false,
      "start",
      node.id,
    );
  });

  // ── Sub-agent badges (dag/rendering.md §12) ──
  // Shown whenever the spawn square itself is on screen — which
  // already means its owner's thread is open, the opt-in that keeps
  // the default canvas clean. The badge is the agent's name (the
  // canvas draws no captions) AND the switch: same pill, same verb —
  // clicking checks the agent chain's tip out as the active branch.
  if (thread) {
    Object.keys(tree.byId).forEach((sid) => {
      const root = tree.byId[sid];
      if (!isSpawnRoot(root)) return;
      // The agent chain's tip: walk conversation successors in the
      // FULL graph (its turns are merged into the spawn glyph here).
      let tipId = sid;
      const seen: Record<string, boolean> = Object.create(null);
      for (;;) {
        seen[tipId] = true;
        let next: string | null = null;
        for (const id of Object.keys(fullById)) {
          const n = fullById[id];
          if (n.predecessor === tipId && (!n.caller || n.caller === "ROOT")
              && !seen[id]) { next = id; break; }
        }
        if (!next) break;
        tipId = next;
      }
      const isActive = !!_currentHead && (tipId === _currentHead
        || _currentHead === sid);
      // The badge is the agent's NAME TAG: at the RIGHT of the agent
      // thread's LAST visible node, level with it (the square itself
      // when the thread is folded or empty). Branch badges keep their
      // own rule — directly below the branch tail — untouched.
      const evs = thread.events[sid] || [];
      let anchor: GNode = root;
      for (let i = evs.length - 1; i >= 0; i--) {
        const e = tree.byId[evs[i].id];
        if (e) { anchor = e; break; }
      }
      const p = pos(anchor);
      // An agent with no name goes by its task, never by its id.
      const label = thread.nameOf[sid] || taskLabel(root)
        || translateText("Sub-agent", "子 agent");
      const bw = Math.max(
        Math.ceil(_textWidth(label, BADGE_FONT_SMALL)) + 18, 40);
      const finalTip = tipId;
      _drawPill(
        tagG, placed, p.x + 26 + bw / 2, p.y, label,
        _branchColor(root, stableLeafOfNode), isActive,
        isActive
          ? translateText("Current branch", "当前分支")
          : translateText(
            "Click to take over this agent's branch",
            "点击接管这个 agent 的分支"),
        isActive ? null : () => _sendCheckout(sessionId, finalTip),
        finalTip,
        true,
      );
    });
  }
  svg.appendChild(tagG);
}
