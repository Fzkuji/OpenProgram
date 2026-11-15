/** Numbered transcript updates and what happens to a view's window around them.
 *
 *  A view's cursor is the revision of its last full read, advanced by each
 *  update applied here. An update that does not follow the cursor means the
 *  view missed something, and it reads once at the anchor. */
import { applyTranscriptChanges, viewHoldsTurn, type UpdateEffect } from "@/lib/chat/transcript/apply-transcript-update";
import { isSubAgentChatKey } from "@/lib/chat/transcript/sub-agent-keys";
import { reloadSubAgentTranscript, subAgentCursor, advanceSubAgentCursor, forgetSubAgentCursor, subAgentViews } from "@/lib/chat/transcript/sub-agent-transcripts";
import type { TranscriptChange } from "@/lib/chat/transcript/wire-types.generated";
import { useSessionHistory, updateSessionHistory } from "@/lib/chat/history/session-history";
import { flushTextFrames, onTextFrameGap } from "@/lib/net/ws/text-frames";
import { applyReadToView } from "@/lib/chat/transcript/apply-read";
import { rowsOfTurns } from "@/lib/chat/transcript/turn-rows";
import { settleDeliveredSteer } from "@/lib/chat/send/send-queue";
import { registerSessionHistory, type HistoryPage } from "@/lib/chat/history/session-history";
import type { TurnWindow, WireTurn } from "@/lib/chat/history/history-window";
import { selectServerRows, useSessionStore } from "@/lib/session-store";
import { dropHistoryWindow, historyWindowOf, isViewShown, retrySessionHistory, seedTurnWindow } from "./session-history-loader";
import { exclusiveSessionRead, requestSessionLoad, sessionReadBusy } from "./session-load";

interface UpdateFrame {
  session_id: string;
  view: string;
  previous: number;
  revision: number;
  changes: TranscriptChange[];
}

function readAtAnchor(view: string): void {
  if (sessionReadBusy(view)) return;
  if (isSubAgentChatKey(view)) void exclusiveSessionRead(view, () => reloadSubAgentTranscript(view), undefined);
  else void retrySessionHistory(view);
}

function readLatest(view: string): void {
  if (isSubAgentChatKey(view) && sessionReadBusy(view)) return;
  if (isSubAgentChatKey(view)) void exclusiveSessionRead(view, () => reloadSubAgentTranscript(view), undefined);
  else requestSessionLoad({ action: "load_session", session_id: view }, true);
}

function runEffect(view: string, effect: UpdateEffect): void {
  if (effect.kind === "settle_steer") {
    if (!isSubAgentChatKey(view)) settleDeliveredSteer(view, effect.commandId);
  } else if (effect.kind === "read_latest") readLatest(view);
  else readAtAnchor(view);
}

onTextFrameGap(readAtAnchor);

/** A chat ack told a socket with no view of its session what the view holds. */
export function applyFirstRead(sessionId: string, read: { turns: WireTurn[]; window: TurnWindow }): void {
  seedTurnWindow(sessionId, read.turns, read.window);
  registerSessionHistory(sessionId, read.window as unknown as HistoryPage);
  applyReadToView(sessionId, rowsOfTurns(read.turns));
}

/** Apply one update, or send the view for one read when it has fallen behind. */
export function applyTranscriptUpdate(frame: UpdateFrame): void {
  const view = frame.view;
  const isSubAgent = isSubAgentChatKey(view);
  const page = isSubAgent ? undefined : useSessionHistory.getState().pages[view];
  const cursor = isSubAgent ? subAgentCursor(view) : page?.revision;
  // A frame for a view with no window names nothing this client can place.
  if (cursor === undefined || frame.revision <= cursor) return;
  const stale = frame.changes.find((change) => change.kind === "window_stale");
  if (stale) {
    runEffect(view, { kind: stale.reason === "branch" ? "read_latest" : "read_anchor" });
    return;
  }
  if (frame.previous !== cursor) { readAtAnchor(view); return; }
  flushTextFrames();
  const store = useSessionStore.getState();
  const window = isSubAgent ? undefined : historyWindowOf(view);
  const atLatest = isSubAgent ? true : !page?.after;
  const rows = selectServerRows(view)(store);
  const result = applyTranscriptChanges(
    { order: store.messageOrder[view] ?? [], rows, overlays: store.overlaysByView[view], heldFrames: store.heldFramesByView[view] },
    frame.changes,
    { holdsTurn: viewHoldsTurn(window, rows), atLatest, windowListsTurn: window ? (turnId) => window.holdsTurn(turnId) : undefined },
  );
  store.writeView(view, result);
  if (isSubAgent) advanceSubAgentCursor(view, frame.revision);
  else if (page) {
    window?.noteRows(result.addedRows);
    window?.noteTurns(result.addedTurns, result.turnsPastWindow);
    updateSessionHistory(view, page.generation, { revision: frame.revision, total: (page.total ?? 0) + result.addedTurns.length + result.turnsPastWindow });
  }
  for (const effect of result.effects) runEffect(view, effect);
}

/** The socket lost updates for this session: every view of it reads once. */
export function repairSessionViews(sessionId: string): void {
  if (useSessionHistory.getState().pages[sessionId]) readAtAnchor(sessionId);
  for (const view of subAgentViews(sessionId)) readAtAnchor(view);
}

/** The server stopped keeping this view for the socket. */
export function handleViewClosed(view: string): void {
  if (isSubAgentChatKey(view)) { forgetSubAgentCursor(view); return; }
  dropHistoryWindow(view);
  if (isViewShown(view)) requestSessionLoad({ action: "load_session", session_id: view }, true);
}
