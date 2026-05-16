/**
 * TopBar — chat-page header strip.
 *
 * Renders the hamburger button + four badges (status, branch, chat
 * agent, exec agent) that used to live in the legacy `<div class="topbar"
 * id="mainTopbar">` template. Each piece reads its state from the
 * zustand session store; the store is populated by `legacy-bridge.ts`
 * wrapping the legacy DOM-mutating updaters and pushing through.
 *
 * Dropdowns (channel / branch / chat-agent / exec-agent pickers) are
 * still owned by `conversations.js` / `providers.js`; the click
 * handlers below delegate to the legacy `window.open…` globals.
 */
"use client";

import { useEffect, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";

import { useSessionStore } from "@/lib/session-store";

import { AgentSelector } from "./agent-selector";
import { BranchMenu } from "./branch-menu";
import { ChannelMenu } from "./channel-menu";
import { installLegacyWrappers, legacyTopbarReady } from "./legacy-bridge";
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
      aria-label="Toggle sidebar"
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
  const ref = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const close = () => setOpen(false);
    window.addEventListener("topbar-close-menus", close);
    return () => window.removeEventListener("topbar-close-menus", close);
  }, []);

  function onClick() {
    if (open) {
      setOpen(false);
      return;
    }
    // Close every other top-bar dropdown first.
    window.dispatchEvent(new Event("topbar-close-menus"));
    (
      window as unknown as { _closeAllPopovers?: () => void }
    )._closeAllPopovers?.();
    setOpen(true);
  }
  const cls =
    "status-badge" +
    (statusBadge.tone === "connecting" ? " connecting" : "") +
    (statusBadge.tone === "err" ? " disconnected" : "") +
    (statusBadge.paused ? " paused" : "");
  const dotCls =
    "status-dot " +
    (statusBadge.tone === "ok" ? "ok" :
     statusBadge.tone === "warn" ? "warn" :
     statusBadge.tone === "err" ? "err" : "");
  return (
    <>
      <span
        ref={ref}
        id="statusBadge"
        className={cls}
        onClick={onClick}
        title={statusBadge.title || statusBadge.label}
      >
        <span className={dotCls} aria-hidden="true" />
        <span className="badge-short">{statusBadge.label}</span>
      </span>
      {open ? (
        <ChannelMenu anchorRef={ref} onClose={() => setOpen(false)} />
      ) : null}
    </>
  );
}

function BranchBadge({
  branchInfo,
}: {
  branchInfo: ReturnType<typeof useSessionStore.getState>["branchInfo"];
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const close = () => setOpen(false);
    window.addEventListener("topbar-close-menus", close);
    return () => window.removeEventListener("topbar-close-menus", close);
  }, []);

  function onClick() {
    if (open) {
      setOpen(false);
      return;
    }
    window.dispatchEvent(new Event("topbar-close-menus"));
    (
      window as unknown as { _closeAllPopovers?: () => void }
    )._closeAllPopovers?.();
    setOpen(true);
  }
  return (
    <>
    <span
      ref={ref}
      id="branchBadge"
      className="runtime-badge branch-badge"
      onClick={onClick}
      title={`${branchInfo.name} (${branchInfo.count} branches)`}
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
      <span
        className="branch-name"
        style={{
          display: "inline-block",
          maxWidth: 180,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          verticalAlign: "bottom",
        }}
      >
        {branchInfo.name} ({branchInfo.count})
      </span>
    </span>
    {open ? (
      <BranchMenu anchorRef={ref} onClose={() => setOpen(false)} />
    ) : null}
    </>
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
  const ref = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);

  // A `topbar-close-menus` event (fired by the other agent badge, or
  // by the legacy branch/channel popovers) closes this menu, so only
  // one top-bar dropdown is ever open.
  useEffect(() => {
    const close = () => setOpen(false);
    window.addEventListener("topbar-close-menus", close);
    return () => window.removeEventListener("topbar-close-menus", close);
  }, []);

  function onClick() {
    if (locked) return;
    if (open) {
      setOpen(false);
      return;
    }
    // Close every other top-bar dropdown — the sibling agent badge
    // (via the event) and the legacy branch/channel popovers.
    window.dispatchEvent(new Event("topbar-close-menus"));
    (window as unknown as { _closeAllPopovers?: () => void })._closeAllPopovers?.();
    setOpen(true);
  }

  const label = kind === "chat" ? "Chat" : "Exec";
  const tooltip = (kind === "chat" ? "Chat agent" : "Execution agent") + details;
  return (
    <>
      <span
        ref={ref}
        id={id}
        className={"runtime-badge agent-badge" + (locked ? " locked" : "")}
        onClick={onClick}
        title={tooltip}
      >
        <span className="badge-short">{label}</span>
        <span className="badge-details">{details}</span>
      </span>
      {open ? (
        <AgentSelector
          kind={kind}
          anchorRef={ref}
          currentProvider={provider}
          currentModel={model}
          onClose={() => setOpen(false)}
        />
      ) : null}
    </>
  );
}
