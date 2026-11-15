// Annotate tools. PDF.js's editor layer handles highlight, pen (ink), text
// boxes and images/signatures (stamps); this module adds the tools PDF.js
// has no editor for (underline, strikethrough, notes, shapes, dates) on an
// SVG overlay per page. Overlay items are written into the PDF by the
// backend (pypdf) when the draft is committed.
import { pdfjs } from "./pdfjs.js";
import { $, el, icon, svgEl, toast, dialog, clamp } from "./util.js";
import { store, doc, hooks, COLORS } from "./state.js";

const T = pdfjs.AnnotationEditorType;
const P = pdfjs.AnnotationEditorParamsType;
const PDFJS_TOOLS = { select: T.NONE, highlight: T.HIGHLIGHT, ink: T.INK, freetext: T.FREETEXT, stamp: T.STAMP };
const MARKUP_TOOLS = new Set(["underline", "strikethrough"]);
const DRAW_TOOLS = new Set(["rectangle", "ellipse", "line", "arrow", "note", "date", "eraser"]);
const SHAPES = { rectangle: "square", ellipse: "circle", line: "line", arrow: "arrow" };
const DEFAULT_COLOR = { highlight: "#ffd43b", underline: "#4dabf7", strikethrough: "#e03131", ink: "#e03131", rectangle: "#e03131",
  ellipse: "#e03131", line: "#e03131", arrow: "#e03131", freetext: "#1f1f24", note: "#ffd43b", date: "#1f1f24", select: "#ffd43b" };
const DEFAULT_WIDTH = { ink: 2, rectangle: 2, ellipse: 2, line: 2, arrow: 2 };
// What can change on an annotation already in the PDF (backend draft_apply update).
const RECOLOURABLE = new Set(["highlight", "underline", "strikethrough", "squiggly", "note", "free_text", "ink", "rectangle", "ellipse", "line", "arrow"]);
const WIDTHS = new Set(["ink", "rectangle", "ellipse", "line", "arrow"]);
const TEXTS = new Set(["note", "free_text"]);
const HINTS = {
  select: "Select text to highlight, underline or comment on it",
  highlight: "Select text to highlight it, or drag over an area",
  underline: "Select text to underline it",
  strikethrough: "Select text to strike it through",
  ink: "Draw on the page · ⇧ for straight strokes",
  eraser: "Click or drag over a drawing or shape to erase it",
  rectangle: "Drag to draw · ⇧ keeps it square", ellipse: "Drag to draw · ⇧ keeps it round",
  line: "Drag to draw a line · ⇧ snaps to 45°", arrow: "Drag to draw an arrow · ⇧ snaps to 45°",
  freetext: "Click where the text should go", note: "Click to place a note", stamp: "Drag the image to place it; drag a corner to resize",
  date: "Click to place today's date",
};

const state = {
  tool: "select",
  annotateTool: "select",
  shape: "rectangle",
  selected: null,       // id of the selected overlay item
  saved: null,          // id of the selected annotation already in the PDF (doc.annotations)
  editorTexts: new Map(),
  noteItem: null, noteIsNew: false,
  drawing: null,
  selColor: "#ffd43b",
};

// ======================================================================
// Setup
// ======================================================================
export function init() {
  const tool = store.value.tool || {};
  state.shape = tool.shape || "rectangle";
  state.selColor = tool.color?.highlight || DEFAULT_COLOR.highlight;
  buildSwatches($("swatches"), color => setColor(color));
  buildSwatches($("sel-swatches"), color => { state.selColor = color; markSwatches($("sel-swatches"), color); rememberColor("highlight", color); }, { small: true });
  buildSwatches($("item-swatches"), color => recolorSelected(color), { small: true });
  markSwatches($("sel-swatches"), state.selColor);
  setShapeIcon(state.shape);

  document.querySelectorAll("#strip-annotate [data-tool], #strip-fill [data-tool]").forEach(button => {
    button.addEventListener("click", event => {
      if (button.id === "shape-tool" && event.target.closest(".caret-icon")) return;
      setTool(button.dataset.tool === "rectangle" && button.id === "shape-tool" ? state.shape : button.dataset.tool);
    });
  });
  $("shape-tool").querySelector(".caret-icon").addEventListener("click", event => {
    event.stopPropagation();
    const open = $("shape-menu").hidden;
    hooks.closeMenus();
    $("shape-menu").hidden = !open;
  });
  $("shape-menu").addEventListener("click", event => {
    const item = event.target.closest("[data-shape]");
    if (!item) return;
    hooks.closeMenus();
    setTool(item.dataset.shape);
  });
  $("stamp-tool").addEventListener("click", event => {
    event.stopPropagation();
    const open = $("stamp-menu").hidden;
    hooks.closeMenus();
    $("stamp-menu").hidden = !open;
  });
  document.querySelectorAll("[data-stamp]").forEach(button => button.addEventListener("click", () => {
    hooks.closeMenus();
    if (button.dataset.stamp === "image") $("image-input").click();
    else signatureDialog();
  }));
  $("image-input").addEventListener("change", async event => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) await placeImage(file);
  });

  $("width-input").addEventListener("input", event => setWidth(Number(event.target.value)));
  $("size-input").addEventListener("change", event => {
    const size = Number(event.target.value);
    store.update(v => { v.tool = { ...(v.tool || {}), size }; });
    params(P.FREETEXT_SIZE, size);
  });

  // Text selection → popover or immediate markup.
  document.addEventListener("pointerup", event => {
    if (event.button !== 0 || !event.target.closest?.("#pages")) return;
    setTimeout(onSelectionEnd, 0);
  });
  document.addEventListener("keyup", event => { if (event.shiftKey && event.key.startsWith("Arrow")) onSelectionEnd(); });
  document.addEventListener("selectionchange", () => {
    const sel = document.getSelection();
    if (!sel || sel.isCollapsed) $("sel-pop").hidden = true;
  });
  $("viewer").addEventListener("scroll", () => { $("sel-pop").hidden = true; positionItemPop(); positionNote(); }, { passive: true });
  $("sel-pop").addEventListener("pointerdown", event => event.preventDefault());
  $("sel-pop").addEventListener("click", event => {
    const button = event.target.closest("[data-act]");
    if (button) selectionAction(button.dataset.act);
  });
  $("item-pop").addEventListener("click", event => {
    const button = event.target.closest("[data-act]");
    if (!button) return;
    const saved = savedEntry();
    if (saved) {
      if (button.dataset.act === "delete") deleteSaved(saved);
      else if (button.dataset.act === "note") openSavedText(saved);
      return;
    }
    if (!state.selected) return;
    const item = doc.overlay.get(state.selected);
    if (button.dataset.act === "delete") removeItem(state.selected, { record: true });
    else if (button.dataset.act === "note" && item) openNote(item, false);
  });
  $("item-width").addEventListener("input", event => {
    const width = Number(event.target.value);
    $("item-width-dot").style.setProperty("--w", `${Math.max(2, width * 1.4)}px`);
    const item = state.selected && doc.overlay.get(state.selected);
    if (item && "width" in item) { item.width = width; drawPage(item.page); changed(); }
  });
  $("item-width").addEventListener("change", event => {
    const saved = savedEntry();
    if (saved && Number(event.target.value) !== saved.width) restyleSaved(saved, { width: Number(event.target.value) }, "Changed the line width of");
  });

  // Overlay drawing, picking and erasing.
  $("pages").addEventListener("pointerdown", onPointerDown, true);
  $("pages").addEventListener("click", event => {
    const target = event.target.closest?.("[data-item]");
    if (target && !DRAW_TOOLS.has(state.tool)) {
      event.preventDefault();
      event.stopPropagation();
      selectItem(target.dataset.item);
      const item = doc.overlay.get(target.dataset.item);
      if (item?.type === "note") openNote(item, false);
    } else if (!event.target.closest(".item-pop")) {
      const saved = pickable() && savedAt(event.clientX, event.clientY);
      if (saved) {
        event.preventDefault();
        event.stopPropagation();
        selectSaved(saved.id);
      } else selectItem(null);
    }
  }, true);
  $("pages").addEventListener("pointermove", event => {
    $("viewer").classList.toggle("over-saved", !!(pickable() && !event.target.closest?.("[data-item]") && savedAt(event.clientX, event.clientY)));
  }, { passive: true });

  // Image drop onto the pages → stamp.
  $("viewer").addEventListener("dragover", event => {
    if ([...(event.dataTransfer?.items || [])].some(i => i.kind === "file" && i.type.startsWith("image/"))) { event.preventDefault(); }
  });
  $("viewer").addEventListener("drop", event => {
    const file = [...(event.dataTransfer?.files || [])].find(f => f.type.startsWith("image/"));
    if (!file) return;
    event.preventDefault();
    if (doc.mode !== "annotate" && doc.mode !== "fill") hooks.setMode("annotate");
    placeImage(file);
  });

  // Note popup.
  $("note-text").addEventListener("input", () => { if (state.noteItem) { state.noteItem.contents = $("note-text").value; hooks.scheduleAnnotationList(); } });
  $("note-text").addEventListener("keydown", event => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) { event.preventDefault(); closeNote(); }
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeNote(); }
  });
  $("note-close").addEventListener("click", () => closeNote());
  $("note-delete").addEventListener("click", () => {
    const item = state.noteItem;
    state.noteItem = null;
    $("note-pop").hidden = true;
    if (item?.saved) deleteSaved(item.saved);
    else if (item) removeItem(item.id, { record: !state.noteIsNew });
  });
}

