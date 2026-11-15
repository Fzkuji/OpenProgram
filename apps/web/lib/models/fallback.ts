/**
 * The spent-quota reply card's model offer and its re-send.
 *
 * The Worker picks the offer (`GET /api/providers/fallback`): the same model
 * id on another healthy provider, else the most used healthy model of the
 * last 7 days with the same tool / vision / reasoning support and a context
 * window that holds the conversation. Nothing switches by itself: a button
 * pins the chosen model on the branch the pane shows (`pickBranchModel`) and
 * re-sends the failed turn there (`POST /api/chat/retry`), so other branches
 * and conversations keep their model.
 */
import { jsonFetch } from "@/lib/net/fetch-client";
import { api } from "@/lib/net/api";
import type { FallbackPayload } from "@/lib/settings/provider-status.generated";

export function fallbackUrl(provider: string, model: string, contextTokens = 0): string {
  const query = new URLSearchParams({ provider, model });
  if (contextTokens > 0) query.set("context_tokens", String(Math.round(contextTokens)));
  return `/api/providers/fallback?${query.toString()}`;
}

export function fetchFallback(provider: string, model: string, contextTokens = 0): Promise<FallbackPayload> {
  return jsonFetch<FallbackPayload>(fallbackUrl(provider, model, contextTokens));
}

/** The bare model id the fallback route expects ("openai-codex:gpt" → "gpt"). */
export function bareModel(provider: string | undefined, model: string | undefined): string {
  if (!model) return "";
  return provider && model.startsWith(`${provider}:`) ? model.slice(provider.length + 1) : model;
}

/** Pin `provider`/`model` on this pane's branch, then send the failed turn
 *  again there. */
export async function continueWith(sessionId: string, replyId: string, provider: string, model: string): Promise<void> {
  const { pickBranchModel } = await import("@/lib/agents/model-pick");
  await pickBranchModel(sessionId, provider, model);
  await resend(sessionId, replyId);
}

/** Send the failed turn again on this branch with whatever model it has. */
export async function resend(sessionId: string, replyId: string): Promise<void> {
  await api.retryChat(sessionId, replyId);
  const [{ useSessionStore }, { setRunActive }, { sendRuntimeCommand }] = await Promise.all([
    import("@/lib/session-store"),
    import("@/lib/runtime-bridge/session/chat-handlers"),
    import("@/lib/runtime-bridge/session/session-load"),
  ]);
  if (useSessionStore.getState().currentSessionId === sessionId) setRunActive(true);
  sendRuntimeCommand({ action: "load_session", session_id: sessionId });
}
