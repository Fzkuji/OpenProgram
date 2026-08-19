import assert from "node:assert/strict";
import test from "node:test";

import {
  buildRuntimeProgramDirectories,
  programInvocationName,
} from "../components/programs/programs-catalog.ts";
import { TOOL_GROUPS } from "../components/functions/tool-groups.ts";

const tools = [
  { name: "bash", group: "file", source: "builtin", server: null },
  { name: "read", group: "file", source: "builtin", server: null },
  { name: "web_search", group: "web", source: "builtin", server: null },
  { name: "linear_get_issue", group: "connected", source: "mcp", server: "linear" },
  { name: "drawio_export", group: "connected", source: "mcp", server: "drawio" },
];

const programs = [
  {
    name: "agentic_workflow",
    category: "agentic",
    filepath: "/pkg/openprogram/programs/functions/agentic/agentic_workflow/__init__.py",
  },
  {
    name: "run_docs_question",
    category: "agentic",
    filepath: "/pkg/openprogram/programs/functions/agentic/docs_question/__init__.py",
  },
  { name: "gui_agent", category: "app", filepath: "/tmp/gui_agent/__init__.py" },
];

test("runtime catalog retains built-in groups and agentic names without duplicating MCP tools", () => {
  const { directories, firstFunction } = buildRuntimeProgramDirectories(tools, programs, TOOL_GROUPS);

  assert.deepEqual(directories.functions.map((entry) => entry.path), [
    "functions/vanilla",
    "functions/agentic",
  ]);
  assert.deepEqual(directories["functions/vanilla"].map((entry) => entry.name), [
    "Files & Shell",
    "Web & Media",
  ]);
  assert.deepEqual(directories["functions/vanilla/file"].map((entry) => entry.name), [
    "bash",
    "read",
  ]);
  assert.deepEqual(directories["functions/agentic"].map((entry) => entry.name), [
    "agentic_workflow",
    "run_docs_question",
  ]);
  assert.equal(
    directories["functions/agentic"].find((entry) => entry.name === "run_docs_question").logic_path,
    "functions/agentic/docs_question",
  );
  assert.equal(directories["functions/connected"], undefined);
  assert.equal(firstFunction, "functions/agentic/agentic_workflow");
});

test("every registered callable appears exactly once as a leaf", () => {
  const { directories } = buildRuntimeProgramDirectories(tools, programs, TOOL_GROUPS);
  const leaves = Object.values(directories)
    .flat()
    .filter((entry) => entry.program_kind);

  assert.deepEqual(
    leaves.map((entry) => entry.name).sort(),
    ["bash", "read", "web_search", "agentic_workflow", "run_docs_question"].sort(),
  );
});

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
