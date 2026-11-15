import assert from "node:assert/strict";
import test from "node:test";

import {
  branchWorktree,
  changedFiles,
  errorCode,
  errorText,
  markerText,
  shortName,
  subAgentWorktrees,
} from "../../lib/worktrees/worktree-model.ts";

const text = (en) => en;
const row = (over) => ({
  id: "wt_1", source_repo: "/repo", worktree_path: "/state/worktrees/wt_1-x", branch_name: "op/wt/x-abc123",
  status: "active", created_at: 1, parent_session: "s1", parent_job: null, parent_branch: null, ...over,
});

test("a branch reads its own open worktree before a finished one", () => {
  const rows = [
    row({ id: "old", parent_branch: "b1", status: "merged", created_at: 5 }),
    row({ id: "open", parent_branch: "b1", status: "active", created_at: 2 }),
    row({ id: "other", parent_branch: "b2", created_at: 9 }),
  ];
  assert.equal(branchWorktree(rows, "b1").id, "open");
  assert.equal(branchWorktree(rows, "b2").id, "other");
  assert.equal(branchWorktree(rows, "b3"), null);
});

test("a branch with none of its own runs in the conversation-wide worktree", () => {
  const rows = [row({ id: "shared" }), row({ id: "mine", parent_branch: "b1", status: "discarded", created_at: 3 })];
  assert.equal(branchWorktree(rows, "b2").id, "shared");
  // Its own finished one still marks the branch once the shared one is gone.
  assert.equal(branchWorktree([rows[1]], "b1").id, "mine");
});

test("sub-agent worktrees are listed on the branch that spawned them while open", () => {
  const rows = [
    row({ id: "sub1", parent_job: "job_1", parent_branch: "b1" }),
    row({ id: "sub2", parent_job: "job_2", parent_branch: "b2" }),
    row({ id: "done", parent_job: "job_3", parent_branch: "b1", status: "discarded" }),
    row({ id: "branch", parent_branch: "b1" }),
  ];
  assert.deepEqual(subAgentWorktrees(rows, "b1").map((r) => r.id), ["sub1"]);
  // A sub-agent's worktree is never the branch's own.
  assert.equal(branchWorktree(rows, "b1").id, "branch");
});

test("changed files read the summary while open and the merge record after", () => {
  assert.equal(changedFiles(row({ summary: { commits: 1, files: ["a", "b"], dirty: true, exists: true } })), 2);
  assert.equal(changedFiles(row({ status: "merged", files_changed: 4 })), 4);
  assert.equal(changedFiles(row({})), 0);
});

test("labels and markers", () => {
  assert.equal(shortName(row({})), "x-abc123");
  assert.match(markerText(row({ summary: { commits: 0, files: ["a"], dirty: true, exists: true } }), text), /Runs in its own worktree · op\/wt\/x-abc123 · 1 file changed/);
  assert.match(markerText(row({ status: "merged", files_changed: 2 }), text), /merged into \/repo · 2 files/);
  assert.equal(markerText(row({ status: "discarded" }), text), "Worktree discarded");
  assert.match(markerText(row({ status: "kept" }), text), /kept as branch op\/wt\/x-abc123/);
});

test("refusals read in the person's words", () => {
  assert.equal(errorCode("merge_conflict: CONFLICT (content)"), "merge_conflict");
  assert.match(errorText("merge_conflict: x", text), /conflict with the checkout/);
  assert.match(errorText("not_a_git_repo: /x", text), /not in a git repository/);
  assert.equal(errorText("something else", text), "something else");
});
