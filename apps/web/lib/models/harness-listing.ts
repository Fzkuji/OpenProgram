/**
 * The harnesses on the machine a conversation runs on: the `list_harnesses`
 * WebSocket action is forwarded to that machine like any action naming the
 * conversation; with no socket, `GET /api/harnesses` answers for this
 * computer.
 */
import { wsRequest } from "@/lib/net/ws/ws-request";
import { jsonFetch } from "@/lib/net/fetch-client";
import type { Harness } from "./harnesses";

export function harnessQueryKey(sessionId: string | null): readonly unknown[] {
  return ["harnesses", sessionId ?? ""] as const;
}

/** The harnesses on the machine `sessionId` runs on (this computer without one). */
export async function listHarnesses(sessionId: string | null): Promise<Harness[]> {
  const reply = await wsRequest<{ harnesses?: Harness[] }>(
    "list_harnesses", sessionId ? { session_id: sessionId } : {}, "harnesses_list",
    // Reading Codex starts its app-server once: allow it the seconds that take.
    { requestId: true }, 30000,
  );
  if (reply?.harnesses) return reply.harnesses.map(normalized);
  const read = await jsonFetch<{ harnesses?: Harness[] }>("/api/harnesses");
  return (read.harnesses ?? []).map(normalized);
}

function normalized(harness: Harness): Harness {
  return { ...harness, models: harness.models ?? [], agents: harness.agents ?? [] };
}

export interface HarnessUpdate {
  ok: boolean;
  error?: string;
  output?: string;
  harness?: Harness;
}

/** Update `harness` on the machine `sessionId` runs on with the installer it
 *  came from (npm, Homebrew, `codex update`…); the reply carries it read again. */
export async function updateHarness(sessionId: string | null, harness: string): Promise<HarnessUpdate> {
  const reply = await wsRequest<HarnessUpdate>(
    "update_harness", { harness, ...(sessionId ? { session_id: sessionId } : {}) }, "harness_updated",
    { requestId: true }, 11 * 60_000,
  );
  const result = reply ?? await jsonFetch<HarnessUpdate>(
    `/api/harnesses/${encodeURIComponent(harness)}/update`, { method: "POST" },
  );
  return result.harness ? { ...result, harness: normalized(result.harness) } : result;
}
