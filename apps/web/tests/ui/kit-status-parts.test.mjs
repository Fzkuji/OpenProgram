// The kit's status parts: a Badge in the success / warning / accent tones
// with a leading dot, the Progress bar every quota row draws, the CopyButton
// that turns to a check once the value is on the clipboard, and the
// SecretInput that masks a key until its button shows it.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installLoader, STUBS } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

let copied = null;
installLoader({
  stubs: { "@/lib/i18n": STUBS.i18n },
  replace: {
    "/lib/clipboard.ts": "export const copyText = async (value) => { globalThis.__copied(value); return true; };",
    "/components/ui/tooltip.tsx": "export const HoverTip = ({ children }) => children;",
  },
});
installDom();
globalThis.__copied = (value) => { copied = value; };

const { createElement: h } = await import("react");
const { Badge } = await import("../../components/ui/badge.tsx");
const { Progress } = await import("../../components/ui/progress.tsx");
const { CopyButton } = await import("../../components/ui/copy-button.tsx");
const { SecretInput } = await import("../../components/ui/secret-input.tsx");

test("a status badge has its tone and a leading dot; a plain one has none", async () => {
  const view = await render(h("div", null,
    h(Badge, { variant: "success", dot: true }, "Ready"),
    h(Badge, { variant: "warning", dot: true }, "Back at 14:32"),
    h(Badge, { variant: "accent" }, "In use"),
  ));
  const [ready, cooling, inUse] = view.container.querySelectorAll('[data-slot="badge"]');
  assert.equal(ready.dataset.variant, "success");
  assert.match(ready.className, /text-\(--success-soft\)/);
  assert.ok(ready.querySelector('[data-slot="badge-dot"]'));
  assert.equal(ready.textContent, "Ready");
  assert.match(cooling.className, /text-\(--warning-soft\)/);
  assert.equal(inUse.querySelector('[data-slot="badge-dot"]'), null);
  await view.unmount();
});

test("progress clamps its value, colours its fill by tone and dims a stale reading", async () => {
  const view = await render(h("div", null,
    h(Progress, { value: 62 }),
    h(Progress, { value: 140, tone: "danger" }),
    h(Progress, { value: -5, tone: "warning", stale: true }),
  ));
  const [normal, spent, stale] = view.container.querySelectorAll('[role="progressbar"]');
  assert.equal(normal.getAttribute("aria-valuenow"), "62");
  assert.equal(normal.querySelector('[data-slot="progress-indicator"]').style.transform, "translateX(-38%)");
  assert.equal(spent.getAttribute("aria-valuenow"), "100");
  assert.match(spent.querySelector('[data-slot="progress-indicator"]').className, /bg-\(--danger-soft\)/);
  assert.match(spent.className, /--danger-soft/, "a spent bar keeps a danger-tinted track");
  assert.equal(stale.getAttribute("aria-valuenow"), "0");
  assert.match(stale.querySelector('[data-slot="progress-indicator"]').className, /opacity-50/);
  await view.unmount();
});

test("the copy button copies its value and says so", async () => {
  const view = await render(h(CopyButton, { value: "openprogram login", label: "Copy command" }));
  const button = view.container.querySelector("button");
  assert.equal(button.getAttribute("aria-label"), "Copy command");
  await view.act(async () => { button.click(); await Promise.resolve(); });
  assert.equal(copied, "openprogram login");
  assert.equal(button.getAttribute("aria-label"), "Copied");
  assert.equal(button.dataset.copied, "true");
  await view.unmount();
});

test("a secret input is masked until its button shows it", async () => {
  const view = await render(h(SecretInput, { id: "k", defaultValue: "sk-test" }));
  const input = view.container.querySelector("#k");
  const toggle = view.container.querySelector("button");
  assert.equal(input.getAttribute("type"), "password");
  assert.equal(toggle.getAttribute("aria-pressed"), "false");
  await view.act(() => toggle.click());
  assert.equal(input.getAttribute("type"), "text");
  assert.equal(toggle.getAttribute("aria-label"), "Hide");
  await view.unmount();
});

test("a masked key of eight characters or more shows its last four; a short or shown one does not", async () => {
  const view = await render(h("div", null,
    h(SecretInput, { id: "long", value: "sk-test-0000c41e", onChange() {} }),
    h(SecretInput, { id: "short", value: "sk-1", onChange() {} }),
  ));
  const tail = (id) => view.container.querySelector(`#${id}`).parentElement.querySelector('[data-slot="secret-input-tail"]');
  assert.equal(tail("long").textContent, "c41e");
  assert.equal(tail("long").getAttribute("aria-hidden"), "true");
  assert.equal(tail("short"), null);
  await view.act(() => view.container.querySelector("#long").parentElement.querySelector("button").click());
  assert.equal(tail("long"), null, "a shown key needs no tail");
  await view.unmount();
});
