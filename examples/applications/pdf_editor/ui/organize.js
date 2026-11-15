// Organize view: a thumbnail grid of every page. Changes are made on a
// page list (rotate, delete, duplicate, reorder, blank pages, pages from
// other PDFs) and applied in one backend step (pypdf) when you leave the
// view or save. Extract and split write new files from the applied result.
import { pdfjs, ASSETS } from "./pdfjs.js";
import { op, cleanError } from "./bridge.js";
import { $, el, icon, toast, busy, dialog, plural, clamp } from "./util.js";
import { store, doc, hooks } from "./state.js";
import { thumbnail, place } from "./thumbs.js";

const org = {
  entries: [], history: [], future: [], selected: new Set(), anchor: null, focus: null,
  pdf: null, external: new Map(), observer: null, counter: 0, dragging: false,
};

const key = () => `e${++org.counter}`;

export function init() {
  const size = Number(store.value.orgSize) || 160;
  $("org-size").value = String(size);
  $("organize").style.setProperty("--thumb", `${size}px`);
  $("org-size").addEventListener("input", event => {
    $("organize").style.setProperty("--thumb", `${event.target.value}px`);
    clearTimeout(init.timer);
    init.timer = setTimeout(() => { store.update(v => { v.orgSize = Number(event.target.value); }); render(); }, 250);
  });
  $("org-rotate-left").addEventListener("click", () => rotate(-90));
  $("org-rotate-right").addEventListener("click", () => rotate(90));
  $("org-delete").addEventListener("click", remove);
  $("org-duplicate").addEventListener("click", duplicate);
  $("org-blank").addEventListener("click", blank);
  $("org-merge").addEventListener("click", merge);
  $("org-extract").addEventListener("click", extract);
  $("org-split").addEventListener("click", split);
  $("org-select-all").addEventListener("click", selectAll);

  const grid = $("org-grid");
  grid.addEventListener("dragover", onDragOver);
  grid.addEventListener("dragleave", event => { if (!grid.contains(event.relatedTarget)) clearDrop(); });
  grid.addEventListener("drop", onDrop);
  $("organize").addEventListener("click", event => {
    if (!event.target.closest(".org-card")) { org.selected.clear(); markSelection(); }
  });
}

function identity() {
  return Array.from({ length: doc.pages }, (_, i) => ({ key: key(), page: i + 1, rotate: 0 }));
}

function isIdentity() {
  return org.entries.length === doc.pages && org.entries.every((e, i) => !e.file && !e.blank && e.page === i + 1 && !e.rotate);
}

export function pendingCount() {
  if (!org.pdf || org.pdf !== doc.pdf || isIdentity()) return 0;
  return Math.max(1, org.history.length);
}
export function canUndo() { return org.history.length > 0; }
export function canRedo() { return org.future.length > 0; }

export function reset() {
  org.entries = [];
  org.history = [];
  org.future = [];
  org.selected.clear();
  org.pdf = null;
  for (const pdf of org.external.values()) pdf.loadingTask?.destroy();
  org.external.clear();
  $("org-grid").replaceChildren();
}

export function enter() {
  if (org.pdf !== doc.pdf) {
    reset();
    org.pdf = doc.pdf;
    org.entries = identity();
    const current = doc.viewer?.currentPageNumber || 1;
    org.selected = new Set([org.entries[current - 1]?.key].filter(Boolean));
    org.anchor = org.focus = org.entries[current - 1]?.key || null;
  }
  render();
  $("organize").focus();
  requestAnimationFrame(() => $("org-grid").querySelector(".org-card.selected")?.scrollIntoView({ block: "center" }));
}

