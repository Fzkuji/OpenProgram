const { contextBridge, ipcRenderer, webUtils } = require("electron");

// Channels that every web tab, bookmark folder or menu owner subscribes to
// would add one Electron listener each and trip its 10-listener warning.
// Each channel keeps one listener and fans out to its subscribers, in
// subscription order; a subscriber that throws does not starve the rest.
const fanOuts = new Map();
function fanOut(channel, cb) {
  let entry = fanOuts.get(channel);
  if (!entry) {
    const subscribers = new Set();
    const listener = (_event, payload) => {
      for (const subscriber of [...subscribers]) {
        try { subscriber(payload); } catch (error) { queueMicrotask(() => { throw error; }); }
      }
    };
    entry = { subscribers, listener };
    fanOuts.set(channel, entry);
    ipcRenderer.on(channel, listener);
  }
  const own = (payload) => cb(payload);
  entry.subscribers.add(own);
  return () => {
    entry.subscribers.delete(own);
    if (entry.subscribers.size || fanOuts.get(channel) !== entry) return;
    ipcRenderer.removeListener(channel, entry.listener);
    fanOuts.delete(channel);
  };
}

const windowIdArgument = process.argv.find((argument) =>
  argument.startsWith("--openprogram-window-id="),
);
const windowId = windowIdArgument
  ? windowIdArgument.slice("--openprogram-window-id=".length)
  : "main";
const surfaceArgument = process.argv.find((argument) =>
  argument.startsWith("--openprogram-surface="),
);
const overlaySurface = surfaceArgument
  ? surfaceArgument.slice("--openprogram-surface=".length)
  : "";

// Chromium emits resize for application zoom as well as window resizing.
// Notify only; native coordinates and current zoom remain owned by main.
if (process.platform === "darwin" && !overlaySurface && typeof window !== "undefined") {
  const syncWindowChrome = () => ipcRenderer.send("window:sync-chrome");
  window.addEventListener("resize", syncWindowChrome);
}

// The OS appearance as the main process reads it. "Auto" follows this, not the
// page's prefers-color-scheme: a debugger attached to the page can override the
// media query (Playwright forces "light" on every page it adopts) but not this.
// Read on first use, then kept current by the main process.
let systemDark;
function readSystemDark() {
  if (typeof systemDark !== "boolean") {
    try {
      systemDark = ipcRenderer.sendSync("theme:system-dark");
    } catch (_error) {
      /* The page falls back to its media query. */
    }
  }
  return typeof systemDark === "boolean" ? systemDark : undefined;
}
ipcRenderer.on("theme:system-changed", (_event, dark) => {
  if (typeof dark === "boolean") systemDark = dark;
});

