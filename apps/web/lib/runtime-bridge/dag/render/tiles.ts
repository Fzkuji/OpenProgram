/**
 * Renderer: node tiles (dag/rendering.md §4).
 *
 * Every node is a round chip tinted with its colour, icon in full colour:
 * a call gets the icon and tone
 * `presentTool` gives it there, so "ran npm test" looks alike in the
 * transcript and on the graph. Conversation nodes get their own icons:
 * your message, the session start, a compaction summary, a merge. An
 * agent's reply and a sub-agent's start wear the agent's character
 * (./agent-look.ts), drawn flat in the chip's colour with its eyes cut
 * through, like any icon; render/nodes.ts gives those chips their branch's
 * colour, as it does your messages.
 *
 * The tile's colour comes from `--tone` (app/styles/tool-tones.css), set
 * by a `t-<tone>` class, so status, coverage and theme are CSS states of
 * the same element rather than repainted attributes.
 */

import { presentTool, type ToolTone } from "@/components/chat/messages/execution/tool-presentation";
import { SOLAR_BODIES, SOLAR_VIEWBOXES, type SolarIconName } from "@/components/solar-icons/bodies";
import { botGlyph } from "@/components/avatar/bot/glyph";
import { getLocale, translateText } from "@/lib/i18n";
import { type GNode } from "../types";
import { isSpawnRoot } from "../passes/thread";
import { _svg } from "./shapes";
import { mainAgentShape, subAgentShape } from "./agent-look";

export type TileTone = ToolTone | "user" | "llm" | "root";

export interface Tile {
  icon: SolarIconName;
  /** An agent's character, drawn in place of the icon. */
  bot?: string;
  tone: TileTone;
  /** What the node does, for the agent's activity bubble. */
  doing: string;
  /** What the node did, for its caption on the canvas (render/labels/captions.ts);
   *  empty for kinds that need none. */
  did?: string;
}

/** The chip's radius. On the 40px grid (types.ts) a 28px chip leaves a
 *  12px gap, so a belt between two chips stays visible. */
export const TILE_R = 14;
const ICON = 16;

// The transcript names a call by what it did ("Ran", "Edited"); a bubble
// over a working agent says what it is doing. Chinese titles are plain
// verbs and read right either way.
const NOW: Record<string, string> = {
  "Analyzed image": "Analyzing image", Asked: "Asking", Edited: "Editing",
  "Executed code": "Executing code", Fetched: "Fetching", "Found files": "Finding files",
  "Generated image": "Generating image", "Handed to a new agent": "Handing to a new agent",
  "Inspected code": "Inspecting code", Listed: "Listing", Messaged: "Messaging",
  Patched: "Patching", "Ran program": "Running program", Ran: "Running",
  "Read conversation": "Reading conversation", Read: "Reading", Scheduled: "Scheduling",
  "Searched code": "Searching code", "Searched the web": "Searching the web",
  Searched: "Searching", Typed: "Typing", "Updated todos": "Updating todos",
  "Used skill": "Using skill", "Waited for output": "Waiting for output", Wrote: "Writing",
};

function toolName(node: GNode): string {
  return String(node.name || node.function || "");
}

/** ``presentTool`` parses the call's JSON arguments, and every capture
 *  repaints every tile — three times over (nodes, captions, agents) —
 *  so its answers are kept per name, arguments and language. Bounded:
 *  a long session's worth, then it starts over. */
const _presented = new Map<string, ReturnType<typeof presentTool>>();

function presented(name: string, input: string | undefined): ReturnType<typeof presentTool> {
  const key = `${getLocale()}\0${name}\0${input ?? ""}`;
  let shown = _presented.get(key);
  if (!shown) {
    if (_presented.size >= 4000) _presented.clear();
    shown = presentTool(name, input, translateText);
    _presented.set(key, shown);
  }
  return shown;
}

