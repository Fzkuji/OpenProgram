"use client";

import { jsonFetch } from "@/lib/net/fetch-client";
import { useSessionStore } from "@/lib/session-store";
import { branchTarget } from "./branch-target.ts";

interface SwitchResult {
  session_id: string;
  agent_id: string;
  branch_id?: string | null;
  active_branch_id?: string | null;
  model: { provider: string; id: string };
}

/** Switch the Agent of the branch a pane shows. The server answers with the
 *  Agent's model, which that branch now uses. The pane's own model settings
 *  are read again, focused or not, so its model chip follows at once. */
export async function switchSessionAgent(sessionId: string, agentId: string): Promise<SwitchResult> {
  const branchId = useSessionStore.getState().composerSettingsBySession[sessionId]?.active_branch_id ?? null;
  const result = await jsonFetch<SwitchResult>(`/api/sessions/${encodeURIComponent(sessionId)}/agent`, {
    method: "POST",
    body: JSON.stringify({ agent_id: agentId, ...branchTarget(branchId) }),
  });
  if ((useSessionStore.getState().composerSettingsBySession[sessionId]?.active_branch_id ?? null) !== branchId) return result;
  const written = typeof result.branch_id === "string" ? result.branch_id : result.active_branch_id;
  if (typeof written === "string") useSessionStore.getState().setComposerSettings({active_branch_id: written}, sessionId);
  // The sidebar shows the selected branch's Agent; another branch's switch leaves it.
  const selected = !result.branch_id || result.branch_id === result.active_branch_id;
  const store = useSessionStore.getState();
  const summary = store.conversations[sessionId];
  if (summary && selected) store.upsertConversation({ ...summary, agent_id: result.agent_id });
  const [{ runtimeState }, { loadAgentSettings, loadSessionAgentSettings }] = await Promise.all([
    import("@/lib/runtime-bridge/state"), import("@/lib/runtime-bridge/providers"),
  ]);
  const conv = runtimeState.conversations[sessionId];
  if (conv && selected) conv.agent_id = result.agent_id;
  const focused = useSessionStore.getState().currentSessionId === sessionId;
  await Promise.all([
    loadSessionAgentSettings(sessionId, { written: true }), ...(focused ? [loadAgentSettings()] : []),
  ]);
  return result;
}
