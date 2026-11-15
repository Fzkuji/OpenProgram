import assert from "node:assert/strict";
import test, { after } from "node:test";
import { registerHooks } from "node:module";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { parseHTML } from "linkedom";

const base = new URL("../../", import.meta.url);
const mocks = {
  "@/lib/runtime-bridge/dom/markdown-render": "export const typesetMath=()=>{};",
  "@/lib/i18n": "export const useTranslation=()=>({text:(en)=>en,t:(key)=>key});",
  "@/components/animated-icons": "export const FileTextIcon=()=>null; export const RefreshCwIcon=()=>null;",
  "@/components/chat/messages/bubbles/use-action-icon-animation": "export const useActionIconAnimation=()=>({});",
  "@/lib/documents/office-editor": "export const convertOfficeDocument=()=>{throw Error('unexpected office conversion')};",
  "@/lib/documents/raster-format": "export const convertRasterToPng=()=>{throw Error('unexpected image conversion')};",
  "./office-surface": "export const OfficeSurface=()=>{throw Error('unexpected office surface')};",
  "./pdf-surface": "export const PdfSurface=()=>{throw Error('unexpected pdf surface')};",
  "./raster-surface": "export const RasterSurface=()=>{throw Error('unexpected raster surface')};",
};
const hooks = registerHooks({
  resolve(specifier, context, next) {
    if (Object.hasOwn(mocks, specifier)) return { url: "data:text/javascript," + encodeURIComponent(mocks[specifier]), shortCircuit: true };
    if (specifier.endsWith(".css")) return { url: "data:text/javascript,export default {}", shortCircuit: true };
    const path = specifier.startsWith("@/") ? new URL(specifier.slice(2), base) : specifier.startsWith(".") ? new URL(specifier, context.parentURL) : null;
    if (path) for (const suffix of [".ts", ".tsx", "/index.ts"]) {
      if (existsSync(fileURLToPath(path) + suffix)) return { url: path.href + suffix, shortCircuit: true };
    }
    return next(specifier, context);
  },
  load(url, context, next) {
    if (url.endsWith(".tsx")) return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), "utf8"), { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText };
    return next(url, context);
  },
});
after(() => hooks.deregister());

const replaced = ["window", "document", "HTMLElement", "localStorage", "fetch", "location", "history", "IS_REACT_ACT_ENVIRONMENT"];
const originals = Object.fromEntries(replaced.map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
const { document, window } = parseHTML("<html><body></body></html>");
window.location = { pathname: "/", search: "" };
window.history = { pushState() {} };
const storage = new Map();
Object.assign(globalThis, { document, window, HTMLElement: window.HTMLElement, IS_REACT_ACT_ENVIRONMENT: true,
  localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) } });
after(() => { for (const key of replaced) { if (originals[key]) Object.defineProperty(globalThis, key, originals[key]); else delete globalThis[key]; } });
globalThis.fetch = async () => { throw new Error("Unexpected network request in navigation fixture"); };
const { useCenterTabs: tabs, snapshotCenterTabsPayload, replaceCenterTabsPayload } = await import("../../lib/tabs/center-tabs-store.ts");
const { MarkdownPreview } = await import("../../components/files/preview/markdown-preview.tsx");
const { PersistentFilePanes } = await import("../../components/center-tabs/files/persistent-file-panes.tsx");
const { DocumentWindow } = await import("../../components/files/surfaces/document-window.tsx");
const { DocumentController } = await import("../../lib/files/document/document-controller.ts");
const { documentControllers } = await import("../../lib/files/document/document-controller-registry.ts");
const { MemoryDraftStore } = await import("../../lib/files/drafts/file-draft-store.ts");
const { setDraftStoreAdapterForTests } = await import("../../lib/files/drafts/file-drafts.ts");
after(() => setDraftStoreAdapterForTests(null));
const props = { projectId: "p", path: "docs/a.md" };
function reset() { tabs.setState({ tabs: [], activeId: null, groups: [], splitWebTabId: null, fileSectionRequests: {} }); }
function request(id = tabs.getState().activeId) { return tabs.getState().fileSectionRequests[id]; }
async function mounted(run) {
  const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
  try { await run(host, root); } finally { await act(() => root.unmount()); host.remove(); }
}
async function click(element) { const event = new window.Event("click", { bubbles: true, cancelable: true }); await act(() => element.dispatchEvent(event)); return event; }

 test("heading keys derive from Markdown tokens and restart per document render", () => {
  const source = "# **Title**\n# Title\n# Title-1\n# 中文\n# Cafe\u0301\n# !!!\n```\n# fenced\n```";
  const render = () => parseHTML(renderToStaticMarkup(createElement(MarkdownPreview, { ...props, source }))).document;
  for (let pass = 0; pass < 2; pass++) assert.deepEqual([...render().querySelectorAll("[data-section-key]")].map(node => node.getAttribute("data-section-key")), ["title", "title-1", "title-1-1", "中文", "café", "section"]);
});

