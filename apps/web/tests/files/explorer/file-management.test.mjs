import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";
import { createRequire } from "node:module";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import React, { createElement as h, act, Fragment } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { parseHTML } from "linkedom";
const require = createRequire(import.meta.url);
const bundle = await build({
  stdin: { contents: 'export * from "./components/files/explorer/explorer-header"; export * from "./components/files/file-management"; export * from "./components/files/tree/file-tree"; export * from "./components/files/explorer/pierre-file-tree";', resolveDir: fileURLToPath(new URL("../../../", import.meta.url)), loader: "tsx" },
  bundle: true, write: false, format: "cjs", platform: "node", jsx: "automatic", loader: { ".css": "empty" },
  plugins: [{ name: "host", setup(builder) {
    builder.onLoad({ filter: /\.css$/ }, () => ({ contents: "export default {};", loader: "js" }));
    builder.onResolve({ filter: /^react(?:-dom)?(?:\/.*)?$/ }, ({ path }) => ({ path: require.resolve(path), external: true }));
    builder.onResolve({ filter: /^@\/lib\/i18n$/ }, () => ({ path: "i18n", namespace: "fake" }));
    builder.onResolve({ filter: /^@\/lib\/net\/ws\/ws-request$/ }, () => ({ path: "ws", namespace: "fake" }));
    builder.onResolve({ filter: /^@\/lib\/(state\/center-tabs-store|session-store|navigate)$/ }, ({ path }) => ({ path, namespace: "state" }));
    builder.onLoad({ filter: /.*/, namespace: "state" }, ({ path }) => ({ contents: path.endsWith("center-tabs-store") ? 'const state={tabs:[],activeId:null,openFileTab:()=>{}}; export const useCenterTabs=Object.assign(fn=>fn(state),{getState:()=>state});' : path.endsWith("session-store") ? 'export const useSessionStore={getState:()=>({currentSessionId:null})};' : 'export const navigate=()=>{};', loader: "js" }));
    builder.onLoad({ filter: /.*/, namespace: "fake" }, ({ path }) => ({ contents: path === "i18n" ? 'export const useTranslation=()=>({text:(en)=>en});' : 'export const wsRequest=(action,payload)=>globalThis.__fileManagementQuery?.(action,payload) ?? Promise.resolve(null); export const idempotencyKeyFor=()=>"test"; export class MutationRegistryCapacityError extends Error {} export const reconcileWsMutation=()=>{}; export const wsMutationRequest=()=>{};', loader: "js" }));
  } }],
});
const temporary = mkdtempSync(join(tmpdir(), "op-file-management-"));
let api;
try {
  const output = join(temporary, "module.cjs"); writeFileSync(output, bundle.outputFiles[0].text); api = require(output);
} finally { rmSync(temporary, { recursive: true, force: true }); }
const noop = () => {};
const props = { rootName: "Project", rootPath: "/project", searchOpen: true, onSearchOpenChange: noop, query: "", onQueryChange: noop, mode: "filter", onModeChange: noop, fuzzy: true, onFuzzyChange: noop, resultCount: 0, resultIndex: 0, onMoveResult: noop };

test("Files path, actions and expanded search occupy separate ordered rows", () => {
  const markup = renderToStaticMarkup(h(api.ExplorerHeader, { ...props, pathNavigation: h("nav", { "data-path": true }, "Project / src"), actions: h("button", { "data-action": true }, "Sort") }));
  const { document } = parseHTML(markup);
  const header = document.firstElementChild;
  assert.equal(header.children.length, 3);
  assert.ok(header.children[0].querySelector("nav"));
  assert.ok(header.children[1].querySelector("[data-action]"));
  assert.ok(header.children[2].querySelector('input[aria-label="Search"]'));
  assert.equal(header.children[0].querySelector("input"), null);
  assert.equal(document.querySelectorAll('input').length, 1);
});

test("shared Programs header retains its single toolbar without a path row", () => {
  const { document } = parseHTML(renderToStaticMarkup(h(api.ExplorerHeader, { ...props, showRootPath: false, leading: h("span", { "data-leading": true }, "Programs") })));
  assert.equal(document.firstElementChild.children.length, 2);
  assert.ok(document.firstElementChild.children[0].querySelector("[data-leading]"));
});

test("narrow breadcrumb keeps project root and current filename", () => {
  const { document } = parseHTML(renderToStaticMarkup(h(api.FileBreadcrumb, { root: "Project", path: "src/deep/file2.py", onLocate: noop })));
  assert.equal(Boolean(document.querySelector('nav button[title]')), false, "breadcrumb uses styled hints rather than native square title tooltips");
  assert.match(document.firstElementChild.textContent, /Project/);
  assert.match(document.firstElementChild.textContent, /file2.py/);
  assert.ok(document.querySelector('button[aria-label="Parent folders"]'));
  assert.equal(document.querySelector('button[data-path="src/deep/file2.py"]').textContent, "file2.py");
});

test("byte display covers zero and binary unit boundaries", () => {
  assert.equal(api.formatFileBytes(0), "0 B");
  assert.equal(api.formatFileBytes(1023), "1023 B");
  assert.equal(api.formatFileBytes(1024), "1.0 KiB");
  assert.equal(api.formatFileBytes(1024 ** 3), "1.0 GiB");
});


