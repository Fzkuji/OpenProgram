import assert from "node:assert/strict";
import test from "node:test";

import {
  canPreview, findingFixText, hunkRange, lineCommentText, reviewCommentsMessage, reviewHunks, reviewRows,
} from "../../components/center-tabs/review/review-diff-rows.ts";
import { installLoader } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

// The pane's socket requests are captured, then answered by each test.
installLoader({
  stubs: {
    // Stable functions: the pane's effects list `text` as a dependency, as the real hook keeps it stable.
    "@/lib/i18n": "const t = (key) => key; const text = (en) => en; const value = { t, text, locale: 'en' }; export const useTranslation = () => value; export const translateText = text;",
    "@/components/animated-icons": "export const FeatherIcon = () => null;",
    "@/lib/chat/send/send-queue": "export const sendFromTranscript = (sessionId, text) => { (globalThis.__sentMessages ??= []).push({ sessionId, text }); return 'q1'; };",
    "@/lib/format-utils/toast": "export const showToast = () => {};",
    // The socket is open unless a test closes it; reopening calls the reconnect handlers.
    "@/lib/runtime-bridge/state": "export const getSocket = () => ({ readyState: globalThis.__socketOpen === false ? 3 : 1 });",
    "@/lib/net/ws/change-bus": "export const onReconnected = (handler) => { (globalThis.__onReconnected ??= new Set()).add(handler); return () => globalThis.__onReconnected.delete(handler); };",
  },
  replace: {
    "/lib/net/ws/ws-request.ts": [
      "export const wsRequest = (action, payload, responseType, match, timeout, options) =>",
      "  new Promise((resolve) => (globalThis.__wsRequests ??= []).push({ action, payload, responseType, match, options, resolve }));",
      "export const wsFire = (action, payload) => { (globalThis.__wsFired ??= []).push({ action, payload }); return true; };",
    ].join("\n"),
    "/components/files/file-viewer.tsx": "export const FileViewer = () => null;",
    "/components/ui/tooltip.tsx": "export const HoverTip = ({ children }) => children;",
    "/components/ui/dropdown-menu.tsx": [
      // Menus render open, their items as buttons, so a test picks an item by name.
      "import { createElement } from 'react';",
      "export const DropdownMenu = ({ children }) => children;",
      "export const DropdownMenuTrigger = ({ children }) => children;",
      "export const DropdownMenuContent = ({ children }) => createElement('div', { role: 'menu' }, children);",
      "export const DropdownMenuItem = ({ children, onSelect, disabled }) => createElement('button', { type: 'button', role: 'menuitem', disabled, onClick: () => onSelect?.() }, children);",
      "export const DropdownMenuSeparator = () => null;",
      "export const DropdownMenuSub = ({ children }) => children;",
      "export const DropdownMenuSubTrigger = ({ children }) => createElement('div', null, children);",
      "export const DropdownMenuSubContent = ({ children }) => createElement('div', { role: 'menu' }, children);",
    ].join("\n"),
    "/components/ui/menu/menu-parts.tsx": [
      "import { createElement } from 'react';",
      "export const MenuRow = ({ label, checked }) => createElement('span', { 'data-checked': checked ? 'true' : undefined }, label);",
      "export const MenuIcon = () => null;",
    ].join("\n"),
  },
});
installDom();

const newFile = [
  "--- a/A4_Promo/note.md",
  "+++ b/A4_Promo/note.md",
  "@@ -0,0 +1,3 @@",
  "+# Title",
  "+",
  "+A long line that the pane wraps instead of cutting off",
].join("\n");

test("Review drops the file headers the pane header already shows", () => {
  const rows = reviewRows(newFile);
  assert.deepEqual(rows.map((row) => row.kind), ["hunk", "add", "add", "add"]);
  assert.deepEqual(rows.slice(1).map((row) => row.newNo), ["1", "2", "3"]);
});

