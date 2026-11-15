import { pdfjs, viewer as V, ASSETS } from "./pdfjs.js";
import { api, op, cachedOp, formatBytes, formatDate, renderMarkdown, cleanError } from "./bridge.js";
import { $, el, icon, toast, busy, dialog, plural, clamp } from "./util.js";
import { store, doc, hooks, HIGHLIGHT_COLORS } from "./state.js";
import { thumbnail, place, resetThumbs } from "./thumbs.js";
import * as annotate from "./annotate.js";
import * as organize from "./organize.js";

const CHUNK = 600_000;
const ZOOM_STEPS = [0.25, 0.33, 0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4, 5];
const reader = $("reader");

// ======================================================================
// Library
// ======================================================================
let files = [];
let fileCursor = 0;

async function showLibrary() {
  reader.hidden = true;
  $("error-screen").hidden = true;
  $("library").hidden = false;
  $("file-filter").focus();
  await refreshFiles();
}

async function refreshFiles() {
  const list = $("file-list");
  if (!files.length) list.innerHTML = `<div class="file-empty"><div class="spinner" style="margin:0 auto 10px"></div>Looking for PDFs…</div>`;
  try {
    const result = await op("list_files", {});
    files = result.files;
    $("project-name").textContent = `PDF files in ${result.project}`;
    renderFiles();
  } catch (error) {
    list.replaceChildren(el("div", { class: "file-empty error-text", text: error.message }));
  }
}

function renderFiles() {
  const filter = $("file-filter").value.trim().toLowerCase();
  const list = $("file-list");
  list.replaceChildren();
  const visible = files.filter(f => !filter || f.path.toLowerCase().includes(filter));
  const recentPaths = (store.value.recent || []).map(r => r.path).filter(p => visible.some(f => f.path === p)).slice(0, 4);
  const groups = [];
  if (recentPaths.length && !filter) groups.push(["Recent", recentPaths.map(p => visible.find(f => f.path === p))]);
  groups.push([filter ? "Matches" : "All PDFs", visible]);
  let index = 0;
  for (const [label, items] of groups) {
    if (!items.length) continue;
    list.append(el("div", { class: "file-group", text: `${label}${label === "Recent" ? "" : ` · ${items.length}`}` }));
    for (const file of items) {
      const slash = file.path.lastIndexOf("/");
      const draft = store.value.docs[file.path]?.draft;
      const row = el("button", { type: "button", class: "file-row", role: "option", dataset: { index: String(index++) }, onclick: () => openDocument(file.path) },
        el("span", { class: "file-icon", text: "PDF" }),
        el("span", { class: "file-text" }, el("span", { class: "name", text: file.path.slice(slash + 1) }),
          el("span", { class: "dir", text: slash > 0 ? shortDir(file.path.slice(0, slash)) : "Project root" })),
        draft?.edits ? el("span", { class: "file-draft", text: `Draft · ${plural(draft.edits, "edit")}` }) : el("span"),
        el("span", { class: "meta", html: `${formatBytes(file.size)}<br>${formatDate(file.modified)}` }));
      list.append(row);
    }
  }
  if (!visible.length) {
    list.replaceChildren(el("div", { class: "file-empty", text: files.length ? "No PDF matches this filter." : "This project has no PDF files yet." }));
  }
  fileCursor = Math.min(fileCursor, Math.max(0, index - 1));
  markFileCursor();
}

// A deep folder keeps its first and last two segments: Library/…/file/2023-05.
function shortDir(dir) {
  const parts = dir.split("/");
  return parts.length > 3 ? `${parts[0]}/…/${parts.slice(-2).join("/")}` : dir;
}

function markFileCursor() {
  const rows = [...document.querySelectorAll(".file-row")];
  rows.forEach((row, i) => row.classList.toggle("active", i === fileCursor));
  rows[fileCursor]?.scrollIntoView({ block: "nearest" });
}

$("file-filter").addEventListener("input", () => { fileCursor = 0; renderFiles(); });
$("file-filter").addEventListener("keydown", event => {
  const rows = document.querySelectorAll(".file-row");
  if (event.key === "ArrowDown") { event.preventDefault(); fileCursor = Math.min(fileCursor + 1, rows.length - 1); markFileCursor(); }
  else if (event.key === "ArrowUp") { event.preventDefault(); fileCursor = Math.max(fileCursor - 1, 0); markFileCursor(); }
  else if (event.key === "Enter") { event.preventDefault(); rows[fileCursor]?.click(); }
});
$("refresh-files").addEventListener("click", refreshFiles);

// ======================================================================
// Bytes in and out (base64 chunks through operations)
// ======================================================================
function decode(base64) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function encode(bytes) {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

// Fetches a project file ({path, revision, size}) or a draft ({draft, size}).
async function fetchBytes(source, onProgress = () => {}) {
  const key = source.draft ? { draft: source.draft } : { path: source.path, revision: source.revision };
  const first = await cachedOp("chunk", { ...key, offset: 0, length: CHUNK });
  const size = first.size;
  const data = new Uint8Array(size);
  data.set(decode(first.data), 0);
  const count = Math.max(1, Math.ceil(size / CHUNK));
  let done = 1, next = 1;
  onProgress(done / count);
  async function worker() {
    while (next < count) {
      const index = next++;
      const result = await cachedOp("chunk", { ...key, offset: index * CHUNK, length: CHUNK });
      data.set(decode(result.data), result.offset);
      onProgress(++done / count);
    }
  }
  await Promise.all(Array.from({ length: Math.min(4, count - 1) }, worker));
  return data;
}

// Uploads serialised bytes and returns the new draft's summary.
async function uploadBytes(bytes) {
  let upload = null;
  for (let offset = 0; offset < bytes.length || offset === 0; offset += CHUNK) {
    const slice = bytes.subarray(offset, offset + CHUNK);
    const final = offset + CHUNK >= bytes.length;
    const result = await op("upload", { ...(upload ? { upload } : {}), offset, data: encode(slice), final,
      ...(final && store.value.author ? { author: store.value.author } : {}) });
    if (final) return result;
    upload = result.upload;
  }
  throw new Error("Nothing to upload");
}

// ======================================================================
// Viewer (PDF.js PDFViewer with forms and the annotation editor enabled)
// ======================================================================
function createViewer() {
  const bus = new V.EventBus();
  const links = new V.PDFLinkService({ eventBus: bus, externalLinkTarget: V.LinkTarget.BLANK, externalLinkRel: "noopener noreferrer nofollow" });
  const find = new V.PDFFindController({ eventBus: bus, linkService: links });
  const viewer = new V.PDFViewer({
    container: $("viewer"), viewer: $("pages"), eventBus: bus, linkService: links, findController: find,
    textLayerMode: 1,
    annotationMode: pdfjs.AnnotationMode.ENABLE_FORMS,
    annotationEditorMode: pdfjs.AnnotationEditorType.NONE,
    annotationEditorHighlightColors: HIGHLIGHT_COLORS,
    enableHighlightFloatingButton: false,
    imageResourcesPath: ASSETS.imageResourcesPath,
    maxCanvasPixels: 16_000_000, enableDetailCanvas: false,
  });
  links.setViewer(viewer);
  fitSpreads(viewer);
  Object.assign(doc, { viewer, bus, links, find });

  bus.on("annotationeditoruimanager", ({ uiManager }) => { doc.ui = uiManager; annotate.onUiManager(uiManager); });
  bus.on("pagechanging", ({ pageNumber }) => setCurrent(pageNumber));
  bus.on("scalechanging", ({ scale }) => {
    $("zoom-label").textContent = `${Math.round(scale * 100)}%`;
    markZoom();
    saveView();
  });
  // Pages after the first take their real size only once PDF.js has fetched
  // them; a spread fitted before that is fitted again.
  bus.on("pagesloaded", () => {
    const value = viewer.currentScaleValue;
    const scale = SPREAD_FITS.has(value) && spreadScale(viewer, value);
    if (scale && Math.abs(scale - viewer.currentScale) > 0.001) viewer.currentScaleValue = value;
  });
  bus.on("pagerendered", ({ pageNumber }) => annotate.drawPage(pageNumber));
  bus.on("annotationlayerrendered", ({ pageNumber }) => annotate.decorateForms(pageNumber));
  bus.on("editingstateschanged", ({ details }) => {
    doc.canUndo = !!details.hasSomethingToUndo;
    doc.canRedo = !!details.hasSomethingToRedo;
    if (details.hasSomethingToUndo) doc.pdfTouched = true;
    refreshDirty();
    scheduleAnnotationList();
  });
  bus.on("annotationeditormodechanged", ({ mode }) => annotate.onEditorMode(mode));
  // The viewer component leaves mode requests from the editors to the app.
  bus.on("showannotationeditorui", ({ mode }) => { viewer.annotationEditorMode = { mode }; });
  bus.on("switchannotationeditormode", evt => { viewer.annotationEditorMode = evt; });
  bus.on("updatefindmatchescount", ({ matchesCount }) => updateSearchCount(matchesCount));
  bus.on("updatefindcontrolstate", ({ state, matchesCount }) => {
    if (matchesCount) updateSearchCount(matchesCount);
    $("search-count").classList.toggle("none", state === V.FindState?.NOT_FOUND || state === 1);
  });
  // Form edits mark the document as changed.
  $("pages").addEventListener("input", event => { if (event.target.closest(".annotationLayer")) markFormChange(event.target); });
  $("pages").addEventListener("change", event => { if (event.target.closest(".annotationLayer")) markFormChange(event.target); });
}

function markZoom() {
  const value = doc.viewer?.currentScaleValue;
  document.querySelectorAll("#zoom-menu [data-zoom]").forEach(b => b.classList.toggle("current", b.dataset.zoom === value));
}

// PDF.js fits a spread as two copies of the current page, so a spread with a
// rotated (landscape) page or pages of different sizes overflows the window.
// In spread mode the three fit modes are computed here from every page's own
// rotated size, so the widest spread and the tallest page fit; the viewer
// still reports the fit mode as its scale value.
const SPREAD_FITS = new Set(["auto", "page-width", "page-fit"]);
const MAX_AUTO_SCALE = 1.25;

function fitSpreads(viewer) {
  const property = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(viewer), "currentScaleValue");
  let fit = null;
  Object.defineProperty(viewer, "currentScaleValue", {
    configurable: true,
    get() { return fit || property.get.call(this); },
    set(value) {
      const scale = SPREAD_FITS.has(value) ? spreadScale(this, value) : null;
      fit = scale ? value : null;
      property.set.call(this, scale ? String(scale) : value);
      markZoom();
    },
  });
}

