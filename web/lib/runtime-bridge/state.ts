/**
 * Global mutable state — TS port of `public/js/shared/state.js`.
 *
 * These were top-level `var`s in the legacy script; every migrated
 * module reads/writes them via `window.*`, and the one remaining
 * legacy script (history-graph.js) reads them as bare globals (which
 * resolve to `window.*`). This module installs the initial values.
 *
 * Imported FIRST by AppShell so the globals exist before any other
 * module touches them. Idempotent — only sets a key when unset, so a
 * re-import never clobbers live state.
 */

const w = window as unknown as Record<string, unknown>;

function init(key: string, value: unknown): void {
  if (w[key] === undefined) w[key] = value;
}

init("ws", null);
init("trees", []);
init("selectedPath", null);
init("isPaused", false);
init("expandedNodes", new Set());
init("reconnectTimer", null);
// session_id is derived from the URL (source of truth).
init(
  "currentSessionId",
  (() => {
    const m = window.location.pathname.match(/^\/s\/([^/]+)/);
    return m ? m[1] : null;
  })(),
);
init("conversations", {});
init("availableFunctions", []);
init("pendingResponses", {});
init(
  "sidebarOpen",
  (() => {
    try {
      return localStorage.getItem("sidebarOpen") !== "0";
    } catch {
      return true;
    }
  })(),
);
init("_nodeCache", {});
init("_liveTreeCollapsed", false);
init("_lastRunCommand", null);
init("_skipScrollToBottom", false);
init("isRunning", false);
init("execLogStartTime", 0);
init("_modelList", []);
init("_currentModel", "");
init("_hasActiveSession", false);
init("programsMeta", { favorites: [], folders: {} });
init("_thinkingEffort", null);
init("_execThinkingEffort", null);
init("_thinkingConfig", null);
init("_lastChatProvider", null);
init("_lastChatModel", null);
init("_lastExecProvider", null);
init("_lastExecModel", null);
init("_agentSettings", { chat: {}, exec: {}, available: {} });
init("_elapsedTimer", null);

export {};
