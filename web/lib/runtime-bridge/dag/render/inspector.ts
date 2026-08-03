/**
 * Renderer: the node context menu (and the raw-JSON layer it opens).
 *
 * The hover card (../tooltip.ts) is a node's ONE info surface; a click
 * is the node's own action (fold/unfold its thread). What remains here
 * is the surface for VERBS:
 *
 *   * **Right-click a node** → menu: checkout, fork, fork-and-edit
 *     (user turns only), copy id, raw JSON (dag/rendering.md §11).
 *
 * Every action is an existing operation, reached by its existing
 * route. Nothing here invents a verb:
 *
 *   * checkout / fork → `POST /api/chat/checkout`. Fork is checkout
 *     plus intent — moving HEAD onto a node is exactly what makes the
 *     next turn a sibling of whatever followed it, which is how the
 *     transcript's own "branch from here" button works.
 *   * fork & edit → the same checkout, then the message text into the
 *     composer. The user edits and sends; the send is an ordinary send
 *     against the new HEAD, so it forks with no protocol change and no
 *     "send from node X" concept to maintain.
 *
 * Built imperatively because the graph is: these float over an SVG the
 * renderer owns, keyed to node geometry, outside React's tree.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

import type { GNode } from "../types";
import { runtimeState } from "../../state";
import { useSessionStore } from "../../../session-store";
import { showToast } from "@/lib/format-utils/toast";

/** Rough token count. The graph payload carries no measured count for
 *  user turns, and asking the backend per node for a number that only
 *  has to be indicative would be a request per click. ~4 chars/token is
 *  the standard English approximation; ``llm.input_tokens`` is used
 *  verbatim when the node actually has a measurement. */
export function _estimateTokens(node: GNode): { n: number; exact: boolean } {
  const meta = (node.llm || {}) as Record<string, unknown>;
  const out = meta.output_tokens;
  if (typeof out === "number" && out > 0) return { n: out, exact: true };
  const text = _bodyText(node);
  return { n: Math.max(1, Math.ceil(text.length / 4)), exact: false };
}

function _bodyText(node: GNode): string {
  const v = node.preview ?? node.content ?? node.output ?? "";
  return typeof v === "string" ? v : String(v);
}

function _roleLabel(node: GNode, el?: Element): string {
  if (node.display === "root") return "root";
  if (Array.isArray((node as Record<string, unknown>).covers_ids)) {
    return "context/summary";
  }
  // A spawn root is a user turn by role, but "user" is the wrong answer
  // to "what am I looking at" — it is the head of another agent's chain
  // (dag/rendering.md §12). The name comes off the DOM because the fold
  // pass is what resolves it, and the picture must not disagree.
  if ((node as Record<string, unknown>).source === "agent_spawn"
      && !node.predecessor) {
    const nm = (el?.getAttribute("data-spawn-name") || "").trim();
    return nm ? `子 agent · ${nm}` : "子 agent";
  }
  if (node.role === "tool") {
    const name = (node.name as string | undefined) || node.function;
    return name ? `tool · ${name}` : "tool";
  }
  return String(node.role || "node");
}

// ── shared shell ───────────────────────────────────────────────────

let _layer: HTMLElement | null = null;

function _closeLayer(): void {
  if (_layer && _layer.parentElement) _layer.parentElement.removeChild(_layer);
  _layer = null;
}

/** Mount ``el`` as the one floating layer, anchored near ``rect`` and
 *  clamped inside the viewport. A second call replaces the first —
 *  only ever one popover, so a click elsewhere never leaves a trail. */
function _openLayer(el: HTMLElement, rect: DOMRect): void {
  _closeLayer();
  el.style.position = "fixed";
  el.style.visibility = "hidden";
  document.body.appendChild(el);
  _layer = el;
  const w = el.offsetWidth;
  const h = el.offsetHeight;
  let left = rect.right + 12;
  if (left + w > window.innerWidth - 8) left = Math.max(8, rect.left - 12 - w);
  let top = rect.top - 8;
  if (top + h > window.innerHeight - 8) top = Math.max(8, window.innerHeight - 8 - h);
  el.style.left = `${left}px`;
  el.style.top = `${top}px`;
  el.style.visibility = "visible";
}

export function closeNodeLayers(): void {
  _closeLayer();
}

// ── actions ────────────────────────────────────────────────────────

function _copy(text: string, toast: string): void {
  const done = () => showToast(toast);
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done, done);
  } else {
    done();
  }
}

/** A checkout/fork target is a CHAIN-level turn. On disk those carry
 *  ``caller`` of "ROOT" (user turns, ROOT-hung records) or "" (reply
 *  nodes); a node whose caller is ANOTHER CALL is function-internal
 *  machinery, which the backend rejects — so the actions are not
 *  offered there in the first place. Must mirror the caller-based gate
 *  in webui/_chat_routes.py. */
function _isChainTurn(node: GNode): boolean {
  if (node.display === "root") return false;
  const c = node.caller;
  return c == null || c === "" || c === "ROOT";
}

/** Move HEAD onto ``id``. This is checkout AND fork: the difference is
 *  only what you do next, because the next turn sent from a HEAD that
 *  has children is by definition a sibling of them. */
