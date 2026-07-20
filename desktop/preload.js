const { contextBridge, ipcRenderer } = require("electron");

const windowIdArgument = process.argv.find((argument) =>
  argument.startsWith("--openprogram-window-id="),
);
const windowId = windowIdArgument
  ? windowIdArgument.slice("--openprogram-window-id=".length)
  : "main";

contextBridge.exposeInMainWorld("openprogramDesktop", {
  isDesktop: true,
  windowId,
  openExternal: (url) => ipcRenderer.send("desktop:open-external", url),
  webTab: {
    ensure: (id, url) => ipcRenderer.send("webtab:ensure", id, url),
    navigate: (id, url) => ipcRenderer.send("webtab:navigate", id, url),
    activate: (id, url) => ipcRenderer.invoke("webtab:activate", id, url),
    setBounds: (id, bounds) => ipcRenderer.send("webtab:set-bounds", id, bounds),
    show: (id) => ipcRenderer.send("webtab:show", id),
    hide: (id) => ipcRenderer.send("webtab:hide", id),
    syncVisible: (items) => ipcRenderer.send("webtab:sync-visible", items),
    destroy: (id) => ipcRenderer.send("webtab:destroy", id),
    reload: (id) => ipcRenderer.send("webtab:reload", id),
    goBack: (id) => ipcRenderer.send("webtab:go-back", id),
    goForward: (id) => ipcRenderer.send("webtab:go-forward", id),
    onState: (cb) => {
      const listener = (_event, state) => cb(state);
      ipcRenderer.on("webtab:state", listener);
      return () => ipcRenderer.removeListener("webtab:state", listener);
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
  },
});

function subscribe(channel) {
  return (cb) => {
    const listener = (_event, detail) => cb(detail);
    ipcRenderer.on(channel, listener);
    return () => ipcRenderer.removeListener(channel, listener);
  };
}

// Menu shortcuts re-dispatched as DOM events for the renderer app.
ipcRenderer.on("menu:new-tab", () =>
  window.dispatchEvent(new CustomEvent("op-desktop-new-tab"))
);
ipcRenderer.on("menu:close-tab", () =>
  window.dispatchEvent(new CustomEvent("op-desktop-close-tab"))
);