function buildSwatches(container, onPick, { small = false } = {}) {
  container.replaceChildren(...COLORS.map(c => el("button", {
    type: "button", class: `swatch${small ? " sm" : ""}`, role: "radio", "aria-checked": "false", "aria-label": c.name,
    "data-tip": c.name, style: `--c:${c.value}`, dataset: { color: c.value },
    onclick: event => { event.stopPropagation(); onPick(c.value); },
  })));
}

function markSwatches(container, color) {
  container.querySelectorAll(".swatch").forEach(s => s.setAttribute("aria-checked", String(s.dataset.color.toLowerCase() === String(color).toLowerCase())));
}

function setShapeIcon(shape) {
  $("shape-tool").querySelector(".shape-icon use").setAttribute("href", `#i-${SHAPES[shape] || "square"}`);
  $("shape-tool").dataset.tool = shape;
  document.querySelectorAll("#shape-menu [data-shape]").forEach(b => b.classList.toggle("current", b.dataset.shape === shape));
}

function colorFor(tool) { return store.value.tool?.color?.[tool] || DEFAULT_COLOR[tool] || "#e03131"; }
function widthFor(tool) { return store.value.tool?.width?.[tool] || DEFAULT_WIDTH[tool] || 2; }
function rememberColor(tool, color) { store.update(v => { v.tool = { ...(v.tool || {}), color: { ...(v.tool?.color || {}), [tool]: color } }; }); }

function params(type, value) { doc.bus?.dispatch("switchannotationeditorparams", { source: null, type, value }); }

// ======================================================================
// Tools and modes
// ======================================================================
export function setMode(mode) {
  closeNote();
  selectItem(null);
  $("sel-pop").hidden = true;
  if (mode === "annotate") setTool(state.annotateTool || "select");
  else setTool("select", { silent: true });
}

