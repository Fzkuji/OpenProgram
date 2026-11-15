// Settings → Browser: the clear-data switches need the desktop bridge and
// say so without it; clearing sends only the kinds of data chosen.
import assert from "node:assert/strict";
import { afterEach, describe, test } from "node:test";
import { installLoader, STUBS } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

const SWITCH = "import { createElement as h } from 'react'; export const Switch = ({ checked, onCheckedChange, disabled, size, className, title, ...rest }) => h('button', { type: 'button', role: 'switch', 'aria-checked': String(Boolean(checked)), disabled, onClick: () => onCheckedChange?.(!checked), ...rest });";

installLoader({
  stubs: { "@/lib/i18n": STUBS.i18n },
  replace: {
    // Radix Switch needs HTMLFormElement, which linkedom lacks; this keeps its role and ARIA contract.
    "/components/ui/switch.tsx": SWITCH,
    "/components/ui/checkbox.tsx": "export const Checkbox = () => null;",
    "/components/ui/confirm-dialog.tsx": "export const confirmAction = async () => true;",
    "/lib/desktop/desktop-bridge.ts": "export const desktopBridge = () => globalThis.browserBridge ?? null;",
    "/components/center-tabs/browser/browser-home-page.tsx": "export const BrowserImportDialog = () => null;",
  },
});
installDom();

const { createElement: h } = await import("react");
const { BrowserSettings } = await import("../../components/settings/sections/browser-settings.tsx");

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

afterEach(() => { document.body.innerHTML = ""; });

describe("browser settings", () => {
  test("without the desktop bridge the clear-data switches are off limits and say why", async () => {
    globalThis.browserBridge = null;
    const view = await render(h(BrowserSettings));
    for (const label of ["Browsing history", "Cookies"]) {
      const control = view.container.querySelector(`[aria-label="${label}"]`);
      assert.equal(control.getAttribute("role"), "switch");
      assert.equal(control.disabled, true);
    }
    assert.match(view.container.textContent, /available in the desktop app/);
    assert.match(view.container.textContent, /Bookmarks are not removed\./);
    await view.unmount();
  });

  test("clearing sends only the selected kinds of data and reports the result", async () => {
    const cleared = [];
    globalThis.browserBridge = { browserData: { clear: async (what) => { cleared.push(what); return { ok: true }; } } };
    const view = await render(h(BrowserSettings));
    await view.act(() => view.container.querySelector('[aria-label="Cookies"]').click());
    const clear = [...view.container.querySelectorAll("button")].find((b) => b.textContent === "Clear data");
    await view.act(async () => { clear.click(); await settle(); });
    assert.deepEqual(cleared, [{ history: true, cookies: false }]);
    assert.equal(view.container.querySelector('[role="status"]').textContent, "Selected browsing data was cleared.");
    globalThis.browserBridge = null;
    await view.unmount();
  });
});
