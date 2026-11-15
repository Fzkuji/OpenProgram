// While a sidebar or panel moves, the composer's input in each pane of a
// split view changes width in every frame. CodeMirror's ResizeObserver then
// asks for a measurement, which it runs in an animation frame, before that
// frame's layout: a forced style and layout in the frames of the move. The
// input holds those requests until the move ends and measures once after.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installLoader } from "../../_support/load.mjs";
import { installDom, render, resizeObservers } from "../../_support/dom.mjs";

installLoader();
const window = installDom();
const selection = { rangeCount: 0, anchorNode: null, anchorOffset: 0, focusNode: null, focusOffset: 0, getRangeAt() {}, removeAllRanges() {}, addRange() {}, collapse() {}, extend() {} };
document.getSelection = window.getSelection = () => selection;
document.hasFocus = () => false;
// Animation frames run only when the test says so.
const frames = new Map();
let frameId = 0;
window.requestAnimationFrame = globalThis.requestAnimationFrame = (callback) => { frames.set(++frameId, callback); return frameId; };
window.cancelAnimationFrame = globalThis.cancelAnimationFrame = (id) => { frames.delete(id); };
const runFrame = () => { const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach((callback) => callback(performance.now())); };

const { createElement: h, createRef } = await import("react");
const { EditorView } = await import("@codemirror/view");
const { MarkdownEditor } = await import("../../../components/chat/composer/input/markdown-editor.tsx");
const { railsMoving } = await import("../../../lib/tabs/canvas/rail-motion.ts");
const { setCanvasDragFlag } = await import("../../../lib/tabs/canvas/canvas-motion.ts");

const noop = () => {};
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** A rail's width transition, as the browser reports it. */
function transition(target, type, propertyName = "width") {
  const event = new window.Event(type, { bubbles: true });
  Object.defineProperty(event, "propertyName", { value: propertyName });
  target.dispatchEvent(event);
}

async function mountEditor() {
  const shell = document.body.appendChild(document.createElement("div"));
  shell.className = "sidebar right-sidebar rail-shell";
  const rendered = await render(h(MarkdownEditor, {
    value: "a draft that wraps", textareaRef: createRef(),
    onChange: noop, onCaret: noop, onKeyDown: noop, onPaste: noop, onFocus: noop, onBlur: noop,
  }));
  const view = EditorView.findFromDOM(rendered.container.querySelector(".cm-editor"));
  // CodeMirror's measurement reads layout linkedom does not have; count it,
  // and leave the view ready to schedule the next one as it would.
  let measured = 0;
  view.measure = () => { measured += 1; view.measureScheduled = -1; };
  runFrame();
  measured = 0;
  /** One frame of the input changing width: CodeMirror's own observer, and
   *  the 50 ms it waits before asking for a measurement. */
  const resize = async () => {
    view.docView.lastUpdate = 0;
    resizeObservers.trigger(view.scrollDOM);
    await wait(60);
  };
  return { shell, view, resize, measured: () => measured, async unmount() { await rendered.unmount(); shell.remove(); } };
}

test("a rail moving holds the input's measurements; it measures once, in the frame after the move", async () => {
  const editor = await mountEditor();
  try {
    transition(editor.shell, "transitionrun");
    assert.equal(railsMoving(), true);
    for (let i = 0; i < 3; i += 1) {
      await editor.resize();
      assert.equal(frames.size, 0, "no frame callback measures while the rail moves");
      runFrame();
    }
    assert.equal(editor.measured(), 0);
    transition(editor.shell, "transitionend");
    assert.equal(railsMoving(), false);
    assert.equal(editor.measured(), 0, "not in the move's last frame");
    runFrame();
    assert.equal(frames.size, 1, "the held requests become one measurement");
    runFrame();
    assert.equal(editor.measured(), 1);
    runFrame();
    assert.equal(editor.measured(), 1, "once");
  } finally { await editor.unmount(); }
});

test("at rest the input measures after a resize as before", async () => {
  const editor = await mountEditor();
  try {
    await editor.resize();
    assert.equal(frames.size, 1);
    runFrame();
    assert.equal(editor.measured(), 1);
  } finally { await editor.unmount(); }
});

test("a reversed move keeps holding; a move of another property or element does not hold", async () => {
  const editor = await mountEditor();
  try {
    // Turned around mid-move: the browser cancels the transition and starts another.
    transition(editor.shell, "transitionrun");
    transition(editor.shell, "transitioncancel");
    transition(editor.shell, "transitionrun");
    await editor.resize();
    assert.equal(frames.size, 0);
    transition(editor.shell, "transitionend");
    runFrame(); runFrame();
    assert.equal(editor.measured(), 1);
    // A fade inside the rail, and a width transition on something that is not a rail.
    transition(editor.shell, "transitionrun", "opacity");
    const other = document.body.appendChild(document.createElement("div"));
    transition(other, "transitionrun");
    assert.equal(railsMoving(), false);
    await editor.resize();
    assert.equal(frames.size, 1);
    other.remove();
  } finally { await editor.unmount(); }
});

test("a rail removed mid-move does not leave the input holding", async () => {
  const editor = await mountEditor();
  try {
    transition(editor.shell, "transitionrun");
    assert.equal(railsMoving(), true);
    editor.shell.remove();
    assert.equal(railsMoving(), false);
  } finally { await editor.unmount(); }
});

test("a canvas drag holds the input's measurements too", async () => {
  const editor = await mountEditor();
  try {
    setCanvasDragFlag(true);
    await editor.resize();
    assert.equal(frames.size, 0);
    setCanvasDragFlag(false);
    runFrame(); runFrame();
    assert.equal(editor.measured(), 1);
  } finally { setCanvasDragFlag(false); await editor.unmount(); }
});

test("a move that starts again before the frame after it keeps holding", async () => {
  const editor = await mountEditor();
  try {
    transition(editor.shell, "transitionrun");
    await editor.resize();
    transition(editor.shell, "transitionend");
    transition(editor.shell, "transitionrun");
    runFrame();
    assert.equal(frames.size, 0, "still moving: nothing measured");
    transition(editor.shell, "transitionend");
    runFrame(); runFrame();
    assert.equal(editor.measured(), 1);
  } finally { await editor.unmount(); }
});