test("A hunk reads as the line range it covers", () => {
  assert.deepEqual(hunkRange("@@ -0,0 +1,232 @@"), { from: 1, to: 232, context: "" });
  assert.deepEqual(hunkRange("@@ -10,4 +12,6 @@ def handler():"), { from: 12, to: 17, context: "def handler():" });
  // A deleted file has no new lines; count the old side.
  assert.deepEqual(hunkRange("@@ -1,5 +0,0 @@"), { from: 1, to: 5, context: "" });
  assert.deepEqual(hunkRange("@@ -3 +3 @@"), { from: 3, to: 3, context: "" });
  assert.equal(hunkRange("not a hunk"), null);
});

test("A missing newline note stays; renames and modes are dropped", () => {
  const rows = reviewRows([
    "diff --git a/x.py b/x.py",
    "index 1..2 100644",
    "@@ -1 +1 @@",
    "-a",
    "+b",
    "\\ No newline at end of file",
  ].join("\n"));
  assert.deepEqual(rows.map((row) => row.kind), ["hunk", "del", "add", "meta"]);
});

test("Documents and images offer a rendered preview; deleted files do not", () => {
  assert.equal(canPreview({ path: "/w/A4_Promo/宣传稿.md", op: "add" }), true);
  assert.equal(canPreview({ path: "/w/figure.png", op: "modify" }), true);
  assert.equal(canPreview({ path: "/w/paper.pdf", op: "modify" }), true);
  assert.equal(canPreview({ path: "/w/app.py", op: "modify" }), false);
  assert.equal(canPreview({ path: "/w/old.md", op: "delete" }), false);
});

test("Each hunk carries its old and new sides for a revert", () => {
  const hunks = reviewHunks([
    "--- a/x.py",
    "+++ b/x.py",
    "@@ -1,3 +1,3 @@ def f():",
    " keep",
    "-old",
    "+new",
    " tail",
    "@@ -10,2 +10,1 @@",
    " ctx",
    "-gone",
  ].join("\n"), "modify");
  assert.deepEqual(hunks, [
    {
      old_start: 1, new_start: 1, old_lines: ["keep", "old", "tail"], new_lines: ["keep", "new", "tail"],
      lines: [" keep", "-old", "+new", " tail"], revertible: true, stageable: true,
    },
    {
      old_start: 10, new_start: 10, old_lines: ["ctx", "gone"], new_lines: ["ctx"],
      lines: [" ctx", "-gone"], revertible: true, stageable: true,
    },
  ]);
});

test("Cut, staged, newline-less and whole-file hunks cannot be reverted", () => {
  // The page ends before the hunk's counted lines do.
  assert.equal(reviewHunks("@@ -1,3 +1,3 @@\n a\n-b", "modify")[0].revertible, false);
  // A continuation page starts with a synthetic header and no counts that fit.
  assert.equal(reviewHunks("@@ -5 +5 @@\n a\n b", "modify")[0].revertible, false);
  const staged = reviewHunks([
    "--- a/x.py [base-to-index]", "+++ b/x.py [base-to-index]", "@@ -1 +1 @@", "-a", "+b",
    "--- a/x.py [index-to-worktree]", "+++ b/x.py [index-to-worktree]", "@@ -1 +1 @@", "-b", "+c",
  ].join("\n"), "modify");
  assert.deepEqual(staged.map((hunk) => hunk.revertible), [false, true]);
  // A staged hunk can still be unstaged; cut and whole-file hunks cannot.
  assert.deepEqual(staged.map((hunk) => hunk.stageable), [true, true]);
  assert.equal(reviewHunks("@@ -1,3 +1,3 @@\n a\n-b", "modify")[0].stageable, false);
  assert.equal(reviewHunks("@@ -0,0 +1 @@\n+new", "add")[0].stageable, false);
  assert.equal(reviewHunks("@@ -1 +1 @@\n-a\n+b\n\\ No newline at end of file", "modify")[0].revertible, false);
  assert.equal(reviewHunks("@@ -0,0 +1 @@\n+new", "add")[0].revertible, false);
});

