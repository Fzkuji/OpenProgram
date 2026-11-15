// Each working-folder chip carries a remove control that a keyboard reaches:
// a focusable button named "Remove folder" that Enter and Space activate
// (Space without scrolling the page), and that other keys leave alone.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installLoader } from "../../_support/load.mjs";
import { installDom, render } from "../../_support/dom.mjs";

installLoader({
  replace: {
    "/lib/i18n/index.ts": "export const useTranslation = () => ({ text: (en) => en });",
    "/components/ui/button.tsx": "import { createElement as h } from 'react'; export const Button = ({ variant, size, ...props }) => h('button', props); export const buttonVariants = () => '';",
    "/components/ui/folder-picker.tsx": "export const useFolderPicker = () => ({ pickFolder: async () => null, folderPickerDialog: null, manualOpen: false });",
    "/lib/session-store/session-scope.tsx": "export const useOptionalScopedSessionId = () => null;",
    "/lib/runtime-bridge/ui.ts": "export const closeAllPopovers = () => {};",
    "/components/ui/popover.tsx": "export const Popover = ({ children }) => children; export const PopoverTrigger = Popover; export const PopoverContent = () => null;",
    "/components/ui/tooltip.tsx": "export const HoverTip = ({ children }) => children; export const TipBody = () => null;",
    "/components/chat/top-bar/chips/git-chip.tsx": "export const GitChip = () => null;",
    "/lib/session-store/index.ts": [
      "import { useSyncExternalStore } from 'react';",
      "export const useSessionStore = (selector) => useSyncExternalStore(",
      "  (fn) => { globalThis.storeListeners.add(fn); return () => globalThis.storeListeners.delete(fn); },",
      "  () => selector(globalThis.store));",
      "useSessionStore.getState = () => globalThis.store;",
    ].join("\n"),
    "/lib/net/ws/ws-request.ts": "export const wsRequest = async () => ({ projects: [], current_project_id: null, session_id: null }); export const wsListen = () => () => {}; export const wsFire = () => true;",
  },
});
installDom();

const listeners = new Set();
globalThis.storeListeners = listeners;
const notify = () => { for (const fn of listeners) fn(); };
globalThis.store = {
  currentSessionId: null,
  activeChatKey: "draft",
  conversations: {},
  pendingProjectsByChat: {},
  additionalWorkingDirsBySession: { draft: ["/alpha", "/beta"] },
  setAdditionalWorkingDirs(key, dirs) {
    globalThis.store = { ...globalThis.store, additionalWorkingDirsBySession: { ...globalThis.store.additionalWorkingDirsBySession, [key]: dirs } };
    notify();
  },
};

const { createElement: h } = await import("react");
const { WorkingDirChips } = await import("../../../components/chat/top-bar/chips/working-dir-chips.tsx");

const dirs = () => globalThis.store.additionalWorkingDirsBySession.draft;
const removers = (container) => [...container.querySelectorAll(".workdir-badge .workdir-remove")];
async function press(view, element, key) {
  // linkedom has no KeyboardEvent; React reads `key` off the native event.
  const event = new Event("keydown", { bubbles: true, cancelable: true });
  Object.defineProperty(event, "key", { value: key });
  await view.act(async () => { element.dispatchEvent(event); });
  return event;
}

test("the remove control on a folder chip is a focusable, named button that Enter and Space activate", async () => {
  const view = await render(h(WorkingDirChips));
  const { container } = view;
  assert.deepEqual(
    [...container.querySelectorAll(".workdir-badge[data-path]")].map((chip) => chip.getAttribute("data-path")),
    ["/alpha", "/beta"],
  );
  const [first, second] = removers(container);
  for (const control of [first, second]) {
    assert.equal(control.getAttribute("role"), "button");
    assert.equal(control.getAttribute("tabindex"), "0", "the control is in the Tab order");
    assert.equal(control.getAttribute("aria-label"), "Remove folder");
  }

  const ignored = await press(view, second, "x");
  assert.deepEqual(dirs(), ["/alpha", "/beta"], "other keys do nothing");
  assert.equal(ignored.defaultPrevented, false);

  await press(view, second, "Enter");
  assert.deepEqual(dirs(), ["/alpha"], "Enter removes that folder only");

  const space = await press(view, removers(container)[0], " ");
  assert.deepEqual(dirs(), [], "Space removes the folder too");
  assert.equal(space.defaultPrevented, true, "Space must not scroll the page");
  assert.equal(removers(container).length, 0);
  await view.unmount();
});
