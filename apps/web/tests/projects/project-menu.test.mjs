// The top-bar project picker: a draft chat chooses among projects, listed
// under the machine each is on, or opens a new folder on a machine picked
// first; the choice waits in the session store; once the chat
// has a session its main folder is frozen, and the only thing left is the
// relocate repair for a folder that went missing. No "remove" anywhere.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { installLoader } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

installLoader({
  replace: {
    "/components/chat/top-bar/bound-chat.tsx": "export const useBoundChat = () => ({ sessionId: globalThis.menuStore.currentSessionId, chatKey: 'draft-key' });",
    "/lib/session-store/index.ts": [
      "export const useSessionStore = (selector) => selector(globalThis.menuStore);",
      "useSessionStore.getState = () => globalThis.menuStore;",
    ].join("\n"),
    "/lib/net/ws/ws-request.ts": [
      "export const wsRequest = async (action, payload, reply) => { globalThis.menuRequests.push([action, payload, reply]); return globalThis.menuReplies[action]?.(payload) ?? null; };",
      "export const wsListen = () => () => {};",
      "export const wsFire = () => true;",
    ].join("\n"),
    "/lib/runtime-bridge/ui.ts": "export const closeAllPopovers = () => {};",
    "/components/ui/popover.tsx": "export const Popover = ({ children }) => children; export const PopoverTrigger = Popover; export const PopoverContent = Popover;",
    "/components/ui/tooltip.tsx": "export const HoverTip = ({ children }) => children; export const TipBody = () => null;",
    "/components/ui/folder-picker.tsx": "export const useFolderPicker = () => ({ pickFolder: async () => null, folderPickerDialog: null, manualOpen: false });",
    "/components/chat/top-bar/chips/git-chip.tsx": "export const GitChip = () => null;",
    "/components/solar-icons/index.tsx": "import { createElement } from 'react'; export const SolarIcon = ({ name, motionPreset, size, ...props }) => createElement('i', { 'data-icon': name, ...props });",
  },
});
installDom();

const { createElement: h } = await import("react");
const { ProjectMenu } = await import("../../components/chat/top-bar/menus/project-menu.tsx");
const { useMachines } = await import("../../lib/machines/store.ts");
const { forgetProjectLists } = await import("../../lib/projects/list-projects.ts");

const PROJECTS = [
  { id: "home", name: "Home", path: "/home/me", is_default: true, session_count: 0 },
  { id: "alpha", name: "Alpha", path: "/work/alpha", is_default: false, session_count: 2, location_revision: 3 },
  { id: "gone", name: "Gone", path: "/work/gone", is_default: false, session_count: 0, path_missing: true, location_revision: 7 },
];

function setup({ sessionId = null, current = null, replies = {}, machines = [] } = {}) {
  const pending = [];
  // Each test answers with its own list; drop the one the last test kept.
  forgetProjectLists();
  globalThis.menuStore = {
    currentSessionId: sessionId,
    pendingProjectsByChat: {},
    setPendingProject: (key, id) => pending.push([key, id]),
  };
  globalThis.menuRequests = [];
  useMachines.setState({ items: [LOCAL, ...machines], kinds: [], loaded: true });
  globalThis.menuReplies = {
    list_projects: () => ({ projects: PROJECTS, current_project_id: current, session_id: sessionId }),
    ...replies,
  };
  return { pending };
}

const LOCAL = { id: "local", name: "This computer", enabled: true, status: { state: "connected" }, connection: { kind: "local", label: "", where: "", known: true }, shell: { remote_files: false, instance_scope: "machine" } };
const openOnThisComputer = (path) => async (machine) => (path ? { path, machineId: machine.id } : null);

const item = (container, label) =>
  [...container.querySelectorAll("span")].find((span) => span.textContent === label)?.closest("div");
const actions = () => globalThis.menuRequests.map(([action]) => action);

afterEach(() => { document.body.innerHTML = ""; });

