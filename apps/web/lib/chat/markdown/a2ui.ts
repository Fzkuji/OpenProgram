/**
 * A2UI surfaces inside chat markdown.
 *
 * A reply can carry a laid-out view (a status card, a metric row, a
 * checklist) as a fenced ```a2ui block holding A2UI v0.9 JSON — Google's
 * open generative-UI format (https://a2ui.org): a flat list of components,
 * each with an `id`, containers naming their children by id, and the tree
 * starting at the component whose id is `root`. The block may be the bare
 * `updateComponents` payload `{"components":[…]}`, full envelope messages
 * (`updateComponents` / `updateDataModel`, as one JSON value or JSONL), or
 * a bare component array.
 *
 * Only the display part of the basic catalog renders — Card, Column, Row,
 * List, Tabs, Text, Divider, Icon, Image and a read-only CheckBox — styled
 * by the app theme. Interactive components (Button, TextField, …) have no
 * agent to answer them in a chat transcript and are left out, as are
 * function calls. Text is escaped by the caller-supplied markdown hook;
 * every attribute value here is an escaped literal or comes from a fixed
 * table, so the output needs no second sanitizer pass.
 *
 * Anything that does not parse into a tree with a `root` renders as the
 * plain code block it would have been, so a malformed surface never hides
 * the model's text.
 */
import { A2UI_ICONS } from "./a2ui-icons.ts";
import { retagOpenFence } from "./open-fence.ts";

export const A2UI_LANG = "a2ui";
/** Info string given to a fence that is still open at the end of the
 *  source, i.e. a block the model is still streaming. */
export const A2UI_PARTIAL_LANG = "a2ui-partial";

export type TextHook = (markdown: string, inline: boolean) => string;

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
type Component = { [key: string]: Json };

const MAX_COMPONENTS = 1000;
const MAX_DEPTH = 32;

const JUSTIFY = new Set(["start", "center", "end", "spaceBetween", "spaceAround", "spaceEvenly", "stretch"]);
const ALIGN = new Set(["start", "center", "end", "stretch"]);
const TEXT_VARIANTS = new Set(["h1", "h2", "h3", "h4", "h5", "caption", "body"]);
const IMAGE_VARIANTS = new Set(["icon", "avatar", "smallFeature", "mediumFeature", "largeFeature", "header"]);
const IMAGE_FITS = new Set(["contain", "cover", "fill", "none", "scaleDown"]);
const SAFE_IMAGE_URL = /^(?:https:\/\/|data:image\/(?:png|jpe?g|gif|webp);base64,)/i;
const SVG_PATH = /^[MmZzLlHhVvCcSsQqTtAaEe0-9.,+\-\s]{1,4000}$/;

const ICON_SHELL =
  'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';

/** Render the body of an ```a2ui fence, or null when it is not a surface
 *  (the caller then keeps the code block). `source` is the raw JSON text. */
export function renderA2ui(source: string, text: TextHook): string | null {
  const surface = parseSurface(source);
  if (!surface) return null;
  const root = surface.components.get("root");
  if (!root) return null;
  const ctx: RenderContext = { ...surface, text, stack: new Set(), rendered: 0 };
  const body = renderComponent(ctx, "root", "/", 0);
  if (!body) return null;
  return `<div class="a2ui">${body}</div>`;
}

/** Placeholder for a surface whose JSON is still arriving: skeleton bars
 *  in the card frame, no words, so it needs no translation. */
export function a2uiPending(): string {
  return '<div class="a2ui a2ui-pending" aria-busy="true"><div class="a2ui-card">'
    + '<span class="a2ui-skeleton"></span><span class="a2ui-skeleton"></span><span class="a2ui-skeleton"></span>'
    + "</div></div>";
}

/** Retag a fenced ```a2ui block that is still open at the end of `src`
 *  as ```a2ui-partial, so the renderer can tell an unfinished stream from
 *  a malformed finished block. */
export function markOpenA2uiFence(src: string): string {
  return retagOpenFence(src, A2UI_LANG, A2UI_PARTIAL_LANG);
}

// ---- Parsing -------------------------------------------------------------

type Surface = { components: Map<string, Component>; data: Json };

function parseSurface(source: string): Surface | null {
  const messages = parseMessages(source.trim());
  if (!messages) return null;
  const components = new Map<string, Component>();
  let data: Json = {};
  const addComponents = (list: Json): boolean => {
    if (!Array.isArray(list)) return false;
    for (const item of list) {
      if (!isObject(item) || typeof item.id !== "string" || typeof item.component !== "string") continue;
      if (components.size >= MAX_COMPONENTS) return false;
      components.set(item.id, item);
    }
    return true;
  };
  for (const msg of messages) {
    if (Array.isArray(msg)) {
      if (!addComponents(msg)) return null;
      continue;
    }
    if (!isObject(msg)) return null;
    if (Array.isArray(msg.components)) {
      if (!addComponents(msg.components)) return null;
    } else if (isObject(msg.updateComponents)) {
      if (!addComponents(msg.updateComponents.components)) return null;
    } else if (isObject(msg.updateDataModel)) {
      const path = typeof msg.updateDataModel.path === "string" ? msg.updateDataModel.path : "/";
      data = setPointer(data, path, msg.updateDataModel.value ?? null);
    }
  }
  return components.size ? { components, data } : null;
}

