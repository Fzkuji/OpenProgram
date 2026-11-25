"use client";

/**
 * A branch's harness (the engine that runs its turns) and that harness's
 * agent choice, written on the branch the pane shows like its other run
 * settings. A switch takes the harness's default model and agent; a switch
 * back to OpenProgram restores the branch's OpenProgram model (server side).
 * The pane's model settings are read again afterwards, so every chip
 * follows at once.
 */
import { jsonFetch } from "@/lib/net/fetch-client";
import { useSessionStore } from "@/lib/session-store";
import { branchTarget } from "./branch-target.ts";

interface HarnessReply {
  branch_id?: string | null;
  active_branch_id?: string | null;
}

async function write(sessionId: string, path: "harness" | "harness_agent", body: Record<string, unknown>): Promise<void> {
  const branchId = useSessionStore.getState().composerSettingsBySession[sessionId]?.active_branch_id ?? null;
  const reply = await jsonFetch<HarnessReply>(`/api/sessions/${encodeURIComponent(sessionId)}/${path}`, {
    method: "POST",
    body: JSON.stringify({ ...body, ...branchTarget(branchId) }),
  });
  if ((useSessionStore.getState().composerSettingsBySession[sessionId]?.active_branch_id ?? null) !== branchId) return;
  const written = typeof reply.branch_id === "string" ? reply.branch_id : reply.active_branch_id;
  if (typeof written === "string") useSessionStore.getState().setComposerSettings({ active_branch_id: written }, sessionId);
  const { loadAgentSettings, loadSessionAgentSettings } = await import("@/lib/runtime-bridge/providers");
  const focused = useSessionStore.getState().currentSessionId === sessionId;
  await Promise.all([
    loadSessionAgentSettings(sessionId, { written: true }), ...(focused ? [loadAgentSettings()] : []),
  ]);
}

/** Run the pane's branch in `harness` (`"openprogram"` or `"codex"`); `model`
 *  is that harness's default model as the conversation's machine lists it. */
export function switchSessionHarness(sessionId: string, harness: string, model?: string): Promise<void> {
  return write(sessionId, "harness", { harness, ...(model ? { model } : {}) });
}

/** Pick the harness agent (`default`, `profile:<name>`, `personality:<name>`). */
export function chooseHarnessAgent(sessionId: string, agent: string): Promise<void> {
  return write(sessionId, "harness_agent", { agent });
}

/** The conversation a chip acts on: its session, else a new draft's key. */
export async function ensureChatSession(sessionId: string | null, chatKey: string | null): Promise<string | undefined> {
  const sid = sessionId || (chatKey && chatKey !== "__new__" ? chatKey : undefined);
  if (sid) return sid;
  const { useCenterTabs } = await import("@/lib/tabs/center-tabs-store");
  const { newSession } = await import("@/lib/runtime-bridge/session/conversations");
  const tabs = useCenterTabs.getState();
  const existing = tabs.tabs.find(
    (tab: { kind: string; draft?: boolean; sessionId?: string }) => tab.kind === "session" && tab.draft && tab.sessionId,
  );
  const created = existing?.sessionId ?? tabs.openDraftSessionTab();
  if (!created) return undefined;
  newSession(created);
  return created;
}