export function tileFor(node: GNode): Tile {
  const record = node as Record<string, unknown>;
  if (node.display === "root") {
    return { icon: "home-2", tone: "root", doing: translateText("Starting", "开始") };
  }
  if (Array.isArray(record.covers_ids) || record.superseded_summary) {
    return { icon: "archive", tone: "memory", doing: translateText("Compacting", "压缩上下文") };
  }
  if (isSpawnRoot(node)) {
    return { icon: "ghost", bot: subAgentShape(spawnName(node)), tone: "agent", doing: translateText("Handing to an agent", "交给子 agent") };
  }
  if (node.function === "merge") {
    return { icon: "git-pull-request", tone: "plan", doing: translateText("Merging", "合并分支") };
  }
  if (node.function === "attach") {
    return { icon: "link", tone: "plan", doing: translateText("Attaching", "挂接分支") };
  }
  const isCall = node.role === "tool" || node.display === "runtime"
    || (node.role === "assistant" && !!node.function);
  if (isCall) {
    const input = typeof node.input === "string" ? node.input : undefined;
    const shown = presented(toolName(node), input);
    const title = NOW[shown.title] ?? shown.title;
    const doing = shown.target ? `${title} ${shown.target}` : title;
    const did = shown.target ? `${shown.title} ${shown.target}` : shown.title;
    return { icon: shown.icon, tone: shown.tone, doing, did };
  }
  if (node.role === "assistant" || node.role === "llm") {
    return { icon: "ghost", bot: mainAgentShape(), tone: "llm", doing: translateText("Thinking…", "思考中…") };
  }
  if (node.message_from) {
    return { icon: "user-rounded", tone: "user",
      doing: translateText("Reading a message", "读取消息"),
      did: `${node.message_from}: ${saidIn(node)}` };
  }
  return { icon: "user-rounded", tone: "user",
    doing: translateText("Reading your message", "读取你的消息"), did: saidIn(node) };
}

/** What a message of yours says, in words: an attachment's raw marker
 *  ("[attachment: a.png (png, 12 KB) @json …]") gives way to the text
 *  after it, or to the file's name when there is none. */
function saidIn(node: GNode): string {
  const said = String(node.preview || "").replace(/\s+/g, " ").trim();
  const m = /^\[attachment: ([^\]]*?)(?: \([^)]*\))?(?: @json[^\]]*)?(?:\]\s*(.*))?$/.exec(said);
  if (!m) return said;
  if (m[2]) return m[2];
  const name = m[1].replace(/…$/, "");
  return translateText(`Attached ${name}`, `附件 ${name}`);
}

/** A short name for an agent that has none: the start of its task. */
export function taskLabel(root: GNode | undefined): string {
  const text = root ? saidIn(root) : "";
  if (!text || text.startsWith("[")) return "";
  const chars = Array.from(text);
  return chars.length > 14 ? chars.slice(0, 13).join("") + "…" : text;
}

/** A sub-agent's name as its start node records it: the label the runner
 *  stamped, else its branch's name, else the start of its task. */
export function spawnName(root: GNode | undefined): string {
  const record = root as Record<string, unknown> | undefined;
  const label = (record?.spawned_from as { label?: string } | undefined)?.label
    || (record?.branch_name as string | undefined);
  return label?.trim() || taskLabel(root);
}

/** The tile group: a round chip of the node's own colour and its icon
 *  (dag/rendering.md §4). Every kind of node is the same chip — your
 *  message, a reply, a call — so they read as one family; states restyle
 *  the chip itself (app/styles/dag/graph/nodes.css), never add a frame. */
export function buildTile(tile: Tile): SVGElement {
  const g = _svg("g", { class: `dag-tile t-${tile.tone}`, "pointer-events": "none" });
  g.appendChild(_svg("circle", { r: TILE_R, "data-node-shape": "1", "data-shape": "tile" }));
  g.appendChild((tile.bot ? botIconFor(tile.bot) : iconFor(tile.icon)).cloneNode(true));
  return g;
}

/** One parsed icon per name, cloned into each tile: parsing the body's
 *  markup for every tile on every capture was most of a tile's cost. */
const _icons = new Map<string, SVGElement>();

function iconFor(name: SolarIconName): SVGElement {
  let icon = _icons.get(name);
  if (!icon) {
    icon = _svg("svg", {
      x: -ICON / 2, y: -ICON / 2, width: ICON, height: ICON,
      viewBox: SOLAR_VIEWBOXES[name] ?? "0 0 24 24", class: "dag-tile-icon", "aria-hidden": "true",
    });
    icon.innerHTML = SOLAR_BODIES[name];
    _icons.set(name, icon);
  }
  return icon;
}

function botIconFor(shape: string): SVGElement {
  const key = `bot:${shape}`;
  let icon = _icons.get(key);
  if (!icon) {
    const glyph = botGlyph(shape);
    icon = _svg("svg", {
      x: -ICON / 2, y: -ICON / 2, width: ICON, height: ICON,
      viewBox: glyph.viewBox, class: "dag-tile-icon", "aria-hidden": "true", "data-bot-glyph": shape,
    });
    icon.appendChild(_svg("path", { d: glyph.path, fill: "currentColor" }));
    _icons.set(key, icon);
  }
  return icon;
}
