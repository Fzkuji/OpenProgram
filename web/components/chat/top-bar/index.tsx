/**
 * TopBar — chat-page header strip.
 *
 * Renders the hamburger button + four badges (status, branch, chat
 * agent, exec agent) that used to live in the legacy `<div class="topbar"
 * id="mainTopbar">` template. Each piece reads its state from the
 * zustand session store; the store is populated by `window-bridge.ts`
 * wrapping the legacy DOM-mutating updaters and pushing through.
 *
 * Dropdowns (channel / branch / chat-agent / exec-agent pickers) are
 * still owned by `conversations.js` / `providers.js`; the click
 * handlers below delegate to the legacy `window.open…` globals.
 */
"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useQuery } from "@tanstack/react-query";

import { useSessionStore } from "@/lib/session-store";
import { api } from "@/lib/net/api";
import { useTranslation } from "@/lib/i18n";
import {
  type AnimatedNavIconHandle,
  FolderOpenIcon,
  GitBranchIcon,
  MessageCircleIcon,
  MonitorIcon,
  PanelLeftOpenIcon,
  TerminalIcon,
} from "@/components/animated-icons";
import { useFilesPanel } from "@/lib/state/files-panel-store";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { HoverTip } from "@/components/ui/tooltip";

import { AgentSelector } from "./agent-selector";
import { BranchMenu } from "./branch-menu";
import { ChannelMenu } from "./channel-menu";
import { ProjectBadge } from "./project-menu";
import { PermissionBadge } from "./permission-menu";
import { installLegacyWrappers, legacyTopbarReady } from "./window-bridge";
import styles from "./top-bar.module.css";

export function TopBar() {
  // Install legacy-updater wrappers once the legacy globals have
  // loaded. Polled on a short interval because providers.js / ui.js
  // are inserted asynchronously by PageShell.
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

  const { agentSettings, branchInfo, statusBadge } = useSessionStore(
    useShallow((s) => ({
      agentSettings: s.agentSettings,
      branchInfo: s.branchInfo,
      statusBadge: s.statusBadge,
    })),
  );

  const chat = agentSettings.chat || {};
  const exec = agentSettings.exec || {};
  const chatLocked = !!chat.locked;

  // Collapse chip labels by MEASURED overflow, not fixed width breakpoints.
  // `fit()` tries compaction levels 0→3 and stops at the first that keeps
  // the last chip inside the row, so labels are dropped (longest first)
  // only when they genuinely don't fit — never while there's still room.
  const leftRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const left = leftRef.current;
    const bar = left?.parentElement; // the .topbar
    if (!left || !bar || typeof ResizeObserver === "undefined") return;
    const fit = () => {
      const last = document.getElementById("execAgentBadge");
      if (!last) {
        left.dataset.compact = "0";
        return;
      }
      // Available room = the topbar's inner right edge minus its right
      // padding and anything pinned to the right. The chips overflow this
      // edge before they overflow `left` (which shrink-wraps its content),
      // so we must measure against the bar, not against `left`.
      const cs = getComputedStyle(bar);
      const rightSide = bar.querySelector(".topbar-right");
      const reserved =
        (parseFloat(cs.paddingRight) || 0) +
        (rightSide ? rightSide.getBoundingClientRect().width : 0);
      const limit = bar.getBoundingClientRect().right - reserved;
      for (let lvl = 0; lvl <= 3; lvl++) {
        left.dataset.compact = String(lvl);
        if (last.getBoundingClientRect().right <= limit + 0.5) break;
      }
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(bar);
    return () => ro.disconnect();
  }, [agentSettings, branchInfo, statusBadge]);

  return (
    <div className={`topbar ${styles.bar}`} id="mainTopbar">
      <div ref={leftRef} className={`topbar-left ${styles.left}`}>
        <HamburgerButton />
        <StatusBadge statusBadge={statusBadge} />
        <ProjectBadge />
        {branchInfo.visible ? <BranchBadge branchInfo={branchInfo} /> : null}
        <AgentBadge
          id="chatAgentBadge"
          kind="chat"
          locked={chatLocked}
          provider={chat.provider}
          model={chat.model}
        />
        <AgentBadge
          id="execAgentBadge"
          kind="exec"
          locked={false}
          provider={exec.provider}
          model={exec.model}
        />
      </div>

      <div className={`topbar-right ${styles.right}`}>
        <FilesToggleButton />
        <PermissionBadge />
      </div>
    </div>
  );
}

