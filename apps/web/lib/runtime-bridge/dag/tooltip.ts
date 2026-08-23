/**
 * History DAG node card — ONE window, two states.
 *
 * The same ``.history-tooltip`` element serves both interactions
 * (dag/rendering.md §11); there is never a second window:
 *
 *   * **Hover** → the brief state: what the node is, its tokens, a
 *     short output preview, its folded call count. Appears after a
 *     short delay so sweeping the cursor doesn't strobe;
 *     ``pointer-events: none``; gone on mouse-off.
 *   * **Right-click** → the SAME card expands in place
 *     (``expandTooltip``): full fields, longer previews, coverage /
 *     context / id, and the verbs (render/inspector.ts builds them)
 *     appended below. The card turns interactive and stays until a
 *     click lands elsewhere; hover cannot re-summon the brief state
 *     over it.
 *   * **Click** → the node's own action only (fold/unfold its thread).
 *
 * No row labels are reinvented — each line uses the actual schema key
 * (``name`` / ``input`` / ``output`` / ``model`` / ``label`` / ...),
 * plus the facts only the picture knows: the node's context-coverage
 * state and its folded call count, both read off the DOM flags the
 * node drawer stamped so card and picture cannot disagree.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

import { type GNode } from "./types";
import { translateText } from "@/lib/i18n";

/** Rough token count. The graph payload carries no measured count for
 *  user turns, and asking the backend per node for a number that only
 *  has to be indicative would be a request per click. ~4 chars/token is
 *  the standard English approximation; ``llm.output_tokens`` is used
 *  verbatim when the node actually has a measurement. */
function _estimateTokens(node: GNode): { n: number; exact: boolean } {
  const meta = (node.llm || {}) as Record<string, unknown>;
  const out = meta.output_tokens;
  if (typeof out === "number" && out > 0) return { n: out, exact: true };
  return { n: Math.max(1, Math.ceil(_bodyText(node).length / 4)), exact: false };
}

/** The node's main text body — shared with fork-and-edit, which drops
 *  it into the composer. */
export function _bodyText(node: GNode): string {
  const v = node.preview ?? node.content ?? node.output ?? "";
  return typeof v === "string" ? v : String(v);
}

const SHOW_DELAY_MS = 450;   // hover-stay before the card appears
const BRIEF_CHARS = 120;     // chars per body block, hover cut
const DETAIL_CHARS = 600;    // chars per body block, right-click cut
const GAP = 10;              // px gap between node and card

let _tooltip: HTMLDivElement | null = null;
let _showTimer = 0;
let _currentId: string | null = null;
let _detailOpen = false;

export function ensureTooltip(body: HTMLElement): HTMLDivElement {
  if (_tooltip && _tooltip.parentElement === body) return _tooltip;
  _tooltip = document.createElement("div");
  _tooltip.className = "history-tooltip";
  body.appendChild(_tooltip);
  return _tooltip;
}

/** Hide the hover state. A no-op while the card is expanded: mouse-off
 *  fires this, and the expanded card must survive the pointer's trip
 *  down to its own verbs. ``closeTooltipDetail`` is the way down. */
export function hideTooltip(): void {
  if (_detailOpen) return;
  if (_showTimer) {
    window.clearTimeout(_showTimer);
    _showTimer = 0;
  }
  _currentId = null;
  if (_tooltip) _tooltip.classList.remove("visible");
}

/** Collapse the expanded card entirely (elsewhere-click, verb run,
 *  re-render). Hover starts over from nothing afterwards. */
export function closeTooltipDetail(): void {
  if (!_detailOpen) return;
  _detailOpen = false;
  if (_tooltip) _tooltip.classList.remove("detail");
  hideTooltip();
}

export function resetTooltip(): void {
  _tooltip = null;
  _currentId = null;
  _detailOpen = false;
  if (_showTimer) {
    window.clearTimeout(_showTimer);
    _showTimer = 0;
  }
}

/** Show the brief card for ``node`` next to ``nodeRect`` (viewport
 *  coordinates). ``el`` is the node's ``<g>`` — the drawer's data-*
 *  flags on it carry the coverage / thread facts. Idempotent on
 *  repeated calls with the same node. */