test("a draft chat lists every project, including one whose folder is missing, and offers no remove", async () => {
  setup();
  const view = await render(h(ProjectMenu, { onClose() {}, pickFolder: async () => null }));
  const text = view.container.textContent;
  for (const name of ["This computer", "Home", "Alpha", "Gone", "Open folder on…"]) assert.match(text, new RegExp(name));
  assert.doesNotMatch(text, /Remove/);
  assert.ok(view.container.querySelector('[aria-label="Folder unavailable"]'), "the missing project carries a warning");
  await view.unmount();
});

test("only the project list scrolls; Open folder on… stays pinned under it", async () => {
  setup();
  const view = await render(h(ProjectMenu, { onClose() {}, pickFolder: async () => null }));
  const panel = view.container.firstElementChild;
  assert.match(panel.className, /overflow-hidden/);
  assert.doesNotMatch(panel.className, /overflow-y-auto/, "the panel itself never scrolls");
  const list = view.container.querySelector(".project-menu-list");
  assert.match(list.className, /overflow-y-auto/);
  assert.ok(list.contains(item(view.container, "Alpha")), "projects live in the scrolling list");
  assert.ok(!list.contains(item(view.container, "Open folder on…")), "Open folder on… sits outside it");
  await view.unmount();
});

test("picking a project in a draft stores it as the chat's pending project, sent with the first message", async () => {
  const { pending } = setup();
  let closed = 0;
  const view = await render(h(ProjectMenu, { onClose: () => closed++, pickFolder: async () => null }));
  await view.act(() => item(view.container, "Alpha").click());
  assert.deepEqual(pending, [["draft-key", "alpha"]]);
  assert.equal(closed, 1);
  assert.ok(!actions().includes("set_session_project"), "a draft never rebinds a session on the server");
  await view.unmount();
});

test("a missing project in the draft list locates its folder instead of being selected", async () => {
  const { pending } = setup({ replies: { relocate_project: () => ({ ok: true, project_id: "gone" }) } });
  const picked = [];
  const view = await render(h(ProjectMenu, { onClose() {}, pickFolder: async (start) => { picked.push(start); return "/work/found"; } }));
  await view.act(() => item(view.container, "Gone").click());
  assert.deepEqual(picked, ["/work/gone"]);
  const relocate = globalThis.menuRequests.find(([action]) => action === "relocate_project");
  assert.deepEqual(relocate[1], { project_id: "gone", path: "/work/found", expected_path: "/work/gone", expected_revision: 7, replace_identity: true });
  assert.deepEqual(pending, [], "locating never selects the project");
  await view.unmount();
});

test("Open folder on… this computer creates a project there and makes it the draft's pending project", async () => {
  const { pending } = setup({ replies: { create_project: () => ({ ok: true, project: { id: "new", name: "New", path: "/work/new" } }) } });
  const view = await render(h(ProjectMenu, { onClose() {}, pickFolder: async () => null, openOn: openOnThisComputer("/work/new") }));
  await view.act(() => view.container.querySelector('[data-machine-choice="local"]').click());
  const create = globalThis.menuRequests.find(([action]) => action === "create_project");
  assert.deepEqual(create[1], { path: "/work/new", machine_id: "local", session_id: "" });
  assert.deepEqual(pending, [["draft-key", "new"]]);
  await view.unmount();
});

test("a failed Open folder on… shows the server's error and selects nothing", async () => {
  const { pending } = setup({ replies: { create_project: () => ({ ok: false, error: "not a directory" }) } });
  const view = await render(h(ProjectMenu, { onClose() {}, pickFolder: async () => null, openOn: openOnThisComputer("/work/file.txt") }));
  await view.act(() => view.container.querySelector('[data-machine-choice="local"]').click());
  assert.match(view.container.textContent, /not a directory/);
  assert.deepEqual(pending, []);
  await view.unmount();
});