export function setTool(tool, { silent = false } = {}) {
  closeNote();
  // Re-applying the current tool (PDF.js reports a new editor layer after each
  // reload) keeps the selection.
  if ((state.selected || state.saved) && tool !== state.tool) selectItem(null);
  if (SHAPES[tool]) { state.shape = tool; setShapeIcon(tool); store.update(v => { v.tool = { ...(v.tool || {}), shape: tool }; }); }
  state.tool = tool;
  if (doc.mode === "annotate") state.annotateTool = tool;
  document.querySelectorAll(".strip [data-tool]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.tool === tool)));
  $("stamp-tool").setAttribute("aria-pressed", String(tool === "stamp"));
  $("viewer").classList.toggle("op-capture", DRAW_TOOLS.has(tool));
  $("viewer").dataset.tool = tool;
  $("tool-hint").textContent = HINTS[tool] || "";
  // Style controls for this tool.
  const color = colorFor(tool);
  markSwatches($("swatches"), color);
  $("swatches").hidden = tool === "eraser";
  const hasWidth = ["ink", "rectangle", "ellipse", "line", "arrow"].includes(tool);
  $("width-ctl").hidden = !hasWidth;
  $("size-ctl").hidden = tool !== "freetext";
  if (hasWidth) { $("width-input").value = String(widthFor(tool)); updateWidthDot(); }
  $("size-input").value = String(store.value.tool?.size || 12);
  setEditorMode(PDFJS_TOOLS[tool] ?? T.NONE);
  if (tool === "highlight") params(P.HIGHLIGHT_COLOR, color);
  if (tool === "ink") { params(P.INK_COLOR, color); params(P.INK_THICKNESS, widthFor("ink")); params(P.INK_OPACITY, 1); }
  if (tool === "freetext") { params(P.FREETEXT_COLOR, color); params(P.FREETEXT_SIZE, store.value.tool?.size || 12); }
  if (!silent && tool !== "select") $("sel-pop").hidden = true;
}

function setEditorMode(mode) {
  const viewer = doc.viewer;
  if (!viewer?.pdfDocument || !doc.ui) return;
  if (viewer.annotationEditorMode === mode) return;
  try { viewer.annotationEditorMode = { mode }; } catch (error) { console.warn("editor mode", error); }
}

export function onUiManager() {
  // Defaults for the editors, then the current tool's mode.
  setTimeout(() => setTool(state.tool, { silent: true }), 0);
}

export function onEditorMode(mode) {
  // PDF.js switches modes itself, e.g. when an existing ink annotation is
  // double-clicked; follow it in the tool strip.
  if (state.followEditorMode === false) return;
  const tool = Object.entries(PDFJS_TOOLS).find(([, m]) => m === mode)?.[0];
  if (!tool || tool === state.tool || DRAW_TOOLS.has(state.tool) || MARKUP_TOOLS.has(state.tool)) return;
  if (mode === T.NONE && state.tool !== "select") return;
  if (doc.mode !== "annotate" && tool !== "select" && tool !== "freetext" && tool !== "stamp") return;
  state.tool = tool;
  document.querySelectorAll(".strip [data-tool]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.tool === tool)));
}

function setColor(color) {
  rememberColor(state.tool, color);
  markSwatches($("swatches"), color);
  if (state.tool === "highlight" || state.tool === "select") params(P.HIGHLIGHT_COLOR, color);
  if (state.tool === "ink") params(P.INK_COLOR, color);
  if (state.tool === "freetext") params(P.FREETEXT_COLOR, color);
  if (state.selected || state.saved) recolorSelected(color);
}

function setWidth(width) {
  store.update(v => { v.tool = { ...(v.tool || {}), width: { ...(v.tool?.width || {}), [state.tool]: width } }; });
  updateWidthDot();
  if (state.tool === "ink") params(P.INK_THICKNESS, width);
  const item = state.selected && doc.overlay.get(state.selected);
  if (item && "width" in item) { item.width = width; drawPage(item.page); $("item-width").value = String(width); }
}

function updateWidthDot() {
  const width = Number($("width-input").value);
  $("width-dot").style.setProperty("--w", `${Math.max(2, width * 1.4)}px`);
}

// ======================================================================
// Geometry helpers
// ======================================================================
function pageView(n) { return doc.viewer?.getPageView(n - 1); }

function baseViewport(view) {
  return view.pdfPage.getViewport({ scale: 1, rotation: view.viewport.rotation });
}

// Client point → { page, x, y } in PDF user space.
function clientToPdf(clientX, clientY, page) {
  const view = pageView(page);
  if (!view?.div) return null;
  const rect = view.div.getBoundingClientRect();
  const x = clientX - rect.left - view.div.clientLeft, y = clientY - rect.top - view.div.clientTop;
  const [px, py] = view.viewport.convertToPdfPoint(x, y);
  return { x: px, y: py };
}

function pageAt(clientX, clientY) {
  for (const node of document.querySelectorAll("#pages .page")) {
    const r = node.getBoundingClientRect();
    if (clientX >= r.left && clientX <= r.right && clientY >= r.top && clientY <= r.bottom) return Number(node.dataset.pageNumber);
  }
  return null;
}

function toView(vp, x, y) { return vp.convertToViewportPoint(x, y); }

export function itemRect(item) {
  const pts = itemPoints(item);
  if (!pts.length) return null;
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

function itemPoints(item) {
  if (item.quads) return item.quads.flatMap(q => [[q[0], q[1]], [q[2], q[3]], [q[4], q[5]], [q[6], q[7]]]);
  if (item.rect) return [[item.rect[0], item.rect[1]], [item.rect[2], item.rect[3]]];
  if (item.points) return item.points;
  if (item.at) return [item.at, [item.at[0] + 22, item.at[1] - 22]];
  return [];
}

// ======================================================================
// Overlay rendering
// ======================================================================
function overlayFor(view) {
  let svg = view.div.querySelector(":scope > svg.op-overlay");
  const vp = baseViewport(view);
  if (!svg) {
    svg = svgEl("svg", { class: "op-overlay", preserveAspectRatio: "none" });
    view.div.append(svg);
  }
  svg.setAttribute("viewBox", `0 0 ${vp.width} ${vp.height}`);
  return { svg, vp };
}

export function drawPage(n) {
  const view = pageView(n);
  if (!view?.div || !view.pdfPage) return;
  const { svg, vp } = overlayFor(view);
  svg.replaceChildren();
  for (const item of doc.overlay.values()) {
    if (item.page !== n) continue;
    const node = renderItem(item, vp);
    if (node) svg.append(node);
  }
  const saved = savedEntry();
  if (saved?.page === n && saved.rect) {
    svg.append(renderSavedSelection(saved, vp));
    // After a restyle the page renders again; the popover follows the new box.
    if ($("note-pop").hidden) positionItemPop();
  }
}

// The selection box of an annotation already in the PDF (PDF.js draws the annotation itself).
function renderSavedSelection(entry, vp) {
  const g = svgEl("g", { class: "op-item op-saved selected", "data-saved": entry.id });
  const [ax, ay] = toView(vp, entry.rect[0], entry.rect[1]), [bx, by] = toView(vp, entry.rect[2], entry.rect[3]);
  const box = { x: Math.min(ax, bx), y: Math.min(ay, by), width: Math.abs(bx - ax), height: Math.abs(by - ay) };
  g.append(svgEl("rect", { class: "anchor", ...box }));
  g.append(svgEl("rect", { class: "sel-box", x: box.x - 4, y: box.y - 4, width: box.width + 8, height: box.height + 8, rx: 3 }));
  return g;
}

function redrawAll() {
  const pages = new Set([...doc.overlay.values()].map(i => i.page));
  document.querySelectorAll("#pages .page svg.op-overlay").forEach(svg => { const n = Number(svg.parentElement.dataset.pageNumber); if (!pages.has(n)) svg.replaceChildren(); });
  for (const n of pages) drawPage(n);
}

function renderItem(item, vp) {
  const g = svgEl("g", { class: `op-item op-${item.type}${state.selected === item.id ? " selected" : ""}`, "data-item": item.id });
  const color = item.color;
  if (item.type === "underline" || item.type === "strikethrough") {
    for (const q of item.quads) {
      const [tl, tr, bl, br] = [[q[0], q[1]], [q[2], q[3]], [q[4], q[5]], [q[6], q[7]]].map(p => toView(vp, p[0], p[1]));
      const height = Math.hypot(tl[0] - bl[0], tl[1] - bl[1]);
      const t = item.type === "strikethrough" ? 0.45 : 0.12;
      const a = [bl[0] + (tl[0] - bl[0]) * t, bl[1] + (tl[1] - bl[1]) * t];
      const b = [br[0] + (tr[0] - br[0]) * t, br[1] + (tr[1] - br[1]) * t];
      g.append(svgEl("polygon", { class: "hit", points: [tl, tr, br, bl].map(p => p.join(",")).join(" ") }));
      g.append(svgEl("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1], stroke: color, "stroke-width": Math.max(1, height / 14) }));
    }
  } else if (item.type === "note") {
    const [x, y] = toView(vp, item.at[0], item.at[1]);
    const note = svgEl("g", { transform: `translate(${x} ${y})` });
    note.append(svgEl("path", { class: "note-body", d: "M2 2h18v15h-11l-4 3.5v-3.5h-3z", fill: color, stroke: "rgba(0,0,0,.45)", "stroke-width": 1, "stroke-linejoin": "round" }));
    note.append(svgEl("path", { d: "M5.5 6.5h11M5.5 10h11M5.5 13.5h7", stroke: "rgba(0,0,0,.55)", "stroke-width": 1, "stroke-linecap": "round" }));
    note.append(svgEl("rect", { class: "hit", x: 0, y: 0, width: 22, height: 22 }));
    g.append(note);
  } else if (item.type === "rectangle" || item.type === "ellipse") {
    const [x0, y0] = toView(vp, item.rect[0], item.rect[1]);
    const [x1, y1] = toView(vp, item.rect[2], item.rect[3]);
    const x = Math.min(x0, x1), y = Math.min(y0, y1), w = Math.abs(x1 - x0), h = Math.abs(y1 - y0);
    const attrs = { fill: item.fill || "none", stroke: color, "stroke-width": item.width, class: "shape" };
    g.append(item.type === "rectangle"
      ? svgEl("rect", { ...attrs, x: x + item.width / 2, y: y + item.width / 2, width: Math.max(0, w - item.width), height: Math.max(0, h - item.width) })
      : svgEl("ellipse", { ...attrs, cx: x + w / 2, cy: y + h / 2, rx: Math.max(0, w / 2 - item.width / 2), ry: Math.max(0, h / 2 - item.width / 2) }));
  } else if (item.type === "line" || item.type === "arrow") {
    const [a, b] = item.points.map(p => toView(vp, p[0], p[1]));
    g.append(svgEl("line", { class: "hit-line", x1: a[0], y1: a[1], x2: b[0], y2: b[1], "stroke-width": Math.max(10, item.width + 8) }));
    g.append(svgEl("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1], stroke: color, "stroke-width": item.width, "stroke-linecap": "round" }));
    if (item.type === "arrow") {
      const head = item.width * 3 + 5;
      const angle = Math.atan2(b[1] - a[1], b[0] - a[0]);
      const pts = [-1, 1].map(side => [b[0] - head * Math.cos(angle + side * 0.49), b[1] - head * Math.sin(angle + side * 0.49)]);
      g.append(svgEl("path", { d: `M${pts[0].join(" ")} L${b.join(" ")} L${pts[1].join(" ")}`, fill: "none", stroke: color, "stroke-width": item.width, "stroke-linecap": "round", "stroke-linejoin": "round" }));
    }
  } else if (item.type === "free_text") {
    const [x, y] = toView(vp, item.rect[0], item.rect[3]);
    const text = svgEl("text", { x: x + 2, y: y + item.font_size, fill: color, "font-size": item.font_size, "font-family": "Helvetica, Arial, sans-serif" });
    text.textContent = item.contents;
    g.append(text);
    const [x1, y1] = toView(vp, item.rect[2], item.rect[1]);
    g.append(svgEl("rect", { class: "hit", x, y, width: Math.abs(x1 - x), height: Math.abs(y1 - y) }));
  } else return null;
  if (state.selected === item.id) {
    const r = itemRect(item);
    if (r) {
      const [ax, ay] = toView(vp, r[0], r[1]), [bx, by] = toView(vp, r[2], r[3]);
      g.append(svgEl("rect", { class: "sel-box", x: Math.min(ax, bx) - 4, y: Math.min(ay, by) - 4, width: Math.abs(bx - ax) + 8, height: Math.abs(by - ay) + 8, rx: 3 }));
    }
  }
  return g;
}

// ======================================================================
// Overlay items (recorded in PDF.js's undo stack when it exists)
// ======================================================================
function newId() { return "op-" + crypto.randomUUID().replace(/-/g, ""); }

function put(item) { doc.overlay.set(item.id, item); drawPage(item.page); changed(); }
function drop(id) {
  const item = doc.overlay.get(id);
  doc.overlay.delete(id);
  if (state.selected === id) selectItem(null);
  if (item) drawPage(item.page);
  changed();
}
function changed() { hooks.refreshDirty(); hooks.scheduleAnnotationList(); }

export function addItem(item, { record = true } = {}) {
  item.id ||= newId();
  item.author ||= store.value.author || "";
  item.created ||= new Date().toISOString();
  if (record && doc.ui) {
    doc.ui.addCommands({ cmd: () => put(item), undo: () => drop(item.id), mustExec: true });
    doc.pdfTouched = true;
  } else put(item);
  return item;
}

export function removeItem(id, { record = true } = {}) {
  const item = doc.overlay.get(id);
  if (!item) return;
  if (state.noteItem?.id === id) { state.noteItem = null; $("note-pop").hidden = true; }
  if (record && doc.ui) doc.ui.addCommands({ cmd: () => drop(id), undo: () => put(item), mustExec: true });
  else drop(id);
}

function recolorSelected(color) {
  const saved = savedEntry();
  if (saved) {
    if (RECOLOURABLE.has(saved.type) && String(saved.color).toLowerCase() !== color.toLowerCase()) restyleSaved(saved, { color }, "Recoloured");
    return;
  }
  const item = state.selected && doc.overlay.get(state.selected);
  if (!item) return;
  const before = item.color;
  const apply = value => { item.color = value; drawPage(item.page); markSwatches($("item-swatches"), value); changed(); };
  if (doc.ui) doc.ui.addCommands({ cmd: () => apply(color), undo: () => apply(before), mustExec: true });
  else apply(color);
}

function selectItem(id) {
  const previous = state.selected;
  clearSaved();
  state.selected = id && doc.overlay.has(id) ? id : null;
  for (const which of new Set([previous, state.selected])) {
    const item = which && doc.overlay.get(which);
    if (item) drawPage(item.page);
  }
  const item = state.selected && doc.overlay.get(state.selected);
  $("item-pop").hidden = !item;
  if (item) showItemPop(item, { colour: true, width: "width" in item, text: item.type === "note" ? "Edit note" : null });
}

// Configures the popover: colours, line width and the note button, as the type allows.
function showItemPop(item, { colour, width, text }) {
  const pop = $("item-pop");
  $("item-swatches").hidden = !colour;
  if (colour) markSwatches($("item-swatches"), item.color);
  $("item-width-ctl").hidden = !width;
  if (width) {
    $("item-width").value = String(Math.round(item.width || 2));
    $("item-width-dot").style.setProperty("--w", `${Math.max(2, (item.width || 2) * 1.4)}px`);
  }
  $("item-sep").hidden = !colour && !width;
  const note = pop.querySelector('[data-act="note"]');
  note.hidden = !text;
  if (text) note.dataset.tip = text;
  positionItemPop();
}

// ---------- annotations already in the PDF
// They are selected with the Select tool in Annotate; a change is applied by
// the backend into a new draft (like deleting one), so it is one draft edit
// that Save writes and Undo takes back.
function pickable() {
  return doc.mode === "annotate" && state.tool === "select" && !!doc.annotations?.length && !!document.getSelection()?.isCollapsed;
}

function savedEntry() { return state.saved ? doc.annotations.find(a => a.id === state.saved) || null : null; }

function savedAt(clientX, clientY) {
  const page = pageAt(clientX, clientY);
  if (!page) return null;
  const point = clientToPdf(clientX, clientY, page);
  if (!point) return null;
  const pad = 2;
  const hits = doc.annotations.filter(a => a.page === page && a.rect && !a.reply_to
    && point.x >= Math.min(a.rect[0], a.rect[2]) - pad && point.x <= Math.max(a.rect[0], a.rect[2]) + pad
    && point.y >= Math.min(a.rect[1], a.rect[3]) - pad && point.y <= Math.max(a.rect[1], a.rect[3]) + pad);
  const area = a => Math.abs((a.rect[2] - a.rect[0]) * (a.rect[3] - a.rect[1]));
  return hits.sort((a, b) => area(a) - area(b))[0] || null;
}

function clearSaved() {
  const entry = savedEntry();
  state.saved = null;
  if (entry) drawPage(entry.page);
}

export function selectSaved(id) {
  if (state.selected) selectItem(null);
  clearSaved();
  const entry = doc.annotations.find(a => a.id === id);
  if (!entry) { $("item-pop").hidden = true; return; }
  state.saved = id;
  $("sel-pop").hidden = true;
  drawPage(entry.page);
  $("item-pop").hidden = false;
  showItemPop(entry, { colour: RECOLOURABLE.has(entry.type), width: WIDTHS.has(entry.type),
    text: TEXTS.has(entry.type) ? (entry.type === "note" ? "Edit note" : "Edit text") : null });
}

async function restyleSaved(entry, change, verb) {
  if (doc.busy) return;
  const label = (hooks.typeLabel?.(entry.type) || "annotation").toLowerCase();
  try {
    const summary = await hooks.commit({ update: [{ id: entry.id, ...change }], log: [`${verb} ${label} on p. ${entry.page}`], busyText: "Applying…" });
    if (summary) selectSaved(summary.updated?.[entry.id] || entry.id);
  } catch (error) { console.warn("restyle", error); }
}

async function deleteSaved(entry) {
  clearSaved();
  $("item-pop").hidden = true;
  await hooks.deleteAnnotation?.({ ...entry, kind: "saved" });
}

function openSavedText(entry) {
  openNote({ id: `saved:${entry.id}`, saved: entry, type: entry.type, page: entry.page, contents: entry.contents || "",
    author: entry.author, created: entry.created || entry.modified, color: entry.color, quote: entry.text || "" }, false);
}

function positionItemPop() {
  const pop = $("item-pop");
  const saved = savedEntry();
  const item = saved || (state.selected && doc.overlay.get(state.selected));
  if (!item) { pop.hidden = true; return; }
  const node = document.querySelector(saved ? `#pages [data-saved="${CSS.escape(saved.id)}"]` : `#pages [data-item="${item.id}"]`);
  if (!node) { pop.hidden = true; return; }
  const r = node.getBoundingClientRect(), wrap = $("viewer-wrap").getBoundingClientRect();
  pop.hidden = false;
  const width = pop.offsetWidth;
  pop.style.left = `${clamp(r.left + r.width / 2 - wrap.left - width / 2, 8, wrap.width - width - 8)}px`;
  pop.style.top = `${Math.max(8, r.top - wrap.top - pop.offsetHeight - 10)}px`;
}

export function toBackend(item) {
  const out = { id: item.id, type: item.type, page: item.page, color: item.color };
  for (const key of ["quads", "rect", "points", "at", "contents", "width", "fill", "font_size", "opacity"]) if (item[key] !== undefined && item[key] !== null && item[key] !== "") out[key] = item[key];
  if (item.author) out.author = item.author;
  return out;
}

export function reset() {
  closeNote();
  state.selected = null;
  state.saved = null;
  $("item-pop").hidden = true;
  $("sel-pop").hidden = true;
  state.editorTexts.clear();
}

export function onDocument() {
  state.selected = null;
  state.saved = null;
  $("item-pop").hidden = true;
  closeNote();
  redrawAll();
}

export function decorateForms() { /* the annotation layer renders fields; nothing to add */ }

// ======================================================================
// Text selection: popover, markup tools
// ======================================================================
function selectionInfo() {
  const sel = document.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const anchor = sel.anchorNode?.nodeType === 1 ? sel.anchorNode : sel.anchorNode?.parentElement;
  if (!anchor?.closest?.("#pages .textLayer")) return null;
  const text = sel.toString().replace(/\s+/g, " ").trim();
  if (!text) return null;
  const range = sel.getRangeAt(0);
  const rects = [...range.getClientRects()].filter(r => r.width > 0.5 && r.height > 0.5);
  if (!rects.length) return null;
  // Group rectangles by page, then merge them into one box per line.
  const byPage = new Map();
  for (const r of rects) {
    const page = pageAt(r.left + r.width / 2, r.top + r.height / 2);
    if (!page) continue;
    const view = pageView(page);
    if (r.height > view.div.clientHeight * 0.2) continue;
    (byPage.get(page) || byPage.set(page, []).get(page)).push(r);
  }
  const pages = [];
  for (const [page, list] of byPage) {
    list.sort((a, b) => a.top - b.top || a.left - b.left);
    const lines = [];
    for (const r of list) {
      const line = lines.find(l => Math.min(l.bottom, r.bottom) - Math.max(l.top, r.top) > Math.min(l.bottom - l.top, r.height) * 0.5);
      if (line) { line.left = Math.min(line.left, r.left); line.right = Math.max(line.right, r.right); line.top = Math.min(line.top, r.top); line.bottom = Math.max(line.bottom, r.bottom); }
      else lines.push({ left: r.left, right: r.right, top: r.top, bottom: r.bottom });
    }
    const quads = lines.map(l => {
      const tl = clientToPdf(l.left, l.top, page), tr = clientToPdf(l.right, l.top, page);
      const bl = clientToPdf(l.left, l.bottom, page), br = clientToPdf(l.right, l.bottom, page);
      return [tl.x, tl.y, tr.x, tr.y, bl.x, bl.y, br.x, br.y].map(v => Math.round(v * 100) / 100);
    });
    pages.push({ page, quads });
  }
  return { text, pages, last: rects[rects.length - 1], first: rects[0] };
}

function onSelectionEnd() {
  if (DRAW_TOOLS.has(state.tool)) return;
  const info = selectionInfo();
  if (!info) { $("sel-pop").hidden = true; return; }
  if (MARKUP_TOOLS.has(state.tool)) {
    markupFromSelection(state.tool, colorFor(state.tool), info);
    return;
  }
  if (state.tool !== "select" || doc.mode === "organize") return;
  const pop = $("sel-pop"), wrap = $("viewer-wrap").getBoundingClientRect();
  pop.hidden = false;
  const r = info.first;
  const width = pop.offsetWidth;
  let top = r.top - wrap.top - pop.offsetHeight - 10;
  if (top < 8) top = info.last.bottom - wrap.top + 10;
  pop.style.left = `${clamp(r.left - wrap.left, 8, wrap.width - width - 8)}px`;
  pop.style.top = `${top}px`;
}

function markupFromSelection(type, color, info) {
  for (const { page, quads } of info.pages) addItem({ type, page, quads, color, text: info.text });
  document.getSelection().removeAllRanges();
  $("sel-pop").hidden = true;
}

async function selectionAction(action) {
  const info = selectionInfo();
  if (!info) { $("sel-pop").hidden = true; return; }
  if (action === "copy") {
    const text = document.getSelection().toString();
    try { await navigator.clipboard.writeText(text); }
    catch { document.execCommand("copy"); }
    toast("Copied");
    $("sel-pop").hidden = true;
    return;
  }
  if (action === "underline" || action === "strikethrough") {
    markupFromSelection(action, action === "underline" ? colorFor("underline") : colorFor("strikethrough"), info);
    return;
  }
  if (action === "note") {
    const { page, quads } = info.pages[info.pages.length - 1];
    const q = quads[quads.length - 1];
    const item = addItem({ type: "note", page, at: [Math.max(q[2], q[6]) + 2, Math.max(q[1], q[3]) + 6], color: colorFor("note"), contents: "", quote: info.text }, { record: false });
    document.getSelection().removeAllRanges();
    $("sel-pop").hidden = true;
    openNote(item, true);
    return;
  }
  if (action === "highlight") highlightSelection(info);
}

function highlightSelection(info) {
  if (!doc.ui) return;
  const before = new Set();
  for (let i = 0; i < doc.pages; i++) for (const e of doc.ui.getEditors(i)) before.add(e.id);
  const wasTool = state.tool;
  // From selection mode PDF.js first switches itself to highlight mode; keep
  // the tool strip on the current tool while that happens.
  state.followEditorMode = false;
  params(P.HIGHLIGHT_COLOR, state.selColor);
  doc.ui.highlightSelection("floating_button");
  $("sel-pop").hidden = true;
  doc.pdfTouched = true;
  let tries = 0;
  const settle = () => {
    const added = [];
    for (let i = 0; i < doc.pages; i++) for (const e of doc.ui.getEditors(i)) if (!before.has(e.id)) added.push(e);
    if (!added.length && ++tries < 40) { setTimeout(settle, 25); return; }
    for (const e of added) state.editorTexts.set(e.id, info.text);
    state.followEditorMode = true;
    if (wasTool !== "highlight") setEditorMode(PDFJS_TOOLS[wasTool] ?? T.NONE);
    hooks.refreshDirty();
    hooks.scheduleAnnotationList();
  };
  setTimeout(settle, 25);
}

export function editorText(editor) { return state.editorTexts.get(editor.id) || ""; }

// ======================================================================
// Notes
// ======================================================================
function openNote(item, isNew) {
  state.noteItem = item;
  state.noteIsNew = isNew;
  $("note-text").value = item.contents || "";
  $("note-author").textContent = item.author || store.value.author || "Note";
  $("note-date").textContent = new Date(item.created || Date.now()).toLocaleDateString("en", { month: "short", day: "numeric" });
  $("note-dot").style.setProperty("--c", item.color);
  $("note-quote").textContent = item.quote ? `“${item.quote.length > 60 ? item.quote.slice(0, 60) + "…" : item.quote}”` : "";
  $("note-pop").hidden = false;
  $("item-pop").hidden = true;
  positionNote();
  setTimeout(() => $("note-text").focus(), 0);
}

function positionNote() {
  const item = state.noteItem;
  const pop = $("note-pop");
  if (!item || pop.hidden) return;
  const node = document.querySelector(item.saved ? `#pages [data-saved="${CSS.escape(item.saved.id)}"]` : `#pages [data-item="${item.id}"]`);
  if (!node) return;
  const r = node.getBoundingClientRect(), wrap = $("viewer-wrap").getBoundingClientRect();
  let left = r.right - wrap.left + 8;
  if (left + pop.offsetWidth > wrap.width - 8) left = r.left - wrap.left - pop.offsetWidth - 8;
  pop.style.left = `${clamp(left, 8, wrap.width - pop.offsetWidth - 8)}px`;
  pop.style.top = `${clamp(r.top - wrap.top, 8, wrap.height - pop.offsetHeight - 8)}px`;
}

function closeNote() {
  const item = state.noteItem;
  $("note-pop").hidden = true;
  state.noteItem = null;
  if (!item) return;
  const text = $("note-text").value.trim();
  if (item.saved) {
    // An emptied note or text box keeps its old text.
    if (text && text !== (item.saved.contents || "").trim()) restyleSaved(item.saved, { contents: text }, "Edited");
    else if (savedEntry()) selectSaved(item.saved.id);
    return;
  }
  if (!text) { removeItem(item.id, { record: false }); return; }
  item.contents = text;
  if (state.noteIsNew && doc.overlay.has(item.id)) {
    // Record the finished note as one undoable step.
    doc.overlay.delete(item.id);
    addItem(item, { record: true });
  }
  state.noteIsNew = false;
  changed();
}

// ======================================================================
// Pointer: drawing shapes, notes, dates; erasing
// ======================================================================
function onPointerDown(event) {
  if (event.button !== 0 || !DRAW_TOOLS.has(state.tool)) return;
  const page = pageAt(event.clientX, event.clientY);
  if (!page) return;
  event.preventDefault();
  event.stopPropagation();
  const start = clientToPdf(event.clientX, event.clientY, page);
  if (state.tool === "eraser") { eraseAt(event.clientX, event.clientY); startErase(); return; }
  if (state.tool === "note") {
    const item = addItem({ type: "note", page, at: [start.x - 4, start.y + 4], color: colorFor("note"), contents: "" }, { record: false });
    openNote(item, true);
    return;
  }
  if (state.tool === "date") {
    const size = 12;
    const text = new Date().toLocaleDateString("en-CA");
    addItem({ type: "free_text", page, rect: [start.x, start.y - size * 1.4, start.x + size * 0.62 * text.length + 6, start.y], contents: text, font_size: size, color: colorFor("date") });
    toast("Date added");
    return;
  }
  // Shapes: drag out a preview, commit on release.
  const tool = state.tool;
  const item = { type: tool, page, color: colorFor(tool), width: widthFor(tool), id: newId(), author: store.value.author || "" };
  const view = pageView(page);
  const { svg, vp } = overlayFor(view);
  let preview = null;
  const update = (clientX, clientY, shift) => {
    let end = clientToPdf(clientX, clientY, page);
    if (shift) {
      const dx = end.x - start.x, dy = end.y - start.y;
      if (tool === "rectangle" || tool === "ellipse") { const s = Math.max(Math.abs(dx), Math.abs(dy)); end = { x: start.x + Math.sign(dx || 1) * s, y: start.y + Math.sign(dy || 1) * s }; }
      else { const angle = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * (Math.PI / 4), len = Math.hypot(dx, dy); end = { x: start.x + len * Math.cos(angle), y: start.y + len * Math.sin(angle) }; }
    }
    if (tool === "line" || tool === "arrow") item.points = [[start.x, start.y], [end.x, end.y]];
    else item.rect = [Math.min(start.x, end.x), Math.min(start.y, end.y), Math.max(start.x, end.x), Math.max(start.y, end.y)];
    preview?.remove();
    preview = renderItem(item, vp);
    preview?.classList.add("preview");
    if (preview) svg.append(preview);
  };
  const move = e => update(e.clientX, e.clientY, e.shiftKey);
  const up = e => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    update(e.clientX, e.clientY, e.shiftKey);
    preview?.remove();
    const r = itemRect(item);
    const big = r && (tool === "line" || tool === "arrow" ? Math.hypot(r[2] - r[0], r[3] - r[1]) > 4 : r[2] - r[0] > 3 && r[3] - r[1] > 3);
    if (big) addItem(item);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}

function startErase() {
  const move = e => eraseAt(e.clientX, e.clientY);
  const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}

function eraseAt(x, y) {
  for (const node of document.elementsFromPoint(x, y)) {
    const id = node.closest?.("[data-item]")?.dataset.item;
    if (id && doc.overlay.has(id)) { removeItem(id); return; }
  }
  if (!doc.ui) return;
  const page = pageAt(x, y);
  if (!page) return;
  for (const editor of doc.ui.getEditors(page - 1)) {
    if (editor.editorType !== "ink" || !editor.div) continue;
    const r = editor.div.getBoundingClientRect();
    if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) { deleteEditor(editor); return; }
  }
}

function layerOf(editor) {
  return doc.viewer?.getPageView(editor.pageIndex)?.annotationEditorLayer?.annotationEditorLayer;
}

export function deleteEditor(editor) {
  if (!doc.ui) return;
  doc.pdfTouched = true;
  doc.ui.addCommands({
    cmd: () => editor.remove(),
    undo: () => { const layer = layerOf(editor); if (layer) layer.addOrRebuild(editor); else { doc.ui.addEditor(editor); doc.ui.addToAnnotationStorage?.(editor); } },
    mustExec: true,
  });
  changed();
}

// ======================================================================
// Images and signatures (PDF.js stamp editor)
// ======================================================================
async function placeImage(file) {
  if (!doc.ui) return;
  if (file.size > 8 * 1024 * 1024) { toast("Images up to 8 MB can be placed"); return; }
  setTool("stamp");
  await new Promise(resolve => setTimeout(resolve, 60));
  params(P.CREATE, { bitmapFile: file });
  doc.pdfTouched = true;
  setTimeout(() => { hooks.refreshDirty(); hooks.scheduleAnnotationList(); }, 300);
}

function dataUrlFile(url, name) {
  const [head, body] = url.split(",");
  const type = /data:([^;]+)/.exec(head)?.[1] || "image/png";
  const binary = atob(body);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new File([bytes], name, { type });
}

async function signatureDialog() {
  let pad;
  const result = await dialog({
    title: "Signature",
    width: 560,
    buttons: [{ label: "Cancel", value: null }, { label: "Place signature", kind: "primary", value: "place" }],
    body: (container) => {
      const saved = store.value.signatures || [];
      const canvas = el("canvas", { class: "sig-pad", width: 1040, height: 360, "aria-label": "Draw your signature" });
      const colors = el("div", { class: "sig-colors" });
      let ink = "#1f1f24";
      for (const c of ["#1f1f24", "#1c3faa", "#c92a2a"]) {
        colors.append(el("button", { type: "button", class: "swatch sm", style: `--c:${c}`, "aria-label": "Ink colour", "aria-checked": String(c === ink), role: "radio",
          onclick: event => { ink = c; colors.querySelectorAll(".swatch").forEach(s => s.setAttribute("aria-checked", String(s === event.currentTarget))); } }));
      }
      const keep = el("input", { type: "checkbox", id: "sig-keep", checked: true });
      const savedRow = el("div", { class: "sig-saved" });
      let chosen = null;
      const renderSaved = () => {
        savedRow.replaceChildren();
        savedRow.hidden = !(store.value.signatures || []).length;
        (store.value.signatures || []).forEach((url, index) => {
          const tile = el("div", { class: "sig-tile", role: "button", tabindex: "0", onclick: () => {
            chosen = chosen === url ? null : url;
            savedRow.querySelectorAll(".sig-tile").forEach(t => t.classList.toggle("chosen", t === tile && chosen === url));
          } }, el("img", { src: url, alt: "Saved signature" }),
            el("button", { type: "button", class: "btn ghost icon sm", "aria-label": "Remove saved signature", onclick: e => {
              e.stopPropagation();
              store.update(v => { v.signatures = (v.signatures || []).filter((_, i) => i !== index); });
              setTimeout(renderSaved, 0);
            } }, icon("x")));
          savedRow.append(tile);
        });
      };
      renderSaved();
      container.append(
        saved.length ? el("div", { class: "dialog-label", text: "Saved signatures" }) : "", savedRow,
        el("div", { class: "dialog-label", text: "Draw a new signature" }),
        el("div", { class: "sig-wrap" }, canvas, el("div", { class: "sig-line" }), el("span", { class: "sig-x", text: "×" })),
        el("div", { class: "sig-tools" }, colors, el("span", { class: "strip-fill" }),
          el("label", { class: "check" }, keep, " Keep for later"),
          el("button", { type: "button", class: "btn ghost sm", text: "Clear", onclick: () => pad.clear() })));
      pad = signaturePad(canvas, () => ink);
      return async () => {
        if (chosen && pad.empty()) return { url: chosen, keep: false };
        if (pad.empty()) { toast("Draw your signature, or choose a saved one"); return undefined; }
        return { url: pad.toDataURL(), keep: keep.checked };
      };
    },
  });
  if (!result) return;
  if (result.keep) store.update(v => { v.signatures = [result.url, ...(v.signatures || []).filter(u => u !== result.url)].slice(0, 4); });
  await placeImage(dataUrlFile(result.url, "signature.png"));
}

function signaturePad(canvas, inkColor) {
  const ctx = canvas.getContext("2d");
  let drawing = false, last = null, empty = true;
  const bounds = { x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity };
  const point = e => { const r = canvas.getBoundingClientRect(); return [(e.clientX - r.left) * canvas.width / r.width, (e.clientY - r.top) * canvas.height / r.height]; };
  canvas.addEventListener("pointerdown", e => { drawing = true; last = point(e); canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener("pointermove", e => {
    if (!drawing) return;
    const p = point(e);
    ctx.strokeStyle = inkColor();
    ctx.lineWidth = 5;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.moveTo(...last);
    ctx.quadraticCurveTo(last[0], last[1], (last[0] + p[0]) / 2, (last[1] + p[1]) / 2);
    ctx.lineTo(...p);
    ctx.stroke();
    for (const [x, y] of [last, p]) { bounds.x0 = Math.min(bounds.x0, x); bounds.y0 = Math.min(bounds.y0, y); bounds.x1 = Math.max(bounds.x1, x); bounds.y1 = Math.max(bounds.y1, y); }
    last = p;
    empty = false;
  });
  const end = () => { drawing = false; };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  return {
    empty: () => empty,
    clear() { ctx.clearRect(0, 0, canvas.width, canvas.height); empty = true; Object.assign(bounds, { x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity }); },
    toDataURL() {
      const pad = 12;
      const x0 = Math.max(0, bounds.x0 - pad), y0 = Math.max(0, bounds.y0 - pad);
      const w = Math.min(canvas.width, bounds.x1 + pad) - x0, h = Math.min(canvas.height, bounds.y1 + pad) - y0;
      const out = document.createElement("canvas");
      out.width = Math.max(1, Math.round(w)); out.height = Math.max(1, Math.round(h));
      out.getContext("2d").drawImage(canvas, x0, y0, w, h, 0, 0, w, h);
      return out.toDataURL("image/png");
    },
  };
}

// ======================================================================
// Reveal and flash
// ======================================================================
export function reveal(page, rect) {
  hooks.goToPage(page);
  requestAnimationFrame(() => requestAnimationFrame(() => {
    const view = pageView(page);
    if (!view?.div || !rect) return;
    const [, top] = view.viewport.convertToViewportPoint(rect[0], rect[3]);
    const container = $("viewer");
    const offset = view.div.offsetTop + view.div.clientTop + top - container.clientHeight / 3;
    container.scrollTo({ top: Math.max(0, offset), behavior: "instant" });
    flash(page, rect);
  }));
}

function flash(page, rect) {
  const view = pageView(page);
  if (!view?.div || !view.pdfPage) return;
  const { svg, vp } = overlayFor(view);
  const [ax, ay] = toView(vp, rect[0], rect[1]), [bx, by] = toView(vp, rect[2], rect[3]);
  const box = svgEl("rect", { class: "flash", x: Math.min(ax, bx) - 5, y: Math.min(ay, by) - 5, width: Math.abs(bx - ax) + 10, height: Math.abs(by - ay) + 10, rx: 4 });
  svg.append(box);
  setTimeout(() => box.remove(), 1500);
}

export function flashEditor(editor) {
  const node = editor.div;
  if (!node) return;
  node.classList.add("op-flash");
  setTimeout(() => node.classList.remove("op-flash"), 1500);
}

// ======================================================================
// Keyboard
// ======================================================================
const KEYS = { v: "select", h: "highlight", u: "underline", k: "strikethrough", p: "ink", e: "eraser", r: "rectangle", o: "ellipse", l: "line", a: "arrow", t: "freetext", n: "note" };

export function onKey(event) {
  if (event.key === "Escape") {
    if (!$("note-pop").hidden) { closeNote(); return true; }
    if (state.selected || state.saved) { selectItem(null); return true; }
    if (!$("sel-pop").hidden) { $("sel-pop").hidden = true; return true; }
    if (doc.mode === "annotate" && state.tool !== "select") { setTool("select"); return true; }
    return false;
  }
  if ((event.key === "Delete" || event.key === "Backspace") && (state.selected || state.saved)) {
    event.preventDefault();
    const saved = savedEntry();
    if (saved) deleteSaved(saved);
    else removeItem(state.selected);
    return true;
  }
  if (event.altKey) return false;
  const tool = KEYS[event.key.toLowerCase()];
  if (!tool) return false;
  if (doc.mode === "annotate") { event.preventDefault(); setTool(tool); return true; }
  if (doc.mode === "fill" && tool === "freetext") { event.preventDefault(); setTool(tool); return true; }
  return false;
}
