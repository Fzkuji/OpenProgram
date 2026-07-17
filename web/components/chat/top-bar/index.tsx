/**
 * Top-bar chip components — the session-scope chips that used to live
 * in the 48px `.topbar` row above the chat. That row is gone (chat
 * chrome is just the 40px tab strip now); the chips live on elsewhere:
 *
 *   - ProjectBadge / AgentBadge ×2 / PermissionBadge → composer bottom
 *     row (see composer/index.tsx, `.sessionChips`)
 *   - StatusDot → tab strip right corner (center-tab-strip.tsx)
 *   - BranchBadge → right sidebar History view header
 *
 * `LegacyTopbarBridge` (rendered unconditionally by AppShell) keeps the
 * window-bridge wrappers installed: legacy DOM-mutating updaters are
 * wrapped so their state lands in the zustand store, which these chips
 * read. Dropdowns still delegate to the submodules here
 * (project-menu / agent-selector / permission-menu / channel-menu /
 * branch-menu).
 */
"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { useSessionStore } from "@/lib/session-store";
import { api } from "@/lib/net/api";
import { useTranslation } from "@/lib/i18n";
import {
  type AnimatedNavIconHandle,
  GitBranchIcon,
  MessageCircleIcon,
  TerminalIcon,
} from "@/components/animated-icons";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { HoverTip } from "@/components/ui/tooltip";

import { AgentSelector } from "./agent-selector";
import { BranchMenu } from "./branch-menu";
import { ChannelMenu } from "./channel-menu";
import { installLegacyWrappers, legacyTopbarReady } from "./window-bridge";

export { ProjectBadge } from "./project-menu";
export { PermissionBadge } from "./permission-menu";

/**
 * Headless bridge — installs the legacy-updater wrappers once the
 * legacy globals have loaded. Polled on a short interval because
 * providers.js / ui.js are inserted asynchronously by PageShell.
 * Must stay mounted for the whole session (AppShell renders it
 * unconditionally next to the portals); without it the status /
 * branch / agent state never reaches the store.
 */
export function LegacyTopbarBridge() {
  useEffect(() => {
    let cancelled = false;
    if (legacyTopbarReady()) {
      installLegacyWrappers();
      return;
    }
    const t = setInterval(() => {
      if (cancelled) return;
      if (legacyTopbarReady()) {
        installLegacyWrappers();
        clearInterval(t);
      }
    }, 120);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);
  return null;
}

/* ---- Chips --------------------------------------------------------- */

/**
 * StatusDot — the old StatusBadge chip reduced to an 8px dot that sits
 * at the tab strip's right end. Same popover content (ChannelMenu),
 * same store slice; only the trigger shrank. Keeps `id="statusBadge"`
 * so the legacy ui.ts updaters' element guards still pass (they only
 * push to the store — no DOM mutation reaches the dot).
 */
export function StatusDot({ className }: { className?: string }) {
  const statusBadge = useSessionStore((s) => s.statusBadge);
  const [open, setOpen] = useState(false);
  const { text } = useTranslation();

  useEffect(() => {
    const close = () => setOpen(false);
    window.addEventListener("topbar-close-menus", close);
    return () => window.removeEventListener("topbar-close-menus", close);
  }, []);

  function onOpenChange(next: boolean) {
    if (next) {
      window.dispatchEvent(new Event("topbar-close-menus"));
      (
        window as unknown as { _closeAllPopovers?: () => void }
      )._closeAllPopovers?.();
    }
    setOpen(next);
  }

  const color =
    statusBadge.tone === "ok"
      ? "var(--accent-green)"
      : statusBadge.tone === "err"
        ? "var(--accent-red)"
        : "var(--accent-yellow)"; // connecting / warn / paused
  const label =
    statusBadge.label || text("Conversation channel", "会话渠道");
  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <HoverTip label={label}>
        <PopoverTrigger asChild>
          {/* 外层是 24px 点击区（module css 提供背景/sticky），内层
              才是 8px 着色圆点——inline 色值放外层会把整个点击区染色。 */}
          <span
            id="statusBadge"
            role="button"
            aria-label={label}
            className={className}
          >
            <span style={{ background: color }} />
          </span>
        </PopoverTrigger>
      </HoverTip>
      <PopoverContent
        align="end"
        sideOffset={4}
        className="w-auto border-0 bg-transparent p-0 shadow-none"
      >
        <ChannelMenu onClose={() => setOpen(false)} />
      </PopoverContent>
    </Popover>
  );
}

