"use client";

import type { AgentConfigDTO } from "@/components/agents/agent-types";
import { jsonFetch } from "@/lib/net/fetch-client";
import { useSessionStore } from "@/lib/session-store";
import { confirmAction } from "@/components/ui/confirm-dialog";

export interface AgentConversationIntent {
  agentId: string;
  config?: AgentConfigDTO;
  trial?: boolean;
  model?: { provider: string; id: string };
  thinkingEffort?: string;
  label?: string;
  resolvedModel?: { provider: string; id: string };
  thinkingLevels?: string[];
  defaultThinking?: string;
}

async function modelDetails(model: { provider: string; id: string }) {
  if (!model.provider || !model.id) return { thinkingLevels: [] as string[], defaultThinking: "" };
  const data = await jsonFetch<{ models: Array<{ id: string; thinking_levels?: string[]; default_thinking_level?: string }> }>(
    `/api/providers/${encodeURIComponent(model.provider)}/models`,
  );
  const entry = data.models.find((item) => item.id === model.id || `${model.provider}:${item.id}` === model.id);
  return { thinkingLevels: entry?.thinking_levels ?? [], defaultThinking: entry?.default_thinking_level ?? "" };
}

/** The pending Agent binding a draft sends with its first message. */
async function draftAgentSettings(options: AgentConversationIntent) {
  const { agent: saved } = await jsonFetch<{ agent: AgentConfigDTO }>(`/api/agents/${encodeURIComponent(options.agentId)}`);
  const effective = options.trial && options.config ? options.config : saved;
  let model = options.model ?? effective.model;
  if (!model.provider && !model.id) {
    const defaults = await jsonFetch<{ chat?: { provider?: string; model?: string } }>("/api/agent_settings");
    model = { provider: defaults.chat?.provider ?? "", id: defaults.chat?.model ?? "" };
  }
  const details = await modelDetails(model);
  const intent: AgentConversationIntent = {
    agentId: options.agentId,
    label: effective.name,
    resolvedModel: model,
    ...details,
    ...(options.model ? { model: options.model } : {}),
    ...(options.thinkingEffort !== undefined ? { thinkingEffort: options.thinkingEffort } : {}),
  };
  if (options.trial && options.config) {
    intent.trial = true;
    intent.config = structuredClone(options.config);
    intent.config.memory.mode = saved.memory.mode === "off" || intent.config.memory.mode === "off" ? "off" : "read_only";
  }
  return {
    name: effective.name,
    settings: {
      agentInvocation: intent,
      thinking: options.thinkingEffort ?? effective.thinking_effort,
    },
  };
}

/** Prepare an unsent, independently owned chat; no Agent or session is written. */
export async function startAgentConversation(options: AgentConversationIntent): Promise<void> {
  const { name, settings } = await draftAgentSettings(options);
  const [{ useCenterTabs }, { newSession }] = await Promise.all([
    import("@/lib/tabs/center-tabs-store"), import("@/lib/runtime-bridge/session/conversations"),
  ]);
  const key = useCenterTabs.getState().openDraftSessionTab();
  useSessionStore.getState().setComposerSettings(settings, key);
  useCenterTabs.getState().renameSessionTab(key, name);
  newSession(key);
}

/** Point an unsent chat at another Agent; its first message binds it. */
export async function setDraftAgent(key: string, agentId: string): Promise<void> {
  const { settings } = await draftAgentSettings({ agentId });
  useSessionStore.getState().setComposerSettings(settings, key);
}

const pendingModelRequests = new WeakMap<AgentConversationIntent, object>();

/** Candidate model selection remains local until the first chat acknowledgement. */
export async function updatePendingAgentModel(key: string, model: { provider: string; id: string }): Promise<boolean> {
  const before = useSessionStore.getState().composerSettingsBySession[key]?.agentInvocation;
  if (!before) return false;
  const request = {};
  pendingModelRequests.set(before, request);
  let details: Awaited<ReturnType<typeof modelDetails>>;
  try {
    details = await modelDetails(model);
  } catch (error) {
    if (pendingModelRequests.get(before) !== request || useSessionStore.getState().composerSettingsBySession[key]?.agentInvocation !== before) return true;
    throw error;
  }
  const store = useSessionStore.getState();
  const settings = store.composerSettingsBySession[key];
  // An operation that began on an Agent draft is always handled locally,
  // including stale responses and closed tabs. Never fall back to /api/model.
  if (pendingModelRequests.get(before) !== request || settings?.agentInvocation !== before) return true;
  if (settings.thinking && !details.thinkingLevels.includes(settings.thinking)) {
    // The picker is an explicit user action; do not silently rewrite effort.
    const zh = document.documentElement.lang.startsWith("zh");
    if (!(await confirmAction({
      title: zh ? "此模型不支持当前思考强度。恢复为模型默认值？" : "This model does not support the current reasoning effort. Use its default?",
      confirmLabel: zh ? "使用默认值" : "Use default",
      cancelLabel: zh ? "取消" : "Cancel",
    }))) return true;
  }
  const thinking = details.thinkingLevels.includes(settings.thinking) ? settings.thinking : "";
  store.setComposerSettings({ agentInvocation: { ...before, model, resolvedModel: model, thinkingEffort: thinking, ...details }, thinking }, key);
  return true;
}