/** One JSON value (object or array of messages/components), or JSONL. */
function parseMessages(source: string): Json[] | null {
  if (!source) return null;
  try {
    const value = JSON.parse(source) as Json;
    if (Array.isArray(value)) {
      return value.every((v) => isObject(v) && typeof v.component === "string") ? [value] : value;
    }
    return [value];
  } catch {
    const lines = source.split("\n").map((l) => l.trim()).filter(Boolean);
    if (lines.length < 2) return null;
    try {
      return lines.map((l) => JSON.parse(l) as Json);
    } catch {
      return null;
    }
  }
}

// ---- Rendering -----------------------------------------------------------

type RenderContext = Surface & { text: TextHook; stack: Set<string>; rendered: number };

function renderComponent(ctx: RenderContext, id: string, scope: string, depth: number): string {
  const c = ctx.components.get(id);
  if (!c || depth > MAX_DEPTH || ctx.stack.has(id) || ctx.rendered >= MAX_COMPONENTS * 4) return "";
  ctx.rendered += 1;
  ctx.stack.add(id);
  try {
    return renderBody(ctx, c, scope, depth);
  } finally {
    ctx.stack.delete(id);
  }
}

function renderBody(ctx: RenderContext, c: Component, scope: string, depth: number): string {
  const weight = weightStyle(c.weight);
  switch (c.component) {
    case "Card": {
      const child = typeof c.child === "string" ? renderComponent(ctx, c.child, scope, depth + 1) : "";
      return `<div class="a2ui-card"${weight}>${child}</div>`;
    }
    case "Column":
    case "Row": {
      const cls = c.component === "Row" ? "a2ui-row" : "a2ui-col";
      return `<div class="${cls}"${enumAttr("justify", c.justify, JUSTIFY)}${enumAttr("align", c.align, ALIGN)}${weight}>`
        + renderChildren(ctx, c.children, scope, depth) + "</div>";
    }
    case "List": {
      const dir = c.direction === "horizontal" ? "horizontal" : "vertical";
      return `<div class="a2ui-list" data-direction="${dir}"${enumAttr("align", c.align, ALIGN)}${weight}>`
        + renderChildren(ctx, c.children, scope, depth) + "</div>";
    }
    case "Tabs": {
      if (!Array.isArray(c.tabs)) return "";
      const tabs = c.tabs.filter(isObject).filter((t) => typeof t.child === "string");
      if (!tabs.length) return "";
      const head = tabs.map((t, i) =>
        `<button type="button" class="a2ui-tab" data-a2ui-tab="${i}" aria-selected="${i === 0}">${escapeHtml(str(ctx, t.title, scope))}</button>`,
      ).join("");
      const panels = tabs.map((t, i) =>
        `<div class="a2ui-tab-panel"${i === 0 ? "" : " hidden"}>${renderComponent(ctx, t.child as string, scope, depth + 1)}</div>`,
      ).join("");
      return `<div class="a2ui-tabs"${weight}><div class="a2ui-tab-bar" role="tablist">${head}</div>${panels}</div>`;
    }
    case "Text": {
      const variant = typeof c.variant === "string" && TEXT_VARIANTS.has(c.variant) ? c.variant : "body";
      const raw = str(ctx, c.text, scope);
      if (variant.startsWith("h")) {
        const level = variant.slice(1);
        const inner = ctx.text(raw.replace(/^#{1,6}\s+/, ""), true);
        return `<div class="a2ui-text" data-variant="${variant}" role="heading" aria-level="${level}"${weight}>${inner}</div>`;
      }
      return `<div class="a2ui-text" data-variant="${variant}"${weight}>${ctx.text(raw, false)}</div>`;
    }
    case "Divider":
      return `<div class="a2ui-divider" role="separator" data-axis="${c.axis === "vertical" ? "vertical" : "horizontal"}"></div>`;
    case "Icon":
      return renderIcon(ctx, c.name, scope, weight);
    case "Image": {
      const url = str(ctx, c.url, scope).trim();
      if (!SAFE_IMAGE_URL.test(url)) return "";
      const variant = typeof c.variant === "string" && IMAGE_VARIANTS.has(c.variant) ? c.variant : "mediumFeature";
      const fit = typeof c.fit === "string" && IMAGE_FITS.has(c.fit) ? c.fit : "fill";
      return `<img class="a2ui-image" src="${escapeHtml(url)}" alt="${escapeHtml(str(ctx, c.description, scope))}"`
        + ` data-variant="${variant}" data-fit="${fit}" loading="lazy" referrerpolicy="no-referrer"${weight}>`;
    }
    case "CheckBox": {
      const checked = resolve(ctx, c.value, scope) === true;
      const box = checked ? `<svg class="a2ui-check" ${ICON_SHELL}>${A2UI_ICONS.check}</svg>` : "";
      return `<div class="a2ui-checkbox" data-checked="${checked}"${weight}><span class="a2ui-box">${box}</span>`
        + `<span class="a2ui-checkbox-label">${ctx.text(str(ctx, c.label, scope), true)}</span></div>`;
    }
    default:
      return "";
  }
}

function renderChildren(ctx: RenderContext, children: Json | undefined, scope: string, depth: number): string {
  if (Array.isArray(children)) {
    return children.map((id) => (typeof id === "string" ? renderComponent(ctx, id, scope, depth + 1) : "")).join("");
  }
  // Template: one child per item of the bound array, each item becoming
  // the scope that relative paths inside the template resolve against.
  if (isObject(children) && typeof children.componentId === "string" && typeof children.path === "string") {
    const base = absolutePath(children.path, scope);
    const items = getPointer(ctx.data, base);
    if (!Array.isArray(items)) return "";
    return items.map((_, i) => renderComponent(ctx, children.componentId as string, `${base}/${i}`, depth + 1)).join("");
  }
  return "";
}

function renderIcon(ctx: RenderContext, name: Json | undefined, scope: string, weight: string): string {
  if (isObject(name) && typeof name.svgPath === "string") {
    if (!SVG_PATH.test(name.svgPath)) return "";
    return `<svg class="a2ui-icon" ${ICON_SHELL}${weight}><path d="${name.svgPath}"/></svg>`;
  }
  const key = str(ctx, name, scope);
  const body = Object.prototype.hasOwnProperty.call(A2UI_ICONS, key) ? A2UI_ICONS[key] : "";
  return body ? `<svg class="a2ui-icon" data-icon="${key}" ${ICON_SHELL}${weight}>${body}</svg>` : "";
}

// ---- Values --------------------------------------------------------------

/** A Dynamic* value: a literal, or `{path}` read from the data model.
 *  Function calls (`{call}`) are not evaluated. */
function resolve(ctx: RenderContext, value: Json | undefined, scope: string): Json {
  if (isObject(value)) {
    return typeof value.path === "string" ? getPointer(ctx.data, absolutePath(value.path, scope)) : null;
  }
  return value ?? null;
}

function str(ctx: RenderContext, value: Json | undefined, scope: string): string {
  const v = resolve(ctx, value, scope);
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return "";
}

function absolutePath(path: string, scope: string): string {
  if (path.startsWith("/")) return path;
  return scope === "/" ? `/${path}` : `${scope}/${path}`;
}

function pointerParts(path: string): string[] {
  return path.split("/").slice(1).filter((p) => p !== "").map((p) => p.replace(/~1/g, "/").replace(/~0/g, "~"));
}

function getPointer(data: Json, path: string): Json {
  let cur: Json = data;
  for (const part of pointerParts(path)) {
    if (Array.isArray(cur)) cur = cur[Number(part)] ?? null;
    else if (isObject(cur) && Object.prototype.hasOwnProperty.call(cur, part)) cur = cur[part];
    else return null;
  }
  return cur;
}

function setPointer(data: Json, path: string, value: Json): Json {
  const parts = pointerParts(path);
  if (!parts.length) return value;
  const root: Json = isObject(data) || Array.isArray(data) ? data : {};
  let cur: Json = root;
  parts.forEach((part, i) => {
    if (!isObject(cur) && !Array.isArray(cur)) return;
    const last = i === parts.length - 1;
    const container = cur as Record<string, Json>;
    if (last) {
      container[part] = value;
      return;
    }
    if (!isObject(container[part]) && !Array.isArray(container[part])) container[part] = {};
    cur = container[part];
  });
  return root;
}

// ---- Helpers -------------------------------------------------------------

function isObject(v: Json | undefined): v is { [key: string]: Json } {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function enumAttr(name: string, value: Json | undefined, allowed: Set<string>): string {
  return typeof value === "string" && allowed.has(value) ? ` data-${name}="${value}"` : "";
}

function weightStyle(value: Json | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return "";
  return ` style="flex:${Math.min(value, 12)} 1 0"`;
}

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string);
}

export function unescapeHtml(s: string): string {
  return s.replace(/&(amp|lt|gt|quot|#39);/g, (_m, e: string) =>
    ({ amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'" })[e] as string);
}