function spreadScale(viewer, value) {
  if (viewer.spreadMode === V.SpreadMode.NONE || viewer.scrollMode === V.ScrollMode.HORIZONTAL) return null;
  const views = viewer._pages || [];
  const current = views[viewer.currentPageNumber - 1];
  if (!views.length || !current) return null;
  // Unscaled size of each page as shown (its rotation included).
  const sizes = views.map(view => ({ w: view.width / view.scale, h: view.height / view.scale }));
  const first = viewer.spreadMode === V.SpreadMode.ODD ? 0 : 1;
  const spreads = [];
  sizes.forEach((size, i) => {
    if (i === 0 || (i - first) % 2 === 0) spreads.push([size]);
    else spreads[spreads.length - 1].push(size);
  });
  const container = viewer.container;
  const pad = getComputedStyle(viewer.viewer);
  const page = getComputedStyle(container.querySelector(".spread .page") || current.div);
  const marginX = parseFloat(page.marginLeft) + parseFloat(page.marginRight) || 0;
  const marginY = parseFloat(page.marginTop) + parseFloat(page.marginBottom) || 0;
  const width = container.clientWidth - (parseFloat(pad.paddingLeft) + parseFloat(pad.paddingRight) || 0);
  const height = container.clientHeight - marginY;
  const widthScale = Math.min(...spreads.map(spread => (width - marginX * spread.length) / spread.reduce((sum, s) => sum + s.w, 0)));
  const heightScale = height / Math.max(...sizes.map(s => s.h));
  let scale = widthScale;
  if (value === "page-fit") scale = Math.min(widthScale, heightScale);
  else if (value === "auto") scale = Math.min(MAX_AUTO_SCALE, current.width <= current.height ? widthScale : Math.min(widthScale, heightScale));
  return scale > 0 && Number.isFinite(scale) ? Math.round(scale * 10000) / 10000 : null;
}

function markFormChange(target) {
  doc.pdfTouched = true;
  doc.formChanged.add(target.name || target.id || "field");
  refreshDirty();
}

async function openPdf(bytes, view) {
  const task = pdfjs.getDocument({
    data: bytes, isEvalSupported: false, enableXfa: false,
    cMapUrl: ASSETS.cMapUrl, cMapPacked: true, standardFontDataUrl: ASSETS.standardFontDataUrl, wasmUrl: ASSETS.wasmUrl,
  });
  const pdf = await task.promise;
  const old = doc.pdf;
  doc.pdf = pdf;
  doc.ui = null;
  doc.pages = pdf.numPages;
  doc.canUndo = doc.canRedo = false;
  doc.pdfTouched = false;
  doc.formChanged = new Set();
  const ready = new Promise(resolve => doc.bus.on("pagesinit", resolve, { once: true }));
  doc.viewer.setDocument(pdf);
  doc.links.setDocument(pdf, null);
  await ready;
  old?.loadingTask.destroy();
  applyLayout(store.value.layout, { silent: true });
  restoreView(view);
  doc.baseHash = pdf.annotationStorage.serializable.hash;
  pdf.annotationStorage.onSetModified = () => { refreshDirty(); };
  $("page-total").textContent = `/ ${pdf.numPages}`;
  resetThumbs();
  renderThumbs();
  await loadOutline();
  renderOutline();
  annotate.onDocument();
  loadFields();
}

function captureView() {
  const viewer = doc.viewer;
  if (!viewer?.pdfDocument) return null;
  const page = viewer.currentPageNumber;
  const view = viewer.getPageView(page - 1);
  const container = $("viewer");
  const top = view?.div ? (container.scrollTop - view.div.offsetTop) / Math.max(1, view.div.offsetHeight) : 0;
  return { page, top, scale: viewer.currentScaleValue };
}

function restoreView(view) {
  const viewer = doc.viewer;
  const saved = store.value.docs[doc.path] || {};
  viewer.currentScaleValue = view?.scale || saved.scale || "auto";
  const page = clamp(view?.page || saved.page || 1, 1, doc.pages);
  viewer.currentPageNumber = page;
  if (view && store.value.layout !== "single") {
    const pageView = viewer.getPageView(page - 1);
    if (pageView?.div) $("viewer").scrollTop = pageView.div.offsetTop + view.top * pageView.div.offsetHeight;
  }
  setCurrent(page);
}

async function openDocument(path, { quiet = false } = {}) {
  const token = ++doc.loadToken;
  await leaveDocument();
  $("library").hidden = true;
  $("error-screen").hidden = true;
  reader.hidden = false;
  $("doc-title").textContent = path.split("/").pop();
  setLoading("Opening…", 0.02);
  try {
    const info = await op("info", { path });
    if (token !== doc.loadToken) return;
    Object.assign(doc, { path: info.path, info, draft: null, edits: 0, log: [], history: { undo: [], redo: [] }, annotations: [] });
    doc.overlay.clear();
    $("doc-title").textContent = info.title || info.path.split("/").pop();
    $("back").dataset.tip = `${info.path} · ⌘O for another PDF`;
    const saved = store.value.docs[doc.path]?.draft;
    let source = { path: info.path, revision: info.revision, size: info.size };
    if (saved?.id) {
      source = { draft: saved.id };
      Object.assign(doc, { draft: saved.id, edits: saved.edits || 0, log: saved.log || [] });
    }
    setLoading(`Loading ${formatBytes(info.size)}…`, 0.05);
    let bytes;
    try {
      bytes = await fetchBytes(source, f => setLoading(`Loading ${formatBytes(info.size)}…`, 0.05 + f * 0.85));
    } catch (error) {
      if (!source.draft) throw error;
      // The staged draft expired: fall back to the file.
      toast("The unsaved draft of this file expired; showing the saved file");
      Object.assign(doc, { draft: null, edits: 0, log: [] });
      persistDraft();
      source = { path: info.path, revision: info.revision };
      bytes = await fetchBytes(source, f => setLoading("Loading…", 0.05 + f * 0.85));
    }
    if (token !== doc.loadToken) return;
    setLoading("Preparing pages…", 0.95);
    await openPdf(bytes, null);
    if (token !== doc.loadToken) return;
    renderBookmarks();
    renderSummaryState();
    setLoading(null);
    refreshDirty();
    showStaleBanner(saved);
    $("viewer").focus();
    rememberRecent(doc.path);
    listAnnotations();
  } catch (error) {
    if (token !== doc.loadToken) return;
    setLoading(null);
    if (quiet) { closeDocument(); store.update(v => { v.last = null; }); showLibrary(); return; }
    showError("Could not open this PDF", error.message || String(error));
  }
}

function showStaleBanner(saved) {
  if (saved?.id && doc.draft && saved.base && saved.base !== doc.info.revision) {
    banner("The file changed on disk after this draft was made. Saving replaces it with the draft.", [
      { label: "Discard draft", onClick: () => discardDraft({ confirm: false }) },
    ]);
  } else banner(null);
}

async function listAnnotations() {
  try {
    const source = doc.draft ? { draft: doc.draft } : { path: doc.path };
    const result = await op("list_annotations", source);
    if ((doc.draft ? { draft: doc.draft } : { path: doc.path }).draft !== source.draft) return;
    doc.annotations = result.annotations;
    renderAnnotationList();
  } catch (error) { console.warn("annotations", error); }
}

