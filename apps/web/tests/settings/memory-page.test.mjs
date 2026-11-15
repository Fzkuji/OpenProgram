// The Memory page: the writer's failure is announced above the sections,
// Core shows the injected MEMORY.md with its token budget, Recent shows how
// many records the view keeps, and the Timeline opens on its newest month.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { installLoader } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

installLoader({
  stubs: {
    "next/link": "import { createElement } from 'react'; export default ({ href, children, prefetch, ...props }) => createElement('a', { href, ...props }, children);",
  },
  replace: {
    "/components/memory/document.tsx": [
      "import { createElement as h } from 'react';",
      "export const MemoryDocument = ({ title, meta, url, onSaved, onDelete, onRemovedChange }) => h('article', { 'data-document': url }, title, ' ', (meta ?? []).join(' '),",
      "  onSaved && h('button', { type: 'button', 'data-stub': 'saved', onClick: onSaved }, 'saved'),",
      "  onRemovedChange && h('button', { type: 'button', 'data-stub': 'removed', onClick: onRemovedChange }, 'removed'),",
      "  onDelete && h('button', { type: 'button', 'data-stub': 'delete', onClick: () => onDelete() }, 'delete'),",
      "  onDelete && h('button', { type: 'button', 'data-stub': 'delete-removed', onClick: () => onDelete({ removed: true }) }, 'delete removed'));",
    ].join("\n"),
    "/components/ui/confirm-dialog.tsx": "export const confirmAction = async () => true;",
    "/lib/format-utils/toast.ts": "export const showToast = () => {};",
    "/components/ui/toggle-group.tsx": [
      "import { createElement as h, createContext, useContext } from 'react';",
      "const Group = createContext(null);",
      "export const ToggleGroup = ({ value, onValueChange, children, type, variant, size, spacing, className, ...rest }) => h(Group.Provider, { value: { value, onValueChange } }, h('div', { role: 'group', ...rest }, children));",
      "export const ToggleGroupItem = ({ value, children }) => { const group = useContext(Group); return h('button', { type: 'button', 'aria-pressed': String(group.value === value), onClick: () => group.onValueChange(value) }, children); };",
    ].join("\n"),
  },
});
installDom();

const { createElement: h } = await import("react");
const { MemoryPage } = await import("../../components/memory/index.tsx");

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
const reply = (body, headers = {}) => ({ ok: true, status: 200, json: async () => body, headers: { get: (name) => headers[name] ?? null } });

function memoryFetch(overrides = {}) {
  const urls = [];
  const routes = {
    "/api/memory/status": () => reply({ writer: { last_outcome: "success", last_success_at: null, last_failure: null } }),
    "/api/memory/topics": () => reply([]),
    "/api/memory/timeline": () => reply([]),
    "/api/memory/recent": () => reply([]),
    "/api/memory/core": () => reply({ injected_content: "", injected_tokens: 0, budget_tokens: 2000, injection_enabled: true }),
    ...overrides,
  };
  globalThis.fetch = async (url, init) => {
    urls.push(init?.method && init.method !== "GET" ? `${init.method} ${url}` : url);
    const path = url.split("?")[0];
    const route = routes[path] ?? Object.entries(routes).find(([prefix]) => path.startsWith(`${prefix}/`))?.[1];
    if (!route) throw new Error(`unexpected fetch ${url}`);
    return route(url);
  };
  return urls;
}

async function renderPage() {
  const view = await render(h(MemoryPage, { embedded: true }));
  await view.act(settle);
  return view;
}
// A rail row's text also holds its trailing count, so match its label span.
const railButton = (view, label) =>
  [...view.container.querySelectorAll("button, a")].find((el) =>
    el.textContent.trim() === label || el.querySelector(".labelText")?.textContent.trim() === label);
async function openTab(view, label) {
  await view.act(async () => { railButton(view, label).click(); await settle(); });
}

afterEach(() => { document.body.innerHTML = ""; });

