import assert from "node:assert/strict";
import test from "node:test";

const { useComposerKeyDown } = await import("../../../components/chat/composer/input/use-composer-keydown.ts");

function keydown(init, options = {}) {
  const modes = [];
  const handler = useComposerKeyDown({
    input: "hello",
    setInput() {},
    fileMention: { atToken: null, fileMatches: [], fileMenuIndex: 0, setFileMenuIndex() {}, pickFile() {}, closeMenu() {} },
    historyRecall: { historyIndex: -1, setHistoryIndex() {}, recallPrevious: () => false, recallNext() {} },
    slash: { visible: false, closing: false },
    selectSlashCommand() {},
    submit(mode) { modes.push(mode); },
    ...options,
  });
  let prevented = false;
  handler({
    key: "Enter", shiftKey: false, metaKey: false, ctrlKey: false, altKey: false,
    nativeEvent: { isComposing: false, keyCode: 13 },
    preventDefault() { prevented = true; },
    stopPropagation() {},
    ...init,
  });
  return { modes, prevented };
}

test("Enter adds a running message to the current turn", () => {
  assert.deepEqual(keydown({}).modes, ["steer"]);
});

test("Cmd+Enter and Ctrl+Enter queue it for the next turn", () => {
  assert.deepEqual(keydown({ metaKey: true }).modes, ["queue"]);
  assert.deepEqual(keydown({ ctrlKey: true }).modes, ["queue"]);
});

test("Shift+Enter and IME confirmation never submit", () => {
  assert.deepEqual(keydown({ shiftKey: true }).modes, []);
  assert.deepEqual(keydown({ nativeEvent: { isComposing: true, keyCode: 229 } }).modes, []);
});

test("Esc stops a running turn even with text in the draft", () => {
  let stops = 0;
  const stop = () => { stops += 1; };
  const result = keydown({ key: "Escape" }, { canStop: true, stop });
  assert.equal(stops, 1);
  assert.equal(result.prevented, true);
  assert.deepEqual(result.modes, []);
  keydown({ key: "Escape" }, { canStop: false, stop });
  assert.equal(stops, 1, "nothing to stop when idle");
});

test("Esc closes an open slash menu before it stops anything", () => {
  let stops = 0, closed = 0;
  keydown({ key: "Escape" }, {
    canStop: true, stop: () => { stops += 1; },
    slash: { visible: true, closing: false, close: () => { closed += 1; } },
  });
  assert.deepEqual([closed, stops], [1, 0]);
});

test("Enter in the slash menu runs the fully typed command, not the first match", () => {
  const picked = [];
  keydown({}, {
    input: "/new",
    slash: { visible: true, closing: false, activeIndex: -1, matches: [{ name: "/newsletter" }, { name: "/new" }] },
    selectSlashCommand: cmd => picked.push(cmd.name),
  });
  assert.deepEqual(picked, ["/new"]);
});