async function leaveDocument() {
  if (!doc.pdf) return;
  try {
    if (doc.mode === "organize") await organize.leave({ apply: true });
    if (liveChanges().any) await commit({ silent: true });
  } catch (error) { console.warn("commit on leave", error); }
  closeDocument();
}

function closeDocument() {
  annotate.reset();
  organize.reset();
  if (doc.viewer) doc.viewer.setDocument(null);
  doc.pdf?.loadingTask.destroy();
  Object.assign(doc, { path: null, info: null, pdf: null, ui: null, pages: 0, outline: [], draft: null, annotations: [] });
  doc.overlay.clear();
  closeSearch(true);
  $("summary").hidden = true;
  setMode("read", { force: true });
}

function setLoading(text, fraction = 0) {
  $("loading").hidden = text === null;
  if (text !== null) {
    $("loading-text").textContent = text;
    $("loading-bar").style.width = `${Math.round(fraction * 100)}%`;
  }
}

function showError(title, text) {
  $("error-title").textContent = title;
  $("error-text").textContent = cleanError(text);
  $("error-screen").hidden = false;
}
$("error-back").addEventListener("click", () => { $("error-screen").hidden = true; showLibrary(); });

function rememberRecent(path) {
  store.update(value => {
    value.recent = [{ path, at: Date.now() }, ...(value.recent || []).filter(r => r.path !== path)].slice(0, 12);
    value.last = path;
  });
}

let saveViewTimer;
function saveView() {
  clearTimeout(saveViewTimer);
  const path = doc.path;
  if (!path || !doc.viewer?.pdfDocument) return;
  const view = { page: doc.viewer.currentPageNumber, scale: doc.viewer.currentScaleValue };
  saveViewTimer = setTimeout(() => store.update(value => {
    value.docs[path] = { ...(value.docs[path] || {}), ...view };
  }), 800);
}

function banner(text, actions = []) {
  $("banner").hidden = !text;
  if (!text) return;
  $("banner-text").textContent = text;
  $("banner-actions").replaceChildren(...actions.map(a => el("button", { class: "btn sm", text: a.label, onclick: a.onClick })));
}

// ======================================================================
// Reading modes: page layout and tint
// ======================================================================
function applyLayout(layout, { silent = false } = {}) {
  const viewer = doc.viewer;
  if (!viewer) return;
  const view = silent ? null : captureView();
  const wasSpread = viewer.spreadMode !== V.SpreadMode.NONE;
  if (layout === "single") { viewer.scrollMode = V.ScrollMode.PAGE; viewer.spreadMode = V.SpreadMode.NONE; }
  else if (layout === "spread") { viewer.scrollMode = V.ScrollMode.VERTICAL; viewer.spreadMode = V.SpreadMode.ODD; }
  else { viewer.scrollMode = V.ScrollMode.VERTICAL; viewer.spreadMode = V.SpreadMode.NONE; }
  document.querySelectorAll("[data-layout]").forEach(b => b.setAttribute("aria-checked", String(b.dataset.layout === layout)));
  $("viewer").dataset.layout = layout;
  if (!silent) {
    store.update(v => { v.layout = layout; });
    const scale = viewer.currentScaleValue;
    // Going to or from two pages side by side refits the pages to the window.
    if (wasSpread !== (layout === "spread")) viewer.currentScaleValue = "auto";
    else if (["auto", "page-width", "page-fit"].includes(scale)) viewer.currentScaleValue = scale;
    if (view) viewer.currentPageNumber = view.page;
  }
}
document.querySelectorAll("[data-layout]").forEach(b => b.addEventListener("click", () => applyLayout(b.dataset.layout)));

function applyTint(tint, { silent = false } = {}) {
  $("viewer").dataset.tint = tint;
  $("organize").dataset.tint = tint;
  document.querySelectorAll("[data-tint]").forEach(b => { if (b.getAttribute("role") === "radio") b.setAttribute("aria-checked", String(b.dataset.tint === tint)); });
  if (!silent) store.update(v => { v.tint = tint; });
}
document.querySelectorAll("#tint-seg [data-tint]").forEach(b => b.addEventListener("click", () => applyTint(b.dataset.tint)));

// ======================================================================
// Zoom and navigation
// ======================================================================
function stepZoom(direction) {
  if (!doc.viewer?.pdfDocument) return;
  const current = doc.viewer.currentScale;
  const next = direction > 0 ? ZOOM_STEPS.find(z => z > current + 0.001) : [...ZOOM_STEPS].reverse().find(z => z < current - 0.001);
  if (next) doc.viewer.currentScaleValue = String(next);
}
function setZoom(value) { if (doc.viewer?.pdfDocument) doc.viewer.currentScaleValue = value; }

$("zoom-in").addEventListener("click", () => stepZoom(1));
$("zoom-out").addEventListener("click", () => stepZoom(-1));
menu("zoom-label", "zoom-menu");
$("zoom-menu").addEventListener("click", event => {
  const item = event.target.closest("[data-zoom]");
  if (!item) return;
  setZoom(item.dataset.zoom);
  closeMenus();
});

// Ctrl/⌘ + wheel (and trackpad pinch) zooms around the pointer.
$("viewer").addEventListener("wheel", event => {
  if (!event.ctrlKey && !event.metaKey) return;
  event.preventDefault();
  if (!doc.viewer?.pdfDocument) return;
  const viewer = doc.viewer, container = $("viewer");
  const rect = container.getBoundingClientRect();
  const px = event.clientX - rect.left, py = event.clientY - rect.top;
  const before = viewer.currentScale;
  const after = clamp(before * Math.exp(-event.deltaY * 0.0025), 0.25, 5);
  const x = container.scrollLeft + px, y = container.scrollTop + py;
  viewer.currentScaleValue = String(after);
  const ratio = viewer.currentScale / before;
  container.scrollLeft = x * ratio - px;
  container.scrollTop = y * ratio - py;
}, { passive: false });

function goToPage(n) {
  if (!doc.viewer?.pdfDocument) return;
  doc.viewer.currentPageNumber = clamp(Math.round(n), 1, doc.pages);
}

function setCurrent(n) {
  if (document.activeElement !== $("page-input")) $("page-input").value = String(n);
  $("prev-page").disabled = n <= 1;
  $("next-page").disabled = n >= doc.pages;
  const bookmarked = bookmarks().some(b => b.page === n);
  $("bookmark-page").setAttribute("aria-pressed", String(bookmarked));
  markOutline();
  markThumb(n);
  saveView();
}

$("prev-page").addEventListener("click", () => doc.viewer?.previousPage());
$("next-page").addEventListener("click", () => doc.viewer?.nextPage());
$("page-input").addEventListener("keydown", event => {
  if (event.key === "Enter") { event.preventDefault(); goToPage(Number(event.target.value) || 1); $("viewer").focus(); }
  if (event.key === "Escape") { event.target.value = String(doc.viewer?.currentPageNumber || 1); $("viewer").focus(); }
});
$("page-input").addEventListener("focus", event => event.target.select());
$("page-input").addEventListener("blur", event => { event.target.value = String(doc.viewer?.currentPageNumber || 1); });
$("back").addEventListener("click", async () => {
  doc.loadToken++;
  await leaveDocument();
  store.update(v => { v.last = null; });
  showLibrary();
});
$("toggle-sidebar").addEventListener("click", () => {
  reader.classList.toggle("no-sidebar");
  store.update(v => { v.sidebar = !reader.classList.contains("no-sidebar"); });
});

// ======================================================================
// Menus
// ======================================================================
function menu(buttonId, menuId) {
  $(buttonId).addEventListener("click", event => {
    event.stopPropagation();
    const node = $(menuId);
    const open = node.hidden;
    closeMenus();
    node.hidden = !open;
    $(buttonId).setAttribute("aria-expanded", String(open));
  });
}
export function closeMenus() {
  document.querySelectorAll(".menu, .popover").forEach(m => { m.hidden = true; });
  document.querySelectorAll("[aria-haspopup][aria-expanded='true']").forEach(b => b.setAttribute("aria-expanded", "false"));
}
document.addEventListener("click", event => {
  if (!event.target.closest(".menu-wrap, .draft-wrap")) closeMenus();
});

// ======================================================================
// Modes
// ======================================================================
async function setMode(mode, { force = false } = {}) {
  if (mode === doc.mode && !force) return;
  if (doc.mode === "organize" && mode !== "organize" && !force) {
    const ok = await organize.leave({ apply: true });
    if (ok === false) return;
  }
  doc.mode = mode;
  reader.dataset.mode = mode;
  document.querySelectorAll(".modes [data-mode]").forEach(b => b.setAttribute("aria-selected", String(b.dataset.mode === mode)));
  document.querySelectorAll(".strip").forEach(s => { s.hidden = s.dataset.for !== mode; });
  $("organize").hidden = mode !== "organize";
  $("author-foot").hidden = mode !== "annotate" || currentTab !== "annotations";
  annotate.setMode(mode);
  if (mode === "organize" && doc.pdf) organize.enter();
  updateHistoryButtons();
}
document.querySelectorAll(".modes [data-mode]").forEach(b => b.addEventListener("click", () => setMode(b.dataset.mode)));