export function showTooltip(
  body: HTMLElement,
  node: GNode,
  nodeRect: DOMRect,
  el?: Element | null,
): void {
  // While the card is expanded it belongs to the right-click, and the
  // raw-JSON layer is a reading surface of its own — hover must not
  // repaint either out from under the user. Gating at the entry (not
  // just once at expand time) is what holds: the cursor is still on
  // the node, and every mousemove re-enters here.
  if (_detailOpen || document.querySelector(".dag-inspector")) return;
  const tip = ensureTooltip(body);
  const id = String(node.id || "");

  if (id !== _currentId) {
    _currentId = id;
    tip.classList.remove("visible");
    if (_showTimer) window.clearTimeout(_showTimer);
    // 停留 SHOW_DELAY_MS 才出卡——扫过节点不弹窗。
    _showTimer = window.setTimeout(() => {
      if (_currentId !== id || !_tooltip) return;
      _tooltip.innerHTML = "";
      renderNodeInfo(_tooltip, node, el || null, false);
      _tooltip.classList.add("visible");
      _position(_tooltip, body, nodeRect);
      // 内容宽度在下一帧才量得准，再对一次位，防溢出右缘。
      requestAnimationFrame(() => {
        if (_currentId === id && _tooltip) _position(_tooltip, body, nodeRect);
      });
    }, SHOW_DELAY_MS);
  }
  // Repeated mousemoves over the same node only re-position an
  // already-visible card — they must not bypass the show delay.
  if (tip.classList.contains("visible")) _position(tip, body, nodeRect);
}

/** Expand THE card in place for ``node``: the detail rows plus the
 *  verb list ``menuEl`` (built by render/inspector.ts). If the brief
 *  state is already showing for this node it deepens where it stands —
 *  same window, more of it; otherwise the card appears where hover
 *  would have put it. The card turns interactive (`.detail` flips
 *  ``pointer-events``) and stays until ``closeTooltipDetail``. */
export function expandTooltip(
  node: GNode,
  el: Element,
  menuEl: HTMLElement,
): void {
  const body = document.querySelector(
    "#historyPanel .history-body",
  ) as HTMLElement | null;
  if (!body) return;
  const tip = ensureTooltip(body);
  if (_showTimer) {
    window.clearTimeout(_showTimer);
    _showTimer = 0;
  }
  _currentId = String(node.id || "");
  _detailOpen = true;
  tip.innerHTML = "";
  renderNodeInfo(tip, node, el, true);
  const sep = document.createElement("div");
  sep.className = "dag-menu-sep";
  tip.appendChild(sep);
  tip.appendChild(menuEl);
  tip.classList.add("detail", "visible");
  _position(tip, body, el.getBoundingClientRect());
  // 内容宽高在下一帧才量得准，再对一次位，防溢出视口。
  requestAnimationFrame(() => {
    if (_detailOpen && _tooltip) {
      _position(_tooltip, body, el.getBoundingClientRect());
    }
  });
}

// ── render ─────────────────────────────────────────────────────────

type Row =
  | { kind: "kv"; key: string; value: string }
  | { kind: "block"; key: string; value: string };

/** Render the node's info — header plus rows — into ``into``.
 *  ``detail: false`` is the hover cut (short previews, core rows);
 *  ``detail: true`` is the right-click cut (every field, long
 *  previews, coverage / context / id). Shared with the context menu
 *  so both surfaces read the same facts. */
export function renderNodeInfo(
  into: HTMLElement,
  node: GNode,
  el: Element | null,
  detail: boolean,
): void {
  _appendHeader(into, node, el);
  _rows(node, el, detail).forEach((row) => {
    if (row.kind === "block") {
      _appendBlock(into, row.key, row.value, detail ? DETAIL_CHARS : BRIEF_CHARS);
    } else {
      _appendKv(into, row.key, row.value);
    }
  });
}

