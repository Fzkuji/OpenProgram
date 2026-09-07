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
  stdin: { contents: 'export * from "./components/files/explorer-header"; export * from "./components/files/file-management"; export * from "./components/files/file-tree"; export * from "./components/files/pierre-file-tree";', resolveDir: fileURLToPath(new URL("../", import.meta.url)), loader: "tsx" },
  bundle: true, write: false, format: "cjs", platform: "node", jsx: "automatic", loader: { ".css": "empty" },
  plugins: [{ name: "host", setup(builder) {
    builder.onLoad({ filter: /\.css$/ }, () => ({ contents: "export default {};", loader: "js" }));
    builder.onResolve({ filter: /^react(?:-dom)?(?:\/.*)?$/ }, ({ path }) => ({ path: require.resolve(path), external: true }));
    builder.onResolve({ filter: /^@\/lib\/i18n$/ }, () => ({ path: "i18n", namespace: "fake" }));
    builder.onResolve({ filter: /^@\/lib\/net\/ws-request$/ }, () => ({ path: "ws", namespace: "fake" }));
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
  globalThis.__fileManagementQuery = async (_action, payload) => payload.operation === "start"
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

test("renewed visibility and details activation revalidate one shared size job", async () => {
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
    if (payload.operation === "start") { starts++; return { state: "complete", bytes, entries: 1, skipped: 0, token: null }; }
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
    assert.equal(starts, 1, "both visible views must share the verification");
    assert.equal((document.body.textContent.match(/999 B/g) ?? []).length, 2);
    bytes = 50; starts = 0;
    await act(async () => root.render(h(Fragment, null, row("sidebar"), row("central"), h(api.FileDetails, { projectId, path: "src", onClose: noop, inline: true }))));
    assert.equal(starts, 1, "opening details verifies the existing shared sample");
    assert.equal((document.body.textContent.match(/50 B/g) ?? []).length, 3);
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
  let available = 300, resize;
  const widths = { fzkuji: 30, "…": 10, Desktop: 70, EasyEdit: 65, easyeditor: 60, evaluate: 60 };
  const prototype = parsed.window.HTMLElement.prototype;
  const oldRect = prototype.getBoundingClientRect;
  const oldWidth = Object.getOwnPropertyDescriptor(prototype, "clientWidth");
  prototype.getBoundingClientRect = function () { return { width: widths[this.textContent] ?? 0 }; };
  Object.defineProperty(prototype, "clientWidth", { configurable: true, get: () => available });
  globalThis.ResizeObserver = class { constructor(callback) { resize = callback; } observe() {} disconnect() {} };
  const root = createRoot(document.getElementById("root"));
  try {
    await act(async () => root.render(h(api.FileBreadcrumb, { root: "fzkuji", path: "Desktop/EasyEdit/easyeditor/evaluate", onLocate: noop })));
    const visible = () => [...document.querySelectorAll('nav button')].map(button => button.textContent);
    assert.deepEqual(visible(), ["fzkuji", "…", "EasyEdit", "easyeditor", "evaluate"]);
    available = 190;
    await act(async () => resize());
    assert.deepEqual(visible(), ["fzkuji", "…", "easyeditor", "evaluate"], "use spare width for a truncated ancestor");
    const partial = document.querySelector('button[data-path="Desktop/EasyEdit/easyeditor"]');
    assert.equal(partial.parentElement.style.maxWidth, "56px");
    available = 400;
    await act(async () => resize());
    assert.deepEqual(visible(), ["fzkuji", "Desktop", "EasyEdit", "easyeditor", "evaluate"]);
    available = 220;
    await act(async () => resize());
    assert.deepEqual(visible(), ["fzkuji", "…", "easyeditor", "evaluate"]);
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
  const projectId = "refresh-tree";
  let sizeRequests = 0;
  let holdSize = true; const sizeWaiters = [];
  const requests = []; const held = []; let holding = false; let refreshed = false; const heldPages = [];
  let holdingPages = false; let failPage = true;
  const entry = (name, type, size = 0) => ({ name, type, size, mtime: 1 });
  globalThis.__fileManagementQuery = async (action, payload) => {
    if (action === "list_projects") return { projects: [{ id: projectId, path: "/project" }, { id: "other-project", path: "/other" }] };
    if (action === "project_folder_size") {
      sizeRequests++;
      if (holdSize) {
        if (!payload.operation) return {state:"cached",bytes:1024,complete:false};
        return new Promise(resolve => sizeWaiters.push(resolve));
      }
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
    assert.ok(sizeRequests > 0, "visible folder starts a size scan after shadow renderer mounts");
    const pulse = () => query('[data-item-section="decoration"] span[style*="--op-size-scanning"]');
    assert.equal(pulse()?.textContent, "1.0 KiB");
    await act(async()=>sizeWaiters.shift()({state:"partial",bytes:2048,token:"scan"}));
    assert.equal(pulse()?.textContent, "2.0 KiB", "existing number updates while pulsing");
    holdSize = false;
    await act(async()=>sizeWaiters.shift()({state:"complete",bytes:2048,complete:true}));
    assert.equal(pulse(), null, "completion stops pulse even when byte value does not change");
    assert.doesNotMatch(query('[data-item-path="src/"] [data-item-section="decoration"]').textContent, /计算中|Calculating|—|…/);

    await click('[data-item-path="src/"]');
    assert.match(query('[data-item-path="src/empty.txt"]').textContent, /0 B/);
    await act(async () => { await new Promise(resolve => requestAnimationFrame(resolve)); });
    const failedPageRequests = requests.length;
    const autoRetry = [...document.querySelectorAll("button")].find(node => node.textContent.includes("Refresh failed — retry"));
    assert.ok(autoRetry, "automatic pagination exposes failure instead of silently stopping");
    await act(async () => { await new Promise(resolve => requestAnimationFrame(resolve)); });
    assert.equal(requests.length, failedPageRequests, "failed pagination does not loop");
    failPage = false;
    await act(async () => autoRetry.dispatchEvent(new window.MouseEvent("click", {bubbles:true,composed:true})));

    await act(async () => { await new Promise(resolve => requestAnimationFrame(resolve)); });
    assert.equal(document.querySelector('button[aria-label="Load more entries"]'), null);
    assert.match(query('[data-item-path="src/data.bin"]').textContent, /1.0 KiB/);
    await click('[data-item-path="src/data.bin"]');
    const path = () => document.querySelector('nav[aria-label="File path"]').textContent;
    assert.match(path(), /data.bin/);
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
    const retry = [...document.querySelectorAll("button")].find(node => node.textContent.includes("Refresh failed — retry"));
    assert.ok(retry, "failed page refresh exposes a retry");
    failPage = false;
    await act(async () => retry.dispatchEvent(new window.MouseEvent("click", { bubbles: true, composed: true })));
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
  } finally { await act(async()=>root.unmount()); Object.assign(globalThis,saved); }
});