test("local fragments stay in originating article; cross-file links retain owner and fragment", async () => {
  reset();
  await mounted(async (host, root) => {
    await act(() => root.render(createElement("div", null, ...["one", "two"].map(id => createElement("section", { key: id, id }, createElement(MarkdownPreview, { ...props, source: "# 中文\n[here](#%E4%B8%AD%E6%96%87)\n[file](b.md#target)" }))))));
    const calls = [];
    for (const heading of host.querySelectorAll("h1")) heading.scrollIntoView = () => calls.push(heading.closest("section").id);
    assert.equal((await click(host.querySelector("#two a"))).defaultPrevented, true);
    assert.deepEqual(calls, ["two"]);
    const anchor = host.querySelector("#one a"); anchor.setAttribute("href", "#%broken"); await click(anchor);
    assert.deepEqual(calls, ["two"]); assert.ok(host.querySelector('[role="status"]'));
    await click(host.querySelectorAll("#one a")[1]);
    assert.equal(request().fragment, "#target"); assert.equal(tabs.getState().tabs.at(-1).path, "docs/b.md");
    tabs.getState().openSessionTab("owner", "Chat");
    await act(() => root.render(createElement(MarkdownPreview, { ...props, abs: true, sessionId: "owner", path: "/allowed/a.md", source: "[file](b.md#target)" })));
    await click(host.querySelector("a"));
    const destination = tabs.getState().tabs.find(tab => tab.path === "/allowed/b.md");
    assert.equal(destination.fileSessionId, "owner"); assert.equal(request(destination.id).fragment, "#target");
  });
});

test("public store requests repeat, exclude persistence, and clear on no-section, retarget, close and replacement", () => {
  reset(); const state = () => tabs.getState();
  state().openFileTab("p", "a.md", { section: "#a" }); const first = request(); assert.ok(first, "public file open retains a section request");
  state().openFileTab("p", "a.md", { section: "#a" }); assert.ok(request().sequence > first.sequence);
  state().consumeFileSection(state().activeId, first.sequence); assert.ok(request());
  assert.ok(!JSON.stringify(snapshotCenterTabsPayload()).includes('"section"'));
  assert.ok(!JSON.stringify(snapshotCenterTabsPayload()).includes("fileSectionRequests"));
  state().openFileTab("p", "a.md"); assert.equal(request(), undefined);
  state().openFileTab("p", "a.md", { section: "#a" }); state().retargetFileTab(state().activeId, "p", "other.md"); assert.deepEqual(state().fileSectionRequests, {});
  state().openFileTab("p", "a.md", { section: "#a" }); state().closeTab(state().activeId); assert.deepEqual(state().fileSectionRequests, {});
  state().openFileTab("p", "a.md", { section: "#a" }); replaceCenterTabsPayload(snapshotCenterTabsPayload(), { persist: false }); assert.deepEqual(state().fileSectionRequests, {});
});

test("preview resolves file destination while chat stays active and denies automatic replacement without intent", () => {
  reset(); const state = () => tabs.getState(); state().openSessionTab("s", "Chat"); const chat = state().activeId;
  assert.equal(state().openFilePreview("s", { projectId: "p", path: "a.md", section: "#a" }), true);
  assert.equal(state().activeId, chat); const file = state().tabs.find(tab => tab.kind === "file"); const first = request(file.id);
  state().openFilePreview("s", { projectId: "p", path: "a.md", section: "#a" }); assert.ok(request(file.id).sequence > first.sequence);
  state().setTabDirty(file.id, true);
  assert.equal(state().openFilePreview("s", { projectId: "p", path: "b.md", section: "#b" }, true), false);
  assert.equal(Object.values(state().fileSectionRequests).length, 1);
});