test("A line comment names the file and line and quotes the code", () => {
  assert.equal(
    lineCommentText("src/x.py", { kind: "add", text: "+return 1", oldNo: "", newNo: "12" }, "  why 1? "),
    "`src/x.py` line 12:\n> return 1\nwhy 1?",
  );
  assert.equal(
    lineCommentText("src/x.py", { kind: "del", text: "-x = 2", oldNo: "7", newNo: "" }, "keep this"),
    "`src/x.py` line 7 (removed):\n> x = 2\nkeep this",
  );
});

test("Pending comments go out as one message; a finding's Fix names its line", () => {
  assert.equal(
    reviewCommentsMessage([
      { rel: "a.ts", side: "new", line: 3, quote: "const x = 1;", body: "why 1?" },
      { rel: "b.ts", side: "old", line: 9, quote: "keep()", body: "keep this" },
    ]),
    "Please address my review comments on these changes:\n\n"
      + "`a.ts` line 3:\n> const x = 1;\nwhy 1?\n\n"
      + "`b.ts` line 9 (removed):\n> keep()\nkeep this",
  );
  assert.equal(
    findingFixText({ rel: "a.ts", line: 3, severity: "high", body: " Off by one. " }),
    "Please fix this review finding (high) in `a.ts` line 3:\n\nOff by one.",
  );
});

// ---- Review pane protocol -------------------------------------------------

const { createElement } = await import("react");
const { ReviewTabPane } = await import("../../components/center-tabs/review/review-tab-pane.tsx");

const sent = () => (globalThis.__wsRequests ??= []);
const sentOf = (action) => sent().filter((request) => request.action === action);
const settle = (view) => view.act(async () => { for (let i = 0; i < 6; i += 1) await Promise.resolve(); });
async function answer(view, request, data) {
  await view.act(async () => { request.resolve(data); });
  await settle(view);
}
const scopeAnswer = (request, extra) => ({
  session_id: request.payload.session_id, assistant_msg_id: request.payload.assistant_msg_id,
  scope: request.payload.scope, category: request.payload.category, query: request.payload.query, sort: request.payload.sort,
  ...extra,
});
const FILES = [
  { path: "/repo/a.ts", rel: "a.ts", op: "modify", added: 1, removed: 0 },
  { path: "/repo/b.ts", rel: "b.ts", op: "modify", added: 2, removed: 1 },
];
let session = 0;
async function mountPane(props = {}) {
  sent().length = 0;
  session += 1;
  const sessionId = `review-session-${session}`;
  const view = await render(createElement(ReviewTabPane, { sessionId, assistantMsgId: "assistant-1", ...props }));
  await settle(view);
  return { view, sessionId };
}

test("Review asks for the turn's first page of up to 100 files, without a snapshot", async () => {
  const { view, sessionId } = await mountPane();
  const [request] = sentOf("review_scope");
  assert.equal(request.responseType, "review_scope_result");
  assert.deepEqual(request.payload, {
    session_id: sessionId, assistant_msg_id: "assistant-1", scope: "turn",
    category: "All", query: "", sort: "path", cursor: null, limit: 100,
  });
  // The matcher accepts only this scope's answer.
  assert.equal(request.match(scopeAnswer(request)), true);
  assert.equal(request.match(scopeAnswer(request, { assistant_msg_id: "other" })), false);
  assert.equal(request.match(scopeAnswer(request, { session_id: "other" })), false);
  await view.unmount();
});

test("A ready scope selects the first file and loads its diff against the snapshot; one diff is mounted", async () => {
  const { view, sessionId } = await mountPane();
  const [scope] = sentOf("review_scope");
  await answer(view, scope, scopeAnswer(scope, { status: "ready", snapshot_id: "snap-1", files: FILES, file_count: 2 }));
  const [diff] = sentOf("review_file_diff");
  assert.equal(diff.payload.path, "/repo/a.ts");
  assert.equal(diff.payload.snapshot_id, "snap-1");
  assert.equal(diff.payload.session_id, sessionId);
  assert.equal(diff.match({ ...diff.payload, snapshot_id: "snap-0" }), false, "an answer for another snapshot is ignored");
  assert.equal(view.container.querySelector("[data-mounted-diff-count]").dataset.mountedDiffCount, "1");
  await answer(view, diff, { ...diff.payload, diff: "@@ -1 +1 @@\n-a\n+b", diff_state: "text", line_count: 2 });
  assert.equal(view.container.querySelector("[data-mounted-diff-lines]").dataset.mountedDiffLines, "2");
  await view.unmount();
});

