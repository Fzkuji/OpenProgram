import assert from "node:assert/strict";
import test from "node:test";

import {
  isUserManualWorkflowEntry,
  isWorkflowCapability,
  programSourceCategory,
  workflowCapabilityName,
} from "../components/programs/programs-source.ts";

const capabilities = [
  { name: "search_workflows", path: "workflow/search_workflows" },
  { name: "create_workflow", path: "workflow/create_workflow" },
  { name: "revise_workflow", path: "workflow/revise_workflow" },
  { name: "auto_workflow", path: "workflow/auto_workflow.py" },
];

test("four workflow entries are management capabilities by name or path", () => {
  for (const entry of capabilities) {
    const programKind = entry.name === "auto_workflow" ? "workflow" : "agentic_function";
    assert.equal(isWorkflowCapability({ ...entry, program_kind: programKind }), true);
    assert.equal(workflowCapabilityName({ path: entry.path, program_kind: programKind }), entry.name);
  }
  assert.equal(isWorkflowCapability({
    name: "search_workflows.py",
    path: "workflow/search_workflows.py",
  }), true);
  assert.equal(isWorkflowCapability({
    name: "wrapper",
    callable_name: "create_workflow",
    path: "workflow/not_yet_registered",
  }), true);
});

test("auto_workflow is the user-only auto entry", () => {
  assert.equal(programSourceCategory({ name: "auto_workflow" }), "auto_entry");
  assert.equal(isUserManualWorkflowEntry({ path: "workflow/auto_workflow.py" }), true);
  assert.equal(isUserManualWorkflowEntry({ name: "search_workflows" }), false);
  assert.equal(programSourceCategory({ name: "create_workflow" }), "workflow_capability");
});

test("ordinary programs stay on their source kind", () => {
  assert.equal(isWorkflowCapability({
    name: "web_research",
    path: "workflow/web_research",
    program_kind: "workflow",
  }), false);
  assert.equal(programSourceCategory({
    name: "web_research",
    path: "workflow/web_research",
    program_kind: "workflow",
  }), "workflow");
  assert.equal(isWorkflowCapability({
    name: "docs_question",
    path: "workflow/docs_question",
    program_kind: "agentic_function",
  }), false);
  assert.equal(programSourceCategory({
    name: "translate",
    path: "workflow/translate",
    program_kind: "agentic_function",
  }), "agentic_function");
});