// Applies the page list. Returns false when it could not be applied.
export async function leave({ apply = true, stay = false } = {}) {
  if (!apply || !pendingCount()) return true;
  const sequence = org.entries.map(e => e.blank ? { blank: true, width: e.blank.width, height: e.blank.height }
    : { page: e.page, ...(e.file ? { file: e.file } : {}), ...(e.rotate ? { rotate: e.rotate } : {}) });
  const pageMap = new Map();
  org.entries.forEach((e, i) => { if (!e.file && !e.blank && !pageMap.has(e.page)) pageMap.set(e.page, i + 1); });
  const summary = summarize();
  const firstSelected = org.entries.findIndex(e => org.selected.has(e.key));
  try {
    await hooks.commit({ sequence, pageMap, log: [summary], edits: org.history.length, busyText: "Applying page changes…",
      view: { page: firstSelected >= 0 ? firstSelected + 1 : 1, top: 0, scale: doc.viewer.currentScaleValue } });
  } catch (error) {
    toast(`Page changes were not applied: ${cleanError(error.message)}`);
    return false;
  }
  const keep = org.entries.map((e, i) => org.selected.has(e.key) ? i : -1).filter(i => i >= 0);
  reset();
  org.pdf = doc.pdf;
  org.entries = identity();
  org.selected = new Set(keep.map(i => org.entries[i]?.key).filter(Boolean));
  if (stay || doc.mode === "organize") render();
  return true;
}

function summarize() {
  const parts = [];
  const base = org.entries.filter(e => !e.file && !e.blank);
  const deleted = doc.pages - new Set(base.map(e => e.page)).size;
  const duplicates = base.length - new Set(base.map(e => e.page)).size;
  const rotated = org.entries.filter(e => e.rotate).length;
  const blanks = org.entries.filter(e => e.blank).length;
  const inserted = org.entries.filter(e => e.file).length;
  const order = base.map(e => e.page).filter((p, i, a) => a.indexOf(p) === i);
  const moved = order.some((p, i) => i > 0 && p < order[i - 1]);
  if (deleted) parts.push(`deleted ${plural(deleted, "page")}`);
  if (duplicates) parts.push(`duplicated ${plural(duplicates, "page")}`);
  if (rotated) parts.push(`rotated ${plural(rotated, "page")}`);
  if (blanks) parts.push(`added ${plural(blanks, "blank page")}`);
  if (inserted) parts.push(`inserted ${plural(inserted, "page")} from another PDF`);
  if (moved) parts.push("reordered pages");
  const text = parts.join(", ") || "changed pages";
  return text[0].toUpperCase() + text.slice(1);
}

// ---------------------------------------------------------------- history
function record() {
  org.history.push(org.entries.map(e => ({ ...e })));
  org.future = [];
  if (org.history.length > 200) org.history.shift();
}

export function undo() {
  if (!org.history.length) return;
  org.future.push(org.entries.map(e => ({ ...e })));
  org.entries = org.history.pop();
  render();
}

export function redo() {
  if (!org.future.length) return;
  org.history.push(org.entries.map(e => ({ ...e })));
  org.entries = org.future.pop();
  render();
}

// ---------------------------------------------------------------- rendering
function aspect(entry) {
  if (entry.blank) {
    const r = entry.blank.height / entry.blank.width;
    return entry.rotate % 180 ? 1 / r : r;
  }
  const view = !entry.file && doc.viewer?.getPageView(entry.page - 1);
  let r = 1.414;
  if (view?.pdfPage) { const vp = view.pdfPage.getViewport({ scale: 1 }); r = vp.height / vp.width; }
  else if (entry.ratio) r = entry.ratio;
  return entry.rotate % 180 ? 1 / r : r;
}