for (const state of ["incomplete", "complete"]) test(`refresh preserves ${state} size provenance while verification waits`, async () => {
  const parsed = parseHTML('<html><body><div id="root"></div></body></html>');
  const saved = { window: globalThis.window, document: globalThis.document, IntersectionObserver: globalThis.IntersectionObserver };
  globalThis.window = parsed.window;
  globalThis.document = parsed.document;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  globalThis.IntersectionObserver = class {
    constructor(callback) { this.callback = callback; }
    observe() { queueMicrotask(() => this.callback([{ isIntersecting: true }])); }
    disconnect() {}
  };
  const root = createRoot(document.getElementById("root"));
  const held = [];
  const projectId = `provenance-${state}`;
  globalThis.__fileManagementQuery = async (_action, payload) => payload.operation === "measure"
    ? { state, bytes: 12, entries: 2, skipped: state === "complete" ? 0 : 1, token: null }
    : { state: "unknown" };
  try {
    await act(async () => root.render(h(api.FolderSize, { projectId, path: "src" })));
    assert.equal(document.body.textContent, "12 B");
    await act(async () => root.render(null));
    api.invalidateFolderSizes(projectId);
    globalThis.__fileManagementQuery = (action) => action === "project_file_info"
      ? Promise.resolve({ type: "dir", name: "src", absolute_path: "/src", size: null, mtime: 1, created_at: null, permissions: "drwxr-xr-x" })
      : new Promise(resolve => held.push(resolve));
    await act(async () => root.render(h(Fragment, null,
      ...["blocker1", "blocker2", "src"].map(path => h(api.FolderSize, { key: path, projectId, path })),
      h(api.FileDetails, { projectId, path: "src", onClose: noop, inline: true }),
    )));
    const content = document.body.textContent;
    assert.equal((content.match(state === "complete" ? /≈ 12 B/g : /≥ 12 B/g) ?? []).length, 1, content);
  } finally {
    await act(async () => { root.unmount(); for (const resolve of held) resolve({ state: "unknown" }); });
    Object.assign(globalThis, saved);
    delete globalThis.__fileManagementQuery;
  }
});

test("renewed visibility and details reuse one shared sample until explicit refresh", async () => {
  const parsed = parseHTML('<html><body><div id="root"></div></body></html>');
  const saved = { window: globalThis.window, document: globalThis.document, IntersectionObserver: globalThis.IntersectionObserver };
  globalThis.window = parsed.window; globalThis.document = parsed.document;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  globalThis.IntersectionObserver = class {
    constructor(callback) { this.callback = callback; }
    observe() { queueMicrotask(() => this.callback([{ isIntersecting: true }])); }
    disconnect() {}
  };
  let bytes = 12, starts = 0;
  const projectId = "reactivate";
  globalThis.__fileManagementQuery = async (action, payload) => {
    if (action === "project_file_info") return { type: "dir", name: "src", absolute_path: "/src", size: null, mtime: 1, created_at: null, permissions: "drwxr-xr-x" };
    if (payload.operation === "measure") { starts++; return { state: "complete", bytes, entries: 1, skipped: 0, token: null }; }
    return { state: "unknown" };
  };
  const root = createRoot(document.getElementById("root"));
  const row = (key) => h(api.FolderSize, { key, projectId, path: "src" });
  try {
    await act(async () => root.render(row("sidebar")));
    assert.match(document.body.textContent, /12 B/);
    await act(async () => root.render(null));
    bytes = 999; starts = 0;
    await act(async () => root.render(h(Fragment, null, row("sidebar"), row("central"))));
    assert.equal(starts, 0, "both visible views reuse the cached sample without a scan");
    assert.equal((document.body.textContent.match(/12 B/g) ?? []).length, 2);
    bytes = 50; starts = 0;
    await act(async () => root.render(h(Fragment, null, row("sidebar"), row("central"), h(api.FileDetails, { projectId, path: "src", onClose: noop, inline: true }))));
    assert.equal(starts, 0, "opening details reuses the existing shared sample");
    assert.equal((document.body.textContent.match(/12 B/g) ?? []).length, 3);
    await act(async () => api.invalidateFolderSizes(projectId));
    assert.equal(starts, 1, "explicit refresh scans once for all subscribers");
    assert.equal((document.body.textContent.match(/50 B/g) ?? []).length, 3);
  } finally {
    await act(async () => root.unmount());
    Object.assign(globalThis, saved); delete globalThis.__fileManagementQuery;
  }
});

for (const complete of [true, false]) test(`persisted terminal sample is displayed without scanning (complete=${complete})`, async () => {
  const parsed = parseHTML('<html><body><div id="root"></div></body></html>');
  const saved = { window: globalThis.window, document: globalThis.document, IntersectionObserver: globalThis.IntersectionObserver };
  globalThis.window = parsed.window; globalThis.document = parsed.document;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  globalThis.IntersectionObserver = class {
    constructor(callback) { this.callback = callback; }
    observe() { queueMicrotask(() => this.callback([{ isIntersecting: true }])); }
    disconnect() {}
  };
  const requests = [];
  globalThis.__fileManagementQuery = async (action, payload) => {
    if (action === "project_file_info") return { type: "dir", name: "src", absolute_path: "/src", size: null, mtime: 1, created_at: null, permissions: "drwxr-xr-x" };
    requests.push(payload.operation ?? "peek");
    return payload.operation === "start" ? { state: "complete", bytes: 999, complete: true } : { state: "cached", bytes: 12, complete, skipped: complete ? 0 : 1, token: null };
  };
  const root = createRoot(document.getElementById("root"));
  const projectId = `persisted-${complete}`;
  try {
    await act(async () => root.render(h(api.FolderSize, { projectId, path: "src" })));
    assert.equal(document.body.textContent, "12 B");
    assert.deepEqual(requests, ["measure"], "a terminal cache hit must not start a recursive scan");
    await act(async () => root.render(h(api.FileDetails, { projectId, path: "src", onClose: noop, inline: true })));
    assert.match(document.body.textContent, complete ? /≈ 12 B/ : /≥ 12 B/);
    assert.deepEqual(requests, ["measure"]);
    const recalculate = [...document.querySelectorAll("button")].find(button => button.textContent === "Recalculate");
    await act(async () => recalculate.dispatchEvent(new window.Event("click", { bubbles: true })));
    assert.deepEqual(requests, ["measure", "start"]);
    assert.match(document.body.textContent, /≈ 999 B/);
  } finally {
    await act(async () => root.unmount());
    Object.assign(globalThis, saved); delete globalThis.__fileManagementQuery;
  }
});

