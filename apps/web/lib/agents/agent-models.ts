/** The models an Agent can be given, kept across visits of the Agents page. */
import { jsonFetch } from "@/lib/net/fetch-client";
import { requestAgentSettings } from "@/lib/agents/agent-settings-request";
import { isAbort, isPermanent, lastKept, prefetchKept, readUntilAnswered, retryDelay } from "@/lib/abilities/kept-read";

export type CatalogModel = {
  provider: string; id: string; name: string; enabled?: boolean;
  thinking_levels?: string[]; default_thinking_level?: string;
};
type Provider = { id: string; label?: string; enabled?: boolean; configured?: boolean };
type ChatSettings = { chat?: { provider?: string; model?: string } } | null;
export type ModelList = {
  models: CatalogModel[];
  inherited: { provider: string; id: string } | null;
  /** What did not answer this time and is asked again: provider ids, and
   *  "settings" for the model a new conversation would use. */
  unanswered: string[];
};
export const MODELS_KEY = "agents:models";

type ModelSources = {
  fetchJson: <T>(url: string) => Promise<T>;
  readSettings: () => Promise<ChatSettings>;
};
const SOURCES: ModelSources = {
  fetchJson: (url) => jsonFetch(url),
  readSettings: () => requestAgentSettings() as Promise<ChatSettings>,
};

/** The enabled models of every configured provider, and the model a new
 *  conversation would use. The providers' model lists are read side by side;
 *  the list is built from the providers that answered. One whose read failed
 *  is named in `unanswered`; one that refuses (HTTP 4xx) is left out. Only the
 *  provider catalog itself failing fails the whole read. */
export async function readAgentModels(sources: ModelSources = SOURCES): Promise<ModelList> {
  const [catalog, settings] = await Promise.all([
    sources.fetchJson<{ providers?: Provider[] }>("/api/providers/list"),
    sources.readSettings().then(
      (value): ChatSettings | undefined => value,
      (cause): undefined => { console.warn("[agents] settings read failed; trying again", cause); return undefined; },
    ),
  ]);
  const providers = (catalog.providers || []).filter((provider) => provider.enabled !== false && provider.configured !== false);
  const results = await Promise.allSettled(providers.map(async (provider) => {
    const data = await sources.fetchJson<{ models?: Omit<CatalogModel, "provider">[] }>(`/api/providers/${encodeURIComponent(provider.id)}/models`);
    return (data.models || []).filter((model) => model.enabled !== false).map((model) => ({ ...model, provider: provider.id }));
  }));
  const models: CatalogModel[] = [];
  const unanswered: string[] = [];
  results.forEach((result, index) => {
    const id = providers[index].id;
    if (result.status === "fulfilled") { models.push(...result.value); return; }
    if (isPermanent(result.reason)) { console.warn(`[agents] ${id} models refused`, result.reason); return; }
    console.warn(`[agents] ${id} models read failed; trying again`, result.reason);
    unanswered.push(id);
  });
  if (settings === undefined) unanswered.push("settings");
  const chat = settings?.chat;
  return {
    models,
    inherited: chat?.provider && chat.model ? { provider: chat.provider, id: chat.model } : null,
    unanswered,
  };
}

/**
 * Show the model list as it comes: once the catalog answers, every list is
 * shown; while some source has not answered, the list is read again after a
 * growing pause until all have, or `signal` aborts.
 */
export function watchAgentModels(
  show: (list: ModelList) => void,
  signal: AbortSignal,
  read: () => Promise<ModelList> = readAgentModels,
): void {
  let attempt = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  signal.addEventListener("abort", () => clearTimeout(timer), { once: true });
  const once = () => {
    readUntilAnswered(MODELS_KEY, read, signal)
      .then((list) => {
        show(list);
        if (list.unanswered.length && !signal.aborted) timer = setTimeout(once, retryDelay(attempt++));
      })
      .catch((cause) => {
        if (isAbort(cause)) return;
        // The provider catalog refused: there is no model to offer.
        console.warn("[agents] model list read refused", cause);
        show({ models: [], inherited: null, unanswered: [] });
      });
  };
  once();
}

/** The Agents page's model list, started before the page opens. */
export function prefetchAgentModels(): Promise<void> {
  return lastKept(MODELS_KEY) ? Promise.resolve() : prefetchKept(MODELS_KEY, () => readAgentModels());
}
