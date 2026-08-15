import assert from "node:assert/strict";
import test from "node:test";

import { afterTwoAnimationFrames } from "../components/chat/messages/collapse-frame.ts";

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