test("breadcrumb uses available width and reveals more ancestors when resized", async () => {
  const parsed = parseHTML('<html><body><div id="root"></div></body></html>');
  const saved = { window: globalThis.window, document: globalThis.document, ResizeObserver: globalThis.ResizeObserver };
  globalThis.window = parsed.window; globalThis.document = parsed.document;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  let available = 400, resize;
  const widths = { alex: 30, Desktop: 70, EasyEdit: 65, easyeditor: 60, evaluate: 60 };
  const prototype = parsed.window.HTMLElement.prototype;
  const oldRect = prototype.getBoundingClientRect;
  const oldWidth = Object.getOwnPropertyDescriptor(prototype, "clientWidth");
  prototype.getBoundingClientRect = function () { return { width: widths[this.textContent] ?? 0 }; };
  Object.defineProperty(prototype, "clientWidth", { configurable: true, get: () => available });
  globalThis.ResizeObserver = class { constructor(callback) { resize = callback; } observe() {} disconnect() {} };
  const root = createRoot(document.getElementById("root"));
  try {
    await act(async () => root.render(h(api.FileBreadcrumb, { root: "alex", path: "Desktop/EasyEdit/easyeditor/evaluate", onLocate: noop })));
    const visible = () => [...document.querySelectorAll('nav button')].map(button => button.dataset.path === undefined ? "…" : button.textContent);
    assert.deepEqual(visible(), ["alex", "Desktop", "EasyEdit", "easyeditor", "evaluate"], "every ancestor fits");
    assert.ok(document.querySelector('nav[data-slot="breadcrumb"] ol[data-slot="breadcrumb-list"]'), "the path is shadcn's Nova Breadcrumb");
    assert.equal(document.querySelector('button[aria-current="page"]').textContent, "evaluate");
    available = 250;
    await act(async () => resize());
    assert.deepEqual(visible(), ["alex", "…", "easyeditor", "evaluate"], "compact arrows fit the ancestor without truncation");
    const partial = document.querySelector('button[data-path="Desktop/EasyEdit/easyeditor"]');
    assert.equal(partial.parentElement.style.maxWidth, "");
    available = 400;
    await act(async () => resize());
    assert.deepEqual(visible(), ["alex", "Desktop", "EasyEdit", "easyeditor", "evaluate"]);
    available = 330;
    await act(async () => resize());
    assert.deepEqual(visible(), ["alex", "…", "EasyEdit", "easyeditor", "evaluate"], "spare room shows part of one more ancestor");
  } finally {
    await act(async () => root.unmount());
    prototype.getBoundingClientRect = oldRect;
    if (oldWidth) Object.defineProperty(prototype, "clientWidth", oldWidth); else delete prototype.clientWidth;
    Object.assign(globalThis, saved);
  }
});