test("a failed background write is announced with its reason, and a healthy writer shows nothing", async () => {
  memoryFetch();
  let view = await renderPage();
  assert.equal(view.container.querySelector('[role="alert"]'), null);
  await view.unmount();

  memoryFetch({
    "/api/memory/status": () => reply({ writer: { last_outcome: "failure", last_success_at: null, pending_turns: 3, last_failure: { at: "x", reason_code: "MODEL_TIMEOUT", retryable: true } } }),
  });
  view = await renderPage();
  const banner = view.container.querySelector('[role="alert"]');
  assert.match(banner.textContent, /Conversations are not reaching memory: the writer model timed out; retrying on its own/);
  assert.match(banner.textContent, /nothing written yet · 3 turns waiting/);
  await view.unmount();
});

test("Topic files that are not text are named in one row", async () => {
  memoryFetch({
    "/api/memory/status": () => reply({ writer: { last_outcome: "success", last_success_at: null, last_failure: null, not_text: ["topics/career/a.md", "topics/career/b.md", "topics/career/c.md", "topics/career/d.md"] } }),
  });
  const view = await renderPage();
  const rows = [...view.container.querySelectorAll('[role="alert"]')];
  assert.equal(rows.length, 1);
  assert.match(rows[0].textContent, /4 topic files are not text and stay out of memory/);
  assert.match(rows[0].textContent, /career\/a\.md, career\/b\.md, career\/c\.md and 1 more/);
  await view.unmount();
});

