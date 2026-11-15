/**
 * The composer's thinking-effort control in ControlsCluster: the fire glyph
 * carries the level's colour as --effort-color (composer.module.css paints
 * it only in the compact row), max effort is purple, Fast turns the fire
 * red, and the trigger reports its open state through aria-expanded.
 * The session badges beside it are stubbed.
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { installLoader } from "../../_support/load.mjs";
import { installDom, render } from "../../_support/dom.mjs";

installLoader({
  stubs: {
    "../../top-bar": "export const AgentBadge = () => null; export const AgentConfigBadge = () => null; export const PermissionBadge = () => null;",
    "../../badges/context-badge": "export const ContextBadge = () => null;",
  },
});
installDom();
// The open effort card holds a radix slider, which checks for an enclosing form.
globalThis.HTMLFormElement ??= window.HTMLFormElement ?? class HTMLFormElement extends window.HTMLElement {};

const { createElement, createRef, useState } = await import("react");
const { ControlsCluster } = await import("../../../components/chat/composer/controls/controls-cluster.tsx");
const { effortLevelColor } = await import("../../../lib/effort/effort-color.ts");

const options = [{ value: "low" }, { value: "medium" }, { value: "high" }, { value: "max" }];

async function mount({ thinking = "medium", fastEnabled = false } = {}) {
  function Harness() {
    const [open, setOpen] = useState(false);
    return createElement(ControlsCluster, {
      bound: null, plusMenuOpen: false, setPlusMenuOpen() {}, onPickImages() {},
      pendingImagesCount: 0, pendingDocsCount: 0,
      webSearchEnabled: false, toggleWebSearch() {},
      fastEnabled, fastSupported: true, fastHint: "", toggleFast() {},
      unattended: false, toggleUnattended() {},
      sandboxEnabled: false, sandboxAvailable: false, sandboxReason: null, toggleSandbox() {},
      chatAgent: {}, chatModel: "model", noEnabledModels: false,
      thinking, thinkingOptions: options, setThinking() {},
      thinkingMenuOpen: open, setThinkingMenuOpen: setOpen,
      thinkingTriggerRef: createRef(),
    });
  }
  const view = await render(createElement(Harness));
  const trigger = view.container.querySelector("button.effortText");
  const fire = trigger.querySelector(".effortIcon");
  return { view, trigger, fire };
}

describe("thinking effort control", () => {
  test("the fire glyph carries the level's colour for the compact row", async () => {
    const { view, trigger, fire } = await mount({ thinking: "high" });
    assert.equal(fire.style.getPropertyValue("--effort-color"), effortLevelColor(options, "high"));
    assert.equal(trigger.style.color, "", "the wide row keeps the text colour");
    await view.unmount();
  });

  test("max effort is purple on the label and the fire", async () => {
    const { view, trigger, fire } = await mount({ thinking: "max" });
    assert.equal(fire.style.getPropertyValue("--effort-color"), "#8E6BD9");
    assert.match(trigger.getAttribute("style") ?? "", /#8E6BD9/i);
    await view.unmount();
  });

  test("Fast turns the fire red", async () => {
    const { view, fire } = await mount({ fastEnabled: true });
    assert.equal(fire.style.color, "var(--accent-red)");
    await view.unmount();
  });

  test("the effort trigger reports its open state through aria-expanded", async () => {
    const { view, trigger } = await mount();
    assert.equal(trigger.getAttribute("aria-expanded"), "false");
    assert.equal(trigger.parentElement.getAttribute("aria-expanded"), "false", "the tooltip's trigger wrapper too");
    await view.act(() => trigger.click());
    assert.equal(trigger.getAttribute("aria-expanded"), "true");
    assert.equal(trigger.parentElement.getAttribute("aria-expanded"), "true");
    await view.unmount();
  });
});