// ======================================================================
// Draft, commit, save, undo
// ======================================================================
// PDF.js keeps a pen drawing session open until the tool changes; end it after
// each stroke so the stroke counts as an edit (and can be erased on its own).
$("viewer").addEventListener("pointerup", () => setTimeout(() => {
  if (!doc.ui || doc.viewer?.annotationEditorMode !== pdfjs.AnnotationEditorType.INK) return;
  try { doc.ui.currentLayer?.endDrawingSession(false); } catch (error) { console.warn("ink", error); }
  doc.pdfTouched = true;
  refreshDirty();
  scheduleAnnotationList();
}, 0));

function pdfjsDirty() {
  return !!doc.pdf && doc.pdfTouched && doc.pdf.annotationStorage.serializable.hash !== doc.baseHash;
}

function editorList() {
  const out = [];
  if (!doc.ui || !doc.pdf) return out;
  for (let i = 0; i < doc.pages; i++) {
    for (const editor of doc.ui.getEditors(i)) {
      if (editor.isEmpty?.() || editor.deleted) continue;
      out.push(editor);
    }
  }
  return out;
}

function liveChanges() {
  const editors = editorList().filter(e => !e.annotationElementId);
  const forms = doc.formChanged?.size || 0;
  const pdf = pdfjsDirty();
  const overlay = doc.overlay.size;
  const pages = organize.pendingCount();
  return { pdf, editors, forms, overlay, pages, count: (pdf ? Math.max(1, editors.length + forms) : 0) + overlay + pages, any: pdf || overlay > 0 };
}

let dirtyFrame = 0;
export function refreshDirty() {
  cancelAnimationFrame(dirtyFrame);
  dirtyFrame = requestAnimationFrame(() => {
    const live = liveChanges();
    const total = doc.edits + live.count;
    const dirty = !!doc.draft || live.count > 0;
    const chip = $("draft-chip");
    chip.classList.toggle("dirty", dirty);
    const working = doc.busy || doc.saving;
    chip.classList.toggle("busy", working);
    $("draft-text").textContent = doc.saving ? "Saving…" : doc.busy ? "Applying…" : dirty ? `Draft · ${plural(Math.max(total, 1), "edit")}` : "Saved";
    $("save").disabled = !dirty || working;
    $("revert").disabled = !dirty;
    updateHistoryButtons();
  });
}
hooks.refreshDirty = refreshDirty;

function updateHistoryButtons() {
  if (doc.mode === "organize") {
    $("undo").disabled = !organize.canUndo();
    $("redo").disabled = !organize.canRedo();
    return;
  }
  $("undo").disabled = !(doc.canUndo || doc.history.undo.length);
  $("redo").disabled = !(doc.canRedo || doc.history.redo.length);
}
hooks.updateHistoryButtons = updateHistoryButtons;

function describeLive(live) {
  const lines = [];
  const count = {};
  for (const editor of live.editors) count[editor.editorType] = (count[editor.editorType] || 0) + 1;
  const names = { highlight: "highlight", ink: "drawing", freetext: "text box", stamp: "image", signature: "signature" };
  for (const [type, n] of Object.entries(count)) lines.push(`Added ${plural(n, names[type] || type)}`);
  if (live.forms) lines.push(`Filled ${plural(live.forms, "form field")}`);
  const overlayCount = {};
  for (const item of doc.overlay.values()) overlayCount[item.type] = (overlayCount[item.type] || 0) + 1;
  const labels = { underline: "underline", strikethrough: "strikethrough", note: "note", rectangle: "rectangle", ellipse: "ellipse", line: "line", arrow: "arrow", free_text: "text" };
  for (const [type, n] of Object.entries(overlayCount)) lines.push(`Added ${plural(n, labels[type] || type)}`);
  if (live.pdf && !lines.length) lines.push("Edited annotations");
  return lines;
}

function persistDraft() {
  const path = doc.path;
  if (!path) return;
  const draft = doc.draft ? { id: doc.draft, edits: doc.edits, log: doc.log.slice(-40), base: doc.info?.revision, at: Date.now() } : null;
  store.update(value => {
    value.docs[path] = { ...(value.docs[path] || {}), draft };
    if (!draft) delete value.docs[path].draft;
  });
}

function snapshot() { return { draft: doc.draft, edits: doc.edits, log: [...doc.log] }; }

let chain = Promise.resolve();
function serial(task) {
  const run = chain.then(task);
  chain = run.catch(() => undefined);
  return run;
}

// Folds every pending change (PDF.js edits, form values, this app's
// annotations) plus optional page / removal changes into a new draft and
// reloads the view from it.
export function commit(extra = {}) {
  return serial(async () => {
    if (!doc.pdf) return false;
    try { doc.ui?.endCurrentEditing?.(); } catch { /* nothing being edited */ }
    const live = liveChanges();
    const annotations = [...doc.overlay.values()].map(annotate.toBackend);
    if (!live.pdf && !annotations.length && !extra.sequence && !extra.remove?.length && !extra.update?.length) return false;
    doc.busy = true;
    refreshDirty();
    if (!extra.silent) busy(extra.busyText || "Applying changes…");
    const lines = [...describeLive(live), ...(extra.log || [])];
    const editCount = Math.max(1, (live.pdf ? Math.max(1, live.editors.length + live.forms) : 0) + annotations.length + (extra.edits || 0));
    try {
      if (document.activeElement?.closest?.("#pages")) document.activeElement.blur();
      let source = doc.draft ? { draft: doc.draft } : { path: doc.path };
      let summary = null;
      if (live.pdf) {
        const bytes = await doc.pdf.saveDocument();
        summary = await uploadBytes(bytes);
        source = { draft: summary.draft };
      }
      if (annotations.length || extra.sequence || extra.remove?.length || extra.update?.length) {
        summary = await op("draft_apply", {
          ...source,
          ...(annotations.length ? { annotations } : {}),
          ...(extra.update?.length ? { update: extra.update } : {}),
          ...(extra.remove?.length ? { remove: extra.remove } : {}),
          ...(extra.sequence ? { sequence: extra.sequence } : {}),
          ...(store.value.author ? { author: store.value.author } : {}),
        });
      }
      doc.history.undo.push(snapshot());
      doc.history.redo = [];
      const view = extra.view || captureView();
      doc.draft = summary.draft;
      doc.edits += editCount;
      doc.log.push(...lines);
      doc.overlay.clear();
      if (extra.pageMap) remapBookmarks(extra.pageMap);
      persistDraft();
      const bytes = await fetchBytes({ draft: summary.draft });
      await openPdf(bytes, view);
      doc.annotations = summary.annotations || [];
      renderAnnotationList();
      return summary;
    } catch (error) {
      toast(cleanError(error.message));
      throw error;
    } finally {
      doc.busy = false;
      busy(null);
      refreshDirty();
    }
  });
}
hooks.commit = commit;

async function loadState(state) {
  return serial(async () => {
    const view = captureView();
    doc.overlay.clear();
    Object.assign(doc, { draft: state.draft, edits: state.edits, log: [...state.log] });
    persistDraft();
    busy("Loading…");
    try {
      const source = state.draft ? { draft: state.draft } : { path: doc.path, revision: doc.info.revision };
      const bytes = await fetchBytes(source);
      await openPdf(bytes, view);
      await listAnnotations();
    } finally { busy(null); refreshDirty(); }
  });
}

async function undo() {
  if (doc.mode === "organize") { organize.undo(); return; }
  if (doc.ui && doc.canUndo) { doc.ui.undo(); refreshDirty(); scheduleAnnotationList(); return; }
  const state = doc.history.undo.pop();
  if (!state) return;
  doc.history.redo.push(snapshot());
  await loadState(state);
  toast("Undone");
}

async function redo() {
  if (doc.mode === "organize") { organize.redo(); return; }
  if (doc.ui && doc.canRedo) { doc.ui.redo(); refreshDirty(); scheduleAnnotationList(); return; }
  const state = doc.history.redo.pop();
  if (!state) return;
  doc.history.undo.push(snapshot());
  await loadState(state);
  toast("Redone");
}
$("undo").addEventListener("click", undo);
$("redo").addEventListener("click", redo);

function copyName(path) {
  const base = path.replace(/\.pdf$/i, "");
  const existing = new Set(files.map(f => f.path));
  for (let i = 1; i < 100; i++) {
    const name = `${base} ${i === 1 ? "copy" : `copy ${i}`}.pdf`;
    if (!existing.has(name)) return name;
  }
  return `${base} copy.pdf`;
}

