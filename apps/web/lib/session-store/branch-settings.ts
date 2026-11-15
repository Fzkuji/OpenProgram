import { permissionSnapshotPatch } from "./permission-state";
import type { ComposerSettings, ResolvedBranchSettings } from "./types";

/** A loaded branch replaces overrides; permission versions are local to that branch.
 *  A reload of the same branch keeps the per-model Fast choices made on it. */
export function branchSettingsPatch(
  current: Partial<ComposerSettings>, branchId: string | null,
  settings: Record<string, unknown>,
): Partial<ComposerSettings> {
  const sameBranch = current.active_branch_id === branchId;
  const permission = permissionSnapshotPatch(
    sameBranch ? current : {},
    { mode: settings.permission_mode, version: settings.permission_version ?? 0 },
  );
  const resolved = settings.resolved as ResolvedBranchSettings | undefined;
  const followed = resolved?.values?.web_search;
  return {
    active_branch_id: branchId,
    thinking: typeof settings.thinking_effort === "string" ? settings.thinking_effort : "",
    // A branch that sets no web search shows its Agent's choice.
    webSearch: typeof settings.web_search === "boolean" ? settings.web_search
      : typeof followed === "boolean" ? followed : true,
    ...(resolved && typeof resolved === "object" ? { resolved } : {}),
    fast: settings.service_tier === "priority",
    fastByModel: sameBranch ? { ...(current.fastByModel ?? {}) } : {},
    sandbox: typeof settings.sandbox === "boolean" ? settings.sandbox : undefined,
    ...permission,
  };
}

/** Live replies from another branch must not overwrite the visible composer.
 *  A frame names the branch its settings belong to (``branch_id``); an older
 *  frame only names the selected branch, which was then the one written. */
export function sameSettingsBranch(
  current: Partial<ComposerSettings>, data: { branch_id?: unknown; active_branch_id?: unknown },
): boolean {
  const owner = data.branch_id !== undefined && data.branch_id !== null ? data.branch_id : data.active_branch_id;
  return owner === undefined || owner === (current.active_branch_id ?? null);
}

/** Server settings (a branch's stored fields) as a composer patch's input. */
export function storedBranchSettings(settings: Record<string, unknown>): Record<string, unknown> {
  const state = settings.permission_state as { mode?: unknown; version?: unknown } | null | undefined;
  return {
    thinking_effort: settings.thinking_effort,
    web_search: settings.web_search,
    service_tier: settings.service_tier,
    sandbox: settings.sandbox_enabled,
    ...(state && typeof state === "object" ? { permission_mode: state.mode, permission_version: state.version } : {}),
  };
}

/** What a settings read changes in a composer: the controls a branch stores
 *  the moment they are set (effort, web search), where the stored value
 *  differs from the one shown. A write made elsewhere arrives this way. */
export function storedControlsPatch(
  current: Partial<ComposerSettings>, settings: Record<string, unknown>,
): Partial<ComposerSettings> {
  const thinking = typeof settings.thinking_effort === "string" ? settings.thinking_effort : "";
  const webSearch = typeof settings.web_search === "boolean" ? settings.web_search : true;
  return {
    ...((current.thinking ?? "") === thinking ? {} : { thinking }),
    ...(current.webSearch === webSearch ? {} : { webSearch }),
  };
}

/** Whether a setting follows the layer below the branch (its Agent, the
 *  project) or is set on the branch, and what it would be if the branch
 *  set nothing: the hint a switch carries. ``null`` before the server
 *  said (a draft, an old server). */
export function settingOrigin(
  resolved: ResolvedBranchSettings | undefined, field: string,
): { follows: boolean; inherited: unknown } | null {
  const source = resolved?.sources?.[field];
  if (typeof source !== "string") return null;
  return { follows: source !== "branch", inherited: resolved?.inherited?.[field] };
}