function render() {
  const grid = $("org-grid");
  org.observer?.disconnect();
  grid.replaceChildren();
  const width = Number($("org-size").value) || 160;
  org.observer = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      org.observer.unobserve(entry.target);
      paint(entry.target, width);
    }
  }, { root: $("organize"), rootMargin: "400px 0px" });
  org.entries.forEach((entry, index) => {
    const label = entry.blank ? "Blank" : entry.file ? `${entry.file.split("/").pop()} · ${entry.page}` : `Page ${entry.page}`;
    const card = el("div", {
      class: "org-card", role: "option", tabindex: "-1", draggable: "true", "aria-selected": "false",
      "aria-label": `${index + 1}: ${label}`, dataset: { key: entry.key, index: String(index) },
    },
      el("div", { class: `org-thumb pending${entry.blank ? " blank" : ""}`, style: `aspect-ratio: 1 / ${aspect(entry).toFixed(4)}` },
        entry.rotate ? el("span", { class: "org-badge", text: `${entry.rotate}°` }) : null,
        entry.file ? el("span", { class: "org-badge file", text: "Inserted" }) : null),
      el("div", { class: "org-label" }, el("span", { class: "org-num", text: String(index + 1) }),
        entry.file || entry.blank || entry.page !== index + 1 ? el("span", { class: "org-origin", text: entry.blank ? "blank" : entry.file ? entry.file.split("/").pop() : `was ${entry.page}` }) : null));
    card.addEventListener("click", event => onCardClick(event, entry.key));
    card.addEventListener("dblclick", () => openPage(index));
    card.addEventListener("dragstart", event => onDragStart(event, entry.key));
    card.addEventListener("dragend", () => { org.dragging = false; grid.classList.remove("dragging"); clearDrop(); });
    grid.append(card);
    if (!entry.blank) org.observer.observe(card);
    else card.querySelector(".org-thumb").classList.remove("pending");
  });
  markSelection();
  hooks.refreshDirty();
  hooks.updateHistoryButtons();
}

function paint(card, width) {
  const entry = org.entries.find(e => e.key === card.dataset.key);
  if (!entry || entry.blank) return;
  const pdf = entry.file ? org.external.get(entry.file) : doc.pdf;
  if (!pdf) return;
  thumbnail(pdf, entry.page, width, { key: entry.file || "doc", rotate: entry.rotate || 0 }).then(canvas => {
    const box = card.querySelector(".org-thumb");
    if (!box) return;
    place(box, canvas);
    box.style.aspectRatio = `${canvas.width} / ${canvas.height}`;
  }).catch(() => {});
}

function markSelection() {
  const cards = $("org-grid").querySelectorAll(".org-card");
  cards.forEach(card => {
    const on = org.selected.has(card.dataset.key);
    card.classList.toggle("selected", on);
    card.setAttribute("aria-selected", String(on));
    card.classList.toggle("focused", card.dataset.key === org.focus);
  });
  const count = org.selected.size;
  $("org-count").textContent = count ? `${count} of ${plural(org.entries.length, "page")} selected` : plural(org.entries.length, "page");
  for (const id of ["org-rotate-left", "org-rotate-right", "org-delete", "org-duplicate", "org-extract"]) $(id).disabled = !count;
}

function selectedIndices() { return org.entries.map((e, i) => org.selected.has(e.key) ? i : -1).filter(i => i >= 0); }

// ---------------------------------------------------------------- selection
function onCardClick(event, cardKey) {
  event.stopPropagation();
  const index = org.entries.findIndex(e => e.key === cardKey);
  if (event.shiftKey && org.anchor) {
    const from = org.entries.findIndex(e => e.key === org.anchor);
    const [a, b] = [Math.min(from, index), Math.max(from, index)];
    if (!(event.metaKey || event.ctrlKey)) org.selected.clear();
    for (let i = a; i <= b; i++) org.selected.add(org.entries[i].key);
  } else if (event.metaKey || event.ctrlKey) {
    if (org.selected.has(cardKey)) org.selected.delete(cardKey); else org.selected.add(cardKey);
    org.anchor = cardKey;
  } else {
    org.selected = new Set([cardKey]);
    org.anchor = cardKey;
  }
  org.focus = cardKey;
  markSelection();
}

function selectAll() {
  org.selected = new Set(org.entries.map(e => e.key));
  markSelection();
}

function openPage(index) {
  const entry = org.entries[index];
  hooks.setMode("read").then(() => hooks.goToPage(pendingCount() ? index + 1 : entry.page));
}

// ---------------------------------------------------------------- operations
function rotate(delta) {
  if (!org.selected.size) return;
  record();
  for (const entry of org.entries) if (org.selected.has(entry.key)) entry.rotate = ((entry.rotate || 0) + delta + 360) % 360;
  render();
}

function remove() {
  if (!org.selected.size) return;
  if (org.selected.size >= org.entries.length) { toast("A PDF needs at least one page"); return; }
  record();
  const first = selectedIndices()[0];
  org.entries = org.entries.filter(e => !org.selected.has(e.key));
  const next = org.entries[Math.min(first, org.entries.length - 1)];
  org.selected = new Set(next ? [next.key] : []);
  org.anchor = org.focus = next?.key || null;
  render();
  toast("Pages deleted", { action: "Undo", onAction: undo });
}

