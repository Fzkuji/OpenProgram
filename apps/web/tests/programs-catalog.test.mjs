import assert from "node:assert/strict";
import test from "node:test";

import {
  programInvocationName,
} from "../components/programs/programs-catalog.ts";

test("application entities invoke and favorite their exported callable", () => {
  assert.equal(programInvocationName({
    name: "gui_harness",
    path: "applications/gui_harness",
    kind: "folder",
    program_kind: "application",
    has_children: false,
    callable_name: "gui_agent",
  }), "gui_agent");
  assert.equal(programInvocationName({
    name: "custom_app",
    path: "applications/custom_app",
    kind: "folder",
    program_kind: "application",
    has_children: false,
  }), "custom_app");
});
