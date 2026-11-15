/**
 * Top-bar chip components — the session-scope chips that used to live
 * in the 48px `.topbar` row above the chat. That row is gone (chat
 * chrome is just the 40px tab strip now); the chips live on elsewhere:
 *
 *   - ProjectBadge / AgentBadge (model) / AgentConfigBadge (Agent) /
 *     PermissionBadge → composer bottom row (see composer/index.tsx,
 *     `.sessionChips`)
 *   - connection status → the composer's environment chip; channel
 *     binding → the composer's channel chip (ChannelMenu popover) and
 *     the "+" menu's Channel submenu (environment-row/chips/)
 *
 * `LegacyTopbarBridge` (rendered unconditionally by AppShell) seeds the
 * zustand store from the current runtime state on mount; from then on
 * the runtime-bridge updaters push into it directly via
 * `lib/tabs/top-bar-sync.ts`. Dropdowns still delegate to the submodules here
 * (project-menu / agent-selector / permission-menu / channel-menu).
 * 分支入口只剩右栏 History 的 BranchesPanel（原 BranchBadge chip 已删，
 * 与列表重复）。
 */
"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { useSessionStore } from "@/lib/session-store";
import { closeAllPopovers } from "@/lib/runtime-bridge/ui";
import { api } from "@/lib/net/api";
import { useTranslation } from "@/lib/i18n";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { HoverTip, TipBody } from "@/components/ui/tooltip";

import { AgentSelector } from "./menus/agent-selector";
import { TriggerMark } from "./menus/provider-quota";
import { PICKER_QUERY_KEY, triggerMark } from "@/lib/models/provider-state";
import { ModelMark } from "./chips/model-mark";
import { MENU_PANEL } from "@/components/ui/menu/menu-styles";
import { useBoundChat } from "./bound-chat";
import { pushAgentSettings, pushBranchInfo, pushStatusBadge } from "@/lib/tabs/top-bar-sync";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export { ProjectBadge } from "./menus/project-menu";
export { WorkingDirChips } from "./chips/working-dir-chips";
export { PermissionBadge } from "./menus/permission-menu";
export { AgentConfigBadge } from "./menus/agent-config-menu";

/**
 * Headless bridge — seeds the store from the current runtime state on
 * mount so the chips render something before the first WS event. From
 * then on the runtime-bridge updaters push on their own
 * (`updateAgentBadges` / `refreshBranchBadge` / `updateStatus` /
 * `updateSendBtn` / `setStatusDotHealth`).
 */
export function LegacyTopbarBridge() {
  useEffect(() => {
    pushAgentSettings();
    pushBranchInfo();
    pushStatusBadge();
  }, []);
  return null;
}

/* ---- Chips --------------------------------------------------------- */

/** Strip a leading `provider:` qualifier from a model string. The chat
 *  model often arrives provider-qualified ("openai-codex:gpt-5.5") while
 *  the exec model is bare ("gpt-5.5"); this reduces both to the bare id. */
function bareModelId(provider?: string, model?: string): string {
  if (!model) return "";
  return provider && model.startsWith(provider + ":")
    ? model.slice(provider.length + 1)
    : model;
}

/** Agent-chip label: the model's display name (e.g. "GPT-5.5",
 *  "Claude Opus 4.8") — NOT the provider prefix or the lowercase id.
 *  Looks the bare id up in the enabled-models list (which carries each
 *  model's `name`); falls back to the bare id when the model isn't in
 *  that list (custom / not-yet-enabled). */
function fmtAgentLabel(
  provider: string | undefined,
  model: string | undefined,
  nameById: Map<string, string>,
): string {
  const bare = bareModelId(provider, model);
  if (!bare) return "";
  return nameById.get(`${provider ?? ""}:${bare}`) ?? nameById.get(bare) ?? bare;
}

/** The conversation's chat model chip. Which Agent it runs with is the
 *  neighbouring AgentConfigBadge. */