test("file tree refresh preserves expanded paths and file sizes, and path copy uses the absolute path", async () => {
  const parsed = parseHTML('<html><body><div id="root"></div></body></html>');
  const saved = { window: globalThis.window, document: globalThis.document, ResizeObserver: globalThis.ResizeObserver, IntersectionObserver: globalThis.IntersectionObserver };
  globalThis.window = parsed.window; globalThis.document = parsed.document;
  const browserGlobals = ["Event", "HTMLDivElement", "ShadowRoot", "HTMLElement", "HTMLStyleElement", "Element", "HTMLTemplateElement", "SVGElement", "HTMLInputElement", "Node", "MutationObserver", "customElements"];
  for (const key of browserGlobals) { saved[key] = globalThis[key]; globalThis[key] = parsed.window[key]; }
  Object.defineProperties(parsed.window.HTMLElement.prototype, {
    scrollTop: { configurable: true, writable: true, value: 0 },
    clientHeight: { configurable: true, get: () => 600 },
    clientWidth: { configurable: true, get: () => 300 },
  });
  parsed.window.HTMLElement.prototype.scrollTo = function(options) { this.scrollTop = options.top ?? this.scrollTop; };
  saved.requestAnimationFrame = globalThis.requestAnimationFrame;
  saved.cancelAnimationFrame = globalThis.cancelAnimationFrame;
  globalThis.requestAnimationFrame = callback => setTimeout(callback, 0);
  globalThis.cancelAnimationFrame = clearTimeout;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  globalThis.ResizeObserver = class { observe() {} disconnect() {} };
  globalThis.IntersectionObserver = class { observe() {} disconnect() {} };
  const root = createRoot(document.getElementById("root"));
  const projectId = "refresh-tree";
  let sizeRequests = 0;
  let holdSize = true; const sizeWaiters = [];
  const requests = []; const held = []; let holding = false; let refreshed = false; const heldPages = [];
  let holdingPages = false; let failPage = true;
  // Mid-1970, so the rendered year is 1970 in every time zone.
  const entry = (name, type, size = 0) => ({ name, type, size, mtime: 15_000_000 });
  globalThis.__fileManagementQuery = async (action, payload) => {
    if (action === "list_projects") return { projects: [{ id: projectId, path: "/project" }, { id: "other-project", path: "/other" }] };
    if (action === "project_folder_size") {
      if (payload.operation === "cancel") return { state: "cancelled", token: null };
      sizeRequests++;
      if (holdSize) return new Promise(resolve => sizeWaiters.push(resolve));
      return { state: "complete", bytes: 2048, complete: true };
    }
    if (action !== "project_file_tree") return null;
    requests.push(payload);
    if (failPage && payload.cursor) return { project_id: payload.project_id, path: payload.path, error_code: "IO_ERROR" };
    const response = { project_id: payload.project_id, path: payload.path, snapshot_id: refreshed ? "fresh" : "initial", next_cursor: payload.path !== "src" ? null : !payload.cursor ? "page2" : payload.cursor === "page2" ? "page3" : null, entries: payload.path === "" ? [entry("src", "dir")] : payload.cursor === "page3" ? [entry("last.txt", "file", 7)] : payload.cursor ? [entry("data.bin", "file", refreshed ? 2048 : 1024)] : [entry("empty.txt", "file"), ...(payload.path === "src" ? [entry("nested", "dir")] : [])] };
    if (holding) await new Promise(resolve => held.push(resolve));
    if (refreshed && payload.cursor && holdingPages) await new Promise(resolve => heldPages.push(resolve));
    return response;
  };
  const query = selector => document.querySelector(selector) ?? document.querySelector("file-tree-container")?.shadowRoot?.querySelector(selector);
  const click = async selector => { const node = query(selector); assert.ok(node, selector); await act(async () => node.dispatchEvent(new window.MouseEvent("click", { bubbles: true, composed: true }))); };
  // linkedom provides Event, which React's delegated click handler also accepts.
  window.MouseEvent = window.Event;
  try {
    await act(async () => root.render(h(api.FileTree, { projectId })));
    await act(async () => { await new Promise(resolve => requestAnimationFrame(resolve)); });
    assert.equal(sizeRequests, 1, "a visible folder asks for its size once");
    const pulse = () => query('[data-item-section="decoration"] span[style*="--op-size-scanning"]');
    await act(async()=>sizeWaiters.shift()({state:"partial",bytes:1024,token:"scan"}));
    assert.match(query('[data-item-path="src/"] [data-item-section="decoration"]').textContent, /1.0 KiB/);
    assert.equal(sizeRequests, 1, "a partial answer is not followed by automatic continue requests");
    await act(async () => api.invalidateFolderSizes(projectId));
    assert.equal(pulse()?.textContent, "1.0 KiB", "the existing number pulses while it is re-measured");
    holdSize = false;
    await act(async()=>sizeWaiters.shift()({state:"complete",bytes:1024,complete:true}));
    assert.equal(pulse(), null, "completion stops pulse even when byte value does not change");
    assert.doesNotMatch(query('[data-item-path="src/"] [data-item-section="decoration"]').textContent, /计算中|Calculating|—|…/);

    await click('[data-item-path="src/"]');
    assert.match(query('[data-item-path="src/empty.txt"]').textContent, /0 B/);
    await act(async () => { await new Promise(resolve => requestAnimationFrame(resolve)); });
    const failedPageRequests = requests.length;
    assert.doesNotMatch(document.body.textContent, /Refresh failed|retry/i, "a failed page says nothing");
    await act(async () => { await new Promise(resolve => requestAnimationFrame(resolve)); });
    assert.equal(requests.length, failedPageRequests, "failed pagination does not loop");
    failPage = false;
    // The failed listing is read again by itself after a short wait.
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 1100)); });

    await act(async () => { await new Promise(resolve => requestAnimationFrame(resolve)); });
    assert.equal(document.querySelector('button[aria-label="Load more entries"]'), null);
    assert.match(query('[data-item-path="src/data.bin"]').textContent, /1.0 KiB/);
    await click('[data-item-path="src/data.bin"]');
    const path = () => document.querySelector('nav[aria-label="File path"]').textContent;
    assert.match(path(), /data.bin/);
    const beforeLocateRequests = requests.length;
    const beforeLocateSizes = sizeRequests;
    const beforeLocateRow = query('[data-item-path="src/data.bin"]');
    await click('nav button[data-path="src/data.bin"]');
    await click('nav button[data-path="src"]');
    assert.equal(requests.length, beforeLocateRequests, "cached breadcrumb navigation does not reload directories");
    assert.equal(sizeRequests, beforeLocateSizes, "cached breadcrumb navigation does not restart size scans");
    assert.equal(query('[data-item-path="src/data.bin"]'), beforeLocateRow, "cached navigation preserves mounted rows");
    await click('[data-item-path="src/data.bin"]');
    holding = true; refreshed = true; holdingPages = true;
    await click('button[aria-label="Refresh"]');
    assert.match(path(), /data.bin/);
    assert.ok(query('[data-item-path="src/data.bin"]'), "keep expanded rows while refresh is pending");
    holding = false;
    await act(async () => { for (const resolve of held.splice(0)) resolve(); });
    assert.ok(heldPages.length, "second refreshed page is still pending");
    assert.ok(query('[data-item-path="src/data.bin"]'), "keep previous second-page row until the refreshed range is complete");
    holdingPages = false;
    await act(async () => { for (const resolve of heldPages.splice(0)) resolve(); });
    assert.ok(requests.filter(p => p.path === "src").length >= 2, "refresh expanded directories too");
    assert.match(path(), /data.bin/);
    assert.ok(query('[data-item-path="src/data.bin"]'));
    assert.match(query('[data-item-path="src/data.bin"]').textContent, /2.0 KiB/);
    assert.ok(requests.some(p => p.cursor === "page2" && p.snapshot_id === "fresh"));
    const beforeEvent = requests.length;
    await act(async () => window.dispatchEvent(new window.CustomEvent("project-files-changed", { detail: { project_id: projectId } })));
    assert.ok(requests.slice(beforeEvent).some(p => p.path === "src"), "event refresh reads latest expanded state");
    assert.match(path(), /data.bin/);
    failPage = true;
    await click('button[aria-label="Refresh"]');
    assert.ok(query('[data-item-path="src/data.bin"]'), "failed refresh preserves old content");
    assert.doesNotMatch(document.body.textContent, /Refresh failed/, "a failed refresh says nothing");
    failPage = false;
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 1100)); });
    assert.ok(query('[data-item-path="src/data.bin"]'));
    assert.doesNotMatch(document.body.textContent, /Refresh failed/);
    await act(async () => { await new Promise(resolve => requestAnimationFrame(resolve)); });
    assert.equal(document.querySelector('button[aria-label="Load more entries"]'), null);
    assert.match(query('[data-item-path="src/last.txt"]').textContent, /7 B/);
    const copy = document.querySelector('button[aria-label="Copy absolute path"]');
    assert.ok(copy, "right-hand copy button");
    let copied;
    const previous = Object.getOwnPropertyDescriptor(globalThis, "navigator");
    Object.defineProperty(globalThis, "navigator", { configurable: true, value: { clipboard: { writeText: async value => { copied = value; } } } });
    try { await click('button[aria-label="Copy absolute path"]'); assert.equal(copied, "/project/src/data.bin"); }
    finally { if (previous) Object.defineProperty(globalThis, "navigator", previous); else delete globalThis.navigator; }
    await click('[data-item-path="src/nested/"]');
    assert.ok(query('[data-item-path="src/nested/empty.txt"]'));
    await click('[data-item-path="src/"]');
    await click('button[aria-label="Refresh"]');
    assert.equal(query('[data-item-path="src/nested/"]'), null);
    await click('[data-item-path="src/"]');
    assert.ok(query('[data-item-path="src/nested/empty.txt"]'), "refresh of a collapsed ancestor retains expanded child caches");
    const metadata = query('[data-item-path="src/data.bin"] [data-item-section="decoration"] > span').children;
    assert.match(metadata[1].textContent, /1970/, "listing mtime reaches the public tree");
    assert.equal(metadata[2].textContent, "BIN file");
    const beforeSort = requests.length;
    await click('[role="columnheader"][aria-label="Size"] button');
    assert.ok(requests.slice(beforeSort).some(p => p.path === "" && p.sort === "size:desc:folders:hidden:ignored"), "header uses existing server sorting");
    assert.equal(document.querySelector('[role="columnheader"][aria-label="Size"]').getAttribute("aria-sort"), "descending");
    await click('[role="columnheader"][aria-label="Size"] button');
    assert.ok(requests.some(p => p.sort === "size:asc:folders:hidden:ignored"));
    await act(async () => root.render(h(api.FileTree, { projectId: "other-project" })));
    assert.doesNotMatch(path(), /data.bin/);
    assert.equal(query('[data-item-path="src/data.bin"]'), null);
  } finally {
    await act(async () => { root.unmount(); for (const resolve of [...held, ...heldPages, ...sizeWaiters]) resolve(); });
    Object.assign(globalThis, saved); delete globalThis.__fileManagementQuery;
  }
});


