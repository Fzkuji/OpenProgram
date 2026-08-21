import assert from "node:assert/strict";
import test from "node:test";

import {
  isUserManualWorkflowEntry,
  isWorkflowCapability,
  programSourceCategory,
  workflowCapabilityName,
} from "../components/programs/programs-source.ts";

const capabilities = [
  { name: "search_workflows", path: "functions/agentic/workflow/search_workflows" },
  { name: "create_workflow", path: "functions/agentic/workflow/create_workflow" },
  { name: "revise_workflow", path: "functions/agentic/workflow/revise_workflow" },
  { name: "auto_workflow", path: "functions/agentic/workflow/auto_workflow" },
];

test("four workflow entries are management capabilities by name or path", () => {
  for (const entry of capabilities) {
    assert.equal(isWorkflowCapability({ ...entry, program_kind: "agentic_function" }), true);
    assert.equal(workflowCapabilityName({ path: entry.path, program_kind: "agentic_function" }), entry.name);
  }
  assert.equal(isWorkflowCapability({
    name: "search_workflows.py",
    path: "functions/agentic/workflow/search_workflows.py",
  }), true);
  assert.equal(isWorkflowCapability({
    name: "wrapper",
    callable_name: "create_workflow",
    path: "functions/agentic/not_yet_registered",
  }), true);
});

test("auto_workflow is the user-only auto entry", () => {
  assert.equal(programSourceCategory({ name: "auto_workflow" }), "auto_entry");
  assert.equal(isUserManualWorkflowEntry({ path: "functions/agentic/auto_workflow" }), true);
  assert.equal(isUserManualWorkflowEntry({ name: "search_workflows" }), false);
  assert.equal(programSourceCategory({ name: "create_workflow" }), "workflow_capability");
});

test("ordinary programs stay on their source kind", () => {
  assert.equal(isWorkflowCapability({
    name: "web_research",
    path: "workflows/web_research",
    program_kind: "workflow",
  }), false);
  assert.equal(programSourceCategory({
    name: "web_research",
    path: "workflows/web_research",
    program_kind: "workflow",
  }), "workflow");
  assert.equal(isWorkflowCapability({
    name: "deep_work",
    path: "functions/agentic/deep_work",
    program_kind: "agentic_function",
  }), false);
  assert.equal(programSourceCategory({
    name: "translate",
    path: "functions/agentic/translate",
    program_kind: "agentic_function",
  }), "agentic_function");
});