function duplicate() {
  if (!org.selected.size) return;
  record();
  const copies = new Set();
  const out = [];
  for (const entry of org.entries) {
    out.push(entry);
    if (org.selected.has(entry.key)) { const copy = { ...entry, key: key() }; copies.add(copy.key); out.push(copy); }
  }
  org.entries = out;
  org.selected = copies;
  render();
}

function sizeOf(entry) {
  if (!entry) return { width: 595.28, height: 841.89 };
  if (entry.blank) return { ...entry.blank };
  const view = !entry.file && doc.viewer?.getPageView(entry.page - 1);
  if (view?.pdfPage) {
    const vp = view.pdfPage.getViewport({ scale: 1, rotation: 0 });
    const rot = ((view.pdfPage.rotate || 0) + (entry.rotate || 0)) % 180;
    return rot ? { width: vp.height, height: vp.width } : { width: vp.width, height: vp.height };
  }
  return { width: 595.28, height: 841.89 };
}

function blank() {
  record();
  const indices = selectedIndices();
  const after = indices.length ? indices[indices.length - 1] : org.entries.length - 1;
  const entry = { key: key(), blank: sizeOf(org.entries[after]), rotate: 0 };
  org.entries.splice(after + 1, 0, entry);
  org.selected = new Set([entry.key]);
  org.anchor = org.focus = entry.key;
  render();
}

async function merge() {
  const files = (await hooks.listFiles()).filter(f => f.path !== doc.path);
  if (!files.length) { toast("This project has no other PDF to insert"); return; }
  const indices = selectedIndices();
  const result = await dialog({
    title: "Insert pages from another PDF",
    width: 480,
    buttons: [{ label: "Cancel", value: null }, { label: "Insert", kind: "primary", value: "ok" }],
    body: container => {
      let chosen = files[0].path;
      const list = el("div", { class: "pick-list scroll", role: "listbox" });
      for (const file of files.slice(0, 200)) {
        const row = el("button", { type: "button", class: "pick-row", role: "option", "aria-selected": String(file.path === chosen),
          onclick: () => { chosen = file.path; list.querySelectorAll(".pick-row").forEach(r => r.setAttribute("aria-selected", String(r === row))); } },
          el("span", { class: "file-icon sm", text: "PDF" }), el("span", { class: "pick-name", text: file.path }));
        list.append(row);
      }
      const where = el("select", { class: "field wide", "aria-label": "Position" },
        indices.length ? el("option", { value: "after", text: `After page ${indices[indices.length - 1] + 1} (the selection)` }) : null,
        el("option", { value: "end", text: "At the end" }), el("option", { value: "start", text: "At the beginning" }));
      container.append(el("div", { class: "dialog-label", text: "PDF in this project" }), list, el("div", { class: "dialog-label", text: "Insert" }), where);
      return () => ({ path: chosen, where: where.value });
    },
  });
  if (!result) return;
  busy("Loading pages…");
  try {
    let pdf = org.external.get(result.path);
    if (!pdf) {
      const bytes = await hooks.fetchBytes({ path: result.path });
      pdf = await pdfjs.getDocument({ data: bytes, isEvalSupported: false, enableXfa: false, cMapUrl: ASSETS.cMapUrl, cMapPacked: true,
        standardFontDataUrl: ASSETS.standardFontDataUrl, wasmUrl: ASSETS.wasmUrl }).promise;
      org.external.set(result.path, pdf);
    }
    const first = await pdf.getPage(1);
    const vp = first.getViewport({ scale: 1 });
    record();
    const inserted = Array.from({ length: pdf.numPages }, (_, i) => ({ key: key(), page: i + 1, file: result.path, rotate: 0, ratio: vp.height / vp.width }));
    const at = result.where === "start" ? 0 : result.where === "end" ? org.entries.length : indices[indices.length - 1] + 1;
    org.entries.splice(at, 0, ...inserted);
    org.selected = new Set(inserted.map(e => e.key));
    render();
    toast(`Inserted ${plural(inserted.length, "page")} from ${result.path.split("/").pop()}`);
  } catch (error) {
    toast(cleanError(error.message));
  } finally { busy(null); }
}

