/**
 * TopBar — chat-page header strip.
 *
 * Renders the hamburger button + four badges (status, branch, chat agent,
 * exec agent) that used to be `<div class="topbar" id="mainTopbar">`
 * in the legacy index.html template. Mounted as a portal into the
 * `#topbar-mount` placeholder PageShell leaves in the chat page DOM.
 *
 * Source-of-truth for badge contents still lives in legacy globals
 * (`window._agentSettings`, `window._branchesByConv`, the WS status
 * envelope, etc.). The legacy updaters (`updateAgentBadges`,
 * `refreshBranchBadge`, `updateStatus`, `updateSendBtn`,
 * `setStatusDotHealth`, `refreshChannelBadge`) used to mutate DOM by
 * id; we wrap each of those globals so that *in addition* to (or
 * instead of) DOM mutation, they push a snapshot through to the
 * zustand session store. The TopBar then re-renders off the store.
 *
 * Dropdowns (channel / branch / chat-agent / exec-agent pickers) are
 * not migrated yet — their click handlers delegate straight to the
 * legacy `window.openChannelDropdown`, `window.openBranchDropdown`,
 * `window.openAgentSelector(...)` functions.
 */
"use client";

import { useEffect } from "react";
import { useShallow } from "zustand/react/shallow";

import {
  useSessionStore,
  type AgentSettingsSnapshot,
  type BranchBadgeInfo,
  type StatusBadgeInfo,
  type StatusTone,
} from "@/lib/session-store";

import styles from "./top-bar.module.css";

/* ---- Wrapper installation -----------------------------------------
   Each legacy updater reads from a module-local or window global and
   mutates the matching DOM badge. We replace each one with a wrapper
   that *also* pushes the relevant snapshot into the store before
   running the legacy body. Re-running install() on every store mount
   is safe: if the legacy code has already loaded, our wrapper sees
   the real function in window[name] and chains it; if it loads
   later, we polyfill the stub and the legacy file's own re-export
   chain just overwrites our stub — which is fine because the legacy
   re-export is what does the work anyway. */

type LegacyTopbarGlobals = {
  _agentSettings?: {
    chat?: { provider?: string; model?: string; session_id?: string; locked?: boolean };
    exec?: { provider?: string; model?: string; session_id?: string; locked?: boolean };
  };
  _branchesByConv?: Record<
    string,
    Array<{ name?: string; active?: boolean }>
  >;
  currentSessionId?: string | null;
  isPaused?: boolean;
  // Originals captured before we wrap them, so the wrapper can chain.
  __origUpdateAgentBadges?: () => void;
  __origRefreshBranchBadge?: () => void;
  __origUpdateStatus?: (status: string, source?: string) => void;
  __origSetStatusDotHealth?: (state: string) => void;
  __origUpdateSendBtn?: () => void;
  __origRefreshStatusSource?: () => void;
  updateAgentBadges?: () => void;
  refreshBranchBadge?: () => void;
  updateStatus?: (status: string, source?: string) => void;
  setStatusDotHealth?: (state: string) => void;
  updateSendBtn?: () => void;
  refreshStatusSource?: () => void;
};

function snapshotAgentSettings(): AgentSettingsSnapshot {
  const w = window as unknown as LegacyTopbarGlobals;
  const src = w._agentSettings || {};
  return {
    chat: src.chat ? { ...src.chat } : undefined,
    exec: src.exec ? { ...src.exec } : undefined,
  };
}

function snapshotBranchInfo(): BranchBadgeInfo {
  const w = window as unknown as LegacyTopbarGlobals;
  const sid = w.currentSessionId || null;
  if (!sid) return { visible: false, name: "main", count: 0 };
  const list = (w._branchesByConv && w._branchesByConv[sid]) || [];
  if (list.length === 0) return { visible: false, name: "main", count: 0 };
  const active = list.find((b) => b && b.active);
  const name = active && active.name ? active.name : "detached";
  return { visible: true, name, count: list.length };
}

/* The legacy ui.js carries the canonical mapping from (wsStatus,
   source, isPaused, isRunning) → (label, tone, css class). Rather
   than duplicate it, we read the rendered #statusBadge DOM after the
   legacy updater has run and lift label + tone out of it. That keeps
   the source-of-truth in one place during the cohabitation period.
   When the DOM badge goes away (this commit removes its placeholder
   from the HTML), we fall back to deriving label/tone from the
   wsStatus + isPaused globals — meaning all four updater wrappers
   below pass a state snapshot in to our derive function. */

function deriveStatusBadgeFromGlobals(): StatusBadgeInfo {
  const w = window as unknown as LegacyTopbarGlobals & {
    isRunning?: boolean;
    _lastStatus?: string;
    _lastStatusSource?: string;
  };
  if (w.isPaused) {
    return { label: "paused", tone: "warn", paused: true, title: "Paused" };
  }
  if (w.isRunning) {
    return { label: "running", tone: "ok", title: "Running" };
  }
  const ws = (window as unknown as { ws?: WebSocket }).ws;
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    const lab = ws && ws.readyState === WebSocket.CONNECTING ? "connecting…" : "disconnected";
    return {
      label: lab,
      tone: ws && ws.readyState === WebSocket.CONNECTING ? "connecting" : "err",
      title: lab,
    };
  }
  const source = w._lastStatusSource || "Local";
  return { label: source, tone: "ok", title: `connected · ${source}` };
}