export function AgentBadge({
  id,
  locked,
  provider,
  model,
}: {
  id: string;
  locked: boolean;
  provider?: string;
  model?: string;
}) {
  const [open, setOpen] = useState(false);
  const { text } = useTranslation();
  const { chatKey } = useBoundChat();
  const invocation = useSessionStore((state) => chatKey
    ? state.composerSettingsBySession[chatKey]?.agentInvocation : undefined);
  if (invocation?.resolvedModel) {
    provider = invocation.resolvedModel.provider;
    model = invocation.resolvedModel.id;
  }

  // A `topbar-close-menus` event (fired by another top-bar dropdown)
  // closes this menu, so only one is ever open.
  useEffect(() => {
    const close = () => setOpen(false);
    window.addEventListener("topbar-close-menus", close);
    return () => window.removeEventListener("topbar-close-menus", close);
  }, []);

  function onOpenChange(next: boolean) {
    if (locked) return;
    if (next) {
      window.dispatchEvent(new Event("topbar-close-menus"));
      closeAllPopovers();
    }
    setOpen(next);
  }

  // Enabled-models list (shared cache with the AgentSelector dropdown —
  // same PICKER_QUERY_KEY, so the menu adds no extra fetch) → id-to-display-name map
  // so the chip shows "GPT-5.5" instead of "openai-codex:gpt-5.5". Keyed
  // both by "provider:id" and bare "id" for whichever form the chip has.
  // The same read carries each provider's picker state for the mark.
  const { data: picker } = useQuery({
    queryKey: PICKER_QUERY_KEY,
    queryFn: api.listPickerModels,
  });
  const enabledModels = picker?.models;
  // A small mark on the chip only when this model's provider needs action
  // or its quota warns; the tooltip says which.
  const mark = triggerMark(provider ? picker?.providers[provider] : undefined, text);
  const nameById = useMemo(() => {
    const m = new Map<string, string>();
    for (const mdl of enabledModels ?? []) {
      if (!mdl.name) continue;
      m.set(`${mdl.provider}:${mdl.id}`, mdl.name);
      m.set(mdl.id, mdl.name);
    }
    return m;
  }, [enabledModels]);

  // Tooltip carries the "what is this chip" intro, so the chip itself
  // shows only the provider's logo — plus the model name when one is set.
  const label = fmtAgentLabel(provider, model, nameById);
  const tipBody = (
    <TipBody
      title={text(`Model: ${label || "none"}`, `模型：${label || "未选择"}`)}
      detail={mark
        ? `${(provider && enabledModels?.find((m) => m.provider === provider)?.providerLabel) || provider} · ${mark.detail}`
        : text("Talks with you and plans the work. Click to change model.", "和你对话并规划工作。点击更换模型。")}
    />
  );
  return (
    <DropdownMenu open={open} onOpenChange={onOpenChange} modal={false}>
      <HoverTip label={tipBody}>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            id={id}
            className={cn(buttonVariants({ variant: "elevated", size: "sm" })) + " runtime-badge agent-badge" + (locked ? " locked" : "")}
          >
            {mark ? (
              <span className="relative inline-flex">
                <ModelMark provider={provider} />
                <TriggerMark tone={mark.tone} />
                {/* The dot is drawn only; its meaning is read out here. */}
                <span className="sr-only">{mark.detail}</span>
              </span>
            ) : (
              <ModelMark provider={provider} />
            )}
            {label ? <span className="badge-details">{label}</span> : null}
          </button>
        </DropdownMenuTrigger>
      </HoverTip>
      <DropdownMenuContent
        side="top"
        align="end"
        /* 9 = 10px band gap − 1px 输入框外扩 ring：底部一排弹层底缘
           与输入框可见边缘完全贴合。 */
        sideOffset={9}
        onCloseAutoFocus={(e) => e.preventDefault()}
        className={cn(MENU_PANEL, "text-text-primary")}
      >
        <AgentSelector
          currentProvider={provider}
          currentModel={model}
          onClose={() => setOpen(false)}
        />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