contextBridge.exposeInMainWorld("openprogramDesktop", {
  isDesktop: true,
  writeClipboardText: (text) => ipcRenderer.invoke("clipboard:write-text", text),
  platform: process.platform,
  windowId,
  refreshOwnerAuth: () => ipcRenderer.invoke("owner-auth:refresh"),
  nativeActionCue: (cue) => ipcRenderer.send("native:action-cue", cue),
  nativeControl: (data) => ipcRenderer.send("native:control", data),
  // The floating bar of a demonstration recording (macOS only).
  recording: process.platform === "darwin" ? {
    openBar: (options) => ipcRenderer.invoke("recording:open-bar", options),
    finish: (result) => ipcRenderer.send("recording:finish", result),
    onFinished: (cb) => fanOut("recording:finished", cb),
  } : undefined,
  // Video of one pane of this page: `arm` lets the page's next request for
  // its own display stream through; the rest writes the encoded file.
  paneRecording: {
    arm: () => ipcRenderer.invoke("pane-recording:arm"),
    open: (id, filePath) => ipcRenderer.invoke("pane-recording:open", id, filePath),
    write: (id, chunk) => ipcRenderer.invoke("pane-recording:write", id, chunk),
    close: (id, discard) => ipcRenderer.invoke("pane-recording:close", id, discard),
  },
  selfUpdateCapture: (nonce) => ipcRenderer.invoke("self-update:ui-capture", nonce),
  selfUpdateReopen: {
    getState: () => ipcRenderer.invoke("self-update:reopen-state"),
    sessionLoaded: (sessionId) => ipcRenderer.invoke("self-update:session-loaded", sessionId),
    onState: (callback) => {
      const listener = (_event, state) => callback(state);
      ipcRenderer.on("self-update:reopen-state", listener);
      return () => ipcRenderer.removeListener("self-update:reopen-state", listener);
    },
  },
  // Electron removed the renderer's legacy File.path property. This is the
  // supported path boundary for a File explicitly selected or dropped by
  // the user; it does not expose general filesystem access to the renderer.
  getPathForFile: (file) => webUtils.getPathForFile(file),
  openExternal: (url) => ipcRenderer.send("desktop:open-external", url),
  openFileInDefaultApp: (filePath) => ipcRenderer.invoke("files:open-default", filePath),
  // Close this window (Chrome parity: closing the last tab closes the window).
  closeWindow: () => ipcRenderer.send("window:close-self"),
  // Move this window by a pixel delta (single-tab drag = move the window).
  moveWindowBy: (dx, dy) => ipcRenderer.send("window:move-by", dx, dy),
  // A snapshot of a region of this page (CSS px): a small thumbnail for the
  // window overview.
  captureRegion: (rect) => ipcRenderer.invoke("window:capture-region", rect),
  webTab: {
    ensure: (id, url) => ipcRenderer.send("webtab:ensure", id, url),
    navigate: (id, url) => ipcRenderer.send("webtab:navigate", id, url),
    activate: (id, url, requireVisible) =>
      ipcRenderer.invoke("webtab:activate", id, url, requireVisible),
    resolve: (id) => ipcRenderer.invoke("webtab:resolve", id),
    inspect: (id) => ipcRenderer.invoke("webtab:inspect", id),
    preview: (id, allowBackground) =>
      ipcRenderer.invoke("webtab:preview", id, allowBackground),
    setBounds: (id, bounds) => ipcRenderer.send("webtab:set-bounds", id, bounds),
    show: (id) => ipcRenderer.send("webtab:show", id),
    hide: (id) => ipcRenderer.send("webtab:hide", id),
    syncVisible: (items) => ipcRenderer.send("webtab:sync-visible", items),
    destroy: (id) => ipcRenderer.send("webtab:destroy", id),
    destroyConfirmed: (id) => ipcRenderer.invoke("webtab:destroy-confirmed", id),
    reload: (id, options) => ipcRenderer.send("webtab:reload", id, options),
    stop: (id) => ipcRenderer.send("webtab:stop", id),
    goBack: (id) => ipcRenderer.send("webtab:go-back", id),
    goForward: (id) => ipcRenderer.send("webtab:go-forward", id),
    find: (id, query, options) => ipcRenderer.send("webtab:find", id, query, options),
    stopFind: (id, action) => ipcRenderer.send("webtab:stop-find", id, action),
    pickElement: (id) => ipcRenderer.invoke("webtab:pick-element", id),
    pickRegion: (id) => ipcRenderer.invoke("webtab:pick-region", id),
    cancelPick: (id) => ipcRenderer.send("webtab:cancel-pick", id),
    zoom: (id, action) => ipcRenderer.invoke("webtab:zoom", id, action),
    print: (id) => ipcRenderer.invoke("webtab:print", id),
    capture: (id, mode) => ipcRenderer.invoke("webtab:capture", id, mode),
    showAction: (id, marker) => ipcRenderer.invoke("webtab:show-action", id, marker),
    setControlGlow: (id, held) => ipcRenderer.send("webtab:control-glow", id, held),
    setControlOverlay: (id, payload) => ipcRenderer.send("webtab:control-overlay", id, payload),
    onControlOverlayEvent: (cb) => fanOut("webtab:control-overlay-event", cb),
    setPipZoom: (id, width, height) => ipcRenderer.send("webtab:set-pip-zoom", id, width, height),
    onState: (cb) => fanOut("webtab:state", cb),
    onPopup: (cb) => fanOut("webtab:popup", cb),
    onFindResult: (cb) => fanOut("webtab:find-result", cb),
    onCommand: (cb) => fanOut("webtab:command", cb),
  },
  // Native OS menus return only the selected action ID to this renderer.
  contextMenu: {
    popup: (opts) => ipcRenderer.invoke("native-menu:popup", opts),
    close: (requestId) => ipcRenderer.send("native-menu:close", requestId),
  },
  // Main-menu overlay. The ⋮ menu is a top-layer WebContentsView (so it
  // covers native web tabs a DOM menu can't). open() from the real UI
  // window, choose()/close() from the overlay document, onAction() back on
  // the real UI window. One preload serves both documents.
  mainMenu: {
    open: (opts) => ipcRenderer.send("main-menu:open", opts),
    close: () => ipcRenderer.send("main-menu:close"),
    scheduleClose: (delay) => ipcRenderer.send("main-menu:schedule-close", delay),
    cancelClose: () => ipcRenderer.send("main-menu:cancel-close"),
    choose: (id, options) => ipcRenderer.send("main-menu:choose", id, options),
    updateItems: (items) => ipcRenderer.send("main-menu:update-items", items),
    // Overlay document reports its measured panel size so the host view can
    // resize to fit (labels never wrap, rows never clip).
    resize: (size) => ipcRenderer.send("main-menu:resize", size),
    onUpdate: (cb) => {
      const listener = (_event, state) => cb(state);
      ipcRenderer.on("main-menu:update", listener);
      return () => ipcRenderer.removeListener("main-menu:update", listener);
    },
    onAction: (cb) => fanOut("main-menu:action", cb),
    onClosed: (cb) => {
      const listener = () => cb();
      ipcRenderer.on("main-menu:closed", listener);
      return () => ipcRenderer.removeListener("main-menu:closed", listener);
    },
  },
  ...(overlaySurface === "browser-control" ? {
    browserControlOverlay: {
      ready: () => ipcRenderer.send("webtab:control-overlay-ready"),
      event: (payload) => ipcRenderer.send("webtab:control-overlay-event", payload),
      onUpdate: (cb) => {
        const listener = (_event, payload) => cb(payload);
        ipcRenderer.on("webtab:control-overlay-update", listener);
        return () => ipcRenderer.removeListener("webtab:control-overlay-update", listener);
      },
    },
  } : {}),
  ...(overlaySurface === "agent-control" ? {
    agentControlOverlay: {
      ready: () => ipcRenderer.send("agent-control:ready"),
      onUpdate: (cb) => {
        const listener = (_event, payload) => cb(payload);
        ipcRenderer.on("agent-control:update", listener);
        return () => ipcRenderer.removeListener("agent-control:update", listener);
      },
    },
  } : {}),
  history: {
    list: (options) => ipcRenderer.invoke("history:list", options),
    remove: (url, visitedAt) =>
      ipcRenderer.invoke("history:delete", url, visitedAt),
    clear: () => ipcRenderer.invoke("history:clear"),
  },
  downloads: {
    list: (options) => ipcRenderer.invoke("downloads:list", options),
    open: (id) => ipcRenderer.invoke("downloads:open", id),
    show: (id) => ipcRenderer.invoke("downloads:show", id),
    cancel: (id) => ipcRenderer.invoke("downloads:cancel", id),
    clear: () => ipcRenderer.invoke("downloads:clear"),
    onChanged: (cb) => {
      const listener = (_event, entry) => cb(entry);
      ipcRenderer.on("downloads:changed", listener);
      return () => ipcRenderer.removeListener("downloads:changed", listener);
    },
  },
  attention: {
    getPrefs: () => ipcRenderer.invoke("attention:get-prefs"),
    setPrefs: (patch) => ipcRenderer.invoke("attention:set-prefs", patch),
    sounds: () => ipcRenderer.invoke("attention:sounds"),
    previewSound: (sound) => ipcRenderer.invoke("attention:preview-sound", sound),
    onPrefs: (cb) => fanOut("attention:prefs", cb),
    notify: (request) => ipcRenderer.send("attention:notify", request),
    reportBusy: (busy) => ipcRenderer.send("attention:busy", Boolean(busy)),
    onOpenSession: (cb) => fanOut("attention:open-session", cb),
  },
  updates: {
    getState: () => ipcRenderer.invoke("updates:get-state"),
    check: () => ipcRenderer.invoke("updates:check"),
    setAutomaticChecks: (enabled) =>
      ipcRenderer.invoke("updates:set-automatic-checks", Boolean(enabled)),
    download: () => ipcRenderer.invoke("updates:download"),
    openRelease: () => ipcRenderer.invoke("updates:open-release"),
    onState: (cb) => subscribe("updates:state")(cb),
  },
  browserImport: {
    listSources: () => ipcRenderer.invoke("browser-import:list-sources"),
    run: (request) => ipcRenderer.invoke("browser-import:run", request),
    cancel: (requestId) => ipcRenderer.invoke("browser-import:cancel", requestId),
  },
  browserData: {
    clear: (options) => ipcRenderer.invoke("browser-data:clear", options),
  },
  theme: {
    setChrome: (payload) => ipcRenderer.send("theme:set-chrome", payload),
    trace: (payload) => ipcRenderer.send("theme:trace", payload),
    systemDark: readSystemDark,
    onSystemChange: (cb) => subscribe("theme:system-changed")(cb),
  },
  terminal: {
    start: (request) => ipcRenderer.invoke("terminal:start", request),
    write: (id, data) => ipcRenderer.send("terminal:write", id, data),
    resize: (id, cols, rows) => ipcRenderer.send("terminal:resize", id, cols, rows),
    stop: (id) => ipcRenderer.send("terminal:stop", id),
    onData: (cb) => {
      const listener = (_event, payload) => cb(payload);
      ipcRenderer.on("terminal:data", listener);
      return () => ipcRenderer.removeListener("terminal:data", listener);
    },
  },
  pointerFollow: {
    start(held, onCursor, onReleased) {
      const cursor = (_event, point) => onCursor(point);
      const released = () => onReleased();
      ipcRenderer.on("pointer-follow:cursor", cursor);
      ipcRenderer.on("pointer-follow:released", released);
      ipcRenderer.send("pointer-follow:start", held);
      return () => {
        ipcRenderer.removeListener("pointer-follow:cursor", cursor);
        ipcRenderer.removeListener("pointer-follow:released", released);
        ipcRenderer.send("pointer-follow:stop");
      };
    },
  },
  tabTransfer: {
    // Synchronous by contract: called from pointer/mouse down so the
    // token exists before a same-tick dragstart reads it.
    prepare: (payload) => ipcRenderer.sendSync("tab-transfer:prepare", payload),
    inspect: (token) => ipcRenderer.invoke("tab-transfer:inspect", token),
    accept: (token, placement) =>
      ipcRenderer.invoke("tab-transfer:accept", token, placement),
    reject: (token, reason, duplicateId) =>
      ipcRenderer.invoke("tab-transfer:reject", token, reason, duplicateId),
    status: (token) => ipcRenderer.invoke("tab-transfer:status", token),
    journalOpened: (token, role) =>
      ipcRenderer.invoke("tab-transfer:journal-opened", token, role),
    journalFinalized: (token, role, ownerWindowId) =>
      ipcRenderer.invoke("tab-transfer:journal-finalized", token, role, ownerWindowId),
    destinationReady: (token, ok) =>
      ipcRenderer.invoke("tab-transfer:destination-ready", token, ok),
    sourceRemoved: (token, ok, sourceEmpty) =>
      ipcRenderer.invoke("tab-transfer:source-removed", token, { ok, sourceEmpty }),
    destinationUndone: (token, ok) =>
      ipcRenderer.invoke("tab-transfer:destination-undone", token, ok),
    cancel: (token) => ipcRenderer.invoke("tab-transfer:cancel", token),
    detach: (token) => ipcRenderer.invoke("tab-transfer:detach", token),
    windowAtCursor: () => ipcRenderer.invoke("tab-transfer:window-at-cursor"),
    deliver: (token, targetWindowId) =>
      ipcRenderer.invoke("tab-transfer:deliver", token, targetWindowId),
    claimPending: (id) => ipcRenderer.invoke("tab-transfer:claim-pending", id),
    pendingTerminal: (id) => ipcRenderer.invoke("tab-transfer:pending-terminal", id),
    onRemoveSource: subscribe("tab-transfer:remove-source"),
    onUndoDestination: subscribe("tab-transfer:undo-destination"),
    onCommitted: subscribe("tab-transfer:committed"),
    onRejected: subscribe("tab-transfer:rejected"),
    onRolledBack: subscribe("tab-transfer:rolled-back"),
    onFinalizeOrphaned: subscribe("tab-transfer:finalize-orphaned"),
    onStageIncoming: subscribe("tab-transfer:stage-incoming"),
    // Cross-window drop cue: this window is (or is no longer) the hover target
    // of a drag happening in another window. cb(true) on enter, cb(false) on
    // leave. Mirrors onStageIncoming.
    onTransferHover: (cb) => {
      const enter = (_e) => cb(true);
      const leave = (_e) => cb(false);
      ipcRenderer.on("tab-transfer:hover-enter", enter);
      ipcRenderer.on("tab-transfer:hover-leave", leave);
      return () => {
        ipcRenderer.removeListener("tab-transfer:hover-enter", enter);
        ipcRenderer.removeListener("tab-transfer:hover-leave", leave);
      };
    },
  },
});

function subscribe(channel) {
  return (cb) => fanOut(channel, cb);
}

// Menu shortcuts re-dispatched as DOM events for the renderer app.
ipcRenderer.on("menu:new-tab", () =>
  window.dispatchEvent(new CustomEvent("op-desktop-new-tab"))
);
ipcRenderer.on("menu:close-tab", () =>
  window.dispatchEvent(new CustomEvent("op-desktop-close-tab"))
);
ipcRenderer.on("menu:reload", () =>
  window.dispatchEvent(new CustomEvent("op-desktop-reload", { detail: { hard: false } }))
);
ipcRenderer.on("menu:force-reload", () =>
  window.dispatchEvent(new CustomEvent("op-desktop-reload", { detail: { hard: true } }))
);

// Opaque Agent tickets and exact-instance human operations only. The native
// host checks the sender and redeems tickets; no owner credentials cross here.
contextBridge.exposeInMainWorld("openprogramTerminals", {
  windowId,
  agentTicket: (ticket) => ipcRenderer.invoke("terminal:agent-ticket", ticket),
  resource: (action, request = {}) => ipcRenderer.invoke("terminal:resource", action, request),
  onResource: subscribe("terminal:resource"),
});