export function askPath({ title, label, value, action = "Save", note = "" }) {
  return dialog({
    title,
    buttons: [{ label: "Cancel", value: null }, { label: action, kind: "primary", value: "ok" }],
    body: container => {
      const input = el("input", { class: "field wide", value, spellcheck: "false", "data-autofocus": "", "aria-label": label });
      const error = el("p", { class: "dialog-error" });
      container.append(el("label", { class: "dialog-label", text: label }), input,
        el("p", { class: "dialog-note", text: note || "A path inside this project, ending in .pdf." }), error);
      setTimeout(() => { input.focus(); const dot = input.value.lastIndexOf("."); input.setSelectionRange(input.value.lastIndexOf("/") + 1, dot > 0 ? dot : input.value.length); }, 0);
      return () => {
        const path = input.value.trim().replace(/^\/+/, "");
        if (!/\.pdf$/i.test(path)) { error.textContent = "The name must end in .pdf"; return undefined; }
        if (path.split("/").some(part => part === ".." || part === ".git")) { error.textContent = "Choose a path inside the project"; return undefined; }
        return path;
      };
    },
  });
}

async function save({ copy = false } = {}) {
  if (!doc.path || doc.busy || doc.saving) return;
  closeMenus();
  let target = doc.path;
  if (copy) {
    target = await askPath({ title: "Save a copy", label: "File name", value: copyName(doc.path), note: "Writes a new PDF with every change. This document stays open with its draft." });
    if (!target) return;
  }
  doc.saving = true;
  refreshDirty();
  try {
    await saveNow(target, copy);
  } finally {
    doc.saving = false;
    refreshDirty();
  }
}

async function saveNow(target, copy) {
  if (doc.mode === "organize") await organize.leave({ apply: true, stay: true });
  await commit({ silent: true }).catch(() => undefined);
  const attempt = async (overwrite) => {
    doc.busy = true;
    refreshDirty();
    busy(copy ? "Saving a copy…" : "Saving…");
    try {
      return await op("save", {
        ...(doc.draft ? { draft: doc.draft } : { from: doc.path }), path: target, as_copy: copy,
        ...(copy ? {} : { base_revision: doc.info.revision }), ...(overwrite ? { overwrite: true } : {}),
      });
    } finally { doc.busy = false; busy(null); refreshDirty(); }
  };
  let result;
  try {
    result = await attempt(false);
  } catch (error) {
    const message = cleanError(error.message);
    if (/changed on disk|already exists/.test(message)) {
      const choice = await dialog({
        title: copy ? "Replace the existing file?" : "The file changed on disk",
        body: c => { c.append(el("p", { class: "dialog-note", text: copy ? `${target} already exists.` : `${doc.path} was changed by someone else after you opened it. Replacing it discards those changes.` })); },
        buttons: [{ label: "Cancel", value: null }, ...(copy ? [] : [{ label: "Save a copy…", value: "copy" }]), { label: "Replace", kind: "primary danger-fill", value: "replace" }],
      });
      if (choice === "copy") { doc.saving = false; return save({ copy: true }); }
      if (choice !== "replace") return;
      result = await attempt(true);
    } else { toast(message); return; }
  }
  if (copy) {
    toast(`Saved ${result.path}`, { action: "Open", onAction: () => openDocument(result.path) });
    refreshFiles();
    return;
  }
  doc.info = { ...doc.info, ...(await op("info", { path: doc.path })) };
  Object.assign(doc, { draft: null, edits: 0, log: [], history: { undo: [], redo: [] } });
  doc.baseHash = doc.pdf.annotationStorage.serializable.hash;
  doc.pdfTouched = false;
  doc.formChanged = new Set();
  persistDraft();
  banner(null);
  refreshDirty();
  toast(`Saved ${doc.path}`);
}
$("save").addEventListener("click", () => save());
menu("save-menu-btn", "save-menu");
$("save-copy").addEventListener("click", () => save({ copy: true }));
$("revert").addEventListener("click", () => { closeMenus(); discardDraft({ confirm: true }); });

async function discardDraft({ confirm = true } = {}) {
  if (confirm) {
    const ok = await dialog({
      title: "Discard all changes?",
      body: c => c.append(el("p", { class: "dialog-note", text: "The PDF goes back to the version saved in the project. Undo cannot bring the changes back." })),
      buttons: [{ label: "Cancel", value: null }, { label: "Discard", kind: "primary danger-fill", value: "yes" }],
    });
    if (!ok) return;
  }
  closeMenus();
  organize.reset();
  if (doc.mode === "organize") organize.enter();
  doc.history = { undo: [], redo: [] };
  doc.info = await op("info", { path: doc.path });
  await loadState({ draft: null, edits: 0, log: [] });
  banner(null);
  toast("Changes discarded");
}

$("draft-chip").addEventListener("click", event => {
  event.stopPropagation();
  const pop = $("draft-pop");
  const open = pop.hidden;
  closeMenus();
  if (!open) return;
  const live = liveChanges();
  const lines = [...doc.log, ...describeLive(live)];
  if (live.pages) lines.push(`${plural(live.pages, "page change")} in Organize`);
  $("edit-log").replaceChildren(...(lines.length ? lines : ["No changes since the last save"]).slice(-30).map(line => el("li", { text: line })));
  $("draft-note").textContent = lines.length
    ? "Changes are kept in a draft beside the file. Save writes them into the PDF; Save a copy writes a new file."
    : "The PDF on screen matches the file in the project.";
  $("discard").disabled = !lines.length;
  pop.hidden = false;
  $("draft-chip").setAttribute("aria-expanded", "true");
});
$("draft-pop").addEventListener("click", event => event.stopPropagation());
$("discard").addEventListener("click", () => discardDraft({ confirm: true }));

function remapBookmarks(pageMap) {
  const path = doc.path;
  store.update(value => {
    const entry = value.docs[path];
    if (!entry?.bookmarks) return;
    entry.bookmarks = entry.bookmarks.map(b => ({ ...b, page: pageMap.get(b.page) })).filter(b => b.page);
  });
}

// ======================================================================
// Sidebar
// ======================================================================
let currentTab = "thumbs";
const TAB_TITLES = { thumbs: "Pages", outline: "Outline", bookmarks: "Bookmarks", annotations: "Annotations", results: "Search results" };
function selectTab(name, { reveal = true } = {}) {
  currentTab = name;
  document.querySelectorAll(".side-tabs [data-tab]").forEach(tab => tab.setAttribute("aria-selected", String(tab.dataset.tab === name)));
  for (const panel of Object.keys(TAB_TITLES)) $(`panel-${panel}`).hidden = panel !== name;
  $("side-title").textContent = TAB_TITLES[name];
  $("author-foot").hidden = name !== "annotations";
  if (reveal) reader.classList.remove("no-sidebar");
  if (name === "thumbs") markThumb(doc.viewer?.currentPageNumber || 1, true);
  store.update(v => { v.tab = name; });
}
document.querySelectorAll(".side-tabs [data-tab]").forEach(tab => tab.addEventListener("click", () => selectTab(tab.dataset.tab)));
hooks.selectTab = selectTab;

// ---------- thumbnails
let thumbObserver = null;
function renderThumbs() {
  const panel = $("panel-thumbs");
  thumbObserver?.disconnect();
  panel.replaceChildren();
  if (!doc.pdf) return;
  const pdf = doc.pdf;
  thumbObserver = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      const item = entry.target;
      thumbObserver.unobserve(item);
      const n = Number(item.dataset.page);
      thumbnail(pdf, n, 132).then(canvas => {
        if (doc.pdf !== pdf) return;
        // The slot was sized before PDF.js knew this page's rotated size.
        const slot = item.querySelector(".thumb-canvas");
        slot.style.aspectRatio = `${canvas.width} / ${canvas.height}`;
        place(slot, canvas);
      }).catch(() => {});
    }
  }, { root: panel, rootMargin: "300px 0px" });
  const first = doc.viewer.getPageView(0);
  const ratio = first?.viewport ? first.viewport.height / first.viewport.width : 1.414;
  for (let n = 1; n <= doc.pages; n++) {
    const view = doc.viewer.getPageView(n - 1);
    const r = view?.viewport ? view.viewport.height / view.viewport.width : ratio;
    const item = el("button", { type: "button", class: "thumb-item", dataset: { page: String(n) }, "aria-label": `Page ${n}`, onclick: () => goToPage(n) },
      el("div", { class: "thumb-canvas pending", style: `aspect-ratio: 1 / ${r.toFixed(4)}` }),
      el("span", { class: "thumb-num", text: String(n) }));
    panel.append(item);
    thumbObserver.observe(item);
  }
  markThumb(doc.viewer.currentPageNumber, true);
}

function markThumb(n, scroll = false) {
  const items = $("panel-thumbs").querySelectorAll(".thumb-item");
  items.forEach(item => item.classList.toggle("current", Number(item.dataset.page) === n));
  if (currentTab === "thumbs") {
    const current = $("panel-thumbs").querySelector(".thumb-item.current");
    if (current && (scroll || true)) current.scrollIntoView({ block: "nearest" });
  }
}

