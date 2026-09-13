const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { EventEmitter } = require("node:events");
const vm = require("node:vm");
const {
  validateTransferPayload: productionValidateTransferPayload,
} = require("../tab-transfer-validation");

const runtimeSource = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
const source = require("./main-source").readMainSource();
const preloadSource = fs.readFileSync(
  path.join(__dirname, "..", "preload.js"),
  "utf8",
);
const transferUserData = fs.mkdtempSync(
  path.join(os.tmpdir(), "openprogram-webtab-transaction-"),
);

const ipcListeners = new Map();
const ipcHandlers = new Map();
let focusedWindow = null;
const fakeWindows = [];
const browserWindowOptions = [];
let menuTemplate = null;
const menuPopupOptions = [];
const clipboardWrites = [];
let nextGeneratedWindowId = 1000;
let generatedNativeViews = 0;
const generatedNativeRecords = [];
const rendererQueue = [];
const spawnedChildren = [];
const spawnedPtys = [];
const clearedStorageRequests = [];
const openedDownloadPaths = [];
const shownDownloadPaths = [];
const shownPrintSaveDialogs = [];
let nextPrintSaveDialogResult = { canceled: true };
let fakeBrowserImportRunner = async () => ({
  source: { browserId: "chrome", profileId: "Default", label: "Chrome · Default" },
  history: [],
  bookmarks: [],
  cookies: { imported: 0, failed: 0 },
});

function fakeSpawn() {
  const child = new EventEmitter();
  child.exitCode = null;
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  child.stdin = {
    writable: true,
    writes: [],
    write(value) { this.writes.push(value); },
  };
  child.kills = [];
  child.kill = (signal) => {
    child.kills.push(signal);
    return true;
  };
  child.unref = () => {};
  spawnedChildren.push(child);
  return child;
}

function fakePtySpawn(file, args, options) {
  const process = new EventEmitter();
  process.pid = 50_000 + spawnedPtys.length;
  process.file = file;
  process.args = args;
  process.options = options;
  process.writes = [];
  process.resizes = [];
  process.kills = [];
  process.write = (data) => process.writes.push(data);
  process.resize = (cols, rows) => process.resizes.push([cols, rows]);
  process.kill = (signal) => process.kills.push(signal);
  process.onData = (listener) => {
    process.on("data", listener);
    return { dispose: () => process.off("data", listener) };
  };
  process.onExit = (listener) => {
    process.on("exit", listener);
    return { dispose: () => process.off("exit", listener) };
  };
  spawnedPtys.push(process);
  return process;
}

function flushRendererQueue() {
  let delivered = 0;
  while (rendererQueue.length > 0) {
    delivered += 1;
    if (delivered > 1000) throw new Error("renderer callback queue did not settle");
    rendererQueue.shift()();
  }
}

function createFakeClock() {
  let now = 0;
  let nextId = 1;
  const pending = new Map();
  const all = new Map();
  return {
    setTimeout(callback, delay) {
      const id = nextId++;
      const timer = { id, callback, delay, dueAt: now + delay };
      pending.set(id, timer);
      all.set(id, timer);
      return id;
    },
    clearTimeout(id) { pending.delete(id); },
    setInterval(callback, delay) {
      const id = nextId++;
      const timer = { id, callback, delay, dueAt: now + delay, repeat: true };
      pending.set(id, timer);
      all.set(id, timer);
      return id;
    },
    clearInterval(id) { pending.delete(id); },
    advance(ms) {
      now += ms;
      const ready = [...pending.values()]
        .filter((timer) => timer.dueAt <= now)
        .sort((a, b) => a.dueAt - b.dueAt || a.id - b.id);
      for (const timer of ready) {
        if (!pending.has(timer.id)) continue;
        if (timer.repeat) {
          timer.dueAt = now + timer.delay;
        } else {
          pending.delete(timer.id);
        }
        timer.callback();
      }
    },
    runCleared(id) { all.get(id)?.callback(); },
    pendingIds() { return [...pending.keys()]; },
  };
}

const clock = createFakeClock();

// Set by a test just before a boot to hold that window's ready-to-show,
// so the "reveal only a painted window" guard is observable.
let deferReadyToShowNextWindow = false;

class FakeBrowserWindow {
  constructor(options) {
    browserWindowOptions.push(options);
    const win = fakeWindow(nextGeneratedWindowId++);
    win.constructorOptions = options || {};
    if (options && options.show === false) win.shown = false;
    if (options && options.focusable === false) win.focusable = false;
    win.parentWindow = options && options.parent;
    return win;
  }
  static fromWebContents(sender) {
    return fakeWindows.find((win) => win.webContents === sender) || null;
  }
  static getFocusedWindow() { return focusedWindow; }
  static getAllWindows() {
    return fakeWindows.filter((win) => !win.isDestroyed());
  }
}

const fakeHttp = {
  get(_url, _options, callback) {
    const request = {
      on() { return request; },
      destroy() {},
    };
    callback({ resume() {} });
    return request;
  },
};

