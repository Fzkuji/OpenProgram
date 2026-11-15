import { useSessionStore, type ComposerSettings } from "@/lib/session-store";
import { branchSettingsPatch, storedBranchSettings, storedControlsPatch } from "@/lib/session-store/branch-settings";
import { showToast } from "@/lib/format-utils/toast";
import { branchTarget } from "./branch-target.ts";

/** Adopt the identity assigned by a legacy session's first settings write. */
export function adoptBranchIdentity(sessionId: string, expected: string | null, returned: unknown): void {
  const store = useSessionStore.getState();
  if (typeof returned === "string" && (store.composerSettingsBySession[sessionId]?.active_branch_id ?? null) === expected) {
    store.setComposerSettings({active_branch_id: returned}, sessionId);
  }
}

export { branchTarget };

/** Select the branch a checkout switched to at once, so frames of the
 *  branch left stop drawing before the transcript reloads. */
export function adoptCheckedOutBranch(sessionId: string, reply: unknown): void {
  const branch = (reply as { active_branch_id?: unknown } | null)?.active_branch_id;
  if (typeof branch === "string" && branch) {
    useSessionStore.getState().setComposerSettings({active_branch_id: branch}, sessionId);
  }
}

interface BranchSettingsReply {
  branch_id?: unknown;
  active_branch_id?: unknown;
  settings?: Record<string, unknown>;
  values?: Record<string, unknown>;
  sources?: Record<string, string>;
  inherited?: Record<string, unknown>;
}

/** Keep what the server says each setting now resolves to and where it
 *  comes from, for the pane still showing that branch: a switch turned to
 *  its Agent's value is stored as "follows the Agent", and its hint says so. */
function adoptResolved(sessionId: string, branchId: string | null, reply: BranchSettingsReply): void {
  if (!reply || typeof reply.sources !== "object" || !reply.sources) return;
  const store = useSessionStore.getState();
  if ((store.composerSettingsBySession[sessionId]?.active_branch_id ?? null) !== branchId) return;
  store.setComposerSettings({
    resolved: { values: reply.values ?? {}, sources: reply.sources, inherited: reply.inherited ?? {} },
  }, sessionId);
}

const writes = new Map<string, Promise<void>>();

/** The request body for a settings change. A setting put back to inherit
 *  ("" effort: the model default) goes in ``reset_fields``; the server
 *  refuses a null value. */
function branchSettingsFields(patch: Partial<ComposerSettings>): Record<string, unknown> {
  const fields: Record<string, unknown> = {};
  if (patch.thinking === "") fields.reset_fields = ["thinking_effort"];
  else if (patch.thinking !== undefined) fields.thinking_effort = patch.thinking;
  if (patch.webSearch !== undefined) fields.web_search = patch.webSearch;
  if (patch.fast !== undefined) fields.service_tier = patch.fast ? "priority" : "";
  return fields;
}

/** The branch a refused (409) branch write says the conversation shows;
 *  ``undefined`` when the error is not a branch conflict. */
export function conflictBranch(error: unknown): string | null | undefined {
  const http = error as { status?: number; body?: string };
  if (http?.status !== 409) return undefined;
  try {
    const body = JSON.parse(http.body ?? "{}") as { error?: unknown; active_branch_id?: unknown };
    if (body.error !== "branch_config_conflict") return undefined;
    return typeof body.active_branch_id === "string" ? body.active_branch_id : null;
  } catch {
    return undefined;
  }
}

/** Save controls immediately, in click order, on the branch the pane shows.
 *  Draft settings stay local until first send.
 *
 *  A write names its branch, so another window's branch switch does not
 *  refuse it. When the branch is gone (or an old server refuses the guard),
 *  the pane takes the branch the conversation now shows: it loads that
 *  branch's stored settings and applies the same change to it. */
/** The controls a draft's person changed, with this patch's added; a
 *  conversation the server has stores its changes instead (above), so it
 *  gets nothing. A draft's first message carries only these. */
export function draftTouchedPatch(
  sid: string | null | undefined, touched: string[] | undefined, patch: Partial<ComposerSettings>,
): Partial<ComposerSettings> {
  if (sid && useSessionStore.getState().conversations?.[sid]) return {};
  const changed = (["thinking", "webSearch"] as const).filter((key) => patch[key] !== undefined);
  if (!changed.length) return {};
  return { draftTouched: [...new Set([...(touched ?? []), ...changed])] };
}

export function persistBranchSettings(sessionId: string, patch: Partial<ComposerSettings>): void {
  const store = useSessionStore.getState();
  if (!store.conversations[sessionId]) return;
  const fields = branchSettingsFields(patch);
  if (!Object.keys(fields).length) return;
  const branchId = store.composerSettingsBySession[sessionId]?.active_branch_id ?? null;
  const prior = writes.get(sessionId) ?? Promise.resolve();
  const next = prior.then(async () => {
    const { jsonFetch } = await import("@/lib/net/fetch-client");
    const url = `/api/sessions/${encodeURIComponent(sessionId)}/branch_settings`;
    const post = (target: string | null) => jsonFetch<BranchSettingsReply>(url, {
      method: "POST", body: JSON.stringify({ ...fields, ...branchTarget(target) }),
    });
    try {
      const response = await post(branchId);
      adoptBranchIdentity(sessionId, branchId, response.branch_id ?? response.active_branch_id);
      adoptResolved(sessionId, branchId ?? (typeof response.branch_id === "string" ? response.branch_id : null), response);
      return;
    } catch (error) {
      const shown = conflictBranch(error);
      if (shown === undefined) {
        showToast(`Branch settings were not saved: ${String(error)}`, { tone: "error" });
        return;
      }
      try {
        const response = await post(shown);
        const settled = useSessionStore.getState();
        if ((settled.composerSettingsBySession[sessionId]?.active_branch_id ?? null) !== branchId) return;
        const owner = typeof response.branch_id === "string" ? response.branch_id : shown;
        settled.setComposerSettings({
          ...branchSettingsPatch(settled.composerSettingsBySession[sessionId] ?? {}, owner,
            storedBranchSettings(response.settings ?? {})),
          ...patch,
        }, sessionId);
      } catch (retryError) {
        showToast(`Branch settings were not saved: ${String(retryError)}`, { tone: "error" });
      }
    }
  }).finally(() => { if (writes.get(sessionId) === next) writes.delete(sessionId); });
  writes.set(sessionId, next);
}

export function pendingBranchSettings(sessionId: string): Promise<void> | undefined {
  return writes.get(sessionId);
}

/** A settings read answered for the branch this pane shows and brought its
 *  stored settings: effort or web search written elsewhere (another window,
 *  the API) shows without a reload. The pane's own unfinished write wins,
 *  and a conversation the server does not have yet keeps its draft. */
export function adoptStoredControls(sessionId: string, settings: unknown): void {
  const store = useSessionStore.getState();
  if (!settings || typeof settings !== "object" || !store.conversations[sessionId] || writes.has(sessionId)) return;
  const patch = storedControlsPatch(store.composerSettingsBySession[sessionId] ?? {}, settings as Record<string, unknown>);
  if (Object.keys(patch).length) store.setComposerSettings(patch, sessionId);
}