function rangeLabel(pages) {
  const parts = [];
  for (let i = 0; i < pages.length; i++) {
    let j = i;
    while (j + 1 < pages.length && pages[j + 1] === pages[j] + 1) j++;
    parts.push(i === j ? `${pages[i]}` : `${pages[i]}-${pages[j]}`);
    i = j;
  }
  return parts.join(",");
}

async function extract() {
  const indices = selectedIndices();
  if (!indices.length) return;
  const pages = indices.map(i => i + 1);
  const base = doc.path.replace(/\.pdf$/i, "");
  const output = await hooks.askPath({
    title: `Extract ${plural(pages.length, "page")}`, label: "New file", value: `${base} (pages ${rangeLabel(pages)}).pdf`, action: "Extract",
    note: "The selected pages are written to a new PDF; this document is not changed.",
  });
  if (!output) return;
  if (!(await leave({ apply: true, stay: true }))) return;
  busy("Extracting…");
  try {
    const result = await op("export", { ...(doc.draft ? { draft: doc.draft } : { path: doc.path }), parts: [{ pages, output }] });
    hooks.refreshFiles?.();
    toast(`Saved ${result.files[0].path}`, { action: "Open", onAction: () => hooks.openDocument(result.files[0].path) });
  } catch (error) { toast(cleanError(error.message)); }
  finally { busy(null); }
}

async function split() {
  const total = org.entries.length;
  const cuts = selectedIndices().filter(i => i > 0).map(i => i + 1);
  const base = doc.path.replace(/\.pdf$/i, "");
  const result = await dialog({
    title: "Split into several PDFs",
    width: 480,
    buttons: [{ label: "Cancel", value: null }, { label: "Split", kind: "primary", value: "ok" }],
    body: container => {
      const every = el("input", { class: "field num", type: "number", min: "1", max: String(Math.max(1, total)), value: String(Math.max(1, Math.ceil(total / 2))), "aria-label": "Pages per file" });
      const byEvery = el("input", { type: "radio", name: "split-how", value: "every", checked: !cuts.length });
      const bySel = el("input", { type: "radio", name: "split-how", value: "selection", checked: cuts.length > 0, disabled: !cuts.length });
      const preview = el("ol", { class: "split-preview scroll" });
      const parts = () => {
        if (bySel.checked && cuts.length) {
          const starts = [1, ...cuts];
          return starts.map((s, i) => Array.from({ length: (starts[i + 1] || total + 1) - s }, (_, k) => s + k));
        }
        const n = clamp(Number(every.value) || 1, 1, total);
        const out = [];
        for (let s = 1; s <= total; s += n) out.push(Array.from({ length: Math.min(n, total - s + 1) }, (_, k) => s + k));
        return out;
      };
      const update = () => {
        const list = parts();
        preview.replaceChildren(...list.slice(0, 50).map((p, i) => el("li", {}, el("span", { class: "pick-name", text: `${base.split("/").pop()} (part ${i + 1}).pdf` }), el("span", { class: "muted", text: `pages ${rangeLabel(p)}` }))));
      };
      for (const node of [every, byEvery, bySel]) node.addEventListener("input", update);
      container.append(
        el("label", { class: "radio-row" }, byEvery, el("span", { text: "Every" }), every, el("span", { text: "pages" })),
        el("label", { class: `radio-row${cuts.length ? "" : " disabled"}` }, bySel, el("span", { text: cuts.length ? `Before each selected page (${cuts.join(", ")})` : "Before each selected page (select the first page of each part)" })),
        el("div", { class: "dialog-label", text: "Files" }), preview,
        el("p", { class: "dialog-note", text: "New files are written beside this PDF; this document is not changed." }));
      update();
      return () => {
        const list = parts();
        if (list.length < 2) { toast("That gives only one file"); return undefined; }
        return list;
      };
    },
  });
  if (!result) return;
  if (!(await leave({ apply: true, stay: true }))) return;
  busy("Splitting…");
  try {
    const parts = result.map((pages, i) => ({ pages, output: `${base} (part ${i + 1}).pdf` }));
    const done = await op("export", { ...(doc.draft ? { draft: doc.draft } : { path: doc.path }), parts });
    hooks.refreshFiles?.();
    toast(`Wrote ${plural(done.count, "file")}`);
  } catch (error) { toast(cleanError(error.message)); }
  finally { busy(null); }
}

