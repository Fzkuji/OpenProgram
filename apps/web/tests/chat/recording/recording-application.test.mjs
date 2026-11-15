import assert from "node:assert/strict";
import test from "node:test";

import { RECORDING_APPLICATION, recordingEnabled } from "../../../lib/recording/recording-application.ts";

const row = (enabled) => ({ id: RECORDING_APPLICATION, enabled, loaded: enabled, entries: [] });

test("recording is offered only while the workflow_recording application is enabled", () => {
  assert.equal(RECORDING_APPLICATION, "workflow_recording");
  assert.equal(recordingEnabled([row(true)]), true);
  assert.equal(recordingEnabled([row(false)]), false);
  // Not listed (the list has not loaded, or an older server): not offered.
  assert.equal(recordingEnabled([]), false);
  assert.equal(recordingEnabled([{ id: "browser", enabled: true, loaded: true, entries: [] }]), false);
});
