import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { installLoader, STUBS } from "../../_support/load.mjs";
import { installDom, render } from "../../_support/dom.mjs";

installLoader({
  stubs: {
    "@/lib/i18n": STUBS.i18n,
    "@/components/solar-icons": "export const SolarIcon = () => null;",
  },
  replace: { "/lib/desktop/desktop-bridge.ts": "export const desktopBridge = () => globalThis.__bridge ?? null;" },
});
installDom();

const { createElement } = await import("react");
const { UpdateBadge } = await import("@/components/sidebar/update-badge");

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function installBridge(state) {
  globalThis.__bridge = {
    updates: {
      getState: async () => state,
      onState: () => () => {},
      install: async () => null,
      openRelease: async () => null,
    },
  };
}

afterEach(() => { delete globalThis.__bridge; });

test("a prepared update renders the badge as one button beside the wordmark", async () => {
  installBridge({ status: "ready", currentVersion: "1.4.0", readyVersion: "1.5.0", installsInPlace: true });
  const view = await render(createElement(UpdateBadge));
  await view.act(flush);
  const buttons = view.container.querySelectorAll("button");
  assert.equal(buttons.length, 1);
  assert.equal(buttons[0].textContent, "New");
  assert.ok(buttons[0].className.includes("uppercase"));
  assert.equal(buttons[0].getAttribute("aria-label"), "OpenProgram 1.5.0 is ready");
  await view.unmount();
});

test("nothing shows before an update is prepared", async () => {
  installBridge({ status: "downloading", currentVersion: "1.4.0", readyVersion: null });
  const view = await render(createElement(UpdateBadge));
  await view.act(flush);
  assert.equal(view.container.querySelector("button"), null);
  await view.unmount();
});