// ---------- outline
async function loadOutline() {
  const entries = [];
  try {
    const outline = await doc.pdf.getOutline();
    const walk = async (items, level) => {
      for (const item of items || []) {
        let page = null;
        try {
          const dest = typeof item.dest === "string" ? await doc.pdf.getDestination(item.dest) : item.dest;
          if (Array.isArray(dest)) page = typeof dest[0] === "number" ? dest[0] + 1 : (await doc.pdf.getPageIndex(dest[0])) + 1;
        } catch { /* broken destination */ }
        if (item.title?.trim()) entries.push({ title: item.title.trim(), level, page, dest: item.dest });
        if (entries.length < 2000) await walk(item.items, level + 1);
      }
    };
    await walk(outline, 0);
  } catch { /* no outline */ }
  doc.outline = entries;
}

function renderOutline() {
  const panel = $("panel-outline");
  panel.replaceChildren();
  const items = doc.outline.filter(i => i.page);
  if (!items.length) {
    panel.append(emptyPanel("list", "This PDF has no outline."));
    return;
  }
  const minLevel = Math.min(...items.map(i => i.level));
  items.forEach((item, index) => {
    const level = item.level - minLevel;
    const button = el("button", { type: "button", class: `outline-item l${Math.min(level, 3)}`, dataset: { index: String(index) }, style: `padding-left:${8 + level * 14}px`,
      onclick: () => { if (item.dest) doc.links.goToDestination(item.dest); else goToPage(item.page); } },
      el("span", { class: "t", text: item.title }), el("span", { class: "p", text: String(item.page) }));
    panel.append(button);
  });
  markOutline();
}

function markOutline() {
  const buttons = [...document.querySelectorAll(".outline-item")];
  const items = doc.outline.filter(i => i.page);
  const page = doc.viewer?.currentPageNumber || 1;
  let current = -1;
  items.forEach((item, index) => { if (item.page <= page) current = index; });
  buttons.forEach((button, index) => button.classList.toggle("current", index === current));
}

function emptyPanel(iconName, text, hint) {
  return el("div", { class: "panel-empty" }, icon(iconName), el("div", { text }), hint ? el("div", { class: "hint", html: hint }) : null);
}

// ---------- bookmarks
function bookmarks() { return store.value.docs[doc.path]?.bookmarks || []; }

function renderBookmarks() {
  const panel = $("panel-bookmarks");
  panel.replaceChildren();
  const list = bookmarks();
  $("bookmark-count").textContent = list.length ? String(list.length) : "";
  if (!list.length) {
    panel.append(emptyPanel("bookmark", "No bookmarks yet.", `Press <span class="kbd">⌘D</span> to bookmark a page.`));
    return;
  }
  for (const mark of [...list].sort((a, b) => a.page - b.page)) {
    const label = el("span", { class: "label", text: mark.label });
    const row = el("div", { class: "bookmark-item" },
      el("button", { class: "go", type: "button", onclick: () => goToPage(mark.page), ondblclick: () => {
        label.contentEditable = "true"; label.focus(); document.getSelection().selectAllChildren(label);
      } }, label, el("span", { class: "p", text: `p. ${mark.page}` })),
      el("button", { class: "btn ghost icon sm remove", type: "button", "aria-label": "Remove bookmark", onclick: () => updateBookmarks(l => l.splice(l.findIndex(b => b.page === mark.page), 1)) }, icon("trash")));
    label.addEventListener("keydown", event => {
      if (event.key === "Enter") { event.preventDefault(); label.blur(); }
      if (event.key === "Escape") { label.textContent = mark.label; label.blur(); }
    });
    label.addEventListener("blur", () => {
      if (label.contentEditable !== "true") return;
      label.contentEditable = "false";
      const text = label.textContent.trim() || mark.label;
      updateBookmarks(l => { const m = l.find(b => b.page === mark.page); if (m) m.label = text; });
    });
    panel.append(row);
  }
}

function updateBookmarks(change) {
  const path = doc.path;
  store.update(value => {
    value.docs[path] = value.docs[path] || {};
    value.docs[path].bookmarks = value.docs[path].bookmarks || [];
    change(value.docs[path].bookmarks);
  });
  renderBookmarks();
  setCurrent(doc.viewer?.currentPageNumber || 1);
}

function sectionTitle(page) {
  let title = null;
  for (const item of doc.outline) if (item.page && item.page <= page) title = item.title;
  return title;
}

function toggleBookmark() {
  if (!doc.path) return;
  const page = doc.viewer.currentPageNumber;
  const exists = bookmarks().some(b => b.page === page);
  updateBookmarks(list => {
    if (exists) list.splice(list.findIndex(b => b.page === page), 1);
    else list.push({ page, label: sectionTitle(page) || `Page ${page}`, at: Date.now() });
  });
  toast(exists ? `Removed bookmark on page ${page}` : `Bookmarked page ${page}`);
  if (!exists) selectTab("bookmarks");
}
$("bookmark-page").addEventListener("click", toggleBookmark);

// ---------- annotations
const TYPE_LABELS = {
  highlight: "Highlight", underline: "Underline", strikethrough: "Strikethrough", squiggly: "Squiggly", note: "Note",
  free_text: "Text box", freetext: "Text box", ink: "Drawing", rectangle: "Rectangle", ellipse: "Ellipse", line: "Line",
  arrow: "Arrow", polygon: "Polygon", polyline: "Polyline", stamp: "Stamp", signature: "Signature", caret: "Insert",
};
const TYPE_ICONS = {
  highlight: "highlight", underline: "underline", strikethrough: "strike", squiggly: "underline", note: "note", free_text: "text",
  freetext: "text", ink: "pen", rectangle: "square", ellipse: "circle", line: "line", arrow: "arrow", stamp: "image", signature: "sign",
};

let listTimer;
export function scheduleAnnotationList() {
  clearTimeout(listTimer);
  listTimer = setTimeout(renderAnnotationList, 150);
}
hooks.scheduleAnnotationList = scheduleAnnotationList;

function editorEntry(editor) {
  let data = {};
  try { data = editor.serialize?.(false) || {}; } catch { /* still being drawn */ }
  const type = editor.editorType;
  let color = editor.color || (Array.isArray(data.color) ? "#" + data.color.map(c => c.toString(16).padStart(2, "0")).join("") : "");
  return {
    id: editor.id, page: editor.pageIndex + 1, type, color, author: store.value.author || "", contents: data.value || "",
    text: annotate.editorText(editor) || "", pending: true, kind: "editor", editor, rect: Array.isArray(data.rect) ? data.rect : null,
  };
}

function renderAnnotationList() {
  const panel = $("panel-annotations");
  if (!doc.pdf) { panel.replaceChildren(); return; }
  const entries = [];
  for (const a of doc.annotations) entries.push({ ...a, kind: "saved" });
  for (const item of doc.overlay.values()) entries.push({ id: item.id, page: item.page, type: item.type, color: item.color, author: item.author || store.value.author || "", contents: item.contents || "", text: item.text || "", pending: true, kind: "overlay", item, rect: annotate.itemRect(item) });
  for (const editor of editorList()) if (!editor.annotationElementId) entries.push(editorEntry(editor));
  entries.sort((a, b) => a.page - b.page || (b.rect?.[3] ?? 0) - (a.rect?.[3] ?? 0));
  $("annotation-count").textContent = entries.length ? String(entries.length) : "";
  panel.replaceChildren();
  if (!entries.length) {
    panel.append(emptyPanel("comments", "No annotations yet.", "Switch to <b>Annotate</b> to highlight, draw or add notes."));
    return;
  }
  let lastPage = 0;
  for (const entry of entries) {
    if (entry.page !== lastPage) {
      panel.append(el("div", { class: "ann-page", text: `Page ${entry.page}` }));
      lastPage = entry.page;
    }
    const text = entry.contents || entry.text || "";
    const meta = [entry.author, entry.modified ? formatDate(Date.parse(entry.modified)) : ""].filter(Boolean).join(" · ");
    const row = el("div", { class: `ann-item${entry.pending ? " pending" : ""}`, tabindex: "0", role: "button",
      onclick: () => revealAnnotation(entry), onkeydown: e => { if (e.key === "Enter") revealAnnotation(entry); } },
      el("span", { class: "ann-icon", style: `--c:${entry.color || "var(--muted)"}` }, icon(TYPE_ICONS[entry.type] || "comments")),
      el("div", { class: "ann-main" },
        el("div", { class: "ann-head" }, el("span", { class: "ann-type", text: TYPE_LABELS[entry.type] || entry.type }),
          entry.pending ? el("span", { class: "badge", text: "Unsaved" }) : null),
        text ? el("div", { class: `ann-text${entry.contents ? "" : " quote"}`, text: text.length > 220 ? text.slice(0, 220) + "…" : text }) : null,
        meta ? el("div", { class: "ann-meta", text: meta }) : null),
      el("button", { class: "btn ghost icon sm ann-delete", type: "button", "aria-label": "Delete annotation", "data-tip": "Delete",
        onclick: event => { event.stopPropagation(); deleteAnnotation(entry); } }, icon("trash")));
    panel.append(row);
  }
}