test("sort control exposes an accessible styled menu trigger without a native title", () => {
  const { document } = parseHTML(renderToStaticMarkup(h(api.FileSortMenu, { value: "name:asc:folders:hidden:ignored", onChange: noop })));
  const button = document.querySelector('button[aria-label="Sort and display"]');
  assert.ok(button);
  assert.equal(button.getAttribute("aria-haspopup"), "menu");
  assert.equal(button.hasAttribute("title"), false);
});

test("Pierre search follows current fuzzy result and preserves folder identity", async () => {
  const parsed = parseHTML('<html><body><div id="root"></div></body></html>');
  const saved = { window: globalThis.window, document: globalThis.document, ResizeObserver: globalThis.ResizeObserver, IntersectionObserver: globalThis.IntersectionObserver };
  globalThis.window = parsed.window; globalThis.document = parsed.document;
  const browserGlobals = ["HTMLDivElement", "ShadowRoot", "HTMLElement", "HTMLStyleElement", "Element", "HTMLTemplateElement", "SVGElement", "HTMLInputElement", "Node", "MutationObserver", "customElements"];
  for (const key of browserGlobals) { saved[key] = globalThis[key]; globalThis[key] = parsed.window[key]; }
  Object.defineProperties(parsed.window.HTMLElement.prototype, {
    scrollTop: { configurable: true, writable: true, value: 0 },
    clientHeight: { configurable: true, get: () => 600 },
    clientWidth: { configurable: true, get: () => 300 },
  });
  parsed.window.HTMLElement.prototype.scrollTo = function(options) { this.scrollTop = options.top ?? this.scrollTop; };
  saved.requestAnimationFrame = globalThis.requestAnimationFrame;
  saved.cancelAnimationFrame = globalThis.cancelAnimationFrame;
  globalThis.requestAnimationFrame = callback => setTimeout(callback, 0);
  globalThis.cancelAnimationFrame = clearTimeout;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  globalThis.ResizeObserver = class { observe() {} disconnect() {} };
  globalThis.IntersectionObserver = class { observe() {} disconnect() {} };
  const root = createRoot(document.getElementById("root"));
  const matches = [{ path: "src/zebra.py", type: "file", size: 3 }, { path: "src/alpha.ts", type: "file", size: 7 }];
  const noop = () => {};
  const props = { projectId: "pierre-search", matches, onSelect: noop, onOpen: noop, onContextMenu: noop };
  try {
    await act(async () => root.render(h(api.PierreSearchTree, { ...props, currentPath: "src/zebra.py" })));
    const shadow = document.querySelector("file-tree-container").shadowRoot;
    const row = path => shadow.querySelector(`[role="treeitem"][data-item-path="${path}"]`);
    assert.equal(row("src/zebra.py").getAttribute("aria-selected"), "true");
    assert.match(row("src/zebra.py").textContent, /•/);
    assert.ok(shadow.querySelector("#openprogram-folder"));
    assert.equal(row("src/").getAttribute("aria-expanded"), "true");
    await act(async () => root.render(h(api.PierreSearchTree, { ...props, currentPath: "src/alpha.ts" })));
    assert.equal(row("src/alpha.ts").getAttribute("aria-selected"), "true");
    assert.equal(row("src/zebra.py").getAttribute("aria-selected"), "false");
    assert.deepEqual([...shadow.querySelectorAll('[data-item-type="file"][data-item-path]')].map(node => node.getAttribute("data-item-path")), ["src/zebra.py", "src/alpha.ts"]);
  } finally { await act(async () => root.unmount()); Object.assign(globalThis, saved); }
});