const fakeElectron = {
  app: {
    commandLine: { appendSwitch() {} },
    getPath() { return transferUserData; },
    whenReady() { return { then() {} }; },
    on() {},
    quit() {},
  },
  BrowserWindow: FakeBrowserWindow,
  // Cursor + display stubs for the tear-off placement paths. `cursorPoint`
  // is moved by the tests to drive centerHiddenWindowOnCursor; the single
  // display's work area is deliberately smaller than the display so the
  // clamped / unclamped distinction is observable.
  screen: {
    getCursorScreenPoint() { return { ...fakeElectron.cursorPoint }; },
    getDisplayNearestPoint() {
      return fakeElectron.screen.getPrimaryDisplay();
    },
    getAllDisplays() {
      return [{
        id: 1,
        bounds: { x: 0, y: 0, width: 1440, height: 900 },
        workArea: { x: 0, y: 0, width: 1440, height: 900 },
      }];
    },
    getPrimaryDisplay() {
      return fakeElectron.screen.getAllDisplays()[0];
    },
  },
  cursorPoint: { x: 700, y: 40 },
  nativeTheme: { prefersReducedMotion: false },
  WebContentsView: class {
    constructor() {
      generatedNativeViews += 1;
      const controlled = controlledRecord(`native-view-${generatedNativeViews}`);
      generatedNativeRecords.push(controlled);
      return controlled.record.view;
    }
  },
  Menu: {
    buildFromTemplate(template) {
      menuTemplate = template;
      return {
        popup(options) { menuPopupOptions.push(options); },
      };
    },
    setApplicationMenu() {},
  },
  clipboard: {
    writeText(value) { clipboardWrites.push(value); },
  },
  ipcMain: {
    on(channel, handler) { ipcListeners.set(channel, handler); },
    handle(channel, handler) { ipcHandlers.set(channel, handler); },
  },
  dialog: {
    async showSaveDialog(win, options) {
      shownPrintSaveDialogs.push({ win, options });
      return nextPrintSaveDialogResult;
    },
  },
  shell: {
    openExternal() {},
    async openPath(value) { openedDownloadPaths.push(value); return ""; },
    showItemInFolder(value) { shownDownloadPaths.push(value); },
  },
  session: {
    fromPartition(partition) {
      assert.equal(partition, "persist:webtabs");
      return {
        async clearStorageData(options) {
          clearedStorageRequests.push(options);
        },
      };
    },
  },
};

const processSignals = [];
const windowsTreeKills = [];
const sandboxProcess = Object.create(process);
sandboxProcess.kill = (pid, signal) => {
  processSignals.push([pid, signal]);
  spawnedPtys.find((child) => child.pid === Math.abs(pid))?.kill(signal);
};

