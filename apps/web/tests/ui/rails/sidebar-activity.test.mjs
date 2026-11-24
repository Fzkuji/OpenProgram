// The sidebar bell's Activity view: which conversations it lists and how it
// groups them, the bell's amber dot, the ⌘⌥U shortcut, the remembered
// toggle, and the two-line chat row it draws.
import assert from "node:assert/strict";
import test from "node:test";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { parseHTML } from "linkedom";

import { installLoader } from "../../_support/load.mjs";
import { scratchDir } from "../../scratch-dir.mjs";

installLoader({
  replace: {
    "/components/chat/messages/attachments/user-attachments.tsx": "export const parseAttachments = (text) => ({ text, attachments: [] });",
    "/lib/runtime-bridge/session/session-load.ts": "export const sendRuntimeCommand = () => true;",
  },
});

const { activitySections, activityState, anyNeedsInput, unreadIds } = await import("../../../components/sidebar/sessions/activity.ts");
const { shortcutForKey } = await import("../../../lib/tabs/navigation/tab-shortcuts.ts");

const at = (y, m, d, h = 12) => Math.floor(new Date(y, m - 1, d, h).getTime() / 1000);
const now = at(2026, 11, 22, 15);
const labels = { today: "Today", yesterday: "Yesterday" };

test("Activity lists the last 7 days' running, waiting, failed and unread chats, by day, newest first", () => {
  const convs = [
    { id: "idle", updated_at: at(2026, 11, 22, 14) },
    { id: "unread-today", unread: true, status: "done", updated_at: at(2026, 11, 22, 9) },
    { id: "waiting", status: "needs_input", updated_at: at(2026, 11, 22, 13) },
    { id: "running", updated_at: at(2026, 11, 22, 11) },
    { id: "failed-yesterday", unread: true, status: "failed", updated_at: at(2026, 11, 21) },
    { id: "unread-old", unread: true, updated_at: at(2026, 11, 14) },
    { id: "unread-archived", unread: true, archived: true, updated_at: at(2026, 11, 22) },
    { id: "unread-5d", unread: true, updated_at: at(2026, 11, 17) },
    { id: "failed-seen", status: "failed", updated_at: at(2026, 11, 22) },
  ];
  const sections = activitySections(convs, { nowTs: now, locale: "en", isRunning: (id) => id === "running", labels });
  assert.deepEqual(sections.map((s) => s.label), ["Today", "Yesterday", "Nov 17"]);
  assert.deepEqual(sections[0].items.map((i) => [i.conv.id, i.state]),
    [["waiting", "needs_input"], ["running", "working"], ["unread-today", "unread"]]);
  assert.deepEqual(sections[1].items.map((i) => i.state), ["failed"]);
  assert.deepEqual(sections[2].items.map((i) => i.conv.id), ["unread-5d"]);
  assert.equal(activitySections(convs, { nowTs: now, locale: "zh", isRunning: () => false, labels })[2].label, "11月17日");
  assert.deepEqual(activitySections([convs[0]], { nowTs: now, locale: "en", isRunning: () => false, labels }), [],
    "nothing qualifies: the list is empty");
});

test("waiting outranks running, as on the row's marker", () => {
  assert.equal(activityState({ id: "a", status: "needs_input" }, true), "needs_input");
  assert.equal(activityState({ id: "a", unread: true }, true), "working");
});

test("the bell's dot follows any waiting chat; Mark all as read takes the unread ones", () => {
  assert.equal(anyNeedsInput({ a: { id: "a" }, b: { id: "b", status: "needs_input" } }), true);
  assert.equal(anyNeedsInput({ a: { id: "a" }, b: { id: "b", status: "needs_input", archived: true } }), false);
  assert.deepEqual(unreadIds([{ id: "a", unread: true }, { id: "b" }, { id: "c", unread: true, archived: true }]), ["a"]);
});

test("Cmd+Option+U toggles Activity, whatever character Option makes of U", () => {
  const key = (k, extra = {}) => shortcutForKey({ key: k, code: "KeyU", metaKey: true, ctrlKey: false, altKey: true, shiftKey: false, ...extra }, true);
  assert.deepEqual(key("¨"), { action: "toggle-activity" });
  assert.deepEqual(shortcutForKey({ key: "u", code: "KeyU", metaKey: false, ctrlKey: true, altKey: true, shiftKey: false }, false), { action: "toggle-activity" });
  assert.equal(key("¨", { shiftKey: true }), null);
  assert.equal(key("n", { code: "KeyN" }), null, "other Option chords stay free");
});

test("the Activity toggle is remembered with the list's other view choices", async () => {
  const store = new Map();
  const events = new EventTarget();
  globalThis.window = Object.assign(events, {
    localStorage: { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) },
  });
  try {
    const view = await import("../../../lib/prefs/recents-view.ts");
    assert.equal(view.getRecentsView().activity, false);
    view.toggleActivityView();
    assert.equal(JSON.parse(store.get("recents_view")).activity, true);
    view.toggleActivityView();
    assert.equal(view.getRecentsView().activity, false);
  } finally {
    delete globalThis.window;
  }
});

test("an Activity row is the chat row with the project, the status word and the time", async () => {
  const web = dirname(fileURLToPath(new URL("../../../package.json", import.meta.url)));
  const dir = scratchDir(web, "sidebar-activity");
  await build({
    absWorkingDir: web,
    stdin: { contents: "export {ConvRow, sameConvRow} from './components/sidebar/sessions/conv-item';", resolveDir: web },
    bundle: true, format: "esm", platform: "node", packages: "external", jsx: "automatic",
    outfile: join(dir, "test.mjs"), tsconfig: join(web, "tsconfig.json"), loader: { ".css": "empty" },
  });
  const { window } = parseHTML("<html><body></body></html>");
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  Object.assign(globalThis, {
    window, document: window.document, HTMLElement: window.HTMLElement, customElements: window.customElements,
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    requestAnimationFrame: (fn) => setTimeout(fn, 0), IS_REACT_ACT_ENVIRONMENT: true,
  });
  const { act, createElement } = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { ConvRow, sameConvRow } = await import(pathToFileURL(join(dir, "test.mjs")));
  const noop = () => {};
  const activity = { project: "Weather widget", status: { label: "Needs input", tone: "needs_input" }, time: "2m ago" };
  const props = {
    conv: { id: "a", title: "Fix forecast", status: "needs_input" }, label: "Fix forecast", active: false, running: false, groups: [],
    onClick: noop, onRename: noop, onAutoRename: noop, onTogglePin: noop, onToggleArchive: noop, onMoveToGroup: noop,
    onCopyLink: noop, onExport: noop, onDelete: noop, activity,
  };
  const source = { current: new Map([["a", props]]) };
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(ConvRow, { source, ...props })));
    assert.match(host.textContent, /Fix forecast/);
    assert.match(host.textContent, /Weather widget · Needs input/);
    assert.match(host.textContent, /2m ago/);
  } finally {
    await act(async () => root.unmount());
    host.remove();
  }
  assert.equal(sameConvRow(props, { ...props, activity: { ...activity } }), true);
  assert.equal(sameConvRow(props, { ...props, activity: { ...activity, time: "3m ago" } }), false);
  assert.equal(sameConvRow(props, { ...props, activity: undefined }), false);
});