test("Pierre nested folder collapse survives state synchronization and refresh", async () => {
  const parsed = parseHTML('<html><body><div id="root"></div></body></html>');
  const saved = { window: globalThis.window, document: globalThis.document, ResizeObserver: globalThis.ResizeObserver, IntersectionObserver: globalThis.IntersectionObserver };
  globalThis.window = parsed.window; globalThis.document = parsed.document;
  const browserGlobals = ["HTMLDivElement", "ShadowRoot", "HTMLElement", "HTMLStyleElement", "Element", "HTMLTemplateElement", "SVGElement", "HTMLInputElement", "Node", "MutationObserver", "customElements"];
  for (const key of browserGlobals) { saved[key] = globalThis[key]; globalThis[key] = parsed.window[key]; }
  Object.defineProperties(parsed.window.HTMLElement.prototype, {
    scrollTop: { configurable: true, writable: true, value: 0 },
    clientHeight: { configurable: true, get: () => 600 },
    clientWidth: { configurable: true, get: () => 300 },
  });
  parsed.window.HTMLElement.prototype.scrollTo = function(options) { this.scrollTop = options.top ?? this.scrollTop; };
  saved.requestAnimationFrame = globalThis.requestAnimationFrame;
  saved.cancelAnimationFrame = globalThis.cancelAnimationFrame;
  globalThis.requestAnimationFrame = callback => setTimeout(callback, 0);
  globalThis.cancelAnimationFrame = clearTimeout;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  globalThis.ResizeObserver = class { observe() {} disconnect() {} };
  globalThis.IntersectionObserver = class { observe() {} disconnect() {} };
  const root = createRoot(document.getElementById("root"));
  const entries = [{path:"src",type:"dir",size:0},{path:"src/nested",type:"dir",size:0},{path:"src/nested/file.txt",type:"file",size:7}];
  function Harness({rows=entries}) {
    const [expanded,setExpanded] = React.useState(new Set(["src", "src/nested"]));
    const [selected,setSelected] = React.useState("src/nested/file.txt");
    return h(api.PierreFileTree,{projectId:"collapse",entries:rows,expanded,selected,onExpandedChange:setExpanded,onSelect:setSelected,onOpen:noop,onContextMenu:noop});
  }
  window.MouseEvent = window.Event;
  try {
    await act(async()=>root.render(h(Harness)));
    const shadow=document.querySelector("file-tree-container").shadowRoot;
    const row=path=>shadow.querySelector(`[role="treeitem"][data-item-path="${path}"]`);
    await act(async()=>row("src/").dispatchEvent(new window.MouseEvent("click",{bubbles:true,composed:true})));
    assert.equal(row("src/").getAttribute("aria-expanded"),"false");
    assert.equal(row("src/nested/"),null);
    await act(async()=>root.render(h(Harness,{rows:[...entries]})));
    assert.equal(row("src/").getAttribute("aria-expanded"),"false","refresh does not reopen parent");
    await act(async()=>row("src/").dispatchEvent(new window.MouseEvent("click",{bubbles:true,composed:true})));
    assert.equal(row("src/nested/").getAttribute("aria-expanded"),"true","child expansion survives parent collapse");
    assert.ok(row("src/nested/file.txt"));
    assert.equal(document.querySelectorAll(".overlay-thumb").length, 1, "the tree scrolls with the app's overlay thumb");
    assert.ok(shadow.querySelector('[data-file-tree-virtualized-scroll="true"]').classList.contains("scroll-overlay"));
    await act(async()=>root.unmount());
    assert.equal(document.querySelectorAll(".overlay-thumb").length, 0, "unmounting removes the thumb");
  } finally { await act(async()=>root.unmount()); Object.assign(globalThis,saved); }
});

