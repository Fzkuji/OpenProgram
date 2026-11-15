import assert from "node:assert/strict";
import test from "node:test";

import {
  applicationNoteLabel,
  applicationNoteText,
  keptVersionFailure,
  programInvocationName,
} from "../../components/programs/programs-catalog.ts";

const en = (english) => english;

test("application entities invoke and favorite their exported callable", () => {
  assert.equal(programInvocationName({
    name: "notes_harness",
    path: "applications/notes_harness",
    kind: "folder",
    program_kind: "application",
    has_children: false,
    callable_name: "notes_agent",
  }), "notes_agent");
  assert.equal(programInvocationName({
    name: "custom_app",
    path: "applications/custom_app",
    kind: "folder",
    program_kind: "application",
    has_children: false,
  }), "custom_app");
});

test("an application without an Agent entry gets a neutral note, not a warning", () => {
  assert.equal(applicationNoteText(undefined, en), null);
  assert.deepEqual(applicationNoteText({ kind: "ui_only" }, en), {
    title: "No Agent entry; UI-only application",
    lines: ["Open it from Applications."],
  });
  assert.equal(applicationNoteLabel({ kind: "ui_only" }, en), "UI only");

  const toolkit = applicationNoteText({
    kind: "library",
    exports: ["run_stage", "compete", "STAGES"],
  }, en);
  assert.equal(toolkit.title, "No Agent entry; registers no tools");
  assert.deepEqual(toolkit.lines, [
    "A Python library for other Programs: run_stage, compete, STAGES.",
  ]);
  assert.equal(applicationNoteLabel({ kind: "library" }, en), "Python library");
  assert.equal(applicationNoteLabel(undefined, en), "");
});

test("the detail meta line names what a Program is", async () => {
  const { programKindLabel } = await import("../../components/programs/programs-catalog.ts");
  assert.equal(programKindLabel({ program_kind: "application", origin: "builtin", form: "package" }, en), "Built-in application");
  assert.equal(programKindLabel({ program_kind: "application", origin: "installed", form: "application" }, en), "Installed application");
  assert.equal(programKindLabel({ program_kind: "application", origin: "installed", form: "package" }, en), "Installed package");
  assert.equal(programKindLabel({ program_kind: "application", origin: "installed", form: "package", application_id: "local.x" }, en), "Installed application");
  assert.equal(programKindLabel({ program_kind: "tool" }, en), "Built-in tool");
});

test("capabilities read as one phrase and the call logic as one count", async () => {
  const { applicationPermissions, callLogicSummary } = await import("../../components/programs/programs-catalog.ts");
  assert.equal(applicationPermissions(["storage.app", "model.invoke", "files.project.read", "files.project.write"], en), "Read and write project files, call the model, keep its own data");
  assert.equal(applicationPermissions(["files.project.read"], en), "Read project files");
  assert.equal(applicationPermissions([], en), "");
  const node = { id: "a" };
  assert.equal(callLogicSummary({ root: "a", nodes: [node], edges: [] }, en), "no calls to other Programs");
  assert.equal(callLogicSummary({ root: "a", nodes: [node], edges: [{ source: "a" }, { source: "a" }, { source: "b" }] }, en), "2 direct calls");
  assert.equal(callLogicSummary({ root: "a", nodes: [node], edges: [], entries: ["x", "y", "z"] }, en), "3 entries");
  assert.equal(callLogicSummary(null, en), "");
});

test("Show in Abilities resolves an application id or a built-in directory", async () => {
  const { requestProgramSelection, takeProgramSelection, resolveProgramSelection } = await import("../../components/programs/programs-selection.ts");
  const entries = [
    { name: "browser", path: "applications/browser" },
    { name: "auto_research", path: "applications/auto_research", application_id: "local.auto-research" },
  ];
  requestProgramSelection({ applicationId: "local.auto-research" });
  const request = takeProgramSelection();
  assert.equal(takeProgramSelection(), null);
  assert.equal(resolveProgramSelection(entries, request), "applications/auto_research");
  assert.equal(resolveProgramSelection(entries, { name: "browser" }), "applications/browser");
  assert.equal(resolveProgramSelection(entries, { applicationId: "missing" }), null);
});

test("a kept version names both versions and the reason without its location", () => {
  const entry = {
    load_state: "error",
    load_note: "Kept version abc1234; version def5678 failed.",
    load_error: "ModuleNotFoundError: No module named 'pandas' (/programs/weekly/main.py:3)",
  };
  const failure = keptVersionFailure(entry, en);
  assert.equal(failure.title, "Kept version abc1234; version def5678 failed.");
  assert.equal(failure.message, "It could not load: No module named 'pandas'. This Program keeps working on version abc1234 until the error is fixed.");
  assert.equal(failure.fullError, entry.load_error);
});

test("other load problems keep their ordinary line", () => {
  assert.equal(keptVersionFailure({ load_state: "error", load_error: "SyntaxError: x" }, en), null);
  assert.equal(keptVersionFailure({ load_state: "loaded", load_note: "Kept version a; version b failed." }, en), null);
});