function revealAnnotation(entry) {
  if (doc.mode === "organize") setMode("read");
  if (entry.kind === "editor") {
    goToPage(entry.page);
    setTimeout(() => entry.editor.div?.scrollIntoView({ block: "center", behavior: "smooth" }), 60);
    annotate.flashEditor(entry.editor);
    return;
  }
  annotate.reveal(entry.page, entry.kind === "overlay" ? annotate.itemRect(entry.item) : entry.rect);
  // In Annotate, a saved annotation picked from the list is selected for restyling.
  if (entry.kind === "saved" && doc.mode === "annotate") setTimeout(() => annotate.selectSaved(entry.id), 120);
}

async function deleteAnnotation(entry) {
  if (entry.kind === "overlay") { annotate.removeItem(entry.item.id, { record: true }); return; }
  if (entry.kind === "editor") { annotate.deleteEditor(entry.editor); return; }
  await commit({ remove: [entry.id], log: [`Deleted ${TYPE_LABELS[entry.type]?.toLowerCase() || "annotation"} on p. ${entry.page}`], busyText: "Deleting…" });
}
hooks.deleteAnnotation = deleteAnnotation;
hooks.typeLabel = type => TYPE_LABELS[type] || "Annotation";

$("author-input").addEventListener("change", event => store.update(v => { v.author = event.target.value.trim(); }));

// ======================================================================
// Form fields (Fill & Sign)
// ======================================================================
let fields = [];
async function loadFields() {
  fields = [];
  const pdf = doc.pdf;
  try {
    const objects = await pdf.getFieldObjects();
    if (!objects) { renderFieldCount(); return; }
    const ids = new Map();
    // PDF.js 6 returns a Map; older builds a plain object.
    for (const [name, list] of objects instanceof Map ? objects : Object.entries(objects)) {
      for (const item of list) {
        if (!item.id || item.type === "button" && !item.exportValues) continue;
        if (["text", "checkbox", "radiobutton", "combobox", "listbox"].includes(item.type)) ids.set(item.id, { id: item.id, name, page: item.page + 1, type: item.type });
      }
    }
    // Order by page, then top-to-bottom, left-to-right.
    const pages = [...new Set([...ids.values()].map(f => f.page))].slice(0, 200);
    for (const n of pages) {
      const annotations = await (await pdf.getPage(n)).getAnnotations();
      for (const a of annotations) if (ids.has(a.id)) ids.get(a.id).rect = a.rect;
    }
    if (pdf !== doc.pdf) return;
    fields = [...ids.values()].sort((a, b) => a.page - b.page || (b.rect?.[3] ?? 0) - (a.rect?.[3] ?? 0) || (a.rect?.[0] ?? 0) - (b.rect?.[0] ?? 0));
  } catch (error) { console.warn("fields", error); }
  renderFieldCount();
}

function renderFieldCount() {
  const names = new Set(fields.map(f => f.name));
  $("field-count").textContent = names.size ? `${plural(names.size, "form field")}` : "No form fields";
  $("field-next").disabled = $("field-prev").disabled = !fields.length;
  document.querySelector('.modes [data-mode="fill"]').classList.toggle("has-fields", names.size > 0);
}

// The Next/Previous buttons take focus, so remember the field last edited.
let lastFieldId = null;
$("pages").addEventListener("focusin", event => {
  const id = event.target.closest?.(".annotationLayer [data-annotation-id]")?.dataset.annotationId;
  if (id) lastFieldId = id;
});

function focusField(direction) {
  if (!fields.length) return;
  const active = document.activeElement?.closest?.("[data-annotation-id]")?.dataset.annotationId || lastFieldId;
  let index = fields.findIndex(f => f.id === active);
  index = index < 0 ? (direction > 0 ? 0 : fields.length - 1) : (index + direction + fields.length) % fields.length;
  const field = fields[index];
  goToPage(field.page);
  let tries = 0;
  const attempt = () => {
    const node = document.querySelector(`.annotationLayer [data-annotation-id="${field.id}"] :is(input, textarea, select)`);
    if (node) { node.focus(); node.scrollIntoView({ block: "center" }); return; }
    if (tries++ < 40) setTimeout(attempt, 50);
  };
  attempt();
}
$("field-next").addEventListener("click", () => focusField(1));
$("field-prev").addEventListener("click", () => focusField(-1));
$("field-highlight").addEventListener("click", () => {
  const on = $("field-highlight").getAttribute("aria-pressed") !== "true";
  $("field-highlight").setAttribute("aria-pressed", String(on));
  $("viewer").classList.toggle("no-field-tint", !on);
});

// ======================================================================
// Search (PDF.js find controller highlights; the result list is ours)
// ======================================================================
const search = { query: "", caseSensitive: false, matches: [], token: 0, textCache: new Map() };

async function pageText(n) {
  if (search.textCache.has(n)) return search.textCache.get(n);
  const page = await doc.pdf.getPage(n);
  const content = await page.getTextContent();
  let text = "";
  for (const item of content.items) {
    if (item.str === undefined) continue;
    text += item.str;
    if (item.hasEOL && !item.str.endsWith(" ")) text += " ";
  }
  search.textCache.set(n, text);
  return text;
}

function dispatchFind(type, previous = false) {
  doc.bus?.dispatch("find", { source: null, type, query: search.query, caseSensitive: search.caseSensitive,
    entireWord: false, highlightAll: true, findPrevious: previous, matchDiacritics: false });
}

function openSearch() {
  if (!doc.pdf) return;
  if (doc.mode === "organize") setMode("read");
  $("searchbar").hidden = false;
  $("search-input").focus();
  $("search-input").select();
}

function closeSearch(silent = false) {
  $("searchbar").hidden = true;
  search.token++;
  search.matches = [];
  search.query = "";
  search.textCache = new Map();
  if (doc.bus && doc.pdf) doc.bus.dispatch("findbarclose", { source: null });
  if (!silent) { $("search-input").value = ""; $("viewer").focus(); }
  $("result-count").textContent = "";
  $("search-count").textContent = "";
  $("panel-results").replaceChildren(emptyPanel("search", "Search this document.", `Press <span class="kbd">⌘F</span> to find a phrase.`));
}

async function runSearch() {
  const query = $("search-input").value.trim();
  const caseSensitive = $("search-case").getAttribute("aria-pressed") === "true";
  if (query === search.query && caseSensitive === search.caseSensitive) return false;
  const token = ++search.token;
  search.query = query;
  search.caseSensitive = caseSensitive;
  search.matches = [];
  dispatchFind("");
  if (!query) { renderResults(); $("search-count").textContent = ""; return true; }
  const pattern = new RegExp(query.split(/\s+/).map(s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("\\s+"), caseSensitive ? "g" : "gi");
  for (let n = 1; n <= doc.pages; n++) {
    const text = await pageText(n);
    if (token !== search.token) return true;
    let k = 0;
    for (const m of text.matchAll(pattern)) search.matches.push({ page: n, k: k++, start: m.index, end: m.index + m[0].length, text });
  }
  renderResults();
  return true;
}

function updateSearchCount(count) {
  const node = $("search-count");
  if (!search.query) { node.textContent = ""; return; }
  const total = count?.total ?? search.matches.length;
  node.classList.toggle("none", !total);
  node.textContent = total ? `${Math.max(count?.current || 0, 0)} of ${total}` : "No results";
  $("result-count").textContent = total ? String(total) : "";
}

function renderResults() {
  const panel = $("panel-results");
  panel.replaceChildren();
  $("result-count").textContent = search.matches.length ? String(search.matches.length) : "";
  if (!search.query) return;
  if (!search.matches.length) {
    panel.append(emptyPanel("search", `No results for “${search.query}”.`));
    return;
  }
  let lastPage = 0;
  search.matches.slice(0, 500).forEach(match => {
    if (match.page !== lastPage) {
      panel.append(el("div", { class: "result-page", text: `Page ${match.page}` }));
      lastPage = match.page;
    }
    let before = match.text.slice(Math.max(0, match.start - 56), match.start);
    let after = match.text.slice(match.end, match.end + 72);
    if (match.start > 56) before = "…" + before.replace(/^\S*\s/, "");
    if (match.end + 72 < match.text.length) after = after.replace(/\s\S*$/, "") + "…";
    const item = el("button", { type: "button", class: "result-item", onclick: () => {
      panel.querySelectorAll(".result-item").forEach(i => i.classList.toggle("current", i === item));
      jumpToMatch(match);
    } }, before, el("mark", { text: match.text.slice(match.start, match.end) }), after);
    panel.append(item);
  });
}

function jumpToMatch(match) {
  const fc = doc.find;
  if (fc && fc._pageMatches?.[match.page - 1]?.length > match.k) {
    fc._offset = { pageIdx: match.page - 1, matchIdx: match.k - 1, wrapped: false };
    fc._resumePageIdx = null;
    dispatchFind("again");
  } else goToPage(match.page);
}

let searchTimer;
$("search-input").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 220); });
$("search-input").addEventListener("keydown", async event => {
  if (event.key === "Enter") {
    event.preventDefault();
    clearTimeout(searchTimer);
    const fresh = await runSearch();
    if (!fresh) dispatchFind("again", event.shiftKey);
    if (search.matches.length && currentTab !== "results") selectTab("results");
  } else if (event.key === "Escape") { event.preventDefault(); closeSearch(); }
});
$("search-case").addEventListener("click", () => {
  const button = $("search-case");
  button.setAttribute("aria-pressed", String(button.getAttribute("aria-pressed") !== "true"));
  runSearch();
});
$("search-next").addEventListener("click", () => dispatchFind("again"));
$("search-prev").addEventListener("click", () => dispatchFind("again", true));
$("search-close").addEventListener("click", () => closeSearch());
$("open-search").addEventListener("click", openSearch);