test("once the chat has a session the picker is gone and the main folder reads as fixed", async () => {
  setup({ sessionId: "s1", current: "alpha" });
  const view = await render(h(ProjectMenu, { onClose() {}, pickFolder: async () => null }));
  const text = view.container.textContent;
  assert.match(text, /Main working directory/);
  assert.match(text, /Alpha/);
  assert.match(text, /\/work\/alpha/);
  assert.match(text, /Fixed for this conversation/);
  assert.doesNotMatch(text, /Open folder on…|Home|Gone|Locate folder…/);
  await view.unmount();
});

test("a frozen chat whose folder is missing offers only Locate folder…, which relocates the project for that session", async () => {
  setup({ sessionId: "s1", current: "gone", replies: { relocate_project: () => ({ ok: true, project_id: "gone" }) } });
  let closed = 0;
  const view = await render(h(ProjectMenu, { onClose: () => closed++, pickFolder: async () => "/work/found" }));
  assert.doesNotMatch(view.container.textContent, /Open folder on…/);
  await view.act(() => item(view.container, "Locate folder…").click());
  const relocate = globalThis.menuRequests.find(([action]) => action === "relocate_project");
  assert.deepEqual(relocate[1], { session_id: "s1", project_id: "gone", path: "/work/found", expected_path: "/work/gone", expected_revision: 7, replace_identity: true });
  assert.ok(!actions().includes("set_session_project"), "the session's binding never moves");
  assert.equal(closed, 1);
  await view.unmount();
});

test("an open menu re-lists in place when a project changes elsewhere, keeping its scroll", async () => {
  setup();
  const view = await render(h(ProjectMenu, { onClose() {}, pickFolder: async () => null }));
  const list = view.container.querySelector(".project-menu-list");
  list.scrollTop = 40;
  globalThis.menuReplies.list_projects = () => ({
    projects: [...PROJECTS.filter((p) => p.id !== "gone"), { id: "beta", name: "Beta", path: "/work/beta", is_default: false, session_count: 0 }],
    current_project_id: null,
    session_id: null,
  });
  await view.act(() => window.dispatchEvent(new Event("project-changed")));
  assert.ok(item(view.container, "Beta"), "a project created elsewhere appears without reopening");
  assert.equal(item(view.container, "Gone"), undefined, "a project removed elsewhere leaves the open menu");
  assert.equal(view.container.querySelector(".project-menu-list"), list, "the same scrolling list stays mounted");
  assert.equal(list.scrollTop, 40);

  globalThis.menuReplies.list_projects = () => null;
  await view.act(() => window.dispatchEvent(new Event("project-changed")));
  assert.ok(item(view.container, "Beta"), "an unanswered re-list keeps what is shown");
  await view.unmount();
});

test("projects sit under their machine, this computer first; a project on a machine that is not connected is greyed", async () => {
  const box = { id: "box", name: "Box", enabled: true, status: { state: "disconnected" }, connection: { kind: "ssh", label: "SSH", where: "me@box", known: true }, shell: { remote_files: true, instance_scope: "machine" } };
  const { pending } = setup({ machines: [box] });
  globalThis.menuReplies.list_projects = () => ({
    projects: [...PROJECTS, { id: "far", name: "Far", path: "/srv/far", is_default: false, session_count: 0, machine_id: "box" }],
    current_project_id: null,
    session_id: null,
  });
  const view = await render(h(ProjectMenu, { onClose() {}, pickFolder: async () => null, openOn: async () => null }));
  const groups = [...view.container.querySelectorAll(".project-menu-list [data-project-machine]")];
  assert.deepEqual(groups.map((g) => g.dataset.projectMachine), ["local", "box"]);
  assert.match(groups[1].textContent, /Box.*offline.*Far/);
  const far = item(view.container, "Far");
  assert.equal(far.getAttribute("aria-disabled"), "true");
  await view.act(() => far.click());
  assert.deepEqual(pending, [], "a project on an offline machine is not chosen");
  const choice = view.container.querySelector('[data-machine-choice="box"]');
  assert.equal(choice.getAttribute("aria-disabled"), "true", "an offline machine cannot take a new folder");
  assert.ok(view.container.querySelector('a[href="/settings/machines"]'), "Add device… leads to Settings");
  await view.unmount();
});
