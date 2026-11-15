/**
 * A file dragged over a split-view pane belongs to that pane's composer:
 * only it shows the drop overlay and only it takes the file. Outside every
 * pane the focused pane's composer takes it. An editor inside a message
 * keeps the files dropped on it. The real useComposerAttachments hook is
 * rendered with React; IndexedDB is a stub.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { installLoader } from "../../_support/load.mjs";
import { installDom, render } from "../../_support/dom.mjs";

installLoader({
  stubs: {
    "./attach-idb": [
      "export const markAttachmentOwnerClosed = () => {};",
      "export const attachmentOwnerIsClosed = () => false;",
      "export const onAttachmentOwnerClosed = () => () => {};",
      "export const loadAttachments = async () => ({ images: [], docs: [] });",
      "export const saveAttachments = async () => {};",
      "export const deleteAttachments = async () => {};",
    ].join("\n"),
  },
});
installDom();

const { createElement } = await import("react");
const { useComposerAttachments } = await import("../../../components/chat/composer/attach/use-composer-attachments.ts");

const hooks = {};
const empty = { images: [], docs: [] };

function Pane({ name, focused }) {
  const composer = useComposerAttachments(`pane:${name}`, empty, true);
  const editor = useComposerAttachments(`edit:${name}`, empty);
  hooks[name] = composer;
  hooks[`${name}-editor`] = editor;
  return createElement("div", { className: "peer-session-pane", "data-pane-focused": String(focused), id: `pane-${name}` },
    createElement("div", { id: `rows-${name}` },
      createElement("div", { id: `editor-${name}`, ref: editor.composerRootRef, "data-file-drop": "" })),
    createElement("div", { id: `composer-${name}`, ref: composer.composerRootRef }));
}

function drag(type, target, files = []) {
  const event = new window.Event(type, { bubbles: true, cancelable: true });
  event.dataTransfer = { types: ["Files"], files, items: [] };
  target.dispatchEvent(event);
  return event;
}

test("a file over one pane lights up and lands in that pane's composer only", async () => {
  const view = await render(createElement("div", null,
    createElement("div", { id: "sidebar" }),
    createElement(Pane, { name: "left", focused: true }),
    createElement(Pane, { name: "right", focused: false })));
  // linkedom lays nothing out: a mounted composer reports one rect.
  for (const name of ["left", "right"]) {
    document.getElementById(`composer-${name}`).getClientRects = () => [{}];
  }
  try {
    await view.act(() => drag("dragover", document.getElementById("rows-right")));
    assert.equal(hooks.right.dragActive, true);
    assert.equal(hooks.left.dragActive, false, "the other pane shows nothing");

    await view.act(() => drag("dragover", document.getElementById("sidebar")));
    assert.equal(hooks.left.dragActive, true, "outside every pane, the focused pane takes it");
    assert.equal(hooks.right.dragActive, false);

    await view.act(() => drag("dragover", document.getElementById("editor-right")));
    assert.equal(hooks.right.dragActive, false, "an editor keeps what is dropped on it");
    assert.equal(hooks.left.dragActive, false);

    const file = new File(["hello"], "note.txt", { type: "text/plain" });
    await view.act(() => drag("dragover", document.getElementById("rows-right")));
    const dropped = drag("drop", document.getElementById("rows-right"), [file]);
    await view.act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    assert.equal(dropped.defaultPrevented, true, "the window does not open the file");
    assert.equal(hooks.right.dragActive, false);
    assert.deepEqual(hooks.right.pendingDocs.map((doc) => doc.filename), ["note.txt"]);
    assert.deepEqual(hooks.left.pendingDocs, []);
  } finally {
    await view.unmount();
  }
});

test("a composer that is not shown takes nothing", async () => {
  const view = await render(createElement(Pane, { name: "hidden", focused: true }));
  document.getElementById("composer-hidden").getClientRects = () => [];
  try {
    await view.act(() => drag("dragover", document.getElementById("rows-hidden")));
    assert.equal(hooks.hidden.dragActive, false);
  } finally {
    await view.unmount();
  }
});