// ---------------------------------------------------------------- drag and drop
let dropIndex = null;
function onDragStart(event, cardKey) {
  if (!org.selected.has(cardKey)) { org.selected = new Set([cardKey]); org.anchor = cardKey; markSelection(); }
  org.dragging = true;
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", `${org.selected.size} pages`);
  $("org-grid").classList.add("dragging");
}

function onDragOver(event) {
  if (!org.dragging) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "move";
  const cards = [...$("org-grid").querySelectorAll(".org-card")];
  let index = cards.length;
  for (const [i, card] of cards.entries()) {
    const r = card.getBoundingClientRect();
    if (event.clientY < r.top - 8) { index = i; break; }
    if (event.clientY <= r.bottom + 8 && event.clientX < r.left + r.width / 2) { index = i; break; }
  }
  if (index === dropIndex) return;
  dropIndex = index;
  clearDrop(false);
  if (cards[index]) cards[index].classList.add("drop-before");
  else cards[cards.length - 1]?.classList.add("drop-after");
}

function clearDrop(resetIndex = true) {
  $("org-grid").querySelectorAll(".drop-before, .drop-after").forEach(c => c.classList.remove("drop-before", "drop-after"));
  if (resetIndex) dropIndex = null;
}

function onDrop(event) {
  if (!org.dragging) return;
  event.preventDefault();
  const index = dropIndex ?? org.entries.length;
  clearDrop();
  org.dragging = false;
  $("org-grid").classList.remove("dragging");
  moveSelection(index);
}

function moveSelection(index) {
  const moving = org.entries.filter(e => org.selected.has(e.key));
  const before = org.entries.slice(0, index).filter(e => !org.selected.has(e.key));
  const after = org.entries.slice(index).filter(e => !org.selected.has(e.key));
  const next = [...before, ...moving, ...after];
  if (next.every((e, i) => e === org.entries[i])) return;
  record();
  org.entries = next;
  render();
}

// ---------------------------------------------------------------- keyboard
export function onKey(event, { mod, typing }) {
  if (typing) return false;
  const key = event.key.toLowerCase();
  if (mod && key === "a") { event.preventDefault(); selectAll(); return true; }
  if (mod && key === "d") { event.preventDefault(); duplicate(); return true; }
  if (mod) return false;
  if (event.key === "Delete" || event.key === "Backspace") { event.preventDefault(); remove(); return true; }
  if (event.key === "[") { event.preventDefault(); rotate(-90); return true; }
  if (event.key === "]") { event.preventDefault(); rotate(90); return true; }
  if (event.key === "Escape") { org.selected.clear(); markSelection(); return true; }
  if (event.key === "Enter") {
    const i = org.entries.findIndex(e => e.key === org.focus);
    if (i >= 0) openPage(i);
    return true;
  }
  if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
    event.preventDefault();
    const cards = [...$("org-grid").querySelectorAll(".org-card")];
    if (!cards.length) return true;
    let i = Math.max(0, org.entries.findIndex(e => e.key === org.focus));
    const perRow = Math.max(1, Math.round($("org-grid").clientWidth / (cards[0].offsetWidth + 16)));
    if (event.key === "ArrowLeft") i--;
    if (event.key === "ArrowRight") i++;
    if (event.key === "ArrowUp") i -= perRow;
    if (event.key === "ArrowDown") i += perRow;
    i = clamp(i, 0, cards.length - 1);
    const target = org.entries[i].key;
    if (event.shiftKey) org.selected.add(target);
    else { org.selected = new Set([target]); org.anchor = target; }
    org.focus = target;
    markSelection();
    cards[i].scrollIntoView({ block: "nearest" });
    return true;
  }
  return false;
}
