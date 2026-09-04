/**
 * Single main-window lifecycle for the desktop shell.
 *
 * createWindow() still builds a BrowserWindow (main or torn-off). This
 * module only answers "should we create a main window, or reuse one?"
 * so launch, Dock activate, and second-instance cannot open two mains.
 *
 * Spec: docs/reference/design/ui/window-lifecycle.md
 */

function windowIsLive(win) {
  return !!win && typeof win.isDestroyed === "function" && !win.isDestroyed();
}

/**
 * @param {{
 *   windows: Map<string, { win?: object }>,
 *   createWindow: (options?: object) => Promise<object> | object,
 * }} deps
 * @returns {() => Promise<object>}
 */
function createMainWindowGate({ windows, createWindow }) {
  if (!windows || typeof windows.get !== "function") {
    throw new TypeError("windows Map is required");
  }
  if (typeof createWindow !== "function") {
    throw new TypeError("createWindow is required");
  }
  let task = null;
  return function ensureMainWindow() {
    const existing = windows.get("main");
    if (existing && windowIsLive(existing.win)) {
      return Promise.resolve(existing);
    }
    if (!task) {
      task = Promise.resolve(createWindow({ windowId: "main" })).finally(() => {
        task = null;
      });
    }
    return task;
  };
}

function focusExistingWindow(win) {
  if (!windowIsLive(win)) return;
  if (typeof win.isMinimized === "function" && win.isMinimized()) win.restore();
  if (typeof win.show === "function") win.show();
  if (typeof win.focus === "function") win.focus();
}

/**
 * Wire the Electron app so every "open a window" path shares one gate.
 * `onReady` runs once, after the process is the primary instance and
 * before the first ensureMainWindow().
 *
 * @returns {{ primary: boolean }}
 */
function registerSingleMainWindow({
  app,
  BrowserWindow,
  ensureMainWindow,
  recoverErroredWindows,
  onReady,
  platform = process.platform,
}) {
  if (!app || typeof app.on !== "function") {
    throw new TypeError("app is required");
  }
  const canLock = typeof app.requestSingleInstanceLock === "function";
  const primary = !canLock || app.requestSingleInstanceLock();
  if (!primary) {
    app.quit();
    return { primary: false };
  }

  app.on("second-instance", () => {
    const existing = BrowserWindow.getAllWindows();
    if (existing.length === 0) {
      void ensureMainWindow();
      return;
    }
    if (typeof recoverErroredWindows === "function") recoverErroredWindows();
    focusExistingWindow(BrowserWindow.getFocusedWindow() || existing[0]);
  });

  app.whenReady().then(async () => {
    if (
      platform === "darwin" &&
      typeof app.setAccessibilitySupportEnabled === "function"
    ) {
      app.setAccessibilitySupportEnabled(true);
    }
    if (typeof onReady === "function") await onReady();
    void ensureMainWindow();
    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        void ensureMainWindow();
      } else if (typeof recoverErroredWindows === "function") {
        recoverErroredWindows();
      }
    });
  });

  return { primary: true };
}

module.exports = {
  windowIsLive,
  createMainWindowGate,
  focusExistingWindow,
  registerSingleMainWindow,
};