test("A stale snapshot clears the pane and retries once; a second stale answer says nothing and is asked again later", async () => {
  const { view } = await mountPane();
  const [first] = sentOf("review_scope");
  await answer(view, first, scopeAnswer(first, { status: "stale", error: "STALE_SNAPSHOT" }));
  const retries = sentOf("review_scope");
  assert.equal(retries.length, 2, "the stale answer triggers one fresh request");
  assert.equal(retries[1].payload.cursor, null);
  assert.equal(retries[1].payload.snapshot_id, undefined);
  await answer(view, retries[1], scopeAnswer(retries[1], { status: "stale", error: "STALE_SNAPSHOT" }));
  assert.equal(sentOf("review_scope").length, 2, "no immediate retry loop");
  assert.doesNotMatch(view.container.textContent, /STALE_SNAPSHOT|No changes in this scope/);
  assert.equal(view.container.querySelector("[data-mounted-diff-count]").dataset.mountedDiffCount, "0");
  await view.act(async () => { await new Promise((resolve) => setTimeout(resolve, 1100)); });
  assert.equal(sentOf("review_scope").length, 3, "asked again after a backoff");
  await view.unmount();
});

test("A stale snapshot on the diff clears the selection and reloads the scope", async () => {
  const { view } = await mountPane();
  const [scope] = sentOf("review_scope");
  await answer(view, scope, scopeAnswer(scope, { status: "ready", snapshot_id: "snap-1", files: FILES, file_count: 2 }));
  const [diff] = sentOf("review_file_diff");
  await answer(view, diff, { ...diff.payload, error: "STALE_SNAPSHOT" });
  assert.equal(sentOf("review_scope").length, 2);
  assert.equal(view.container.querySelector("[data-mounted-diff-count]").dataset.mountedDiffCount, "0");
  await view.unmount();
});

test("A history change in the same session reloads the scope; another session's does not", async () => {
  const { view, sessionId } = await mountPane();
  const [scope] = sentOf("review_scope");
  await answer(view, scope, scopeAnswer(scope, { status: "ready", snapshot_id: "snap-1", files: FILES, file_count: 2 }));
  await view.act(() => { window.dispatchEvent(new window.CustomEvent("turn-files-history-changed", { detail: { sessionId: "elsewhere" } })); });
  await settle(view);
  assert.equal(sentOf("review_scope").length, 1);
  await view.act(() => { window.dispatchEvent(new window.CustomEvent("turn-files-history-changed", { detail: { sessionId } })); });
  await settle(view);
  const scopes = sentOf("review_scope");
  assert.equal(scopes.length, 2);
  assert.equal(scopes[1].payload.cursor, null);
  await view.unmount();
});

test("A scope request with no reply says nothing and is asked again", async () => {
  const { view } = await mountPane();
  assert.doesNotMatch(view.container.textContent, /Loading/, "no notice while the files are read");
  const [scope] = sentOf("review_scope");
  await answer(view, scope, null);
  assert.doesNotMatch(view.container.textContent, /disconnected|Loading|No changes in this scope/i);
  await view.act(() => new Promise((resolve) => setTimeout(resolve, 1100)));
  await settle(view);
  assert.equal(sentOf("review_scope").length, 2, "the read is retried after a short wait");
  await view.unmount();
});

const button = (view, label) => [...view.container.querySelectorAll("button")]
  .find((element) => element.getAttribute("aria-label") === label || element.textContent.trim() === label);
const DIFF = "--- a/a.ts [index-to-worktree]\n+++ b/a.ts [index-to-worktree]\n@@ -1,2 +1,2 @@\n keep\n-old\n+new";

