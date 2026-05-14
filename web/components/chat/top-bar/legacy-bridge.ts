"use client";

/**
 * Wrap legacy DOM-mutating updaters (`updateAgentBadges`,
 * `refreshBranchBadge`, `updateStatus`, `setStatusDotHealth`,
 * `updateSendBtn`, `refreshStatusSource`) so that — in addition to
 * the legacy body running — a typed snapshot is pushed into the
 * zustand store and the React top bar re-renders.
 *
 * The wrappers are install-once: each captures the original under a
 * `__orig…` name and chains to it. Re-running `installLegacyWrappers`
 * after install is a no-op because the originals have already moved
 * aside.
 */

import {
  useSessionStore,
  type StatusBadgeInfo,
  type StatusTone,
} from "@/lib/session-store";

import {
  deriveStatusBadgeFromGlobals,
  snapshotAgentSettings,
  snapshotBranchInfo,
  type LegacyTopbarGlobals,
} from "./snapshots";

/* ---- store getters (lazy-bound to window.__sessionStore) ---------- */

function getStore() {
  const w = window as unknown as { __sessionStore?: typeof useSessionStore };
  return w.__sessionStore;
}

export function pushAgentSettings(): void {
  try {
    getStore()?.getState().setAgentSettings(snapshotAgentSettings());
  } catch {
    /* ignore */
  }
}

export function pushBranchInfo(): void {
  try {
    getStore()?.getState().setBranchInfo(snapshotBranchInfo());
  } catch {
    /* ignore */
  }
}

export function pushStatusBadge(override?: Partial<StatusBadgeInfo>): void {
  try {
    const store = getStore();
    if (!store) return;
    const derived = deriveStatusBadgeFromGlobals();
    store.getState().setStatusBadge({ ...derived, ...override });
  } catch {
    /* ignore */
  }
}

/* ---- wrapper installers ------------------------------------------ */

function wrapAgentBadges(w: LegacyTopbarGlobals): void {
  if (!w.updateAgentBadges) return;
  if (!w.__origUpdateAgentBadges) {
    w.__origUpdateAgentBadges = w.updateAgentBadges;
    w.updateAgentBadges = function () {
      try { w.__origUpdateAgentBadges?.(); } catch { /* ignore */ }
      pushAgentSettings();
    };
  }
  pushAgentSettings();
}

function wrapBranchBadge(w: LegacyTopbarGlobals): void {
  if (!w.refreshBranchBadge) return;
  if (!w.__origRefreshBranchBadge) {
    w.__origRefreshBranchBadge = w.refreshBranchBadge;
    w.refreshBranchBadge = function () {
      try { w.__origRefreshBranchBadge?.(); } catch { /* ignore */ }
      pushBranchInfo();
    };
  }
  pushBranchInfo();
}

/**
 * `refreshStatusSource` builds the legacy badge text by joining
 * `channel · account · title` with ` · `. The title slot ends up
 * displaying the conversation's first message in the badge, which
 * isn't what we want — the topbar status badge should show
 * connection / channel state, not chat content. Take only the first
 * segment for the React badge label; keep the full source string in
 * the tooltip so the title is still reachable on hover.
 */
function badgeLabelFromSource(source: string | undefined): string {
  if (!source) return "Local";
  const idx = source.indexOf(" · ");
  return idx >= 0 ? source.slice(0, idx) : source;
}

function wrapUpdateStatus(w: LegacyTopbarGlobals): void {
  if (!w.updateStatus || w.__origUpdateStatus) return;
  w.__origUpdateStatus = w.updateStatus;
  w.updateStatus = function (status: string, source?: string) {
    try { w.__origUpdateStatus?.(status, source); } catch { /* ignore */ }
    w._lastStatus = status;
    w._lastStatusSource = source || "Local";
    const connected = status === "connected";
    pushStatusBadge({
      label: connected ? badgeLabelFromSource(source) : "disconnected",
      tone: connected ? "ok" : "err",
      title: connected
        ? source
          ? `connected · ${source}`
          : "connected · local worker"
        : "disconnected",
    });
  };
}

function wrapStatusDotHealth(w: LegacyTopbarGlobals): void {
  if (!w.setStatusDotHealth || w.__origSetStatusDotHealth) return;
  w.__origSetStatusDotHealth = w.setStatusDotHealth;
  w.setStatusDotHealth = function (state: string) {
    try { w.__origSetStatusDotHealth?.(state); } catch { /* ignore */ }
    const tone: StatusTone =
      state === "ok" ? "ok" :
      state === "warn" ? "warn" :
      state === "err" ? "err" : "connecting";
    try {
      const store = getStore();
      if (!store) return;
      const cur = store.getState().statusBadge;
      store.getState().setStatusBadge({ ...cur, tone });
    } catch { /* ignore */ }
  };
}

function wrapUpdateSendBtn(w: LegacyTopbarGlobals): void {
  if (!w.updateSendBtn || w.__origUpdateSendBtn) return;
  w.__origUpdateSendBtn = w.updateSendBtn;
  w.updateSendBtn = function () {
    try { w.__origUpdateSendBtn?.(); } catch { /* ignore */ }
    pushStatusBadge();
  };
}

function wrapRefreshStatusSource(w: LegacyTopbarGlobals): void {
  if (!w.refreshStatusSource || w.__origRefreshStatusSource) return;
  w.__origRefreshStatusSource = w.refreshStatusSource;
  w.refreshStatusSource = function () {
    try { w.__origRefreshStatusSource?.(); } catch { /* ignore */ }
    // The updateStatus wrapper above already pushes when called.
  };
}

export function installLegacyWrappers(): void {
  const w = window as unknown as LegacyTopbarGlobals;
  wrapAgentBadges(w);
  wrapBranchBadge(w);
  wrapUpdateStatus(w);
  wrapStatusDotHealth(w);
  wrapUpdateSendBtn(w);
  wrapRefreshStatusSource(w);
  // Initial status push so the badge leaves "connecting…" the first
  // time wrappers install. Legacy updateStatus calls land later from
  // chat-ws.js once the WebSocket opens; those re-push through the
  // wrapped path.
  pushStatusBadge();
}

/** True if every legacy entry point needed for wiring is present. */
export function legacyTopbarReady(): boolean {
  const w = window as unknown as LegacyTopbarGlobals;
  return !!(
    w.updateAgentBadges &&
    w.refreshBranchBadge &&
    w.updateStatus &&
    w.setStatusDotHealth &&
    w.updateSendBtn
  );
}
