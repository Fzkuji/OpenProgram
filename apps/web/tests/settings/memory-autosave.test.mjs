import test from "node:test";
import assert from "node:assert/strict";
import { MemoryDraft } from "../../components/memory/data/autosave.ts";
import { installLoader } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

// The real i18n module: MemorySettings reloads whenever `text` changes identity.
installLoader({
  replace: {
    "/lib/net/api.ts": "export const api = { listEnabledModels: () => globalThis.memoryModels() };",
    // Radix Switch needs HTMLFormElement, which linkedom lacks; this keeps its role and ARIA contract.
    "/components/ui/switch.tsx": "import { createElement as h } from 'react'; export const Switch = ({ checked, onCheckedChange, disabled, size, className, ...rest }) => h('button', { type: 'button', role: 'switch', 'aria-checked': String(Boolean(checked)), disabled, onClick: () => onCheckedChange?.(!checked), ...rest });",
    "/components/ui/select.tsx": [
      "import { createElement as h } from 'react';",
      "export const SimpleSelect = ({ value, options, onValueChange, disabled, className, ...rest }) => h('select', { 'aria-label': rest['aria-label'], value, disabled, onChange: (e) => onValueChange(e.target.value) }, options.map((o) => h('option', { key: o.value, value: o.value, disabled: o.disabled }, o.label)));",
    ].join("\n"),
  },
});
installDom();