export function BranchBadge({
  branchInfo,
}: {
  branchInfo: ReturnType<typeof useSessionStore.getState>["branchInfo"];
}) {
  const [open, setOpen] = useState(false);
  const { text } = useTranslation();
  const iconRef = useRef<AnimatedNavIconHandle>(null);

  useEffect(() => {
    const close = () => setOpen(false);
    window.addEventListener("topbar-close-menus", close);
    return () => window.removeEventListener("topbar-close-menus", close);
  }, []);

  function onOpenChange(next: boolean) {
    if (next) {
      window.dispatchEvent(new Event("topbar-close-menus"));
      (
        window as unknown as { _closeAllPopovers?: () => void }
      )._closeAllPopovers?.();
    }
    setOpen(next);
  }
  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <HoverTip label={text("Conversation branch", "对话分支")}>
        <PopoverTrigger asChild>
          <span
            id="branchBadge"
            className="runtime-badge branch-badge"
            onMouseEnter={() => iconRef.current?.startAnimation?.()}
            onMouseLeave={() => iconRef.current?.stopAnimation?.()}
          >
            <span className="branch-icon" aria-hidden="true">
              <GitBranchIcon ref={iconRef} size={14} />
            </span>
            <span className="branch-name">
              {branchInfo.name} ({branchInfo.count})
            </span>
          </span>
        </PopoverTrigger>
      </HoverTip>
      <PopoverContent
        align="start"
        sideOffset={4}
        className="w-auto border-0 bg-transparent p-0 shadow-none"
      >
        <BranchMenu onClose={() => setOpen(false)} />
      </PopoverContent>
    </Popover>
  );
}

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

export function AgentBadge({
  id,
  kind,
  locked,
  provider,
  model,
}: {
  id: string;
  kind: "chat" | "exec";
  locked: boolean;
  provider?: string;
  model?: string;
}) {
  const [open, setOpen] = useState(false);
  const { t } = useTranslation();

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
      (window as unknown as { _closeAllPopovers?: () => void })._closeAllPopovers?.();
    }
    setOpen(next);
  }

  // Enabled-models list (shared cache with the AgentSelector dropdown —
  // same queryKey, so this adds no extra fetch) → id-to-display-name map
  // so the chip shows "GPT-5.5" instead of "openai-codex:gpt-5.5". Keyed
  // both by "provider:id" and bare "id" for whichever form the chip has.
  const { data: enabledModels } = useQuery({
    queryKey: ["models-enabled"],
    queryFn: api.listEnabledModels,
  });
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
  // shows only a glyph — plus the model name when one is set.
  const iconRef = useRef<AnimatedNavIconHandle>(null);
  const tooltip = kind === "chat" ? t("agent.chat_agent") : t("agent.execution_agent");
  // Distinct glyph per role: message bubble = chat model, terminal =
  // execution / tool-running model. Both animated (pqoqubbw set).
  const Icon = kind === "chat" ? MessageCircleIcon : TerminalIcon;
  const label = fmtAgentLabel(provider, model, nameById);
  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <HoverTip label={tooltip}>
        <PopoverTrigger asChild>
          <span
            id={id}
            className={"runtime-badge agent-badge" + (locked ? " locked" : "")}
            onMouseEnter={() => iconRef.current?.startAnimation?.()}
            onMouseLeave={() => iconRef.current?.stopAnimation?.()}
          >
            <Icon
              ref={iconRef}
              size={14}
              className={label ? "shrink-0 mr-[4px]" : "shrink-0"}
              aria-hidden="true"
            />
            {label ? <span className="badge-details">{label}</span> : null}
          </span>
        </PopoverTrigger>
      </HoverTip>
      <PopoverContent
        align="start"
        sideOffset={4}
        onOpenAutoFocus={(e) => e.preventDefault()}
        className="w-auto border-0 bg-transparent p-0 shadow-none"
      >
        <AgentSelector
          kind={kind}
          currentProvider={provider}
          currentModel={model}
          onClose={() => setOpen(false)}
        />
      </PopoverContent>
    </Popover>
  );
}