function pushAgentSettings() {
  try {
    const store = (window as unknown as {
      __sessionStore?: typeof useSessionStore;
    }).__sessionStore;
    store?.getState().setAgentSettings(snapshotAgentSettings());
  } catch {
    /* ignore */
  }
}

function pushBranchInfo() {
  try {
    const store = (window as unknown as {
      __sessionStore?: typeof useSessionStore;
    }).__sessionStore;
    store?.getState().setBranchInfo(snapshotBranchInfo());
  } catch {
    /* ignore */
  }
}

function pushStatusBadge(override?: Partial<StatusBadgeInfo>) {
  try {
    const store = (window as unknown as {
      __sessionStore?: typeof useSessionStore;
    }).__sessionStore;
    if (!store) return;
    const derived = deriveStatusBadgeFromGlobals();
    store.getState().setStatusBadge({ ...derived, ...override });
  } catch {
    /* ignore */
  }
}

function installLegacyWrappers() {
  const w = window as unknown as LegacyTopbarGlobals;

  // updateAgentBadges — call original (DOM no-ops because the IDs
  // are gone) then push the snapshot through.
  if (w.updateAgentBadges && !w.__origUpdateAgentBadges) {
    w.__origUpdateAgentBadges = w.updateAgentBadges;
    w.updateAgentBadges = function () {
      try { w.__origUpdateAgentBadges?.(); } catch { /* ignore */ }
      pushAgentSettings();
    };
    pushAgentSettings();
  } else if (w.updateAgentBadges) {
    pushAgentSettings();
  }

  // refreshBranchBadge
  if (w.refreshBranchBadge && !w.__origRefreshBranchBadge) {
    w.__origRefreshBranchBadge = w.refreshBranchBadge;
    w.refreshBranchBadge = function () {
      try { w.__origRefreshBranchBadge?.(); } catch { /* ignore */ }
      pushBranchInfo();
    };
    pushBranchInfo();
  } else if (w.refreshBranchBadge) {
    pushBranchInfo();
  }

  // updateStatus(status, source) — legacy ui.js entry point for the
  // initial connection state and channel transitions.
  if (w.updateStatus && !w.__origUpdateStatus) {
    w.__origUpdateStatus = w.updateStatus;
    w.updateStatus = function (status: string, source?: string) {
      try { w.__origUpdateStatus?.(status, source); } catch { /* ignore */ }
      (window as unknown as { _lastStatus?: string; _lastStatusSource?: string }).
        _lastStatus = status;
      (window as unknown as { _lastStatus?: string; _lastStatusSource?: string }).
        _lastStatusSource = source || "Local";
      const connected = status === "connected";
      pushStatusBadge({
        label: connected ? (source || "Local") : "disconnected",
        tone: connected ? "ok" : "err",
        title: connected
          ? source
            ? `connected · ${source}`
            : "connected · local worker"
          : "disconnected",
      });
    };
  }

  // setStatusDotHealth(state) — only the dot color changes; label
  // stays whatever updateStatus last set it to.
  if (w.setStatusDotHealth && !w.__origSetStatusDotHealth) {
    w.__origSetStatusDotHealth = w.setStatusDotHealth;
    w.setStatusDotHealth = function (state: string) {
      try { w.__origSetStatusDotHealth?.(state); } catch { /* ignore */ }
      const tone: StatusTone =
        state === "ok" ? "ok" :
        state === "warn" ? "warn" :
        state === "err" ? "err" : "connecting";
      try {
        const store = (window as unknown as {
          __sessionStore?: typeof useSessionStore;
        }).__sessionStore;
        if (!store) return;
        const cur = store.getState().statusBadge;
        store.getState().setStatusBadge({ ...cur, tone });
      } catch { /* ignore */ }
    };
  }

  // updateSendBtn — toggles the badge between "paused", "running",
  // and whatever updateStatus last set. We re-derive from globals.
  if (w.updateSendBtn && !w.__origUpdateSendBtn) {
    w.__origUpdateSendBtn = w.updateSendBtn;
    w.updateSendBtn = function () {
      try { w.__origUpdateSendBtn?.(); } catch { /* ignore */ }
      pushStatusBadge();
    };
  }

  // refreshStatusSource — re-renders the status badge when the
  // channel binding changes. Calls updateStatus internally so the
  // wrapper above handles the push.
  if (w.refreshStatusSource && !w.__origRefreshStatusSource) {
    w.__origRefreshStatusSource = w.refreshStatusSource;
    w.refreshStatusSource = function () {
      try { w.__origRefreshStatusSource?.(); } catch { /* ignore */ }
      // updateStatus wrapper above already pushed.
    };
  }

  // Push an initial status snapshot so the badge transitions out of
  // its "connecting…" default the first time the wrappers install.
  // Legacy updateStatus(…) calls land later from chat-ws.js once the
  // WebSocket opens; those re-push through the wrapped path.
  pushStatusBadge();
}

