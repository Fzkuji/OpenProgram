// The Tasks page's Scheduled tab: a failed load says nothing and is read again quietly, an
// empty list offers suggestions that prefill the create form, and every
// row action names its task.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { installLoader, STUBS } from "../../_support/load.mjs";
import { installDom, render } from "../../_support/dom.mjs";

installLoader({
  stubs: {
    "next/dynamic": "export default () => () => null;",
    "next/link": "import { createElement } from 'react'; export default ({ href, children, prefetch, ...props }) => createElement('a', { href, ...props }, children);",
    "@/lib/i18n": STUBS.i18n,
  },
  replace: {
    "/components/ui/dialog.tsx": "export const Dialog = ({ open, children }) => (open ? children : null); export const DialogContent = ({ children }) => children; export const DialogHeader = DialogContent; export const DialogTitle = DialogContent; export const DialogDescription = DialogContent; export const DialogFooter = DialogContent;",
    "/components/ui/select.tsx": [
      "import { createElement as h } from 'react';",
      "export const SimpleSelect = ({ label, value, options, onValueChange }) => h('select', { 'aria-label': label, value, onChange: (e) => onValueChange(e.target.value) }, options.map((o) => h('option', { key: o.value, value: o.value }, o.label)));",
    ].join("\n"),
  },
});
installDom();

const { createElement: h } = await import("react");
const { ScheduledTasks } = await import("../../../components/scheduler/scheduler-page.tsx");
const { ManagePageHeader } = await import("../../../components/ui/manage-page.tsx");
// The Tasks page hands the tab its header; the tab adds its toolbar and actions.
const header = (parts) => h(ManagePageHeader, { title: "Tasks", ...parts });
const SchedulerPage = () => h(ScheduledTasks, { header });
const { forgetKept } = await import("../../../lib/abilities/kept-read.ts");

const json = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });

function stubFetch(routes) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push([url, init.method ?? "GET"]);
    for (const [prefix, answer] of routes) if (url.startsWith(prefix)) return answer(url, init);
    return json(404, {});
  };
  return calls;
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
const button = (container, name) =>
  [...container.querySelectorAll("button")].find((el) => el.getAttribute("aria-label") === name || el.textContent.trim() === name);

afterEach(() => { document.body.innerHTML = ""; forgetKept(); });

test("a failed task load shows no notice and the list arrives on a quiet retry", async () => {
  let fail = true;
  stubFetch([
    ["/api/scheduler/tasks", () => (fail ? json(500, { error: "boom" }) : json(200, [{ id: "1", title: "Daily brief", type: "recurring", enabled: true, prompt: "p", cron: "0 8 * * 1-5", memory_refs: [] }]))],
    ["/api/memory/refs", () => json(200, [])],
  ]);
  const view = await render(h(SchedulerPage));
  await view.act(settle);
  assert.equal(view.container.querySelector('[role="alert"]'), null);
  assert.doesNotMatch(view.container.textContent, /Loading|unavailable|Could not load/);
  assert.equal(button(view.container, "Retry"), undefined);
  assert.equal(button(view.container, "New task").disabled, true, "nothing can be created before the list has loaded");

  fail = false;
  for (let i = 0; i < 40 && !/Daily brief/.test(view.container.textContent); i += 1) {
    await view.act(() => new Promise((resolve) => setTimeout(resolve, 50)));
  }
  assert.equal(view.container.querySelector('[role="alert"]'), null);
  assert.match(view.container.textContent, /Daily brief/);
  assert.equal(button(view.container, "New task").disabled, false);
  await view.unmount();
});

test("a second visit draws the kept list before any read answers", async () => {
  stubFetch([
    ["/api/scheduler/tasks", () => json(200, [{ id: "1", title: "Daily brief", type: "recurring", enabled: true, prompt: "p", cron: "0 8 * * 1-5", memory_refs: [] }])],
    ["/api/memory/refs", () => json(200, [])],
  ]);
  const first = await render(h(SchedulerPage));
  await first.act(settle);
  await first.unmount();
  const held = [];
  globalThis.fetch = () => new Promise((resolve) => held.push(() => resolve(json(200, []))));
  const second = await render(h(SchedulerPage));
  assert.match(second.container.textContent, /Daily brief/);
  // Answer the held reads so the next test does not join them.
  await second.act(async () => { held.forEach((answer) => answer()); await settle(); });
  await second.unmount();
});

