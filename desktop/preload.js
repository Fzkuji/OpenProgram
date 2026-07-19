const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("openprogramDesktop", {
  isDesktop: true,
  openExternal: (url) => ipcRenderer.send("desktop:open-external", url),
  webTab: {
    ensure: (id, url) => ipcRenderer.send("webtab:ensure", id, url),
    navigate: (id, url) => ipcRenderer.send("webtab:navigate", id, url),
    activate: (id, url) => ipcRenderer.invoke("webtab:activate", id, url),
    setBounds: (id, bounds) => ipcRenderer.send("webtab:set-bounds", id, bounds),
    show: (id) => ipcRenderer.send("webtab:show", id),
    hide: (id) => ipcRenderer.send("webtab:hide", id),
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
});

// Menu shortcuts re-dispatched as DOM events for the renderer app.
ipcRenderer.on("menu:new-tab", () =>
  window.dispatchEvent(new CustomEvent("op-desktop-new-tab"))
);
ipcRenderer.on("menu:close-tab", () =>
  window.dispatchEvent(new CustomEvent("op-desktop-close-tab"))
);
