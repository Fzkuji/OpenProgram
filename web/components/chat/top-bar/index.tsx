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

import { useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";

import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

import { AgentSelector } from "./agent-selector";
import { BranchMenu } from "./branch-menu";
import { ChannelMenu } from "./channel-menu";
import { installLegacyWrappers, legacyTopbarReady } from "./window-bridge";
import { formatAgentDetails } from "./format";
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
  const chatDetails = formatAgentDetails(
    chat.provider,
    chat.model,
    chat.session_id,
  );
  const execDetails = formatAgentDetails(exec.provider, exec.model);
  const chatLocked = !!chat.locked;

  return (
    <div className={`topbar ${styles.bar}`} id="mainTopbar">
      <div className={`topbar-left ${styles.left}`}>
        <HamburgerButton />
        <StatusBadge statusBadge={statusBadge} />
        {branchInfo.visible ? <BranchBadge branchInfo={branchInfo} /> : null}
        <AgentBadge
          id="chatAgentBadge"
          kind="chat"
          details={chatDetails}
          locked={chatLocked}
          provider={chat.provider}
          model={chat.model}
        />
        <AgentBadge
          id="execAgentBadge"
          kind="exec"
          details={execDetails}
          locked={false}
          provider={exec.provider}
          model={exec.model}
        />
      </div>

      <div className={`topbar-right ${styles.right}`} />
    </div>
  );
}

/* ---- Sub-components --------------------------------------------- */

function HamburgerButton() {
  const { t } = useTranslation();

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
      aria-label={t("sidebar.toggle")}
    >
      <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
        <path d="M16.5 4A1.5 1.5 0 0 1 18 5.5v9a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 2 14.5v-9A1.5 1.5 0 0 1 3.5 4zM7 15h9.5a.5.5 0 0 0 .5-.5v-9a.5.5 0 0 0-.5-.5H7zM3.5 5a.5.5 0 0 0-.5.5v9a.5.5 0 0 0 .5.5H6V5z" />
      </svg>
    </button>
  );
}

function StatusBadge({
  statusBadge,
}: {
  statusBadge: ReturnType<typeof useSessionStore.getState>["statusBadge"];
}) {
  const [open, setOpen] = useState(false);

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
  const dotCls =
    "indicator-dot sm " +
    (statusBadge.tone === "ok" ? "--ok" :
     statusBadge.tone === "warn" ? "--warn" :
     statusBadge.tone === "err" ? "--err" : "--neutral");
  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>
        <span
          id="statusBadge"
          className={cls}
          title={statusBadge.title || statusBadge.label}
        >
          <span className={dotCls} aria-hidden="true" />
          <span className="badge-short">{statusBadge.label}</span>
        </span>
      </PopoverTrigger>
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
      <PopoverTrigger asChild>
    <span
      id="branchBadge"
      className="runtime-badge branch-badge"
      title={`${branchInfo.name} (${branchInfo.count} ${text("branches", "个分支")})`}
    >
      <span className="branch-icon" aria-hidden="true">
        <svg
          width="12"
          height="12"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <line x1="4.5" y1="3" x2="4.5" y2="11" />
          <circle cx="11.5" cy="4" r="1.6" />
          <circle cx="4.5" cy="12.5" r="1.6" />
          <path d="M11.5 5.6a6 6 0 0 1-6 6" />
        </svg>
      </span>
      <span className="branch-name">
        {branchInfo.name} ({branchInfo.count})
      </span>
    </span>
      </PopoverTrigger>
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

function AgentBadge({
  id,
  kind,
  details,
  locked,
  provider,
  model,
}: {
  id: string;
  kind: "chat" | "exec";
  details: string;
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

  const label = kind === "chat" ? t("agent.chat") : t("agent.exec");
  const tooltip = (kind === "chat" ? t("agent.chat_agent") : t("agent.execution_agent")) + details;
  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>
        <span
          id={id}
          className={"runtime-badge agent-badge" + (locked ? " locked" : "")}
          title={tooltip}
        >
          <span className="badge-short">{label}</span>
          <span className="badge-details">{details}</span>
        </span>
      </PopoverTrigger>
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