test("a new entry starts unnamed: leaving it empty drops it, a typed name creates it", async () => {
  const parsed = parseHTML('<html><body><div id="root"></div></body></html>');
  const saved = { window: globalThis.window, document: globalThis.document, ResizeObserver: globalThis.ResizeObserver, IntersectionObserver: globalThis.IntersectionObserver };
  globalThis.window = parsed.window; globalThis.document = parsed.document;
  const browserGlobals = ["Event", "HTMLDivElement", "ShadowRoot", "HTMLElement", "HTMLStyleElement", "Element", "HTMLTemplateElement", "SVGElement", "HTMLInputElement", "Node", "MutationObserver", "customElements"];
  for (const key of browserGlobals) { saved[key] = globalThis[key]; globalThis[key] = parsed.window[key]; }
  Object.defineProperties(parsed.window.HTMLElement.prototype, {
    scrollTop: { configurable: true, writable: true, value: 0 },
    clientHeight: { configurable: true, get: () => 600 },
    clientWidth: { configurable: true, get: () => 300 },
  });
  parsed.window.HTMLElement.prototype.scrollTo = function(options) { this.scrollTop = options.top ?? this.scrollTop; };
  saved.requestAnimationFrame = globalThis.requestAnimationFrame;
  saved.cancelAnimationFrame = globalThis.cancelAnimationFrame;
  globalThis.requestAnimationFrame = callback => setTimeout(callback, 0);
  globalThis.cancelAnimationFrame = clearTimeout;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  globalThis.ResizeObserver = class { observe() {} disconnect() {} };
  globalThis.IntersectionObserver = class { observe() {} disconnect() {} };
  const root = createRoot(document.getElementById("root"));
  const entries = [{path:"src",type:"dir",size:0},{path:"src/a.txt",type:"file",size:1}];
  // linkedom has no text selection; Pierre selects the name it opens.
  for (const method of ["select", "setSelectionRange", "focus"]) parsed.window.HTMLInputElement.prototype[method] ??= function() {};
  const committed = []; let cancelled = 0;
  const frames = () => act(async () => { for (let i = 0; i < 4; i++) await new Promise(resolve => setTimeout(resolve, 0)); });
  function Harness({ draft }) {
    return h(api.PierreFileTree,{projectId:"draft",entries,expanded:new Set(["src"]),selected:null,draft,
      onExpandedChange:noop,onSelect:noop,onOpen:noop,onContextMenu:noop,
      onDraftCommit:name=>committed.push(name),onDraftCancel:()=>{ cancelled++; }});
  }
  try {
    await act(async()=>root.render(h(Harness,{draft:{dir:"src",kind:"file"}})));
    await frames();
    const shadow=document.querySelector("file-tree-container").shadowRoot;
    let input=shadow.querySelector("input[data-item-rename-input]");
    assert.equal(input.value, "", "the name starts empty");
    assert.equal(input.placeholder, "untitled", "the default name is only a hint");
    await act(async()=>input.dispatchEvent(new window.Event("blur")));
    assert.equal(cancelled, 1, "leaving with nothing typed drops the entry");
    assert.deepEqual(committed, []);

    await act(async()=>root.render(h(Harness,{draft:null})));
    await act(async()=>root.render(h(Harness,{draft:{dir:"src",kind:"file"}})));
    await frames();
    input=shadow.querySelector("input[data-item-rename-input]");
    assert.equal(input.value, "", "a new draft starts empty again");
    input.value="notes.md";
    await act(async()=>input.dispatchEvent(new window.Event("input",{bubbles:true,composed:true})));
    await act(async()=>input.dispatchEvent(new window.Event("blur")));
    assert.deepEqual(committed, ["notes.md"], "a typed name creates the entry on blur");
  } finally { await act(async()=>root.unmount()); Object.assign(globalThis,saved); }
});

test("breadcrumb copy confirms success only after the clipboard write completes", async () => {
  const parsed = parseHTML('<html><body><div id="root"></div></body></html>');
  const saved = { window: globalThis.window, document: globalThis.document, ResizeObserver: globalThis.ResizeObserver };
  const clipboard = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  Object.assign(globalThis, { window: parsed.window, document: parsed.document, ResizeObserver: class { observe() {} disconnect() {} } });
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  const writes = []; let finishCopy;
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { clipboard: { writeText: value => { writes.push(value); return new Promise(resolve => { finishCopy = resolve; }); } } } });
  const root = createRoot(document.getElementById("root"));
  const click = async node => act(async () => node.dispatchEvent(new window.Event("click", { bubbles: true })));
  try {
    await act(async () => root.render(h(api.FileBreadcrumb, {root:"Project",path:"src/file.txt",absolutePath:"/project/src/file.txt",onLocate:noop})));
    await click(document.querySelector('button[aria-label="Copy absolute path"]'));
    assert.equal(document.querySelector('button[aria-label="Copied"]'), null, "pending write is not a success");
    await act(async () => finishCopy());
    assert.ok(document.querySelector('button[aria-label="Copied"] svg'));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 1550)); });
    assert.ok(document.querySelector('button[aria-label="Copy absolute path"]'));
    assert.deepEqual(writes, ["/project/src/file.txt"]);
    // Actual ancestor context-menu actions use the Chromium acceptance test in
    // tests/e2e/web/browser_tabs/test_browser_control_menu.py.
  } finally {
    await act(async () => root.unmount());
    Object.assign(globalThis, saved);
    if (clipboard) Object.defineProperty(globalThis, "navigator", clipboard); else delete globalThis.navigator;
  }
});

