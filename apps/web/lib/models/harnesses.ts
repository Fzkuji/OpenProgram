/**
 * Outside agents (Codex) a conversation can run in instead of OpenProgram's
 * own engine: the listing the composer's Harness chip, model menu, effort
 * pill, Agent chip and permission chip read, and a branch's switch.
 *
 * The listing is read by harness-listing.ts. A branch's harness is its `harness` run setting
 * (`null` is OpenProgram); a harness's models pin the provider
 * `<harness>-harness`, and `openprogram:<provider>/<model>` runs one of
 * OpenProgram's models in it.
 */
export interface HarnessModel {
  id: string;
  label: string;
  default: boolean;
  efforts: string[];
  default_effort?: string | null;
  images?: boolean;
  /** Takes a personality (`personality:*` agents). */
  personality?: boolean;
}

export interface HarnessAgent {
  id: string;
  label: string;
  kind: "default" | "profile" | "personality";
  default?: boolean;
}

export interface Harness {
  id: string;
  provider: string;
  label: string;
  installed: boolean;
  version: string;
  signedIn: boolean;
  plan?: string;
  models: HarnessModel[];
  agents: HarnessAgent[];
  /** The newest release, when the installed one is older. */
  latest?: string;
  /** Its installer is known, so OpenProgram can run the update. */
  updatable?: boolean;
  /** Models Codex offers that only a newer Codex lists. */
  newModels?: { id: string; label: string }[];
}

/** OpenProgram's own engine, as the Harness chip names it. */
export const OPENPROGRAM_HARNESS = "openprogram";
/** A harness's model run through OpenProgram's own providers. */
export const GATEWAY_PREFIX = "openprogram:";

/** The provider id of each harness's models. */
const PROVIDERS: Record<string, string> = { codex: "codex-harness" };
/** The provider whose logo a harness shows. */
const MARKS: Record<string, string> = { "codex-harness": "openai", codex: "openai" };

export function isHarnessProvider(provider: string | null | undefined): boolean {
  return !!provider && Object.values(PROVIDERS).includes(provider);
}

export function harnessProvider(harness: string): string | undefined {
  return PROVIDERS[harness];
}

/** The harness setting a provider's models belong to (`codex-harness` → `codex`). */
export function harnessOf(provider: string | null | undefined): string | null {
  return Object.entries(PROVIDERS).find(([, id]) => id === provider)?.[0] ?? null;
}

/** The agent's own logo for a harness or its provider: the Harness chip,
 *  its menu and the sidebar's mark of a conversation running there. */
const AGENT_LOGOS: Record<string, string> = {
  codex: "codex", "codex-harness": "codex",
  "claude-code": "claudecode", "claude-code-harness": "claudecode",
  "deepseek-harness": "deepseek",
};
/** Agent names as people know them, for marks, hover cards and the
 *  History page's Source filter. */
const AGENT_NAMES: Record<string, string> = {
  codex: "Codex", "claude-code": "Claude Code", "deepseek-harness": "DeepSeek Harness",
};

/** The agents whose own apps' conversations are synced here, by the
 *  `source` their conversations carry. */
export const SYNCED_SOURCES = ["codex", "claude-code", "deepseek-harness"];

export function agentLogo(id: string | null | undefined): string | undefined {
  return id ? AGENT_LOGOS[id] : undefined;
}

export function agentName(id: string | null | undefined): string | undefined {
  return id ? AGENT_NAMES[id] ?? AGENT_NAMES[harnessOf(id) ?? ""] : undefined;
}

/** The provider whose logo stands for `id` (a harness or its provider). */
export function harnessMark(id: string | undefined): string | undefined {
  return id ? MARKS[id] ?? id : id;
}

/** `openprogram:<provider>/<model>` → its provider and model, else null. */
export function gatewayModel(model: string | null | undefined): { provider: string; model: string } | null {
  if (!model?.startsWith(GATEWAY_PREFIX)) return null;
  const rest = model.slice(GATEWAY_PREFIX.length);
  const slash = rest.indexOf("/");
  return slash > 0 && slash < rest.length - 1 ? { provider: rest.slice(0, slash), model: rest.slice(slash + 1) } : null;
}

export function gatewayRef(provider: string, model: string): string {
  return `${GATEWAY_PREFIX}${provider}/${model}`;
}

/** Why a harness cannot be picked on that machine, or null. */
export function unavailable(harness: Harness): "not_installed" | "not_signed_in" | null {
  if (!harness.installed) return "not_installed";
  if (!harness.signedIn) return "not_signed_in";
  return null;
}

export function defaultModel(harness: Harness | undefined): HarnessModel | undefined {
  return harness?.models.find((m) => m.default) ?? harness?.models[0];
}

/** The agent rows the Agent chip lists for a harness: its default and
 *  profiles always, personalities only for a model that takes one. */
export function agentChoices(harness: Harness | undefined, model: string | undefined): HarnessAgent[] {
  const listed = harness?.models.find((m) => m.id === model);
  return (harness?.agents ?? []).filter((a) => a.kind !== "personality" || !!listed?.personality);
}