async function _checkoutTo(id: string): Promise<boolean> {
  const sid = runtimeState.currentSessionId;
  if (!sid) return false;
  try {
    const r = await fetch("/api/chat/checkout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sid, msg_id: id }),
    });
    if (!r.ok) {
      const j = await r.json().catch(() => null);
      showToast(j?.error || "checkout 失败", { tone: "error" });
      return false;
    }
  } catch {
    showToast("checkout 失败", { tone: "error" });
    return false;
  }
  runtimeState._postCheckoutScrollTo = id;
  const w = window as Window & { ws?: WebSocket };
  if (w.ws && w.ws.readyState === WebSocket.OPEN) {
    w.ws.send(JSON.stringify({ action: "load_session", session_id: sid }));
  }
  return true;
}

/**
 * Fork and edit a user turn.
 *
 * Checkout to the turn's PREDECESSOR, then drop its text in the
 * composer. Sending from there writes a sibling of the original — the
 * same shape `POST /api/chat/edit` produces, reached with the routes
 * that already exist. The predecessor is the fork point precisely
 * because the edited message has to stand beside the original, not
 * after it.
 *
 * A first turn carries the "ROOT" sentinel as its predecessor, which
 * the checkout route accepts like any other node.
 *
 * Strictly the conversation edge — no `caller` fallback. A spawn
 * branch root has predecessor=None and caller=<spawning node in the
 * PARENT branch>, and forking there must not check out into a
 * different branch.
 */
export async function forkAndEditNode(node: GNode): Promise<void> {
  const pivot = node.predecessor;
  if (!pivot) {
    showToast("找不到分叉点", { tone: "error" });
    return;
  }
  if (!(await _checkoutTo(String(pivot)))) return;
  const store = useSessionStore.getState();
  store.setComposerInput(_bodyText(node));
  store.focusComposer();
  showToast("已回到分叉点，改完发送即开新分支");
}

function _showRawJson(node: GNode, rect: DOMRect): void {
  const box = document.createElement("div");
  box.className = "dag-inspector dag-inspector-raw";
  const head = document.createElement("div");
  head.className = "dag-inspector-title";
  head.textContent = `${_roleLabel(node)} · raw`;
  box.appendChild(head);
  const pre = document.createElement("pre");
  pre.className = "dag-inspector-raw-body";
  pre.textContent = JSON.stringify(node, _dropRenderKeys, 2);
  box.appendChild(pre);
  const acts = document.createElement("div");
  acts.className = "dag-inspector-actions";
  acts.appendChild(_actionButton("复制", () => {
    _copy(JSON.stringify(node, _dropRenderKeys, 2), "已复制 JSON");
  }));
  box.appendChild(acts);
  _openLayer(box, rect);
}

/** Drop the renderer's own scratch fields from the raw view. ``_depth``
 *  / ``_lane`` / ``children`` are layout output, not node data, and
 *  ``children`` is cyclic enough to blow up ``JSON.stringify``. */
function _dropRenderKeys(key: string, value: unknown): unknown {
  if (key === "children" || key.startsWith("_")) return undefined;
  return value;
}

function _actionButton(label: string, onClick: () => void): HTMLElement {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "dag-inspector-action";
  b.textContent = label;
  b.addEventListener("click", (e) => {
    e.stopPropagation();
    onClick();
  });
  return b;
}

// ── context menu ───────────────────────────────────────────────────

function _menuItem(label: string, onClick: () => void, muted = false): HTMLElement {
  const d = document.createElement("button");
  d.type = "button";
  d.className = "dag-menu-item" + (muted ? " is-muted" : "");
  d.textContent = label;
  d.addEventListener("click", (e) => {
    e.stopPropagation();
    _closeLayer();
    onClick();
  });
  return d;
}

export function showNodeMenu(node: GNode, el: Element, at: DOMRect): void {
  const menu = document.createElement("div");
  menu.className = "dag-menu";
  menu.addEventListener("click", (e) => e.stopPropagation());

  const id = String(node.id);
  // Only chain-level turns are checkout/fork targets — a
  // function-internal node (caller nested under another call) is
  // execution machinery, and the backend rejects it. Offering the
  // action and then toasting the rejection was worse than not
  // offering it.
  if (_isChainTurn(node)) {
    menu.appendChild(_menuItem("checkout 到此分支", () => { void _checkoutTo(id); }));
    menu.appendChild(_menuItem("从此节点 fork", () => { void _checkoutTo(id); }));
    // Editing means writing a REPLACEMENT for a message, so it only has
    // meaning where a message is the user's own words.
    if (node.role === "user" && node.display !== "root") {
      menu.appendChild(_menuItem("fork 并编辑此消息", () => { void forkAndEditNode(node); }));
    }
  }
  const sep = document.createElement("div");
  sep.className = "dag-menu-sep";
  menu.appendChild(sep);
  menu.appendChild(_menuItem("复制节点 id", () => {
    _copy(id, "已复制节点 id");
  }, true));
  menu.appendChild(_menuItem("查看原始 JSON", () => {
    _showRawJson(node, el.getBoundingClientRect());
  }, true));

  _openLayer(menu, at);
}
