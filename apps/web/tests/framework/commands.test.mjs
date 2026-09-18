import test, { after } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
const root = dirname(fileURLToPath(new URL("../../package.json", import.meta.url)));
const dir = await mkdtemp(join(root, ".framework-test-"));
after(() => rm(dir, { recursive: true, force: true }));
await build({ stdin: { contents: `export {executeInterface, definitions} from './lib/framework/commands'; export {useCenterTabs} from './lib/tabs/center-tabs-store';`, resolveDir: root },
  bundle: true, format: "esm", platform: "node", packages: "external", outfile: join(dir, "entry.mjs"), tsconfig: join(root, "tsconfig.json"),
  loader: { ".css": "empty" },
});
const { executeInterface, definitions, useCenterTabs } = await import(pathToFileURL(join(dir, "entry.mjs")));
test("declared tab commands use the existing state and preserve unsaved files", async () => {
  useCenterTabs.setState({ tabs: [{ id: "file", kind: "file", title: "Draft", dirty: true }], activeId: "file", groups: [] });
  assert.equal((await executeInterface("tabs.list", [])).tabs[0].title, "Draft");
  await assert.rejects(executeInterface("tabs.closeTab", ["file"]), /unsaved/);
  await assert.rejects(executeInterface("tabs.setState", [{}]), /unsupported/);
  useCenterTabs.setState({ tabs: [{ id: "file", kind: "file", title: "Saved", dirty: false }] });
  await executeInterface("tabs.closeTab", ["file"]);
  assert.equal(useCenterTabs.getState().tabs.length, 0);
});
test("only declared native methods can be invoked and absence is explicit", async () => {
  await assert.rejects(executeInterface("native.history.list", []), /native_desktop_unavailable/);
  assert.ok(definitions["native.downloads.cancel"]);
  assert.equal(definitions["native.refreshOwnerAuth"], undefined);
  assert.equal(definitions["native.terminal.write"], undefined);
});
test("profile operations update the same preferences used by General settings", async () => {
  const saved = new Map();
  globalThis.window = { localStorage: { setItem: (key, value) => saved.set(key, value), getItem: key => saved.get(key) ?? null }, dispatchEvent() {} };
  try {
    const profile = { name: "Operator", initial: "OP", color: "#123456" };
    await executeInterface("preferences.setUserProfile", [profile]);
    await executeInterface("preferences.setAgentProfile", [{ ...profile, name: "Assistant" }]);
    assert.equal(JSON.parse(saved.get("user_profile")).name, "Operator");
    assert.equal(JSON.parse(saved.get("agent_profile")).name, "Assistant");
  } finally { delete globalThis.window; }
});
test("framework view commands cannot operate a foreign private Page as human input", async () => {
  const calls = [];
  globalThis.window = { openprogramDesktop: { windowId: "main", webTab: { reload: id => calls.push(id) } } };
  useCenterTabs.setState({ tabs: [{ id: "foreign", kind: "web", agentOpened: true, agentSessionId: "other" }], groups: [] });
  try {
    await assert.rejects(executeInterface("native.webTab.reload", ["foreign"]), /authority_required/);
    await assert.rejects(executeInterface("native.webTab.reload", ["foreign"], { tab_id: "foreign", window_id: "main", session_id: "mine" }), /authority_required/);
    await assert.rejects(executeInterface("tabs.closeTab", ["foreign"]), /resource_web_close/);
    await assert.rejects(executeInterface("tabs.openWebTab", ["https://example.org"]), /resource_web_open/);
    assert.deepEqual(calls, []);
  } finally { delete globalThis.window; }
});
