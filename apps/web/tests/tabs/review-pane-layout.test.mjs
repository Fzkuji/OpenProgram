import assert from "node:assert/strict";
import test from "node:test";

import { canPreview, hunkRange, lineCommentText, reviewHunks, reviewRows } from "../../components/center-tabs/review/review-diff-rows.ts";
import { installLoader } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

// The pane's socket requests are captured, then answered by each test.
installLoader({
  stubs: {
    // Stable functions: the pane's effects list `text` as a dependency, as the real hook keeps it stable.
    "@/lib/i18n": "const t = (key) => key; const text = (en) => en; const value = { t, text, locale: 'en' }; export const useTranslation = () => value; export const translateText = text;",
    "@/components/animated-icons": "export const FeatherIcon = () => null;",
    "@/components/chat/messages/editing/quote-to-chat": "export const appendToComposer = () => {};",
    "@/lib/format-utils/toast": "export const showToast = () => {};",
  },
  replace: {
    "/lib/net/ws/ws-request.ts": [
      "export const wsRequest = (action, payload, responseType, match, timeout, options) =>",
      "  new Promise((resolve) => (globalThis.__wsRequests ??= []).push({ action, payload, responseType, match, options, resolve }));",
    ].join("\n"),
    "/components/files/file-viewer.tsx": "export const FileViewer = () => null;",
    "/components/ui/tooltip.tsx": "export const HoverTip = ({ children }) => children;",
    "/components/ui/dropdown-menu.tsx": [
      "export const DropdownMenu = ({ children }) => children;",
      "export const DropdownMenuTrigger = ({ children }) => children;",
      "export const DropdownMenuContent = () => null;",
      "export const DropdownMenuItem = () => null;",
      "export const DropdownMenuSeparator = () => null;",
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
    { new_start: 1, old_lines: ["keep", "old", "tail"], new_lines: ["keep", "new", "tail"], revertible: true },
    { new_start: 10, old_lines: ["ctx", "gone"], new_lines: ["ctx"], revertible: true },
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
async function mountPane() {
  sent().length = 0;
  session += 1;
  const sessionId = `review-session-${session}`;
  const view = await render(createElement(ReviewTabPane, { sessionId, assistantMsgId: "assistant-1" }));
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

test("A stale snapshot clears the pane and retries once; a second stale answer is shown", async () => {
  const { view } = await mountPane();
  const [first] = sentOf("review_scope");
  await answer(view, first, scopeAnswer(first, { status: "stale", error: "STALE_SNAPSHOT" }));
  const retries = sentOf("review_scope");
  assert.equal(retries.length, 2, "the stale answer triggers one fresh request");
  assert.equal(retries[1].payload.cursor, null);
  assert.equal(retries[1].payload.snapshot_id, undefined);
  await answer(view, retries[1], scopeAnswer(retries[1], { status: "stale", error: "STALE_SNAPSHOT" }));
  assert.equal(sentOf("review_scope").length, 2, "no retry loop");
  assert.match(view.container.textContent, /STALE_SNAPSHOT/);
  assert.equal(view.container.querySelector("[data-mounted-diff-count]").dataset.mountedDiffCount, "0");
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

test("A disconnected scope request says so instead of loading forever", async () => {
  const { view } = await mountPane();
  const [scope] = sentOf("review_scope");
  await answer(view, scope, null);
  assert.match(view.container.textContent, /Review request disconnected/);
  assert.doesNotMatch(view.container.textContent, /Loading files/);
  await view.unmount();
});