const storage = () => {
  const values = new Map();
  return { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
};
const reply = (content, extra = {}) => ({ ok: true, json: async () => ({ content, ...extra }) });
test("serializes saves and never marks newer typing saved by an earlier response", async () => {
  let resolve;
  const calls = [];
  const draft = new MemoryDraft("/note", async (url, init) => {
    if (!init) return reply("before");
    calls.push(JSON.parse(init.body));
    return new Promise(r => { resolve = r; });
  }, storage());
  await draft.load();
  draft.edit("first");
  const first = draft.flush();
  draft.edit("second");
  await draft.flush();
  assert.equal(calls.length, 1);
  resolve(reply("first"));
  await first;
  assert.equal(draft.state.content, "second");
  assert.equal(draft.state.base, "first");
  const second = draft.flush();
  assert.deepEqual(calls[1], { content: "second", base_content: "first", autosave: true });
  resolve(reply("second"));
  await second;
  assert.equal(draft.state.content, draft.state.base);
  assert.equal(draft.state.original, "before");
});
test("retains a rejected draft across reopening and exposes a Git warning", async () => {
  const local = storage();
  const draft = new MemoryDraft("/note", async (_url, init) => init
    ? { ok: false, json: async () => ({ error: "conflict" }) } : reply("before"), local);
  await draft.load();
  draft.edit("mine");
  await draft.flush();
  assert.equal(draft.state.error, "conflict");
  const reopened = new MemoryDraft("/note", async () => reply("elsewhere"), local);
  await reopened.load();
  assert.equal(reopened.state.content, "mine");
  assert.equal(reopened.state.base, "before");
  assert.match(reopened.state.error, /changed elsewhere/);
  const warned = new MemoryDraft("/other", async (_url, init) => reply(init ? "after" : "before", init ? { warning: "Git failed" } : {}), local);
  await warned.load();
  warned.edit("after"); await warned.flush();
  assert.equal(warned.state.warning, "Git failed");
});
test("a held file opens with the server's note on what keeps it out of memory", async () => {
  const draft = new MemoryDraft("/held", async () => reply("- uncited", { warning: "Not in the memory format" }), storage());
  await draft.load();
  assert.equal(draft.state.warning, "Not in the memory format");
});
test("a save that leaves only the title keeps it in the editor with the server's note", async () => {
  const calls = [];
  const draft = new MemoryDraft("/note", async (_url, init) => {
    if (!init) return reply("# Note\n\nA fact.[^e1]\n");
    const body = JSON.parse(init.body);
    calls.push(body);
    return body.content === "# Note\n"
      ? reply(body.content, { removed: true, warning: "topics/note.md has no cited paragraph" })
      : reply(body.content);
  }, storage());
  await draft.load();
  draft.edit("# Note\n");
  await draft.flush();
  assert.equal(draft.state.content, "# Note\n");
  assert.equal(draft.state.base, "# Note\n");
  assert.equal(draft.state.error, "");
  assert.equal(draft.state.removed, true);
  assert.match(draft.state.warning, /no cited paragraph/);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(calls.length, 1, "nothing left to save, so no second request");
  draft.edit("# Note\n\nA fact.[^e1]\n");
  await draft.flush();
  assert.equal(calls[1].base_content, "# Note\n");
  assert.equal(draft.state.removed, false);
  assert.equal(draft.state.warning, "");
});
test("a removed topic that is on the server again loads as an ordinary one", async () => {
  let saved = false;
  const draft = new MemoryDraft("/note", async (_url, init) => {
    if (init) { saved = true; return reply("# Note\n", { removed: true }); }
    return reply(saved ? "# Note\n\nA fact.[^e1]\n" : "# Note\n\nOld.[^e1]\n");
  }, storage());
  await draft.load();
  draft.edit("# Note\n");
  await draft.flush();
  assert.equal(draft.state.removed, true);
  // Another writer puts the topic back; opening it again reads that file.
  await draft.load();
  assert.equal(draft.state.content, "# Note\n\nA fact.[^e1]\n");
  assert.equal(draft.state.removed, false);
});
test("Retry reloads after a failed initial read", async () => {
  let reads = 0;
  const draft = new MemoryDraft("/note", async () => ++reads === 1
    ? { ok: false } : reply("recovered"), storage());
  await draft.load();
  assert.equal(draft.state.loaded, false);
  draft.retry();
  await draft.loading;
  assert.equal(reads, 2);
  assert.equal(draft.state.content, "recovered");
  assert.equal(draft.state.error, "");
});
test("a late read never replaces editing or a newer completed save", async () => {
  let reads = 0, resolve;
  const draft = new MemoryDraft("/note", async (_url, init) => {
    if (init) return reply(JSON.parse(init.body).content);
    if (++reads === 1) return reply("before");
    return new Promise(r => { resolve = r; });
  }, storage());
  await draft.load();
  const refresh = draft.load();
  draft.edit("new edit");
  await draft.flush();
  resolve(reply("stale read"));
  await refresh;
  assert.equal(draft.state.content, "new edit");
  assert.equal(draft.state.base, "new edit");
});
test("browser fetch is invoked with its global receiver", async () => {
  const draft = new MemoryDraft("/note", async function (_url, init) {
    assert.equal(this, globalThis);
    return reply(init ? "after" : "before");
  }, storage());
  await draft.load();
  assert.equal(draft.state.loaded, true);
  draft.edit("after"); await draft.flush();
  assert.equal(draft.state.base, "after");
});
test("undo and redo survive automatic file saves", async () => {
  const draft = new MemoryDraft("/note", async (_url, init) => reply(init ? JSON.parse(init.body).content : "before"), storage());
  await draft.load();
  draft.edit("one"); await draft.flush();
  draft.edit("two"); await draft.flush();
  draft.undo(); await draft.flush();
  assert.equal(draft.state.base, "one");
  draft.undo(); await draft.flush();
  assert.equal(draft.state.base, "before");
  draft.redo(); await draft.flush();
  assert.equal(draft.state.base, "one");
});

// MemorySettings: every change is saved the moment it is made (no Save bar),
// a failed save puts the old value back, controls lock while a save or an
// install is in flight, and neither the Memory status probe nor the writer
// model list holds the form back.
const { createElement: h } = await import("react");
const { MemorySettings, OPTIONAL_LOAD_WAIT_MS } = await import("../../components/settings/memory-settings.tsx");

const SETTINGS = {
  settings: [
    { key: "memory.backend", value: "local", apply: "next_start" },
    { key: "memory.writer.enabled", value: false, apply: "live" },
    { key: "memory.retrieval.method", value: "bm25", apply: "live" },
  ],
};
const ok = (body) => ({ ok: true, status: 200, json: async () => body });
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
const never = () => new Promise(() => {});
const pastOptionalWait = () => new Promise((resolve) => setTimeout(resolve, OPTIONAL_LOAD_WAIT_MS + 50));

function memoryFetch({ status = () => ok({ embedding_available: true }), save = (body) => ok({ value: body.value }), install } = {}) {
  const posts = [];
  globalThis.fetch = async (url, init = {}) => {
    if (url.startsWith("/api/memory/status")) return status();
    if (url === "/api/settings?scope=memory") return ok(SETTINGS);
    if (url === "/api/settings" && init.method === "POST") {
      const body = JSON.parse(init.body);
      posts.push(body);
      return save(body);
    }
    if (url === "/api/memory/embedding/install") return install();
    throw new Error(`unexpected fetch ${url}`);
  };
  return posts;
}

async function renderSettings() {
  const view = await render(h(MemorySettings));
  await view.act(settle);
  return view;
}
const control = (view, label) => view.container.querySelector(`[aria-label="${label}"]`);
const checked = (el) => el.getAttribute("aria-checked") === "true";

test("a change is POSTed immediately and there is no Save button", async () => {
  globalThis.memoryModels = async () => [];
  const posts = memoryFetch();
  const view = await renderSettings();
  assert.ok(![...view.container.querySelectorAll("button")].some((b) => /Save/.test(b.textContent)));
  assert.ok([...view.container.querySelectorAll('[role="switch"]')].every((el) => el.getAttribute("aria-label")), "every switch is named");
  assert.match(view.container.textContent, /Changing this switch requires restarting OpenProgram/);
  await view.act(async () => { control(view, "Automatic writing").click(); await settle(); });
  assert.deepEqual(posts, [{ key: "memory.writer.enabled", value: true }]);
  assert.equal(checked(control(view, "Automatic writing")), true);
  await view.unmount();
});

test("a failed save puts the previous value back and reports it", async () => {
  globalThis.memoryModels = async () => [];
  memoryFetch({ save: () => ({ ok: false, status: 500, json: async () => ({ error: "disk full" }) }) });
  const view = await renderSettings();
  await view.act(async () => { control(view, "Automatic writing").click(); await settle(); });
  assert.equal(checked(control(view, "Automatic writing")), false);
  assert.match(view.container.querySelector('[role="alert"]').textContent, /Save failed: Error: disk full/);
  await view.unmount();
});

test("controls stay disabled while a save is in flight", async () => {
  globalThis.memoryModels = async () => [];
  let finish;
  memoryFetch({ save: (body) => new Promise((resolve) => { finish = () => resolve(ok({ value: body.value })); }) });
  const view = await renderSettings();
  await view.act(() => control(view, "Automatic writing").click());
  for (const label of ["Enable Memory", "Automatic writing", "Writer model", "Recall method"]) {
    assert.equal(control(view, label).disabled, true, `${label} is locked while saving`);
  }
  await view.act(async () => { finish(); await settle(); });
  assert.equal(control(view, "Writer model").disabled, false);
  await view.unmount();
});

test("controls stay disabled while the embedding model installs", async () => {
  globalThis.memoryModels = async () => [];
  let finish;
  memoryFetch({
    status: () => ok({ embedding_available: false, embedding_packages: true }),
    install: () => new Promise((resolve) => { finish = () => resolve(ok({ embedding_available: true })); }),
  });
  const view = await renderSettings();
  const install = [...view.container.querySelectorAll("button")].find((b) => /^Install/.test(b.textContent));
  await view.act(() => install.click());
  assert.match(install.textContent, /Installing…/);
  assert.equal(install.disabled, true);
  assert.equal(control(view, "Recall method").disabled, true);
  await view.act(async () => { finish(); await settle(); });
  assert.equal(control(view, "Recall method").disabled, false);
  assert.match(view.container.textContent, /Available/);
  await view.unmount();
});

test("an unanswered Memory status request does not block the form", async () => {
  globalThis.memoryModels = async () => [];
  const posts = memoryFetch({ status: never });
  const view = await renderSettings();
  await view.act(pastOptionalWait);
  assert.match(view.container.textContent, /Checking…/);
  assert.equal(control(view, "Automatic writing").disabled, false);
  await view.act(async () => { control(view, "Automatic writing").click(); await settle(); });
  assert.equal(posts.length, 1);
  await view.unmount();
});

test("an unanswered writer-model list does not block the form", async () => {
  globalThis.memoryModels = never;
  const posts = memoryFetch();
  const view = await renderSettings();
  await view.act(pastOptionalWait);
  const writer = control(view, "Writer model");
  assert.equal(writer.disabled, false);
  assert.deepEqual([...writer.querySelectorAll("option")].map((o) => o.value), [""], "only Automatic until the models arrive");
  await view.act(async () => { control(view, "Automatic writing").click(); await settle(); });
  assert.equal(posts.length, 1);
  await view.unmount();
});
