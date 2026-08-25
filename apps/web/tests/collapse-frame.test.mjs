import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { afterTwoAnimationFrames } from "../components/chat/messages/collapse-frame.ts";

const bubblesCss = readFileSync(
  new URL("../app/styles/chat/bubbles.css", import.meta.url),
  "utf8",
);
const messageList = readFileSync(
  new URL("../components/chat/messages/message-list.tsx", import.meta.url),
  "utf8",
);

function fakeFrames() {
  let nextHandle = 1;
  const callbacks = new Map();
  return {
    schedule(callback) {
      const handle = nextHandle++;
      callbacks.set(handle, callback);
      return handle;
    },
    cancel(handle) {
      callbacks.delete(handle);
    },
    runNext() {
      const next = callbacks.entries().next().value;
      if (!next) return false;
      const [handle, callback] = next;
      callbacks.delete(handle);
      callback(0);
      return true;
    },
  };
}

test("cancelling between animation frames cannot reopen a closing row", () => {
  const frames = fakeFrames();
  let shown = false;
  const cancel = afterTwoAnimationFrames(
    () => { shown = true; },
    frames.schedule,
    frames.cancel,
  );

  assert.equal(frames.runNext(), true);
  cancel();
  assert.equal(frames.runNext(), false);
  assert.equal(shown, false);
});

test("an uninterrupted two-frame transition opens the row once", () => {
  const frames = fakeFrames();
  let calls = 0;
  afterTwoAnimationFrames(
    () => { calls += 1; },
    frames.schedule,
    frames.cancel,
  );

  assert.equal(frames.runNext(), true);
  assert.equal(calls, 0);
  assert.equal(frames.runNext(), true);
  assert.equal(calls, 1);
  assert.equal(frames.runNext(), false);
});

test("folded originals keep the 0fr close and skip layout only when closed", () => {
  assert.match(bubblesCss, /\.compaction-orig-fold\s*\{[\s\S]*?grid-template-rows:\s*0fr/);
  assert.match(bubblesCss, /\.compaction-orig-fold\[data-open="1"\]\s*\{[\s\S]*?grid-template-rows:\s*1fr/);
  assert.match(
    bubblesCss,
    /\.compaction-orig-fold\[data-open="0"\] \.compaction-orig-fold-inner\s*\{[\s\S]*?content-visibility:\s*hidden/,
  );
  assert.match(
    bubblesCss,
    /content-visibility:\s*hidden[\s\S]*?content-visibility 0s 900ms/,
  );
  assert.match(
    bubblesCss,
    /\.compaction-orig-fold\[data-open="1"\] \.compaction-orig-fold-inner\s*\{[\s\S]*?content-visibility:\s*visible/,
  );
  assert.doesNotMatch(bubblesCss, /\.message\s*\{[^}]*content-visibility/);
  assert.match(messageList, /className="compaction-orig-fold"/);
  assert.match(messageList, /data-open=\{hiddenCovered/);
});