// ======================================================================
// Summary (model.invoke through the backend)
// ======================================================================
let summaryRange = "all";
document.querySelectorAll("[data-range]").forEach(button => button.addEventListener("click", () => {
  summaryRange = button.dataset.range;
  document.querySelectorAll("[data-range]").forEach(b => b.setAttribute("aria-checked", String(b === button)));
  $("range-inputs").hidden = summaryRange !== "custom";
  if (summaryRange === "custom") {
    const current = doc.viewer?.currentPageNumber || 1;
    $("range-start").value ||= String(current);
    $("range-end").value ||= String(Math.min(doc.pages, current + 4));
    $("range-start").focus();
  }
}));

function toggleSummary(force) {
  const panel = $("summary");
  panel.hidden = force === undefined ? !panel.hidden : !force;
  if (!panel.hidden) renderSummaryState();
}
$("open-summary").addEventListener("click", () => toggleSummary());
$("close-summary").addEventListener("click", () => toggleSummary(false));

function renderSummaryState() {
  const body = $("summary-body");
  const saved = store.value.docs[doc.path]?.summary;
  if (!saved) {
    body.innerHTML = `<div class="empty-note">Uses your configured model on the extracted text of the saved file. The latest summary is kept with this document.</div>`;
    return;
  }
  const source = saved.source || {};
  const range = source.start_page ? (source.start_page === source.end_page ? `Page ${source.start_page}` : `Pages ${source.start_page}–${source.end_page}`) : "";
  body.innerHTML = `<div class="summary-meta"><span></span><button class="btn ghost sm" id="copy-summary"><svg><use href="#i-copy"/></svg>Copy</button></div><div class="md"></div>`;
  body.querySelector(".summary-meta span").textContent = [range, formatDate(saved.at)].filter(Boolean).join(" · ") + (source.truncated ? " · text truncated" : "");
  body.querySelector(".md").innerHTML = renderMarkdown(saved.summary);
  body.querySelector("#copy-summary").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(saved.summary); toast("Summary copied"); }
    catch { toast("Copy is blocked here — select the text to copy"); }
  });
}

$("run-summary").addEventListener("click", async () => {
  if (!doc.path) return;
  const input = { path: doc.path };
  const current = doc.viewer?.currentPageNumber || 1;
  if (summaryRange === "current") { input.start_page = current; input.end_page = current; }
  if (summaryRange === "custom") {
    input.start_page = Math.max(1, Number($("range-start").value) || 1);
    input.end_page = Math.max(input.start_page, Number($("range-end").value) || input.start_page);
  }
  const focus = $("summary-focus").value.trim();
  if (focus) input.focus = focus;
  const body = $("summary-body");
  const button = $("run-summary");
  button.disabled = true;
  body.innerHTML = `<div class="progress-line"><div class="spinner"></div><span>Starting…</span></div>`;
  const path = doc.path;
  try {
    const result = await op("summarize", input, { onProgress: value => { const s = body.querySelector(".progress-line span"); if (s) s.textContent = String(value) + "…"; } });
    store.update(value => { value.docs[path] = { ...(value.docs[path] || {}), summary: { ...result, at: Date.now() } }; });
    if (doc.path === path) renderSummaryState();
  } catch (error) {
    body.replaceChildren(el("div", { class: "error-text", text: error.message }));
  } finally {
    button.disabled = false;
  }
});

// ======================================================================
// Keyboard
// ======================================================================
document.addEventListener("keydown", event => {
  if (reader.hidden || !$("dialog").hidden) return;
  const mod = event.metaKey || event.ctrlKey;
  const key = event.key.toLowerCase();
  const typing = event.target.closest?.("input, textarea, select, [contenteditable='true'], .freeTextEditor");
  if (mod && key === "z" && !typing) {
    event.preventDefault(); event.stopImmediatePropagation();
    if (event.shiftKey) redo(); else undo();
    return;
  }
  if (mod && key === "y" && !typing) { event.preventDefault(); event.stopImmediatePropagation(); redo(); return; }
  if (mod && key === "s") { event.preventDefault(); save({ copy: event.shiftKey }); return; }
  if (mod && ["1", "2", "3", "4"].includes(event.key)) {
    event.preventDefault();
    setMode(["read", "annotate", "organize", "fill"][Number(event.key) - 1]);
    return;
  }
  if (mod && key === "f") { event.preventDefault(); openSearch(); return; }
  if (mod && key === "g") { event.preventDefault(); dispatchFind("again", event.shiftKey); return; }
  if (mod && (event.key === "=" || event.key === "+")) { event.preventDefault(); stepZoom(1); return; }
  if (mod && event.key === "-") { event.preventDefault(); stepZoom(-1); return; }
  if (mod && event.key === "0") { event.preventDefault(); setZoom("page-width"); return; }
  if (mod && key === "o") { event.preventDefault(); $("back").click(); return; }
  if (doc.mode === "organize") { if (organize.onKey(event, { mod, typing })) return; }
  if (mod && key === "d") { event.preventDefault(); if (doc.mode !== "organize") toggleBookmark(); return; }
  if (event.key === "Escape" && !$("searchbar").hidden && event.target.closest?.("#searchbar")) { closeSearch(); return; }
  if (typing || mod) return;
  if (annotate.onKey(event)) return;
  if (doc.mode === "organize") return;
  if (event.key === "Home") { event.preventDefault(); goToPage(1); }
  else if (event.key === "End") { event.preventDefault(); goToPage(doc.pages); }
  else if ((event.key === "ArrowRight" || event.key === "PageDown") && $("viewer").scrollWidth <= $("viewer").clientWidth) { event.preventDefault(); doc.viewer?.nextPage(); }
  else if ((event.key === "ArrowLeft" || event.key === "PageUp") && $("viewer").scrollWidth <= $("viewer").clientWidth) { event.preventDefault(); doc.viewer?.previousPage(); }
  else if (event.key === "g") { event.preventDefault(); $("page-input").focus(); }
  else if (event.key === "/") { event.preventDefault(); openSearch(); }
}, true);

// ======================================================================
// Boot
// ======================================================================
hooks.goToPage = goToPage;
hooks.setMode = setMode;
hooks.fetchBytes = fetchBytes;
hooks.listFiles = async () => { if (!files.length) await refreshFiles().catch(() => {}); return files; };
hooks.refreshFiles = refreshFiles;
hooks.openDocument = openDocument;
hooks.renderAnnotationList = renderAnnotationList;
hooks.closeMenus = closeMenus;
hooks.askPath = askPath;

// Another view (or an Agent) may change storage or the file on disk.
setInterval(async () => {
  if (document.hidden || !doc.path) return;
  try {
    if (await store.poll()) { renderBookmarks(); setCurrent(doc.viewer?.currentPageNumber || 1); }
  } catch { /* offline: keep the local copy */ }
}, 5000);

window.addEventListener("pagehide", () => { if (doc.pdf && liveChanges().any) commit({ silent: true }).catch(() => {}); });

async function boot() {
  if (!api) { showError("Open this page inside OpenProgram", "The application bridge is not available."); return; }
  await api.ready;
  try { await store.load(); } catch (error) { toast("Saved state unavailable: " + cleanError(error.message)); }
  if (store.value.sidebar === false) reader.classList.add("no-sidebar");
  $("author-input").value = store.value.author || "";
  createViewer();
  annotate.init();
  organize.init();
  applyTint(store.value.tint || "day", { silent: true });
  selectTab(store.value.tab || "thumbs", { reveal: false });
  closeSearch(true);
  setMode("read", { force: true });
  if (store.value.last) openDocument(store.value.last, { quiet: true });
  else showLibrary();
}
boot();