async function withScrolls(run) {
  const prototype = window.HTMLElement.prototype;
  const descriptor = Object.getOwnPropertyDescriptor(prototype, "scrollIntoView");
  const calls = [];
  Object.defineProperty(prototype, "scrollIntoView", { configurable: true, value() { calls.push({ key: this.getAttribute("data-section-key"), pane: this.closest("[data-file-pane-id]")?.getAttribute("data-file-pane-id") }); } });
  try { await run(calls); } finally { if (descriptor) Object.defineProperty(prototype, "scrollIntoView", descriptor); else delete prototype.scrollIntoView; }
}
function documentRecords() {
  const values = new Map();
  return {
    async get(key) { return values.get(key) ?? null; },
    async put(record) { const version = (values.get(record.key)?.storageVersion ?? 0) + 1; values.set(record.key, { ...record, storageVersion: version }); return version; },
    async delete(key) { values.delete(key); },
  };
}
function navigation(id) { const current = request(id); return current ? { request: current, consume: sequence => tabs.getState().consumeFileSection(id, sequence) } : undefined; }

test("delayed real document load consumes only after current text renders and repeated intent scrolls again", async () => {
  reset(); setDraftStoreAdapterForTests(new MemoryDraftStore());
  let release;
  const response = new Promise(resolve => { release = resolve; });
  const controller = new DocumentController({ projectId: "p", path: "delayed.md", draftStore: documentRecords(), fetchImpl: () => response });
  tabs.getState().openFileTab("p", "delayed.md", { section: "#loaded" }); const id = tabs.getState().activeId;
  try {
    await withScrolls(async calls => mounted(async (host, root) => {
      const render = () => root.render(createElement(DocumentWindow, { projectId: "p", path: "delayed.md", sectionNavigation: navigation(id) }));
      await act(render); assert.ok(request(id)); assert.equal(calls.length, 0);
      await act(async () => { release(new Response("# Loaded", { headers: { "x-document-revision": "a".repeat(64) } })); await controller.load(); });
      assert.deepEqual(calls.map(call => call.key), ["loaded"]); assert.equal(request(id), undefined);
      tabs.getState().openFileTab("p", "delayed.md", { section: "#loaded" });
      await act(render); assert.deepEqual(calls.map(call => call.key), ["loaded", "loaded"]);
    }));
  } finally { await controller.close(); }
});

test("requested active alias keeps one real controller and dirty editor bytes while showing preview", async () => {
  reset(); setDraftStoreAdapterForTests(new MemoryDraftStore());
  const controller = new DocumentController({ projectId: "p", path: "dirty.md", draftStore: documentRecords(), debounceMs: 60000, maxDebounceMs: 60000 });
  await controller.hydrate({ bytes: "# Disk", revision: "a".repeat(64) }); controller.update("# Unsaved\n\n[local](#unsaved)");
  const aliases = [{ id: "alias-a", kind: "file", projectId: "p", path: "dirty.md" }, { id: "f:p:dirty.md", kind: "file", projectId: "p", path: "dirty.md" }];
  tabs.setState({ tabs: aliases, activeId: "alias-a" });
  const activeFileIds = new Set(aliases.map(tab => tab.id)); const layouts = new Map(aliases.map(tab => [tab.id, { className: "pane" }]));
  try {
    await withScrolls(async calls => mounted(async (host, root) => {
      await act(async () => root.render(createElement(PersistentFilePanes, { tabs: aliases, activeFileIds, layouts })));
      const buttons = [...host.querySelectorAll("button")];
      await click(buttons.find(button => button.textContent === "Edit"));
      assert.equal(host.querySelector("textarea").value, "# Unsaved\n\n[local](#unsaved)");
      // Keep a non-file active tab so public open focuses an existing alias.
      tabs.setState({ activeId: null });
      await act(() => tabs.getState().openFileTab("p", "dirty.md", { section: "#unsaved" }));
      const target = tabs.getState().activeId;
      assert.equal(target, aliases[1].id);
      assert.equal(calls.at(-1)?.pane, target); assert.equal(calls.at(-1)?.key, "unsaved");
      assert.equal(documentControllers.get("project:p:dirty.md"), controller);
      assert.equal([...documentControllers.keys()].filter(key => key === "project:p:dirty.md").length, 1);
      assert.equal(host.querySelectorAll("textarea").length, 1);
      assert.equal(await controller.currentDraft().text(), "# Unsaved\n\n[local](#unsaved)");
      assert.equal(host.querySelector('[data-file-pane-id="'+target+'"] button[aria-pressed="true"]').textContent, "Preview");
    }));
  } finally { await controller.discard(); await controller.close(); }
});

test("a duplicate document opened through page history binds the actual independent tab ID", () => {
  reset(); const state = () => tabs.getState();
  state().openFileTab("p", "same.md", { section: "#old" }); const existing = state().activeId;
  state().openSessionTab("new", "New");
  state().openBuiltinTab("files");
  state().openFileTab("p", "same.md", { section: "#section" });
  const target = state().activeId;
  assert.notEqual(target, existing);
  assert.equal(request(existing), undefined); assert.equal(request(target).fragment, "#section");
  state().navigateHistory(-1);
  assert.deepEqual(state().fileSectionRequests, {});
});

