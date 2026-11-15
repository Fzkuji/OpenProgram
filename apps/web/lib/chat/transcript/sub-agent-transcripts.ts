/**
 * Sub-agent transcripts.
 *
 * A sub-agent (an `agent` spawn, or a later `send_message` to it) runs as a
 * Job whose turns are a branch of the SAME session, so its live frames carry
 * the conversation's session id. The agent's step row opens them as a view of
 * its own, under a chat key of its own, and numbered updates keep that view live.
 *
 * A follow-up Job's status names the Job its agent continues
 * (`continues_job_id`, openprogram/runtime/jobs/runner/shared.py).
 */
import type { TurnWindow, WireTurn } from "@/lib/chat/history/history-window";
import type { JobStatusDetail } from "@/lib/net/ws/ws-events";
import { wsRequest } from "@/lib/net/ws/ws-request";
import { getSocket } from "@/lib/runtime-bridge/state";
import { selectViewRows, useSessionStore } from "@/lib/session-store";
import { ownsSubAgentChatKey, subAgentChatKey } from "./sub-agent-keys.ts";
import { rowsOfTurns } from "./turn-rows.ts";
import { applyReadToView } from "./apply-read.ts";

export { ownsSubAgentChatKey, subAgentChatKey };


/** Job → the Job whose branch it continues (a follow-up message). */
const continues = new Map<string, string>();
/** Job → the session it runs in, so a removed session releases its Jobs. */
const jobSessions = new Map<string, string>();

function noteJobSession(jobId: string, sessionId: string | undefined): void {
  if (sessionId) jobSessions.set(jobId, sessionId);
}

/** The views this client holds a cursor on, by key; how to read one again. */
const cursors = new Map<string, number>();
const viewSources = new Map<string, { sessionId: string; agentKey: string; source: { jobId?: string; headId?: string } }>();

export function subAgentCursor(view: string): number | undefined {
  return cursors.get(view);
}

export function advanceSubAgentCursor(view: string, revision: number): void {
  cursors.set(view, revision);
}

export function forgetSubAgentCursor(view: string): void {
  cursors.delete(view);
  viewSources.delete(view);
}

export function subAgentViews(sessionId: string): string[] {
  return [...viewSources].flatMap(([view, read]) => (read.sessionId === sessionId ? [view] : []));
}

/** The expanded view closed: the server stops keeping it for this socket. */
export function closeSubAgentView(sessionId: string, agentKey: string): void {
  const view = subAgentChatKey(sessionId, agentKey);
  forgetSubAgentCursor(view);
  const socket = getSocket();
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ action: "close_view", session_id: sessionId, view }));
  }
}

export async function reloadSubAgentTranscript(view: string): Promise<void> {
  const read = viewSources.get(view);
  if (read) await loadSubAgentTranscript(read.sessionId, read.agentKey, read.source);
}

/** Forget what this module holds for a removed session's Jobs. The store
 *  rows under its sub-agent keys go with the store's removeConversation. */
export function releaseSubAgentTranscripts(sessionId: string): void {
  for (const [jobId, owner] of jobSessions) {
    if (owner !== sessionId) continue;
    jobSessions.delete(jobId);
    continues.delete(jobId);
  }
  for (const view of subAgentViews(sessionId)) forgetSubAgentCursor(view);
}

/** The first Job of the agent this Job belongs to. */
export function agentJobFor(jobId: string): string {
  let current = jobId;
  const seen = new Set<string>();
  while (continues.has(current) && !seen.has(current)) {
    seen.add(current);
    current = continues.get(current)!;
  }
  return current;
}

/** Record which agent a Job continues, before its first frame arrives. */
export function noteJobStatus(detail: JobStatusDetail): void {
  const jobId = detail.job_id;
  if (!jobId) return;
  noteJobSession(jobId, detail.session_id);
  const parentJob = detail.continues_job_id;
  if (parentJob && parentJob !== jobId) continues.set(jobId, parentJob);
}

interface TranscriptReply {
  session_id?: string;
  job_id?: string;
  view?: string;
  turns?: WireTurn[];
  window?: TurnWindow | null;
  error?: string;
}

/**
 * Load an agent's whole branch (every turn sent to it) without moving the
 * session head. Live rows already on screen keep their newer progress.
 * Resolves to the branch tip (the agent's newest reply), or null.
 */
export async function loadSubAgentTranscript(
  sessionId: string,
  agentKey: string,
  source: { jobId?: string; headId?: string },
): Promise<string | null> {
  const data = await wsRequest<TranscriptReply>(
    "load_sub_agent_transcript",
    { session_id: sessionId, job_id: source.jobId ?? "", head_id: source.headId ?? "" },
    "sub_agent_transcript",
    { requestId: true },
    8000,
  );
  if (!data || data.error || !Array.isArray(data.turns)) return null;
  const messages = rowsOfTurns(data.turns);
  if (!messages.length) return null;
  const tip = data.window?.head_id || null;
  const view = data.view ?? subAgentChatKey(sessionId, agentKey);
  viewSources.set(view, { sessionId, agentKey, source });
  if (data.window) cursors.set(view, data.window.revision);
  // A turn that started after the server read the branch stays at the end.
  const key = subAgentChatKey(sessionId, agentKey);
  const store = useSessionStore.getState();
  const loaded = new Set(messages.map((message) => message.id));
  const held = selectViewRows(key)(store);
  const newer = (store.messageOrder[key] ?? [])
    .filter((id) => !loaded.has(id))
    .map((id) => held[id])
    .filter((message) => message !== undefined);
  applyReadToView(key, [...messages, ...newer]);
  return tip;
}

/** `[task from …]` / `[message from …]` receipt line that send_message puts
 *  before a message to an existing agent (send_message/delivery.py). */
const RECEIPT = /^\[(task|message) from [^\]\n]+\][^\n]*\n+/;

/** The words the caller wrote, and whether it sent them as a plain message
 *  rather than a task. */
export function promptBody(content: string): { text: string; message: boolean } {
  const receipt = RECEIPT.exec(content);
  if (!receipt) return { text: content, message: false };
  return { text: content.slice(receipt[0].length), message: receipt[1] === "message" };
}
