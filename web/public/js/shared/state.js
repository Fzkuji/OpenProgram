// ===== Global State =====
var ws = null;
var trees = [];
var selectedPath = null;
var isPaused = false;
var expandedNodes = new Set();
var reconnectTimer = null;
// Drive session_id from URL (source of truth). localStorage is no longer used —
// it caused stale values when opening /new while an old id was cached.
var currentSessionId = (function() {
  var m = window.location.pathname.match(/^\/s\/([^/]+)/);
  return m ? m[1] : null;
})();
var conversations = {};
var availableFunctions = [];
var pendingResponses = {};  // msg_id -> element
// Sidebar collapse state is persisted so a refresh leaves the layout
// exactly where the user had it. Missing key → default open (first visit).
var sidebarOpen = (function () {
  try { return localStorage.getItem('sidebarOpen') !== '0'; } catch (e) { return true; }
})();
var _nodeCache = {};  // path -> node data
var _liveTreeCollapsed = false;
var _lastRunCommand = null;
var _skipScrollToBottom = false;
var isRunning = false;
var execLogStartTime = 0;
var _modelList = [];
var _currentModel = '';
var _hasActiveSession = false;
var programsMeta = { favorites: [], folders: {} };
// null = not yet initialized; buildThinkingMenu will pull the backend default
// the first time. Hardcoding 'medium' here used to override backend defaults
// (since 'medium' is a valid option for every provider, so the menu never
// applied the backend's default).
var _thinkingEffort = null;
var _execThinkingEffort = null;
var _thinkingConfig = null;
// Track previously-seen providers so loadAgentSettings can reset effort to the
// new provider's default when the provider actually changes. Without this,
// switching from Codex (xhigh) back to Claude would keep "xhigh" because it's
// a valid option for both — the default "auto" would never get applied.
var _lastChatProvider = null;
var _lastChatModel = null;
var _lastExecProvider = null;
var _lastExecModel = null;
var _agentSettings = { chat: {}, exec: {}, available: {} };
var _inputWrapperOriginal = '';
var _fnFormActive = false;
var _elapsedTimer = null;
