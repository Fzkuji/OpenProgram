"use client";

/**
 * This composer's model settings (chat model, Fast, effort levels).
 *
 * Each conversation keeps its own entry in `agentSettingsBySession`, so two
 * split panes show and change their own models. The focused conversation
 * also feeds the global `agentSettings` (the WS frames and older callers
 * write there); it answers only while the conversation's own entry has not
 * arrived yet, or for a draft that has none.
 *
 * The entry belongs to the branch the pane shows and to that branch's Agent.
 * A pane that is not focused reads it again when either changes (a branch
 * switch, an Agent switch from another window) or when some conversation's
 * model changed without saying which, so its model chip never keeps the
 * previous branch's model and never blanks while the read is in flight.
 */
import { useEffect } from "react";

import { useBoundChat } from "@/components/chat/top-bar/bound-chat";
import { settingsReadNeeded, settingsShownFor } from "@/lib/agents/agent-settings-read";
import { loadSessionAgentSettings } from "@/lib/runtime-bridge/providers";
import { useSessionStore, type AgentSettingsState } from "@/lib/session-store";

const EMPTY: AgentSettingsState = {};

/** What each conversation's entry was last read for (``settingsShownFor``). */
const readFor = new Map<string, string>();

export function useSessionAgentSettings(): AgentSettingsState {
  const { sessionId, chatKey } = useBoundChat();
  const key = sessionId ?? chatKey;
  const own = useSessionStore((s) => (key ? s.agentSettingsBySession[key] : undefined));
  const focused = useSessionStore((s) => !key || key === (s.activeChatKey ?? s.currentSessionId));
  const global = useSessionStore((s) => s.agentSettings);
  const branchId = useSessionStore((s) => (sessionId ? s.composerSettingsBySession[sessionId]?.active_branch_id ?? null : null));
  const agentId = useSessionStore((s) => (sessionId ? s.conversations[sessionId]?.agent_id ?? null : null));
  const epoch = useSessionStore((s) => s.agentSettingsEpoch);
  const shownFor = settingsShownFor(branchId, agentId, epoch);
  const hasEntry = !!own;
  useEffect(() => {
    if (!sessionId) return;
    const needed = settingsReadNeeded(sessionId, focused, hasEntry, shownFor, readFor.get(sessionId));
    // A focused pane's entry is refreshed by the focused reads; an entry
    // first seen here was read for what the pane shows now.
    readFor.set(sessionId, shownFor);
    if (needed) void loadSessionAgentSettings(sessionId);
  }, [sessionId, focused, hasEntry, shownFor]);
  return own ?? (focused || !sessionId ? global : EMPTY);
}
