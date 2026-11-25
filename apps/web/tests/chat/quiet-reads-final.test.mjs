// Reads that fail are asked again quietly, but a permanent answer ends them:
// a conversation the server does not have, an archive it cannot list, a
// sub-agent read it refuses, a file that is gone.
import assert from "node:assert/strict";
import { test } from "node:test";

import { installLoader, STUBS } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

installLoader({ stubs: { "@/lib/i18n": STUBS.i18n } });
installDom({ pathname: "/s/main" });
globalThis.WebSocket ??= { OPEN: 1, CONNECTING: 0 };

const { createElement } = await import("react");
const { useSessionStore } = await import("../../lib/session-store/index.ts");
const { setSocket } = await import("../../lib/runtime-bridge/state.ts");
const {
  TranscriptReadStatus, permanentLoadFailure, transcriptReadSettled,
} = await import("../../components/chat/messages/list/transcript-read-status.tsx");
const { archiveListRefused } = await import("../../components/chat/messages/attachments/file-card-previews.tsx");
const { subAgentReadRefused } = await import("../../lib/chat/transcript/sub-agent-transcripts.ts");
const { documentReadOutcome } = await import("../../components/files/surfaces/document-window.tsx");
const { reviewScopeIsEmpty } = await import("../../components/center-tabs/review/review-tab-pane.tsx");

const quietly = async (run) => {
  const warn = console.warn;
  console.warn = () => {};
  try { return await run(); } finally { console.warn = warn; }
};
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const loadError = (data) => ({ type: "operation_error", data: { action: "load_session", session_id: "gone", ...data } });

test("a load the server answers with not-found or a malformed request is permanent", () => {
  assert.equal(permanentLoadFailure(loadError({ code: "session_not_found" })), "gone");
  assert.equal(permanentLoadFailure(loadError({ code: "invalid_request" })), "gone");
  assert.equal(permanentLoadFailure(loadError({ code: "session_read_failed", retryable: true })), null);
  assert.equal(permanentLoadFailure(loadError({ code: "handler_error" })), null);
  assert.equal(permanentLoadFailure({ type: "operation_error", data: { action: "chat", code: "session_not_found", session_id: "x" } }), null);
});

test("a conversation the server does not have stops being read again", async () => {
  const listeners = new Set();
  const socket = {
    readyState: 1,
    sent: [],
    addEventListener: (type, listener) => { if (type === "message") listeners.add(listener); },
    removeEventListener: (type, listener) => listeners.delete(listener),
    send(payload) { this.sent.push(JSON.parse(payload)); },
  };
  setSocket(socket);
  useSessionStore.getState().setTranscriptReadStatus("gone", "loading");
  const view = await render(createElement(TranscriptReadStatus, { sessionId: "gone" }));
  try {
    const frame = JSON.stringify(loadError({ code: "session_not_found" }));
    for (const listener of listeners) listener({ data: frame });
    useSessionStore.getState().setTranscriptReadStatus("gone", "error");
    await wait(1200);
    assert.equal(transcriptReadSettled("gone"), true);
    assert.equal(socket.sent.length, 0, "no read is sent again");
  } finally {
    await view.unmount();
    setSocket(null);
  }
});

test("an archive the server refuses is a fact; a failed transfer is not", () => {
  assert.equal(archiveListRefused(new Error("ARCHIVE_LIST_422")), true);
  assert.equal(archiveListRefused(new Error("ARCHIVE_LIST_404")), true);
  assert.equal(archiveListRefused(new Error("ARCHIVE_LIST_503")), false);
  assert.equal(archiveListRefused(new TypeError("Failed to fetch")), false);
});

test("a sub-agent read refused by the server is final; a failed read is asked again", () => {
  assert.equal(subAgentReadRefused("session_id and job_id or head_id are required"), true);
  assert.equal(subAgentReadRefused("OperationalError: database is locked"), false);
});

test("a document read: gone closes, a 4xx is final, a transfer failure is asked again", () => {
  assert.equal(documentReadOutcome(new Error("Unable to read document (404).")), "gone");
  assert.equal(documentReadOutcome(new Error("Unable to read document (403).")), "final");
  assert.equal(documentReadOutcome(new Error("Unable to read document (502).")), "retry");
  assert.equal(documentReadOutcome(new TypeError("Failed to fetch")), "retry");
  assert.equal(documentReadOutcome(new Error("The local document draft is unreadable. It has been retained.")), "final");
});

test("review says no changes only when the server listed an empty scope", () => {
  assert.equal(reviewScopeIsEmpty({ loading: false, status: "ready", files: [] }), true);
  assert.equal(reviewScopeIsEmpty({ loading: false, status: "unavailable", files: [] }), true);
  assert.equal(reviewScopeIsEmpty({ loading: false, status: "error", files: [] }), false);
  assert.equal(reviewScopeIsEmpty({ loading: false, status: "stale", files: [] }), false);
  assert.equal(reviewScopeIsEmpty({ loading: true, status: "ready", files: [] }), false);
});