function _kindLabel(node: GNode, el: Element | null): string {
  if (node.display === "root") return "root";
  if (Array.isArray((node as Record<string, unknown>).covers_ids)) {
    return "context/summary";
  }
  // A spawn root is a user turn by role, but "user" is the wrong answer
  // to "what am I looking at" — it is another agent (dag/rendering.md
  // §12), and this card is where its name lives now that the canvas
  // draws no captions.
  if ((node as Record<string, unknown>).source === "agent_spawn"
      && !node.predecessor) {
    const nm = (el?.getAttribute("data-spawn-name") || "").trim();
    return nm
      ? translateText(`sub-agent · ${nm}`, `子 agent · ${nm}`)
      : translateText("sub-agent", "子 agent");
  }
  const fn = node.function;
  if (fn === "attach") return "function call · attach";
  if (fn === "merge") return "function call · merge";
  if (node.role === "tool") {
    const name = (node.name as string | undefined) || fn;
    return name ? `function call · ${name}` : "function call";
  }
  return (node.role || "?").toString();
}

function _appendHeader(
  tip: HTMLElement,
  node: GNode,
  el: Element | null,
): void {
  const header = document.createElement("div");
  header.className = "history-tooltip-header";
  const title = document.createElement("div");
  title.className = "history-tooltip-kind";
  title.textContent = _kindLabel(node, el);
  header.appendChild(title);
  const chips: string[] = [];
  if (node.source && node.source !== "web") chips.push(node.source);
  if (node.is_error) chips.push("error");
  if (chips.length) {
    const meta = document.createElement("div");
    meta.className = "history-tooltip-chips";
    chips.forEach((c) => {
      const chip = document.createElement("span");
      chip.className = "history-tooltip-chip";
      chip.textContent = c;
      meta.appendChild(chip);
    });
    header.appendChild(meta);
  }
  tip.appendChild(header);
}

/** How the node stands relative to the next request, read off the DOM
 *  flags the node drawer stamped — the card can never disagree with
 *  the picture beside it. */
function _coverageText(el: Element): string {
  if (el.getAttribute("data-failed") === "1") {
    return translateText("failed turn · archived", "失败轮 · 已留档");
  }
  if (el.getAttribute("data-ghost") === "1") {
    return translateText("folded into a summary", "已折叠进摘要");
  }
  if (el.classList.contains("out-of-context")) {
    return translateText("not in coverage", "不在覆盖内");
  }
  return translateText("✓ in coverage", "✓ 在覆盖内");
}

/** The rows for one node. The brief cut keeps what identifies the node
 *  (name, tokens, output, fold count); the detail cut adds every field
 *  plus the standing facts (coverage, context state, id). */
function _rows(node: GNode, el: Element | null, detail: boolean): Row[] {
  const rows: Row[] = [];
  const fn = node.function;
  const role = node.role;

  if (role === "tool") {
    if (node.name) rows.push(_kv("name", String(node.name)));
    if (detail && typeof node.input === "string" && node.input) {
      rows.push(_block("input", node.input));
    }
    const out = _bodyText(node);
    if (out) rows.push(_block("output", out));
  } else if (fn === "attach" || fn === "merge") {
    if (node.attach_manual) rows.push(_kv("manual", "true"));
    if (node.attach_label) rows.push(_kv("label", String(node.attach_label)));
    if (detail) {
      if (node.attach_ref) rows.push(_kv("head_id", String(node.attach_ref)));
      if (node.attach_source_commit_id) {
        rows.push(_kv("source_commit_id", String(node.attach_source_commit_id)));
      }
    }
    const out = _bodyText(node);
    if (out) rows.push(_block("output", out));
  } else {
    let tokensShown = false;
    if (role === "assistant" || role === "llm") {
      const meta = (node.llm || {}) as Record<string, unknown>;
      if (typeof meta.model === "string" && meta.model) {
        rows.push(_kv("model", meta.model));
      }
      if (typeof meta.input_tokens === "number"
          || typeof meta.output_tokens === "number") {
        rows.push(_kv("tokens",
          `${meta.input_tokens ?? "?"} → ${meta.output_tokens ?? "?"}`));
        tokensShown = true;
      }
    }
    if (!tokensShown && node.display !== "root") {
      const tok = _estimateTokens(node);
      rows.push(_kv(
        tok.exact ? "tokens" : translateText("tokens (est.)", "tokens（估）"),
        tok.n.toLocaleString()));
    }
    const out = _bodyText(node);
    if (out) rows.push(_block("output", out));
  }

  if (Array.isArray((node as Record<string, unknown>).covers_ids)) {
    const rec = node as Record<string, unknown>;
    const tb = rec.tokens_before;
    const ta = rec.tokens_after;
    if (typeof tb === "number" && typeof ta === "number") {
      rows.push(_kv("tokens", `${tb} → ${ta}`));
    }
    const at = rec.compacted_at;
    if (typeof at === "number" && at > 0) {
      const d = new Date(at > 1e12 ? at : at * 1000);
      rows.push(_kv(
        translateText("compacted", "压缩于"),
        d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      ));
    }
  }

  // The picture's own facts (§11/§12). The fold count identifies the
  // node and stays on the brief cut; coverage / context / id are
  // standing detail.
  if (el) {
    const threadN = el.getAttribute("data-thread");
    if (threadN) {
      rows.push(_kv(translateText("calls", "调用"),
        translateText(String(threadN), `${threadN} 次`)));
    }
    if (detail) {
      const summaryN = el.getAttribute("data-summary");
      if (summaryN) {
        rows.push(_kv(translateText("covers", "覆盖"),
          translateText(`${summaryN} turns`, `${summaryN} 轮`)));
      }
      if (node.display !== "root") {
        rows.push(_kv(translateText("context", "上下文"), _coverageText(el)));
      }
    }
  }
  if (detail && node.display !== "root") {
    rows.push(_kv("id", String(node.id).slice(0, 12)));
  }
  return rows;
}