async function readyPane(scopeName, extra = {}) {
  const mounted = await mountPane({ initialScope: scopeName });
  const [scope] = sentOf("review_scope");
  assert.equal(scope.payload.scope, scopeName);
  await answer(mounted.view, scope, scopeAnswer(scope, {
    status: "ready", snapshot_id: "snap-1", files: FILES, file_count: 2, source: "git", ...extra,
  }));
  const [diff] = sentOf("review_file_diff");
  await answer(mounted.view, diff, { ...diff.payload, diff: DIFF, diff_state: "available", line_count: 4 });
  return mounted;
}

test("The header is one row: the scope menu with its totals, and a … menu with Review with agent and File type", async () => {
  const { view } = await mountPane();
  const [scopeMenu, moreMenu] = view.container.querySelectorAll("header [role=menu]");
  const items = (menu) => [...menu.querySelectorAll(":scope > * > [role=menuitem], :scope > [role=menuitem]")].map((item) => item.textContent);
  assert.deepEqual(items(scopeMenu), ["This turn", "Unstaged", "Staged", "Branch"]);
  assert.ok(button(view, "Review scope").textContent.startsWith("This turn"));
  assert.ok(button(view, "Review options"));
  assert.ok(moreMenu.textContent.includes("Review with agent"));
  assert.ok(moreMenu.textContent.includes("File type"));
  assert.equal(view.container.querySelector("[role=tab]"), null, "no segmented scope control");
  await view.act(async () => button(view, "Branch").click());
  assert.equal(sentOf("review_scope").at(-1).payload.scope, "branch");
  await view.unmount();
});

test("Unstaged offers Stage on a hunk and the file; Staged offers Unstage; no staging without a local repo", async () => {
  let { view } = await readyPane("unstaged", { staging: true });
  assert.ok(button(view, "Stage"), "hunk Stage");
  assert.ok(button(view, "Revert"), "hunk Revert stays");
  await view.act(async () => button(view, "Stage").click());
  const [stage] = sentOf("review_stage");
  assert.equal(stage.responseType, "review_stage_result");
  assert.equal(stage.payload.scope, "unstaged");
  assert.equal(stage.payload.path, "/repo/a.ts");
  assert.deepEqual(stage.payload.hunk, { old_start: 1, new_start: 1, lines: [" keep", "-old", "+new"] });
  await answer(view, stage, { status: "ready" });
  const reload = sentOf("review_scope");
  assert.equal(reload.length, 2, "the scope reloads after staging");
  await answer(view, reload[1], scopeAnswer(reload[1], {
    status: "ready", snapshot_id: "snap-2", files: FILES, file_count: 2, source: "git", staging: true,
  }));
  await view.act(async () => button(view, "Stage file").click());
  const stageFile = sentOf("review_stage");
  assert.equal(stageFile.length, 2);
  assert.equal(stageFile[1].payload.hunk, undefined);
  assert.equal(stageFile[1].payload.snapshot_id, "snap-2");
  await view.unmount();

  ({ view } = await readyPane("staged", { staging: true }));
  assert.ok(button(view, "Unstage file"));
  await view.unmount();

  ({ view } = await readyPane("unstaged", { staging: false }));
  assert.equal(button(view, "Stage"), undefined);
  assert.equal(button(view, "Stage file"), undefined);
  await view.unmount();
});

test("Line comments stay pinned under their line and go out together", async () => {
  const { useReviewNotes } = await import("../../components/center-tabs/review/review-notes-store.ts");
  const { view, sessionId } = await readyPane("unstaged", { staging: true });
  await view.act(async () => {
    useReviewNotes.getState().addComment(sessionId, {
      id: "c1", scope: "unstaged", path: "/repo/a.ts", rel: "a.ts", side: "new", line: 2, quote: "new", body: "Why new?",
    });
  });
  const card = view.container.querySelector("[data-note=comment]");
  assert.match(card.textContent, /Why new\?/);
  assert.ok(button(view, "Edit") && button(view, "Delete"));
  assert.match(view.container.textContent, /1 comment/);
  globalThis.__sentMessages = [];
  await view.act(async () => button(view, "Send to agent").click());
  assert.deepEqual(globalThis.__sentMessages, [{
    sessionId,
    text: "Please address my review comments on these changes:\n\n`a.ts` line 2:\n> new\nWhy new?",
  }]);
  assert.deepEqual(useReviewNotes.getState().comments[sessionId], []);
  assert.equal(view.container.querySelector("[data-note=comment]"), null);
  await view.unmount();
});

