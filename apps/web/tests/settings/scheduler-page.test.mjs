// The Scheduler page: a failed load is announced and leaves a Retry, an
// empty list offers suggestions that prefill the create form, and every
// row action names its task.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { installLoader, STUBS } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

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
const { SchedulerPage } = await import("../../components/scheduler/scheduler-page.tsx");

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

afterEach(() => { document.body.innerHTML = ""; });

test("a failed task load shows an alert and a Retry that loads the list", async () => {
  let fail = true;
  stubFetch([
    ["/api/scheduler/tasks", () => (fail ? json(500, { error: "boom" }) : json(200, [{ id: "1", title: "Daily brief", type: "recurring", enabled: true, prompt: "p", cron: "0 8 * * 1-5", memory_refs: [] }]))],
    ["/api/memory/refs", () => json(200, [])],
  ]);
  const view = await render(h(SchedulerPage));
  await view.act(settle);
  assert.equal(view.container.querySelector('[role="alert"]')?.textContent, "Could not load scheduled tasks");
  assert.match(view.container.textContent, /Scheduled tasks are unavailable/);
  assert.equal(button(view.container, "New task").disabled, true, "nothing can be created before the list has loaded");

  fail = false;
  await view.act(async () => { button(view.container, "Retry").click(); await settle(); });
  assert.equal(view.container.querySelector('[role="alert"]'), null);
  assert.match(view.container.textContent, /Daily brief/);
  assert.equal(button(view.container, "New task").disabled, false);
  await view.unmount();
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