/* ---- Sub-components --------------------------------------------- */

/** Folder button toggling the in-chat project files panel. */
function FilesToggleButton() {
  const { text } = useTranslation();
  const iconRef = useRef<AnimatedNavIconHandle>(null);
  const open = useFilesPanel((s) => s.open);
  const toggleOpen = useFilesPanel((s) => s.toggleOpen);
  return (
    <HoverTip label={text("Project files", "项目文件")}>
      <button
        type="button"
        className={`${styles.iconToggle} ${open ? styles.iconToggleActive : ""}`}
        aria-label={text("Toggle project files panel", "切换项目文件面板")}
        aria-pressed={open}
        onClick={toggleOpen}
        onMouseEnter={() => iconRef.current?.startAnimation?.()}
        onMouseLeave={() => iconRef.current?.stopAnimation?.()}
      >
        <FolderOpenIcon ref={iconRef} size={16} />
      </button>
    </HoverTip>
  );
}

function HamburgerButton() {
  const { t } = useTranslation();
  const iconRef = useRef<AnimatedNavIconHandle>(null);

  function onClick() {
    const w = window as unknown as { toggleSidebar?: () => void };
    w.toggleSidebar?.();
  }
  return (
    <button
      type="button"
      className="menu-btn"
      id="menuBtn"
      onClick={onClick}
      onMouseEnter={() => iconRef.current?.startAnimation?.()}
      onMouseLeave={() => iconRef.current?.stopAnimation?.()}
      aria-label={t("sidebar.toggle")}
    >
      <PanelLeftOpenIcon ref={iconRef} size={20} />
    </button>
  );
}

function StatusBadge({
  statusBadge,
}: {
  statusBadge: ReturnType<typeof useSessionStore.getState>["statusBadge"];
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
      // Close every other top-bar dropdown first.
      window.dispatchEvent(new Event("topbar-close-menus"));
      (
        window as unknown as { _closeAllPopovers?: () => void }
      )._closeAllPopovers?.();
    }
    setOpen(next);
  }
  const cls =
    "status-badge" +
    (statusBadge.tone === "connecting" ? " connecting" : "") +
    (statusBadge.tone === "err" ? " disconnected" : "") +
    (statusBadge.paused ? " paused" : "");
  // Always show the channel label beside the icon (e.g. "Local") — the
  // chip is never icon-only; "Local" is a real value, not "unset".
  const showLabel = !!statusBadge.label;
  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <HoverTip label={text("Conversation channel", "会话渠道")}>
        <PopoverTrigger asChild>
          <span
            id="statusBadge"
            className={cls}
            onMouseEnter={() => iconRef.current?.startAnimation?.()}
            onMouseLeave={() => iconRef.current?.stopAnimation?.()}
          >
            {/* Monitor = "Local" (this machine). Animated (pqoqubbw set);
                inherits the chip's status colour via currentColor. */}
            <MonitorIcon
              ref={iconRef}
              size={14}
              className={showLabel ? "shrink-0 mr-[4px]" : "shrink-0"}
              aria-hidden="true"
            />
            {showLabel ? (
              <span className="badge-short">{statusBadge.label}</span>
            ) : null}
          </span>
        </PopoverTrigger>
      </HoverTip>
      <PopoverContent
        align="start"
        sideOffset={4}
        className="w-auto border-0 bg-transparent p-0 shadow-none"
      >
        <ChannelMenu onClose={() => setOpen(false)} />
      </PopoverContent>
    </Popover>
  );
}

function BranchBadge({
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

/** Topbar agent-chip label: the model's display name (e.g. "GPT-5.5",
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

function AgentBadge({
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
