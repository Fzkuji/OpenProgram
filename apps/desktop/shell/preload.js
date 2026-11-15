"use strict";
// The only bridge from the shell's two pages to the main process.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("openprogramShell", {
  state: () => ipcRenderer.invoke("shell:recovery-state"),
  useVersion: (id) => ipcRenderer.invoke("shell:recovery-use", id),
  copyLogPath: () => ipcRenderer.invoke("shell:recovery-copy-log"),
  restoreBuiltIn: () => ipcRenderer.invoke("shell:recovery-restore"),
  retrySetup: () => ipcRenderer.invoke("shell:setup-retry"),
  onSetup: (listener) => {
    ipcRenderer.on("shell:setup", (_event, value) => listener(value));
    ipcRenderer.send("shell:setup-ready");
  },
});
