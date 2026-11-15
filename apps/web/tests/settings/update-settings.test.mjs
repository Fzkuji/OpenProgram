import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { installLoader, STUBS } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

installLoader({
  stubs: {
    "@/lib/i18n": STUBS.i18n,
    "@/components/solar-icons": "export const SolarIcon = () => null;",
    "@/components/avatar": "export const Avatar = () => null; export const AvatarPicker = () => null;",
  },
  // By path: the section reaches it through use-desktop-bridge's relative import.
  replace: { "/lib/desktop/desktop-bridge.ts": "export const desktopBridge = () => globalThis.__bridge ?? null;" },
});
const window = installDom();
// The Switch looks for an enclosing form.
globalThis.HTMLFormElement = window.HTMLFormElement;

const { createElement } = await import("react");
const { ApplicationSection } = await import("@/components/settings/sections/general-section");

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function updateState(patch = {}) {
  return {
    status: "idle", currentVersion: "1.4.0", automaticChecks: true, checkedAt: null,
    release: null, progress: null, error: null, ...patch,
  };
}

/** A desktop bridge whose `updates` records calls and lets the test push state. */
function installBridge(initial, overrides = {}) {
  const calls = [];
  let push = () => {};
  const updates = {
    getState: async () => initial,
    onState: (cb) => { push = cb; return () => { push = () => {}; }; },
    check: async () => { calls.push("check"); return null; },
    download: async () => { calls.push("download"); return null; },
    openRelease: async () => { calls.push("openRelease"); return null; },
    setAutomaticChecks: async (on) => { calls.push(["automatic", on]); return null; },
    ...overrides,
  };
  globalThis.__bridge = { updates };
  return { calls, push: (state) => push(state) };
}

async function mount() {
  const view = await render(createElement(ApplicationSection));
  await view.act(flush);
  return view;
}
const status = (root) => root.querySelector("[aria-atomic=true]");
const button = (root, label) => [...root.querySelectorAll("button")].find((el) => el.textContent === label);

afterEach(() => {
  delete globalThis.__bridge;
  delete globalThis.fetch;
});

test("an idle status is a polite live status line with the version from the bridge", async () => {
  installBridge(updateState());
  const view = await mount();
  const line = status(view.container);
  assert.equal(line.getAttribute("role"), "status");
  assert.equal(line.getAttribute("aria-live"), "polite");
  assert.equal(line.textContent, "Not checked");
  assert.ok(view.container.textContent.includes("1.4.0"));
  assert.equal(view.container.textContent.includes("openprogram upgrade"), false);
  await view.unmount();
});

test("a download in progress is a progressbar with its byte counts, not a live region", async () => {
  installBridge(updateState({ status: "downloading", progress: { downloaded: 50, total: 200 } }));
  const view = await mount();
  const line = status(view.container);
  assert.equal(line.getAttribute("role"), "progressbar");
  assert.equal(line.hasAttribute("aria-live"), false);
  assert.equal(line.getAttribute("aria-valuemin"), "0");
  assert.equal(line.getAttribute("aria-valuemax"), "200");
  assert.equal(line.getAttribute("aria-valuenow"), "50");
  assert.match(line.textContent, /\(25%\)/);
  assert.equal(button(view.container, "Check now").disabled, true, "no new check while downloading");
  await view.unmount();
});

test("a failed check says so in words and shows the error text in the row", async () => {
  const bridge = installBridge(updateState());
  const view = await mount();
  await view.act(() => bridge.push(updateState({ status: "error", error: "ENOTFOUND github.com" })));
  const line = status(view.container);
  assert.equal(line.textContent, "Update check failed");
  assert.ok(view.container.textContent.includes("ENOTFOUND github.com"));
  assert.equal(line.hasAttribute("title"), false);
  await view.unmount();
});

test("an action that throws reports Update check failed with the message in the row", async () => {
  installBridge(updateState(), { check: async () => { throw new Error("offline"); } });
  const view = await mount();
  await view.act(async () => { button(view.container, "Check now").click(); await flush(); });
  const line = status(view.container);
  assert.equal(line.textContent, "Update check failed");
  assert.ok(view.container.textContent.includes("offline"));
  await view.unmount();
});

test("an available release shows its date and offers download and the release page", async () => {
  const bridge = installBridge(updateState({
    status: "available",
    release: { status: "available", latestVersion: "1.5.0", publishedAt: "2026-10-01T00:00:00Z", releaseNotes: "Faster." },
  }));
  const view = await mount();
  assert.equal(status(view.container).textContent, "OpenProgram 1.5.0 is available");
  assert.ok(view.container.textContent.includes("Published"));
  assert.ok(view.container.textContent.includes("Faster."));
  await view.act(async () => {
    button(view.container, "Check now").click();
    button(view.container, "Download and open installer").click();
    button(view.container, "View release").click();
    await flush();
  });
  assert.deepEqual(bridge.calls, ["check", "download", "openRelease"]);
  await view.unmount();
});

test("without the desktop bridge the section shows the CLI upgrade commands and the server's version", async () => {
  const requests = [];
  globalThis.fetch = async (url) => {
    requests.push(String(url));
    return { ok: true, json: async () => ({ currentVersion: "1.3.2", installType: "source_checkout" }) };
  };
  const view = await mount();
  assert.deepEqual(requests, ["/api/system/version"]);
  const codes = [...view.container.querySelectorAll("code")].map((code) => code.textContent);
  assert.deepEqual(codes, ["openprogram upgrade --check", "openprogram upgrade"]);
  assert.ok(view.container.textContent.includes("1.3.2"));
  assert.ok(view.container.textContent.includes("Source checkout"));
  assert.equal(status(view.container), null, "no update status without the bridge");
  await view.unmount();
});