const sandbox = {
  Promise,
  AbortController,
  Map,
  Set,
  URL,
  console,
  encodeURIComponent,
  setTimeout: clock.setTimeout,
  clearTimeout: clock.clearTimeout,
  setInterval: clock.setInterval,
  clearInterval: clock.clearInterval,
  process: sandboxProcess,
  __dirname: path.join(__dirname, ".."),
  __filename: path.join(__dirname, "..", "main.js"),
  require(id) {
    if (id === "electron") return fakeElectron;
    if (id === "http") return fakeHttp;
    if (id === "child_process") {
      return {
        spawn: fakeSpawn,
        execFileSync: () => spawnedPtys
          .map((child) => `${child.pid + 1_000} ${child.pid}`)
          .join("\n"),
      };
    }
    if (id === "node-pty") return { spawn: fakePtySpawn };
    if (id === "./terminal-command") {
      return {
        ...require(path.join(__dirname, "..", "terminal-command")),
        killWindowsProcessTree(pid) {
          windowsTreeKills.push(pid);
          return true;
        },
      };
    }
    if (id === "./browser-profile-import") {
      return {
        listBrowserSources: () => [],
        runBrowserImport: (...args) => fakeBrowserImportRunner(...args),
      };
    }
    // Relative requires inside main.js resolve against THIS file, not
    // main.js — map the desktop-local modules explicitly.
    if (id.startsWith("./")) return require(path.join(__dirname, "..", id));
    return require(id);
  },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(
  `${runtimeSource}\n;globalThis.__webtabTestHooks = {
    makeWindowContext,
    contextForSender,
    nativeMenuOwner,
    focusedContext,
    syncVisibleViews,
    showView,
    hideView,
    sendState,
    forwardFindResult,
    handleWebTabShortcut,
    loadView,
    activateView,
    resolveView,
    inspectView,
    previewView,
    setPipZoom,
    runNativeNavigation,
    registerWebTabIpc,
    registerDownloads,
    downloads,
    downloadReservationKey,
    buildMenu,
    createWindow,
    cleanupWindowContext,
    clampContextMenuPanel,
    contextMenuRequestedX,
    ensureView,
    destroyView,
    showActionView,
    validateTransferPayload:
      typeof validateTransferPayload === "function" ? validateTransferPayload : undefined,
    reparentRecords:
      typeof reparentRecords === "function" ? reparentRecords : undefined,
    restoreRecords:
      typeof restoreRecords === "function" ? restoreRecords : undefined,
    makeTransferCoordinator:
      typeof makeTransferCoordinator === "function" ? makeTransferCoordinator : undefined,
    registerTabTransferIpc:
      typeof registerTabTransferIpc === "function" ? registerTabTransferIpc : undefined,
    tabTransfers:
      typeof tabTransfers === "object" ? tabTransfers : undefined,
    windows,
    contextsByBrowserWindowId,
  };`,
  sandbox,
  { filename: "apps/desktop/main.js" },
);
const hooks = sandbox.__webtabTestHooks;

function fakeWindow(id) {
  const listeners = new Map();
  const wcListeners = new Map();
  const sent = [];
  const added = [];
  const removed = [];
  let addCalls = 0;
  const win = {
    id,
    destroyed: false,
    listeners,
    sent,
    added,
    removed,
    shown: false,
    closeCalls: 0,
    failAddAt: null,
    contentView: {
      addChildView(view) {
        addCalls += 1;
        if (win.failAddAt === addCalls) throw new Error("injected addChildView failure");
        added.push(view);
      },
      removeChildView(view) { removed.push(view); },
    },
    webContents: {
      send(...args) {
        sent.push(args);
        win.webContentsSent = win.webContentsSent || [];
        win.webContentsSent.push(args);
        const callback = win.onSend;
        if (callback) rendererQueue.push(() => callback(...args));
      },
      setWindowOpenHandler() {},
      getURL() { return win.loadedUrl || "http://127.0.0.1:18100/"; },
      isDestroyed() { return win.destroyed; },
      loadURL(url) { return win.loadURL(url); },
      on(event, handler) {
        wcListeners.set(event, [...(wcListeners.get(event) || []), handler]);
      },
      once(event, handler) {
        const wrapper = (...args) => {
          this.removeListener(event, wrapper);
          handler(...args);
        };
        wrapper.original = handler;
        this.on(event, wrapper);
      },
      removeListener(event, handler) {
        wcListeners.set(
          event,
          (wcListeners.get(event) || []).filter(
            (item) => item !== handler && item.original !== handler,
          ),
        );
      },
      emit(event, ...args) {
        for (const handler of wcListeners.get(event) || []) handler(...args);
      },
    },
    on(event, handler) { listeners.set(event, handler); },
    removeListener(event, handler) {
      if (listeners.get(event) === handler) listeners.delete(event);
    },
    // Renderers are "already painted" by default so existing tests are
    // unaffected; a test sets `deferReadyToShow` before the boot to hold
    // ready-to-show and fires it manually via `emitReadyToShow()`.
    deferReadyToShow: deferReadyToShowNextWindow,
    once(event, handler) {
      if (event !== "ready-to-show") { listeners.set(event, handler); return; }
      if (this.deferReadyToShow) { this.pendingReadyToShow = handler; return; }
      handler();
    },
    emitReadyToShow() {
      const handler = this.pendingReadyToShow;
      this.pendingReadyToShow = null;
      handler?.();
    },
    isDestroyed() { return this.destroyed; },
    isFocused() { return focusedWindow === this; },
    show() { this.shown = true; this.focusCalls = (this.focusCalls || 0) + 1; },
    showInactive() { this.shown = true; this.showInactiveCalls = (this.showInactiveCalls || 0) + 1; },
    hide() { this.shown = false; this.hideCalls = (this.hideCalls || 0) + 1; },
    setIgnoreMouseEvents(...args) { this.ignoreMouseCalls = this.ignoreMouseCalls || []; this.ignoreMouseCalls.push(args); },
    setMenuBarVisibility() {},
    getContentBounds() { return { ...this.bounds }; },
    close() {
      this.closeCalls += 1;
      let prevented = false;
      const event = { preventDefault() { prevented = true; } };
      this.listeners.get("close")?.(event);
      if (prevented) return;
      this.destroyed = true;
      this.listeners.get("closed")?.();
    },
    bounds: { x: 0, y: 0, width: 800, height: 600 },
    boundsCalls: [],
    opacities: [],
    getBounds() { return { ...this.bounds }; },
    getNormalBounds() { return { ...this.bounds }; },
    isMaximized() { return false; },
    isFullScreen() { return false; },
    maximize() {},
    unmaximize() {},
    setFullScreen() {},
    setBounds(value) {
      this.bounds = { ...this.bounds, ...value };
      this.boundsCalls.push({ ...this.bounds });
    },
    isVisible() { return this.shown && !this.destroyed; },
    setOpacity(value) { this.opacities.push(value); },
    loadURL(url) {
      this.loadedUrl = url;
      return Promise.resolve().then(() => {
        this.webContents.emit("did-finish-load");
      });
    },
  };
  fakeWindows.push(win);
  return win;
}

function controlledRecord(id, currentUrl = "", loading = false) {
  const calls = [];
  const controls = [];
  const visibility = [];
  const boundsCalls = [];
  let bounds = { x: 0, y: 0, width: 0, height: 0 };
  let closeCalls = 0;
  let closeFailure = false;
  let targetCalls = 0;
  let debuggerAttached = false;
  let windowOpenHandler = null;
  let delayPrint = false;
  let pendingPrintCallback = null;
  let printResult = true;
  let printFailureReason = "";
  let printPdfResult = Buffer.from("%PDF-openprogram-test");
  let delayPrintPdf = false;
  let pendingPrintPdfResolve = null;
  let delayCapture = false;
  let pendingCaptureResolve = null;
  let webContentsDestroyed = false;
  let canGoBack = false;
  let canGoForward = false;
  const webContentsListeners = new Map();
  const debuggerCommands = [];
  const capturePageArgs = [];
  const focusCalls = [];
  const executeJavaScriptCalls = [];
  const delayedDebugger = new Map();
  let delayPreview = false;
  let pendingPreviewResolve = null;
  const nativeCalls = {
    reload: 0,
    stop: 0,
    back: 0,
    forward: 0,
    find: [],
    stopFind: [],
    zoom: [],
    print: [],
    printToPDF: [],
    capturePage: 0,
  };
  const editCalls = {
    undo: 0,
    redo: 0,
    cut: 0,
    copy: 0,
    paste: 0,
    selectAll: 0,
  };
  let zoomFactor = 1;
  const webContents = {
    getURL: () => currentUrl,
    getTitle: () => id,
    isLoading: () => loading,
    loadURL(url) {
      calls.push(url);
      loading = true;
      return new Promise((resolve, reject) => {
        controls.push({
          resolve() {
            currentUrl = url;
            loading = false;
            resolve();
          },
          reject,
        });
      });
    },
    debugger: {
      isAttached() { return debuggerAttached; },
      attach() { debuggerAttached = true; },
      sendCommand(method, params) {
        debuggerCommands.push({ method, params });
        let result;
        if (method === "Target.getTargetInfo") {
          targetCalls += 1;
          result = { targetInfo: { targetId: `${id}-target` } };
        } else if (typeof method === "string" && method.startsWith("Overlay.")) {
          result = {};
        } else {
          return Promise.reject(new Error(`unexpected debugger method ${method}`));
        }
        if (delayedDebugger.has(method)) {
          return new Promise((resolve) => {
            delayedDebugger.get(method).push(() => resolve(result));
          });
        }
        return Promise.resolve(result);
      },
      detach() { debuggerAttached = false; },
    },
    navigationHistory: {
      canGoBack: () => canGoBack,
      canGoForward: () => canGoForward,
      goBack() { nativeCalls.back += 1; },
      goForward() { nativeCalls.forward += 1; },
    },
    reload() { nativeCalls.reload += 1; },
    stop() { nativeCalls.stop += 1; },
    undo() { editCalls.undo += 1; },
    redo() { editCalls.redo += 1; },
    cut() { editCalls.cut += 1; },
    copy() { editCalls.copy += 1; },
    paste() { editCalls.paste += 1; },
    selectAll() { editCalls.selectAll += 1; },
    findInPage(query, options) {
      nativeCalls.find.push([query, options]);
      return nativeCalls.find.length;
    },
    stopFindInPage(action) { nativeCalls.stopFind.push(action); },
    getZoomFactor() { return zoomFactor; },
    setZoomFactor(value) {
      zoomFactor = value;
      nativeCalls.zoom.push(value);
    },
    print(options, callback) {
      nativeCalls.print.push(options);
      if (delayPrint) pendingPrintCallback = callback;
      else callback(printResult, printFailureReason);
    },
    printToPDF(options) {
      nativeCalls.printToPDF.push(options);
      if (delayPrintPdf) {
        return new Promise((resolve) => { pendingPrintPdfResolve = resolve; });
      }
      return Promise.resolve(printPdfResult);
    },
    capturePage(...args) {
      nativeCalls.capturePage += 1;
      capturePageArgs.push(args);
      const image = {
        isEmpty: () => false,
        toDataURL: () => "data:image/png;base64,TEST",
      };
      if (delayCapture) {
        return new Promise((resolve) => { pendingCaptureResolve = resolve; });
      }
      return Promise.resolve(image);
    },
    focus() { focusCalls.push("focus"); },
    executeJavaScript(script, userGesture) {
      executeJavaScriptCalls.push([script, userGesture]);
      const result = {
        visible_text_excerpt: "excerpt",
        text_truncated: false,
        aria_landmarks: [],
        landmarks_truncated: false,
        interactive_count: 0,
      };
      if (delayPreview) {
        return new Promise((resolve) => { pendingPreviewResolve = () => resolve(result); });
      }
      return Promise.resolve(result);
    },
    isDestroyed() { return webContentsDestroyed; },
    close() {
      closeCalls += 1;
      if (closeFailure) throw new Error("injected close failure");
    },
    setWindowOpenHandler(handler) {
      windowOpenHandler = handler;
      this.windowOpen = handler;
    },
    on(event, handler) {
      webContentsListeners.set(event, [
        ...(webContentsListeners.get(event) ?? []),
        handler,
      ]);
    },
    once(event, handler) {
      const wrapper = (...args) => {
        this.removeListener(event, wrapper);
        handler(...args);
      };
      wrapper.original = handler;
      this.on(event, wrapper);
    },
    removeListener(event, handler) {
      webContentsListeners.set(
        event,
        (webContentsListeners.get(event) ?? []).filter(
          (item) => item !== handler && item.original !== handler,
        ),
      );
    },
    emit(event, ...args) {
      for (const handler of webContentsListeners.get(event) ?? []) handler(...args);
    },
  };
  const view = {
    webContents,
    setVisible(value) { visibility.push(value); },
    setBackgroundColor() {},
    setBounds(value) {
      bounds = { ...value };
      boundsCalls.push({ ...value });
    },
    getBounds() { return { ...bounds }; },
  };
  return {
    record: { id, view, ownerId: null, navigation: null },
    calls,
    controls,
    visibility,
    boundsCalls,
    closeCallCount: () => closeCalls,
    setCloseFailure(value) { closeFailure = value; },
    targetCallCount: () => targetCalls,
    debuggerCommands,
    isDebuggerAttached: () => debuggerAttached,
    capturePageArgs,
    focusCalls,
    executeJavaScriptCalls,
    delayDebuggerMethod(method) {
      if (!delayedDebugger.has(method)) delayedDebugger.set(method, []);
    },
    completeDebuggerMethod(method) {
      const pending = delayedDebugger.get(method) || [];
      delayedDebugger.delete(method);
      for (const complete of pending) complete();
    },
    delayExecuteJavaScript() { delayPreview = true; },
    completeExecuteJavaScript() {
      delayPreview = false;
      const resolve = pendingPreviewResolve;
      pendingPreviewResolve = null;
      resolve?.();
    },
    windowOpen: (details) => windowOpenHandler?.(details),
    nativeCalls,
    editCalls,
    setNavigationAvailability({ back = false, forward = false } = {}) {
      canGoBack = back;
      canGoForward = forward;
    },
    emitWebContents(event, ...args) {
      if (event === "destroyed") webContentsDestroyed = true;
      for (const handler of webContentsListeners.get(event) ?? []) handler(...args);
    },
    delayPrintCallback() { delayPrint = true; },
    setPrintResult(value, failureReason = "") {
      printResult = value;
      printFailureReason = failureReason;
    },
    setPrintPdfResult(value) { printPdfResult = value; },
    delayPrintPdf() { delayPrintPdf = true; },
    delayCapturePage() { delayCapture = true; },
    completeCapturePage() {
      delayCapture = false;
      const resolve = pendingCaptureResolve;
      pendingCaptureResolve = null;
      resolve?.({
        isEmpty: () => false,
        toDataURL: () => "data:image/png;base64,TEST",
      });
    },
    completePrintPdf() {
      delayPrintPdf = false;
      const resolve = pendingPrintPdfResolve;
      pendingPrintPdfResolve = null;
      resolve?.(printPdfResult);
    },
    completePrint(success, failureReason = "") {
      const callback = pendingPrintCallback;
      pendingPrintCallback = null;
      callback?.(success, failureReason);
    },
    currentBounds: () => ({ ...bounds }),
  };
}

const webtabCheckContext = {
  get Buffer() { return Buffer; },
  set Buffer(value) { Buffer = value; },
  get EventEmitter() { return EventEmitter; },
  set EventEmitter(value) { EventEmitter = value; },
  get addRecord() { return addRecord; },
  set addRecord(value) { addRecord = value; },
  get assert() { return assert; },
  set assert(value) { assert = value; },
  get attachControlledRecord() { return attachControlledRecord; },
  set attachControlledRecord(value) { attachControlledRecord = value; },
  get browserWindowOptions() { return browserWindowOptions; },
  set browserWindowOptions(value) { browserWindowOptions = value; },
  get clearedStorageRequests() { return clearedStorageRequests; },
  set clearedStorageRequests(value) { clearedStorageRequests = value; },
  get clipboardWrites() { return clipboardWrites; },
  set clipboardWrites(value) { clipboardWrites = value; },
  get clock() { return clock; },
  set clock(value) { clock = value; },
  get controlledRecord() { return controlledRecord; },
  set controlledRecord(value) { controlledRecord = value; },
  get eventFor() { return eventFor; },
  set eventFor(value) { eventFor = value; },
  get fakeBrowserImportRunner() { return fakeBrowserImportRunner; },
  set fakeBrowserImportRunner(value) { fakeBrowserImportRunner = value; },
  get fakeElectron() { return fakeElectron; },
  set fakeElectron(value) { fakeElectron = value; },
  get fakeWindow() { return fakeWindow; },
  set fakeWindow(value) { fakeWindow = value; },
  get fakeWindows() { return fakeWindows; },
  set fakeWindows(value) { fakeWindows = value; },
  get finalizeBoth() { return finalizeBoth; },
  set finalizeBoth(value) { finalizeBoth = value; },
  get flushAsync() { return flushAsync; },
  set flushAsync(value) { flushAsync = value; },
  get flushRendererQueue() { return flushRendererQueue; },
  set flushRendererQueue(value) { flushRendererQueue = value; },
  get focusedWindow() { return focusedWindow; },
  set focusedWindow(value) { focusedWindow = value; },
  get fs() { return fs; },
  set fs(value) { fs = value; },
  get generatedNativeRecords() { return generatedNativeRecords; },
  set generatedNativeRecords(value) { generatedNativeRecords = value; },
  get generatedNativeViews() { return generatedNativeViews; },
  set generatedNativeViews(value) { generatedNativeViews = value; },
  get hooks() { return hooks; },
  set hooks(value) { hooks = value; },
  get humanInputMessages() { return humanInputMessages; },
  set humanInputMessages(value) { humanInputMessages = value; },
  get installAmbiguousCommitFailure() { return installAmbiguousCommitFailure; },
  set installAmbiguousCommitFailure(value) { installAmbiguousCommitFailure = value; },
  get installCommittedDecisionWriteFailure() { return installCommittedDecisionWriteFailure; },
  set installCommittedDecisionWriteFailure(value) { installCommittedDecisionWriteFailure = value; },
  get installOneShotRenameFailure() { return installOneShotRenameFailure; },
  set installOneShotRenameFailure(value) { installOneShotRenameFailure = value; },
  get installReadFailureAt() { return installReadFailureAt; },
  set installReadFailureAt(value) { installReadFailureAt = value; },
  get installReadFailureWhenDecisionMissing() { return installReadFailureWhenDecisionMissing; },
  set installReadFailureWhenDecisionMissing(value) { installReadFailureWhenDecisionMissing = value; },
  get ipcHandlers() { return ipcHandlers; },
  set ipcHandlers(value) { ipcHandlers = value; },
  get ipcListeners() { return ipcListeners; },
  set ipcListeners(value) { ipcListeners = value; },
  get loadTransferDecision() { return loadTransferDecision; },
  set loadTransferDecision(value) { loadTransferDecision = value; },
  get menuPopupOptions() { return menuPopupOptions; },
  set menuPopupOptions(value) { menuPopupOptions = value; },
  get menuTemplate() { return menuTemplate; },
  set menuTemplate(value) { menuTemplate = value; },
  get nextGeneratedWindowId() { return nextGeneratedWindowId; },
  set nextGeneratedWindowId(value) { nextGeneratedWindowId = value; },
  get nextPrintSaveDialogResult() { return nextPrintSaveDialogResult; },
  set nextPrintSaveDialogResult(value) { nextPrintSaveDialogResult = value; },
  get openedDownloadPaths() { return openedDownloadPaths; },
  set openedDownloadPaths(value) { openedDownloadPaths = value; },
  get os() { return os; },
  set os(value) { os = value; },
  get path() { return path; },
  set path(value) { path = value; },
  get plain() { return plain; },
  set plain(value) { plain = value; },
  get preloadSource() { return preloadSource; },
  set preloadSource(value) { preloadSource = value; },
  get prepareThroughIpc() { return prepareThroughIpc; },
  set prepareThroughIpc(value) { prepareThroughIpc = value; },
  get process() { return process; },
  set process(value) { process = value; },
  get processSignals() { return processSignals; },
  set processSignals(value) { processSignals = value; },
  get productionValidateTransferPayload() { return productionValidateTransferPayload; },
  set productionValidateTransferPayload(value) { productionValidateTransferPayload = value; },
  get registerContext() { return registerContext; },
  set registerContext(value) { registerContext = value; },
  get require() { return require; },
  set require(value) { require = value; },
  get setImmediate() { return setImmediate; },
  set setImmediate(value) { setImmediate = value; },
  get shownDownloadPaths() { return shownDownloadPaths; },
  set shownDownloadPaths(value) { shownDownloadPaths = value; },
  get shownPrintSaveDialogs() { return shownPrintSaveDialogs; },
  set shownPrintSaveDialogs(value) { shownPrintSaveDialogs = value; },
  get spawnedPtys() { return spawnedPtys; },
  set spawnedPtys(value) { spawnedPtys = value; },
  get stageTransferForRollback() { return stageTransferForRollback; },
  set stageTransferForRollback(value) { stageTransferForRollback = value; },
  get transferDecisionFile() { return transferDecisionFile; },
  set transferDecisionFile(value) { transferDecisionFile = value; },
  get transferUserData() { return transferUserData; },
  set transferUserData(value) { transferUserData = value; },
  get vm() { return vm; },
  set vm(value) { vm = value; },
  get webTransferPayload() { return webTransferPayload; },
  set webTransferPayload(value) { webTransferPayload = value; },
  get windowsTreeKills() { return windowsTreeKills; },
  set windowsTreeKills(value) { windowsTreeKills = value; },
};
const webtabChecks = Object.assign({},
  require("./webtab-checks/popups")(webtabCheckContext),
  require("./webtab-checks/preload")(webtabCheckContext),
  require("./webtab-checks/navigation")(webtabCheckContext),
  require("./webtab-checks/downloads")(webtabCheckContext),
  require("./webtab-checks/terminal")(webtabCheckContext),
  require("./webtab-checks/transfer-fixtures")(webtabCheckContext),
  require("./webtab-checks/transfer-validation")(webtabCheckContext),
  require("./webtab-checks/transfer-commit")(webtabCheckContext),
  require("./webtab-checks/transfer-rollback")(webtabCheckContext),
  require("./webtab-checks/transfer-ownership")(webtabCheckContext),
  require("./webtab-checks/transfer-failures")(webtabCheckContext),
  require("./webtab-checks/transfer-recovery")(webtabCheckContext),
  require("./webtab-checks/browser-data")(webtabCheckContext),
  require("./webtab-checks/cue-freshness")(webtabCheckContext),
  require("./webtab-checks/human-input")(webtabCheckContext),
  require("./webtab-checks/control-overlay")(webtabCheckContext),
);

function checkPopupCreatesIndependentRendererTab(...args) { return webtabChecks.checkPopupCreatesIndependentRendererTab(...args); }

function registerContext(id, win) {
  const ctx = hooks.makeWindowContext(id, win);
  hooks.windows.set(id, ctx);
  hooks.contextsByBrowserWindowId.set(win.id, ctx);
  win.on("close", (event) => hooks.tabTransfers?.windowClosing(ctx, event));
  return ctx;
}

function addRecord(ctx, controlled) {
  controlled.record.ownerId = ctx.id;
  ctx.views.set(controlled.record.id, controlled.record);
  return controlled.record;
}

function checkPreloadWindowIdentity(...args) { return webtabChecks.checkPreloadWindowIdentity(...args); }

function checkPreloadPopupSubscription(...args) { return webtabChecks.checkPreloadPopupSubscription(...args); }

function checkPreloadHumanInputSubscription(...args) { return webtabChecks.checkPreloadHumanInputSubscription(...args); }

function checkPreloadLocalFilePath(...args) { return webtabChecks.checkPreloadLocalFilePath(...args); }

function checkPreloadTabTransfer(...args) { return webtabChecks.checkPreloadTabTransfer(...args); }

function checkLoadView(...args) { return webtabChecks.checkLoadView(...args); }

function checkVisibleCollectionAndActivation(...args) { return webtabChecks.checkVisibleCollectionAndActivation(...args); }

function checkConfirmedDestroyHandler(...args) { return webtabChecks.checkConfirmedDestroyHandler(...args); }

function checkSenderOwnership(...args) { return webtabChecks.checkSenderOwnership(...args); }

function checkDownloadsLifecycle(...args) { return webtabChecks.checkDownloadsLifecycle(...args); }

function checkContextMenuEndAlignment(...args) { return webtabChecks.checkContextMenuEndAlignment(...args); }

function checkTerminalProcessIdentity(...args) { return webtabChecks.checkTerminalProcessIdentity(...args); }

function checkFocusedRoutingAndCleanup(...args) { return webtabChecks.checkFocusedRoutingAndCleanup(...args); }

function eventFor(...args) { return webtabChecks.eventFor(...args); }

function plain(...args) { return webtabChecks.plain(...args); }

function webTransferPayload(...args) { return webtabChecks.webTransferPayload(...args); }

function attachControlledRecord(...args) { return webtabChecks.attachControlledRecord(...args); }

function prepareThroughIpc(...args) { return webtabChecks.prepareThroughIpc(...args); }

function transferDecisionFile(...args) { return webtabChecks.transferDecisionFile(...args); }

function loadTransferDecision(...args) { return webtabChecks.loadTransferDecision(...args); }

function installOneShotRenameFailure(...args) { return webtabChecks.installOneShotRenameFailure(...args); }

function installCommittedDecisionWriteFailure(...args) { return webtabChecks.installCommittedDecisionWriteFailure(...args); }

function installReadFailureAt(...args) { return webtabChecks.installReadFailureAt(...args); }

function installReadFailureWhenDecisionMissing(...args) { return webtabChecks.installReadFailureWhenDecisionMissing(...args); }

function installAmbiguousCommitFailure(...args) { return webtabChecks.installAmbiguousCommitFailure(...args); }

function assertTransferApiRegistered(...args) { return webtabChecks.assertTransferApiRegistered(...args); }

function checkTransferPreparationValidationAndAuthorization(...args) { return webtabChecks.checkTransferPreparationValidationAndAuthorization(...args); }

function checkSuccessfulTransferAndDurableCommit(...args) { return webtabChecks.checkSuccessfulTransferAndDurableCommit(...args); }

function stageTransferForRollback(...args) { return webtabChecks.stageTransferForRollback(...args); }

function checkLockedRecordsRejectOrdinaryIpc(...args) { return webtabChecks.checkLockedRecordsRejectOrdinaryIpc(...args); }

function finalizeBoth(...args) { return webtabChecks.finalizeBoth(...args); }

function checkRollbackOrderingAndLateSourceRace(...args) { return webtabChecks.checkRollbackOrderingAndLateSourceRace(...args); }

function checkDestinationUndoDeadlineDelegatesJournal(...args) { return webtabChecks.checkDestinationUndoDeadlineDelegatesJournal(...args); }

function checkAtomicReparentAndMetadataOnlyTransfer(...args) { return webtabChecks.checkAtomicReparentAndMetadataOnlyTransfer(...args); }

function checkRejectCancelExpiryDetachAndClaim(...args) { return webtabChecks.checkRejectCancelExpiryDetachAndClaim(...args); }

/** Drop-to-place tear-off: at RELEASE the renderer calls detach(), which
 *  creates the window hidden at the drop point (clamped so it never spawns
 *  offscreen), shares one boot across concurrent calls, and is revealed at
 *  commit (covered by the commit-reveal path above). A cancelled token closes
 *  its window so nothing is orphaned. No mid-drag window, no live-follow. */
function checkOnDemandTearOffBootAndFollow(...args) { return webtabChecks.checkOnDemandTearOffBootAndFollow(...args); }

function checkInspectedDestinationCloseClearsPreparedToken(...args) { return webtabChecks.checkInspectedDestinationCloseClearsPreparedToken(...args); }

function checkPrecommitFailurePathsAndDynamicRoles(...args) { return webtabChecks.checkPrecommitFailurePathsAndDynamicRoles(...args); }

function checkSourceEmptyDurabilityAndRestartAcknowledgements(...args) { return webtabChecks.checkSourceEmptyDurabilityAndRestartAcknowledgements(...args); }

function checkOrphanFinalizationPendingTerminalAndWindowClose(...args) { return webtabChecks.checkOrphanFinalizationPendingTerminalAndWindowClose(...args); }

assert.doesNotMatch(source, /\blet mainWindow\b/);
assert.doesNotMatch(source, /\bconst views = new Map\(\)/);
assert.doesNotMatch(source, /\bvisibleViewId\b/);
assert.match(source, /ipcMain\.on\("webtab:sync-visible"/);
assert.match(source, /ipcMain\.on\("webtab:set-pip-zoom"/);
assert.match(source, /ipcMain\.handle\("webtab:show-action"/);
assert.doesNotMatch(source, /webtab:human-input/);
assert.match(source, /const HIDDEN_WEBTAB_BOUNDS = \{ x: 0, y: 0, width: 1920, height: 1080 \}/);
assert.match(source, /const PIP_VIRTUAL_WIDTH = 1920/);
assert.match(source, /if \(!record\.pipLayoutZoom\) wc\.setZoomFactor\(factor\)/);
assert.match(source, /ipcMain\.on\("tab-transfer:prepare"/);
// Cross-window drop cue channels + clean teardown (no stuck highlight).
assert.match(source, /tab-transfer:hover-enter/, "main must push the hover-enter cue");
assert.match(source, /tab-transfer:hover-leave/, "main must push the hover-leave cue");
assert.match(source, /function setTransferHoverTarget/,
  "main must funnel the hover cue through one tracker so at most one window lights up");
for (const handler of ["cancel", "detach", "deliver"]) {
  const start = source.indexOf(`ipcMain.handle("tab-transfer:${handler}"`);
  const body = source.slice(start, start + 400);
  assert.match(body, /setTransferHoverTarget\(null\)/,
    `drag end (${handler}) must clear the hover highlight`);
}
function checkBrowserDataClear(...args) { return webtabChecks.checkBrowserDataClear(...args); }

function checkBrowserImportCancellation(...args) { return webtabChecks.checkBrowserImportCancellation(...args); }

assert.match(preloadSource, /onTransferHover/, "preload must expose onTransferHover");
assert.match(preloadSource, /"tab-transfer:hover-enter"[\s\S]*?"tab-transfer:hover-leave"/,
  "preload onTransferHover must subscribe both hover channels");
assert.match(source, /event\.returnValue\s*=/);
assert.match(source, /TRANSFER_TIMEOUT_MS\s*=\s*15_000/);
assert.match(source, /status:\s*"destination-staged"/);
assert.match(source, /transaction\.status\s*=\s*"awaiting-source"/);
assert.match(source, /putTransferDecision/);
const menuStart = source.indexOf("function buildMenu");
const menuEnd = source.indexOf("// --------------------------------------------------------------------- boot");
assert.doesNotMatch(source.slice(menuStart, menuEnd), /mainWindow/);

// Cross-window drop cue: window-at-cursor must push hover-enter to the window
// under the cursor and hover-leave to the previously highlighted one, never to
// the drag source, and clear all on drag end (deliver / detach / cancel).
function checkCrossWindowHoverCue(...args) { return webtabChecks.checkCrossWindowHoverCue(...args); }

// Overlapping windows under the cursor: the topmost (focused) window wins, not
// map order — otherwise the hover targets a window hidden behind another.
function checkCrossWindowHoverZOrder(...args) { return webtabChecks.checkCrossWindowHoverZOrder(...args); }

function humanInputMessages(...args) { return webtabChecks.humanInputMessages(...args); }

function flushAsync(...args) { return webtabChecks.flushAsync(...args); }

function checkBackgroundPreview(...args) { return webtabChecks.checkBackgroundPreview(...args); }

function checkActionCueFreshness(...args) { return webtabChecks.checkActionCueFreshness(...args); }

function checkActionCueWorkerIncarnation(...args) { return webtabChecks.checkActionCueWorkerIncarnation(...args); }

function checkOverlappingActionCues(...args) { return webtabChecks.checkOverlappingActionCues(...args); }

function checkHumanInputYieldingAndActionCue(...args) { return webtabChecks.checkHumanInputYieldingAndActionCue(...args); }

function checkNativeControlOverlayAndCueReplay(...args) { return webtabChecks.checkNativeControlOverlayAndCueReplay(...args); }

Promise.all([
  checkLoadView(),
  checkVisibleCollectionAndActivation(),
])
  .then(async () => {
    checkPreloadWindowIdentity();
    checkPreloadPopupSubscription();
    checkPreloadHumanInputSubscription();
    checkPreloadLocalFilePath();
    checkPreloadTabTransfer();
    checkContextMenuEndAlignment();
    await checkSenderOwnership();
    await checkConfirmedDestroyHandler();
    await checkHumanInputYieldingAndActionCue();
    await checkBackgroundPreview();
    await checkActionCueFreshness();
    await checkActionCueWorkerIncarnation();
    await checkOverlappingActionCues();
    await checkNativeControlOverlayAndCueReplay();
    await checkDownloadsLifecycle();
    await checkTerminalProcessIdentity();
    await checkFocusedRoutingAndCleanup();
    await checkPopupCreatesIndependentRendererTab();
    assertTransferApiRegistered();
    await checkTransferPreparationValidationAndAuthorization();
    await checkSuccessfulTransferAndDurableCommit();
    await checkLockedRecordsRejectOrdinaryIpc();
    await checkRollbackOrderingAndLateSourceRace();
    await checkDestinationUndoDeadlineDelegatesJournal();
    await checkAtomicReparentAndMetadataOnlyTransfer();
    await checkRejectCancelExpiryDetachAndClaim();
    await checkOnDemandTearOffBootAndFollow();
    await checkInspectedDestinationCloseClearsPreparedToken();
    await checkPrecommitFailurePathsAndDynamicRoles();
    await checkSourceEmptyDurabilityAndRestartAcknowledgements();
    await checkOrphanFinalizationPendingTerminalAndWindowClose();
    await checkCrossWindowHoverCue();
    await checkCrossWindowHoverZOrder();
    await checkBrowserDataClear();
    await checkBrowserImportCancellation();
    assert.equal(rendererQueue.length, 0, "all fake renderer deliveries must be flushed");
    console.log("webtab navigation checks passed");
  })
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  })
  .finally(() => {
    fs.rmSync(transferUserData, { recursive: true, force: true });
  });
