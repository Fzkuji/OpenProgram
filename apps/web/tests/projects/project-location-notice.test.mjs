// The file panel's "Project folder unavailable" notice follows the registry:
// a repair made elsewhere re-reads it, and a re-list that gets no answer
// leaves the notice and its repair buttons as they are.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { installLoader } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

installLoader({
  replace: {
    "/lib/session-store/index.ts": "export const useSessionStore = (selector) => selector({ currentSessionId: null });",
    "/lib/net/ws/ws-request.ts": [
      "export const wsRequest = async (action) => { globalThis.noticeRequests.push(action); return globalThis.noticeReply(); };",
      "export const wsListen = () => () => {};",
      "export const wsFire = () => true;",
    ].join("\n"),
    "/components/ui/folder-picker.tsx": "export const useFolderPicker = () => ({ pickFolder: async () => null, folderPickerDialog: null });",
    "/components/solar-icons/index.tsx": "import { createElement } from 'react'; export const SolarIcon = ({ name, motionPreset, size, ...props }) => createElement('i', { 'data-icon': name, ...props });",
  },
});
installDom();

const { createElement: h } = await import("react");
const { ProjectLocationNotice } = await import("../../components/files/project-location-notice.tsx");

const GONE = { id: "gone", name: "Gone", path: "/work/gone", is_default: false, path_missing: true, location_revision: 7 };
const reply = (projects) => () => ({ projects, current_project_id: null, session_id: null });
const locate = (container) => [...container.querySelectorAll("button")].find((b) => b.textContent.includes("Locate folder"));

afterEach(() => { document.body.innerHTML = ""; });

test("the notice re-reads on project-changed and keeps its repair when a re-list gets no answer", async () => {
  globalThis.noticeRequests = [];
  globalThis.noticeReply = reply([GONE]);
  const view = await render(h(ProjectLocationNotice, { projectId: "gone", onRepaired() {} }));
  assert.ok(locate(view.container), "the repair is offered");
  assert.equal(globalThis.noticeRequests.length, 1);

  globalThis.noticeReply = () => null;
  await view.act(() => window.dispatchEvent(new Event("project-changed")));
  assert.equal(globalThis.noticeRequests.length, 2, "one re-list per change");
  assert.ok(locate(view.container), "an unanswered re-list keeps the repair");
  assert.ok(view.container.textContent.includes("/work/gone"));

  globalThis.noticeReply = reply([]);
  await view.act(() => window.dispatchEvent(new Event("project-changed")));
  assert.equal(locate(view.container), undefined, "a project removed elsewhere leaves no repair");
  await view.unmount();

  await view.act?.(() => window.dispatchEvent(new Event("project-changed")));
  assert.equal(globalThis.noticeRequests.length, 3, "an unmounted notice stops listening");
});
