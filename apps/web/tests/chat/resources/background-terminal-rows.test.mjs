import test from "node:test";
import assert from "node:assert/strict";
import { backgroundTerminalRows, groupSessionResources } from "../../../lib/chat/resources/session-resources.ts";

const command = (id, status, extra = {}) => ({
  id, session_id: "s", execution_id: "e", command: `npm run ${id}`, display: { name: `npm run ${id}`, kind: "script" },
  cwd: "/work", status, started_at: 1, ended_at: null, pid: 10, exit_code: null, backend_id: "local",
  truncated: false, background: true, ...extra,
});

test("running background commands are listed as terminals", () => {
  const rows = backgroundTerminalRows([command("dev", "running"), command("build", "exited"),
    command("quick", "running", { background: false })], "s");
  assert.deepEqual(rows.map(row => row.sourceId), ["dev"]);
  assert.equal(rows[0].source, "process");
  assert.equal(rows[0].kind, "terminal");
  assert.equal(rows[0].title, "npm run dev");
  assert.deepEqual(groupSessionResources(rows).map(group => group.key), ["terminal"]);
});

test("an ended command stays listed while it is being viewed", () => {
  const rows = backgroundTerminalRows([command("build", "exited")], "s", "build");
  assert.deepEqual(rows.map(row => row.sourceId), ["build"]);
  assert.deepEqual(backgroundTerminalRows([command("build", "exited")], null, "build"), []);
});