test("retained version local navigation uses that version and cross-file links open current target", async () => {
  reset(); setDraftStoreAdapterForTests(new MemoryDraftStore());
  const seen = [];
  const controller = new DocumentController({ projectId: "p", path: "history.md", draftStore: documentRecords(), fetchImpl: async url => {
    seen.push(String(url));
    if (String(url).includes("/history?") || String(url).endsWith("/history")) return new Response(JSON.stringify({ entries: [{ version_id: "v1", actor: "user" }], next_cursor: null }));
    return new Response("# Retained\n[here](#retained)\n[other](other.md#target)");
  } });
  await controller.hydrate({ bytes: "# Current", revision: "a".repeat(64) });
  tabs.getState().openSessionTab("owner", "Chat");
  try {
    await withScrolls(async calls => mounted(async (host, root) => {
      await act(() => root.render(createElement(DocumentWindow, { projectId: "p", path: "history.md" })));
      await click([...host.querySelectorAll("button")].find(button => button.textContent === "History"));
      await click([...host.querySelectorAll("button")].find(button => button.textContent === "After"));
      await click(host.querySelector("article a")); assert.equal(calls.at(-1)?.key, "retained");
      await click(host.querySelectorAll("article a")[1]);
      const target = tabs.getState().tabs.find(tab => tab.path === "other.md");
      assert.equal(target.projectId, "p"); assert.equal(request(target.id).fragment, "#target");
      assert.ok(seen.every(url => url.includes("project_id=p")));
    }));
  } finally { await controller.close(); }
});

test("a section request for a non-Markdown target terminates with visible status", async () => {
  reset(); setDraftStoreAdapterForTests(new MemoryDraftStore());
  const controller = new DocumentController({ projectId: "p", path: "plain.txt", draftStore: documentRecords() });
  await controller.hydrate({ bytes: "plain", revision: "a".repeat(64) });
  tabs.getState().openFileTab("p", "plain.txt", { section: "#missing" }); const id = tabs.getState().activeId;
  try {
    await mounted(async (host, root) => {
      await act(() => root.render(createElement(DocumentWindow, { projectId: "p", path: "plain.txt", sectionNavigation: navigation(id) })));
      assert.equal(request(id), undefined); assert.ok(host.textContent.includes("Section not found."));
    });
  } finally { await controller.close(); }
});

test("failed document load reports failure and consumes pending intent", async () => {
  reset(); setDraftStoreAdapterForTests(new MemoryDraftStore());
  const controller = new DocumentController({ projectId: "p", path: "failed.md", draftStore: documentRecords(), fetchImpl: async () => { throw new Error("Synthetic read failure"); } });
  tabs.getState().openFileTab("p", "failed.md", { section: "#missing" }); const id = tabs.getState().activeId;
  try {
    await mounted(async (host, root) => {
      await act(() => root.render(createElement(DocumentWindow, { projectId: "p", path: "failed.md", sectionNavigation: navigation(id) })));
      assert.equal(request(id), undefined); assert.ok(host.textContent.includes("Synthetic read failure"));
    });
  } finally { await controller.close(); }
});

for (const delayed of [false, true]) {
  test(`binary Markdown ${delayed ? "loaded asynchronously" : "already loaded"} consumes section intent with visible status`, async () => {
    reset(); setDraftStoreAdapterForTests(new MemoryDraftStore());
    let release;
    const response = new Promise(resolve => { release = resolve; });
    const path = delayed ? "delayed-binary.md" : "binary.md";
    const bytes = new Uint8Array([0, 1, 2]);
    const controller = new DocumentController({ projectId: "p", path, draftStore: documentRecords(), fetchImpl: () => response });
    if (!delayed) await controller.hydrate({ bytes, binary: true, revision: "a".repeat(64) });
    tabs.getState().openFileTab("p", path, { section: "#missing" }); const id = tabs.getState().activeId;
    try {
      await mounted(async (host, root) => {
        await act(() => root.render(createElement(DocumentWindow, { projectId: "p", path, sectionNavigation: navigation(id) })));
        if (delayed) {
          assert.ok(request(id), "loading must preserve the section request");
          await act(async () => { release(new Response(bytes, { headers: { "x-document-revision": "a".repeat(64) } })); await controller.load(); });
        }
        assert.ok(host.textContent.includes("binary file"));
        assert.equal(request(id), undefined);
        assert.ok(host.textContent.includes("Section not found."));
      });
    } finally { await controller.close(); }
  });
}