/* ---- Component ----------------------------------------------------- */

export function TopBar() {
  // Install legacy-updater wrappers once the legacy globals have
  // loaded. Polled on a short interval because providers.js / ui.js
  // are inserted asynchronously by PageShell.
  useEffect(() => {
    let cancelled = false;
    function tryInstall() {
      const w = window as unknown as LegacyTopbarGlobals;
      if (
        w.updateAgentBadges &&
        w.refreshBranchBadge &&
        w.updateStatus &&
        w.setStatusDotHealth &&
        w.updateSendBtn
      ) {
        installLegacyWrappers();
        return true;
      }
      return false;
    }
    if (tryInstall()) return;
    const t = setInterval(() => {
      if (cancelled) return;
      if (tryInstall()) clearInterval(t);
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
    }))
  );

  const chat = agentSettings.chat || {};
  const exec = agentSettings.exec || {};
  const chatDetails = formatAgentDetails(chat.provider, chat.model, chat.session_id);
  const execDetails = formatAgentDetails(exec.provider, exec.model);
  const chatLocked = !!chat.locked;

  function onHamburger() {
    const w = window as unknown as { toggleSidebar?: () => void };
    w.toggleSidebar?.();
  }
  function onStatusClick(e: React.MouseEvent) {
    const w = window as unknown as { openChannelDropdown?: (e: MouseEvent) => void };
    w.openChannelDropdown?.(e.nativeEvent);
  }
  function onBranchClick(e: React.MouseEvent) {
    const w = window as unknown as { openBranchDropdown?: (e: MouseEvent) => void };
    w.openBranchDropdown?.(e.nativeEvent);
  }
  function onChatAgentClick() {
    if (chatLocked) return;
    const w = window as unknown as { openAgentSelector?: (k: string) => void };
    w.openAgentSelector?.("chat");
  }
  function onExecAgentClick() {
    const w = window as unknown as { openAgentSelector?: (k: string) => void };
    w.openAgentSelector?.("exec");
  }

  const statusClass =
    "status-badge" +
    (statusBadge.tone === "connecting" ? " connecting" : "") +
    (statusBadge.tone === "err" ? " disconnected" : "") +
    (statusBadge.paused ? " paused" : "");
  const dotClass =
    "status-dot " +
    (statusBadge.tone === "ok" ? "ok" :
     statusBadge.tone === "warn" ? "warn" :
     statusBadge.tone === "err" ? "err" : "");

  return (
    <div className={`topbar ${styles.bar}`} id="mainTopbar">
      <div className={`topbar-left ${styles.left}`}>
        <button
          type="button"
          className="menu-btn"
          id="menuBtn"
          onClick={onHamburger}
          aria-label="Toggle sidebar"
        >
          <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
            <path d="M16.5 4A1.5 1.5 0 0 1 18 5.5v9a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 2 14.5v-9A1.5 1.5 0 0 1 3.5 4zM7 15h9.5a.5.5 0 0 0 .5-.5v-9a.5.5 0 0 0-.5-.5H7zM3.5 5a.5.5 0 0 0-.5.5v9a.5.5 0 0 0 .5.5H6V5z" />
          </svg>
        </button>

        <span
          className={statusClass}
          onClick={onStatusClick}
          title={statusBadge.title || statusBadge.label}
        >
          <span className={dotClass} aria-hidden="true" />
          <span className="badge-short">{statusBadge.label}</span>
        </span>

        {branchInfo.visible ? (
          <span
            className="runtime-badge branch-badge"
            onClick={onBranchClick}
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
        ) : null}

        <span
          className={"runtime-badge agent-badge" + (chatLocked ? " locked" : "")}
          onClick={onChatAgentClick}
          title={"Chat agent" + chatDetails}
        >
          <span className="badge-short">Chat</span>
          <span className="badge-details">{chatDetails}</span>
        </span>

        <span
          className="runtime-badge agent-badge"
          onClick={onExecAgentClick}
          title={"Execution agent" + execDetails}
        >
          <span className="badge-short">Exec</span>
          <span className="badge-details">{execDetails}</span>
        </span>
      </div>

      <div className={`topbar-right ${styles.right}`} />
    </div>
  );
}

/** Format the trailing ": provider · model · sid8" suffix on Chat / Exec
 *  chips. Mirrors the legacy ``updateAgentBadges`` shape. */
function formatAgentDetails(
  provider?: string,
  model?: string,
  sessionId?: string
): string {
  const parts: string[] = [];
  parts.push(provider || "?");
  if (model) parts.push(model);
  if (sessionId) parts.push(sessionId.slice(0, 8));
  return ": " + parts.join(" · ");
}