function _kv(key: string, value: string): Row {
  return { kind: "kv", key, value };
}

function _block(key: string, value: string): Row {
  return { kind: "block", key, value };
}

function _appendKv(tip: HTMLElement, key: string, value: string): void {
  const row = document.createElement("div");
  row.className = "history-tooltip-kv";
  const ks = document.createElement("span");
  ks.className = "history-tooltip-kv-key";
  ks.textContent = key;
  const vs = document.createElement("span");
  vs.className = "history-tooltip-kv-val";
  vs.textContent = value;
  row.appendChild(ks);
  row.appendChild(vs);
  tip.appendChild(row);
}

function _appendBlock(
  tip: HTMLElement,
  key: string,
  value: string,
  chars: number,
): void {
  const wrap = document.createElement("div");
  wrap.className = "history-tooltip-block";
  const lbl = document.createElement("div");
  lbl.className = "history-tooltip-label";
  lbl.textContent = key;
  wrap.appendChild(lbl);
  const bod = document.createElement("div");
  bod.className = "history-tooltip-body";
  bod.textContent = _clamp(value || "(empty)", chars);
  wrap.appendChild(bod);
  tip.appendChild(wrap);
}

function _clamp(s: string, n: number): string {
  const trimmed = s.replace(/\s+/g, " ").trim();
  if (trimmed.length <= n) return trimmed;
  return trimmed.slice(0, n).replace(/\s+\S*$/, "") + "…";
}

// ── position ───────────────────────────────────────────────────────

/** Anchor the card BELOW the node, never overlapping it. The user
 *  is looking at the node when they hover — the card must not
 *  obscure that. Card is ``fixed`` so it can float across any
 *  region of the page.
 *
 *  Order of preference (each clamped to viewport):
 *    1. Below the node (default).
 *    2. Above the node (if below would clip the bottom of the viewport).
 *    3. Side fallback (if both above/below would clip). */
function _position(tip: HTMLElement, body: HTMLElement, nodeRect: DOMRect): void {
  void body;
  tip.style.position = "fixed";
  const tw = tip.offsetWidth;
  const th = tip.offsetHeight;
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  // Vertical: below the node by default.
  let topPx = nodeRect.bottom + GAP;
  if (topPx + th > vh - 6) {
    const aboveTop = nodeRect.top - GAP - th;
    if (aboveTop >= 6) {
      topPx = aboveTop;
    } else {
      topPx = Math.max(6, vh - 6 - th);
    }
  }

  // Horizontal: center under the node, then clamp.
  let leftPx = nodeRect.left + nodeRect.width / 2 - tw / 2;
  if (leftPx + tw > vw - 6) leftPx = vw - 6 - tw;
  if (leftPx < 6) leftPx = 6;

  tip.style.left = leftPx + "px";
  tip.style.top = topPx + "px";
}