test("stale pagination retains rendered rows while replacing its snapshot", async () => {
  const parsed = parseHTML('<html><body><div id="root"></div></body></html>');
  const saved = { window: globalThis.window, document: globalThis.document, ResizeObserver: globalThis.ResizeObserver, IntersectionObserver: globalThis.IntersectionObserver };
  globalThis.window = parsed.window; globalThis.document = parsed.document;
  const browserGlobals = ["HTMLDivElement", "ShadowRoot", "HTMLElement", "HTMLStyleElement", "Element", "HTMLTemplateElement", "SVGElement", "HTMLInputElement", "Node", "MutationObserver", "customElements"];
  for (const key of browserGlobals) { saved[key] = globalThis[key]; globalThis[key] = parsed.window[key]; }
  Object.defineProperties(parsed.window.HTMLElement.prototype, {
    scrollTop: { configurable: true, writable: true, value: 0 },
    clientHeight: { configurable: true, get: () => 600 },
    clientWidth: { configurable: true, get: () => 300 },
  });
  parsed.window.HTMLElement.prototype.scrollTo = function(options) { this.scrollTop = options.top ?? this.scrollTop; };
  saved.requestAnimationFrame = globalThis.requestAnimationFrame;
  saved.cancelAnimationFrame = globalThis.cancelAnimationFrame;
  globalThis.requestAnimationFrame = callback => setTimeout(callback, 0);
  globalThis.cancelAnimationFrame = clearTimeout;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  globalThis.ResizeObserver = class { observe() {} disconnect() {} };
  globalThis.IntersectionObserver = class { observe() {} disconnect() {} };
  const root = createRoot(document.getElementById("root"));

  const projectId="stale-scroll"; let release; let requests=0;
  globalThis.__fileManagementQuery=async(action,payload)=>{
    if(action==="list_projects")return {projects:[{id:projectId,path:"/project"}]};
    if(action!=="project_file_tree")return null;
    requests++;
    if(payload.cursor)return {project_id:projectId,path:"",error_code:"STALE_SNAPSHOT"};
    if(requests>1)await new Promise(resolve=>{release=resolve});
    return {project_id:projectId,path:"",snapshot_id:"s"+requests,next_cursor:requests===1?"next":null,entries:[{name:"visible.txt",type:"file",size:1,mtime:1}]};
  };
  try {
    await act(async()=>root.render(h(api.FileTree,{projectId})));
    await act(async()=>{await new Promise(resolve=>requestAnimationFrame(resolve));});
    assert.ok(release,"stale cursor refresh has started");
    assert.ok(document.querySelector("file-tree-container").shadowRoot.querySelector('[data-item-path="visible.txt"]'),"keep rows mounted during stale snapshot recovery instead of collapsing viewport to zero");
    await act(async()=>release()); release=null;
  } finally {if(release)await act(async()=>release());await act(async()=>root.unmount());Object.assign(globalThis,saved);delete globalThis.__fileManagementQuery;}
});

test("a mounted tree asks once per large folder and again only after a Files change", async () => {
  const parsed = parseHTML('<html><body><div id="root"></div></body></html>');
  const browserGlobals = ["Event", "HTMLDivElement", "ShadowRoot", "HTMLElement", "HTMLStyleElement", "Element", "HTMLTemplateElement", "SVGElement", "HTMLInputElement", "Node", "MutationObserver", "customElements"];
  const saved = { window: globalThis.window, document: globalThis.document, ResizeObserver: globalThis.ResizeObserver, IntersectionObserver: globalThis.IntersectionObserver, requestAnimationFrame: globalThis.requestAnimationFrame, cancelAnimationFrame: globalThis.cancelAnimationFrame };
  globalThis.window = parsed.window; globalThis.document = parsed.document;
  for (const key of browserGlobals) { saved[key] = globalThis[key]; globalThis[key] = parsed.window[key]; }
  Object.defineProperties(parsed.window.HTMLElement.prototype, {
    scrollTop: { configurable: true, writable: true, value: 0 },
    clientHeight: { configurable: true, get: () => 600 },
    clientWidth: { configurable: true, get: () => 300 },
  });
  parsed.window.HTMLElement.prototype.scrollTo = function() {};
  globalThis.requestAnimationFrame = callback => setTimeout(callback, 0);
  globalThis.cancelAnimationFrame = clearTimeout;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  globalThis.ResizeObserver = class { observe() {} disconnect() {} };
  globalThis.IntersectionObserver = class { observe() {} disconnect() {} };
  const projectId = "large-folders";
  const folders = ["a", "b", "c", "d", "e", "f"];
  const sizes = [];
  globalThis.__fileManagementQuery = async (action, payload) => {
    if (action === "list_projects") return { projects: [{ id: projectId, path: "/project" }] };
    if (action === "project_folder_size") {
      sizes.push(`${payload.operation ?? "peek"}${payload.fresh ? ":fresh" : ""}`);
      await new Promise(resolve => setTimeout(resolve, 5));
      // Every folder is too large to finish inside one bounded walk.
      return payload.operation === "cancel" ? { state: "cancelled", bytes: 1000 } : { state: "partial", bytes: 1000, complete: false, token: `t-${payload.path}` };
    }
    if (action !== "project_file_tree") return null;
    return { project_id: payload.project_id, path: payload.path, snapshot_id: "s", next_cursor: null, entries: payload.path === "" ? folders.map(name => ({ name, type: "dir", size: 0, mtime: 15_000_000 })) : [] };
  };
  const root = createRoot(document.getElementById("root"));
  const wait = async (ms) => { for (let t = 0; t < ms; t += 25) await act(async () => { await new Promise(resolve => setTimeout(resolve, 25)); }); };
  try {
    await act(async () => root.render(h(api.FileTree, { projectId })));
    await wait(1500);
    // Before the fix each large folder sent a peek, a start and 19 continues (126 here).
    assert.deepEqual(sizes, folders.map(() => "measure"), "one request per visible folder");
    await act(async () => root.render(null));
    await act(async () => root.render(h(api.FileTree, { projectId })));
    await wait(500);
    assert.equal(sizes.length, folders.length, "remounting reuses the answers");
    sizes.length = 0;
    await act(async () => window.dispatchEvent(new window.CustomEvent("project-files-changed", { detail: { project_id: projectId } })));
    await wait(1000);
    assert.equal(sizes.filter(op => op === "measure:fresh").length, folders.length, "a Files change re-measures each folder once");
    assert.equal(sizes.filter(op => op.startsWith("continue") || op.startsWith("start")).length, 0);
  } finally {
    await act(async () => root.unmount());
    Object.assign(globalThis, saved); delete globalThis.__fileManagementQuery;
    delete parsed.window.HTMLElement.prototype.scrollTo;
  }
});