test("an empty list offers three suggestions whose Use prefills the create form", async () => {
  stubFetch([
    ["/api/scheduler/tasks", () => json(200, [])],
    ["/api/memory/refs", () => json(200, [])],
  ]);
  const view = await render(h(SchedulerPage));
  await view.act(settle);
  const uses = [...view.container.querySelectorAll("button")].filter((el) => /^Use /.test(el.getAttribute("aria-label") ?? ""));
  assert.deepEqual(uses.map((el) => el.getAttribute("aria-label")), ["Use Daily brief", "Use Weekly review", "Use Follow-up monitor"]);

  await view.act(() => uses[2].click());
  assert.match(view.container.textContent, /Create scheduled task/);
  const type = view.container.querySelector('select[aria-label="Type"]');
  assert.deepEqual([...type.querySelectorAll("option")].map((o) => o.value), ["once", "recurring", "monitor"], "the form creates the three task kinds");
  assert.equal(type.value, "monitor");
  const inputs = [...view.container.querySelectorAll("input")].map((el) => el.value);
  assert.ok(inputs.includes("Follow-up monitor"));
  assert.ok(inputs.includes("0 9 * * 1-5"));
  await view.unmount();
});

test("a failed create keeps the form open and shows the server's message", async () => {
  stubFetch([
    ["/api/scheduler/tasks", (_url, init) => (init.method === "POST" ? json(400, { error: { message: "cron is invalid" } }) : json(200, []))],
    ["/api/memory/refs", () => json(200, [])],
  ]);
  const view = await render(h(SchedulerPage));
  await view.act(settle);
  await view.act(() => button(view.container, "Use Daily brief").click());
  await view.act(async () => { button(view.container, "Create").click(); await settle(); });
  assert.equal(view.container.querySelector('[role="alert"]')?.textContent, "cron is invalid");
  assert.match(view.container.textContent, /Create scheduled task/);
  await view.unmount();
});

test("row actions carry the task's name and a pause PATCHes enabled", async () => {
  const calls = stubFetch([
    ["/api/scheduler/tasks/1", (_url, init) => json(200, { id: "1", title: "Daily brief", type: "recurring", enabled: JSON.parse(init.body).enabled, cron: "0 8 * * 1-5", memory_refs: [] })],
    ["/api/scheduler/tasks", () => json(200, [{ id: "1", title: "Daily brief", type: "recurring", enabled: true, prompt: "p", cron: "0 8 * * 1-5", memory_refs: [] }])],
    ["/api/memory/refs", () => json(200, [])],
  ]);
  const view = await render(h(SchedulerPage));
  await view.act(settle);
  assert.ok(button(view.container, "Delete Daily brief"));
  await view.act(async () => { button(view.container, "Pause Daily brief").click(); await settle(); });
  assert.deepEqual(calls.at(-1), ["/api/scheduler/tasks/1", "PATCH"]);
  assert.ok(button(view.container, "Resume Daily brief"), "the row flips to Resume once paused");
  await view.unmount();
});

test("the List and Calendar switch sits at the end of the type row, and the tab count follows the list", async () => {
  stubFetch([
    ["/api/scheduler/tasks", () => json(200, [])],
    ["/api/memory/refs", () => json(200, [])],
  ]);
  const counts = [];
  const view = await render(h(ScheduledTasks, { header, onCountChange: (n) => counts.push(n) }));
  await view.act(settle);
  const switcher = view.container.querySelector('[aria-label="Scheduled tasks view"]');
  const types = view.container.querySelector('[role="tablist"][aria-label="Task types"]');
  assert.ok(switcher && types && types.parentElement.contains(switcher), "the switch shares the type row");
  await view.act(() => button(view.container, "Calendar").click());
  assert.match(view.container.textContent, /No tasks yet\. Click a day/);
  assert.equal(counts.at(-1), 0);
  await view.unmount();
});