test("Review with agent shows its findings under their lines; Fix sends them", async () => {
  const { view, sessionId } = await readyPane("unstaged", { staging: true });
  await view.act(async () => button(view, "Review with agent").click());
  const [review] = sentOf("review_agent");
  assert.equal(review.responseType, "review_agent_result");
  assert.equal(review.payload.scope, "unstaged");
  assert.equal(review.payload.snapshot_id, "snap-1");
  assert.equal(button(view, "Stop review").disabled, false, "the busy button stops the review");
  await answer(view, review, { status: "ready", findings: [
    { path: "a.ts", line: 2, severity: "high", body: "This breaks callers." },
  ] });
  const card = view.container.querySelector("[data-note=finding]");
  assert.match(card.textContent, /Agent review · high/);
  assert.match(card.textContent, /This breaks callers\./);
  globalThis.__sentMessages = [];
  await view.act(async () => button(view, "Fix").click());
  assert.equal(globalThis.__sentMessages[0].sessionId, sessionId);
  assert.match(globalThis.__sentMessages[0].text, /review finding \(high\) in `a\.ts` line 2/);
  assert.equal(view.container.querySelector("[data-note=finding]"), null);
  assert.ok(button(view, "Review with agent"));
  await view.unmount();
});

test("Stop review cancels the running review on the server and ignores its answer", async () => {
  const { view } = await readyPane("unstaged");
  await view.act(async () => button(view, "Review with agent").click());
  const [review] = sentOf("review_agent");
  assert.ok(review.payload.review_id, "the review is named so it can be stopped");
  assert.ok(review.match.signal, "the wait is abortable");
  globalThis.__wsFired = [];
  await view.act(async () => button(view, "Stop review").click());
  assert.deepEqual(globalThis.__wsFired, [{ action: "review_agent_cancel", payload: { review_id: review.payload.review_id } }]);
  assert.equal(review.match.signal.aborted, true);
  assert.equal(button(view, "Review with agent").disabled, false);
  await answer(view, review, { status: "ready", findings: [
    { path: "a.ts", line: 2, severity: "high", body: "Too late." },
  ] });
  assert.equal(view.container.querySelector("[data-note=finding]"), null);
  await view.unmount();
});

test("A review whose socket dropped is asked for again and its findings still arrive", async () => {
  const { view } = await readyPane("unstaged");
  await view.act(async () => button(view, "Review with agent").click());
  const [review] = sentOf("review_agent");
  globalThis.__socketOpen = false;
  await answer(view, review, null);
  assert.equal(sentOf("review_agent_wait").length, 0, "waits for the socket to come back");
  assert.ok(button(view, "Stop review"), "still running meanwhile");
  globalThis.__socketOpen = true;
  await view.act(async () => { for (const handler of [...globalThis.__onReconnected]) handler(); });
  const [wait] = sentOf("review_agent_wait");
  assert.equal(wait.payload.review_id, review.payload.review_id);
  await answer(view, wait, { status: "ready", findings: [
    { path: "a.ts", line: 2, severity: "medium", body: "Late but kept." },
  ] });
  assert.match(view.container.querySelector("[data-note=finding]").textContent, /Late but kept\./);
  await view.unmount();
});

test("A failed Review with agent only makes the button usable again", async () => {
  const { view } = await readyPane("unstaged");
  await view.act(async () => button(view, "Review with agent").click());
  const [review] = sentOf("review_agent");
  await answer(view, review, null);
  const [wait] = sentOf("review_agent_wait");
  await answer(view, wait, { status: "error", error_code: "UNKNOWN_REVIEW" });
  assert.equal(button(view, "Review with agent").disabled, false);
  assert.equal(view.container.querySelector("[data-note=finding]"), null);
  await view.unmount();
});