test("writing paused for every conversation says why in its own row", async () => {
  memoryFetch({
    "/api/memory/status": () => reply({ writer: { last_outcome: "success", last_success_at: null, last_failure: null, paused: { scope: "credential", reason_code: "MODEL_AUTHENTICATION", reason: "401" } } }),
  });
  const view = await renderPage();
  const rows = [...view.container.querySelectorAll('[role="alert"]')];
  assert.equal(rows.length, 1);
  assert.match(rows[0].textContent, /Writing paused: the writer model's sign-in is no longer valid/);
  await view.unmount();
});

test("the not-text row follows a save or delete of a topic in the editor", async () => {
  for (const action of ["saved", "delete"]) {
    let unreadable = ["topics/career/a.md"];
    memoryFetch({
      "/api/memory/status": () => reply({ writer: { last_outcome: "success", last_success_at: null, last_failure: null, not_text: unreadable } }),
      "/api/memory/topics": () => reply([{ path: "career/a.md", title: "Applications", type: "", size: 1, mtime: 0 }]),
    });
    const view = await renderPage();
    assert.match(view.container.querySelector('[role="alert"]').textContent, /career\/a\.md/);
    await view.act(async () => { railButton(view, "Applications").click(); await settle(); });

    // The person replaces (or deletes) the file; it is text again.
    unreadable = [];
    const control = view.container.querySelector(`[data-stub="${action}"]`);
    assert.ok(control, `the page hears when a ${action} lands`);
    await view.act(async () => { control.click(); await settle(); await settle(); });

    assert.equal(view.container.querySelector('[role="alert"]'), null, action);
    await view.unmount();
  }
});

test("a save that removes a title-only topic, or brings it back, refreshes the topic list", async () => {
  let pages = [{ path: "career/a.md", title: "Applications", type: "", size: 1, mtime: 0 }];
  const urls = memoryFetch({ "/api/memory/topics": () => reply(pages) });
  const view = await renderPage();
  await view.act(async () => { railButton(view, "Applications").click(); await settle(); });
  const listed = () => urls.filter((url) => url.split("?")[0] === "/api/memory/topics").length;
  const before = listed();

  // The server removed the file; the editor stays open on what was typed.
  pages = [];
  await view.act(async () => { view.container.querySelector('[data-stub="removed"]').click(); await settle(); });

  assert.equal(listed(), before + 1);
  assert.equal(railButton(view, "Applications"), undefined);
  assert.ok(view.container.querySelector("[data-document]"));
  await view.unmount();
});

test("deleting a topic its save already removed closes it without asking the server", async () => {
  let pages = [{ path: "career/a.md", title: "Applications", type: "", size: 1, mtime: 0 }];
  const urls = memoryFetch({ "/api/memory/topics": () => reply(pages) });
  const view = await renderPage();
  await view.act(async () => { railButton(view, "Applications").click(); await settle(); });
  pages = [];

  // The file is already gone; a DELETE would answer 404 and leave the editor open.
  await view.act(async () => { view.container.querySelector('[data-stub="delete-removed"]').click(); await settle(); });

  assert.deepEqual(urls.filter((url) => url.startsWith("DELETE ")), []);
  assert.equal(view.container.querySelector("[data-document]"), null);
  await view.unmount();
});

test("an empty personal memory points to Memory settings", async () => {
  memoryFetch();
  const view = await renderPage();
  assert.match(view.container.textContent, /No topics yet/);
  assert.equal(railButton(view, "Open Memory settings").getAttribute("href"), "/settings/memory");
  await view.unmount();
});

test("Core shows the injected MEMORY.md with its token budget, and Source records opens core.md", async () => {
  memoryFetch({
    "/api/memory/core": () => reply({ injected_content: "# Core\n\nAlways answer in English.", injected_tokens: 2500, budget_tokens: 2000, injection_enabled: false, rendered_mtime: 1, mtime: 1, size: 10 }),
  });
  const view = await renderPage();
  await openTab(view, "Core");
  const meter = view.container.querySelector('[role="progressbar"]');
  assert.equal(meter.getAttribute("aria-valuenow"), "2500");
  assert.equal(meter.getAttribute("aria-valuemax"), "2000");
  assert.match(view.container.textContent, /Injection disabled/);
  assert.match(view.container.textContent, /Always answer in English\./);
  assert.equal(railButton(view, "MEMORY.md as injected").getAttribute("aria-pressed"), "true");

  await view.act(() => railButton(view, "Source records").click());
  const document_ = view.container.querySelector("[data-document]");
  assert.equal(document_.getAttribute("data-document"), "/api/memory/core");
  assert.match(document_.textContent, /topics\/core\.md/);
  await view.unmount();
});

test("Core with nothing injected says so", async () => {
  memoryFetch();
  const view = await renderPage();
  await openTab(view, "Core");
  assert.match(view.container.textContent, /No Core memory is currently injected/);
  assert.match(view.container.textContent, /Injection enabled/);
  await view.unmount();
});

test("Recent counts its records against the limit the server reports", async () => {
  memoryFetch({
    "/api/memory/recent": () => reply([
      { memory_id: "a", when: "today", topic_path: "people/ana.md", content: "Ana prefers mornings." },
      { memory_id: "b", when: "today", topic_path: "", content: "Ship on Friday." },
    ], { "X-Memory-Recent-Limit": "25" }),
  });
  const view = await renderPage();
  await openTab(view, "Recent");
  assert.match(view.container.textContent, /2 \/ 25 records/);
  assert.match(view.container.textContent, /today · people\/ana\.md/);
  assert.match(view.container.textContent, /Ship on Friday\./);
  await view.unmount();
});

test("the Timeline opens on the newest year and month, and a day loads its record", async () => {
  const urls = memoryFetch({
    "/api/memory/timeline": (url) => url === "/api/memory/timeline"
      ? reply([
        { date: "2026-08-15", size: 30, mtime: 3 },
        { date: "2026-07-02", size: 20, mtime: 2 },
        { date: "2025-12-31", size: 10, mtime: 1 },
      ])
      : reply({ content: "Met Ana." }),
  });
  const view = await renderPage();
  await openTab(view, "Timeline");
  const expanded = (label) => railButton(view, label)?.getAttribute("aria-expanded");
  assert.equal(expanded("2026"), "true");
  assert.equal(expanded("August"), "true");
  assert.equal(expanded("July"), "false");
  assert.equal(expanded("2025"), "false");
  assert.equal(railButton(view, "December"), undefined, "a closed year hides its months");

  const day = [...view.container.querySelectorAll("button")].find((el) => el.querySelector(".labelText")?.textContent.startsWith("15 "));
  await view.act(async () => { day.click(); await settle(); });
  assert.equal(urls.at(-1), "/api/memory/timeline/2026-08-15");
  assert.match(view.container.textContent, /Met Ana\./);
  await view.unmount();
});
