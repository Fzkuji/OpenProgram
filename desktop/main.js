// OpenProgram desktop shell. Plain JS, no bundler.
const { app, BrowserWindow, WebContentsView, Menu, ipcMain, shell } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const https = require("https");
const path = require("path");

// 测试期全部走开发版 18200；正式发布改回 18100
const WEB_PORT = process.env.OPENPROGRAM_WEB_PORT || "18200";
const START_URL =
  process.env.OPENPROGRAM_DESKTOP_URL || `http://127.0.0.1:${WEB_PORT}/chat`;
const WORKER_COMMAND = "openprogram worker start";

// agent 接管内置浏览器的数据面通道：后端 browser 工具（engine=auto/app）经
// CDP attach 这里的可见 web tab。Electron 默认只绑 127.0.0.1，不对外暴露；
// 9222 留给后端 sidecar Chrome，互不冲突。必须在 app ready 之前设置。
app.commandLine.appendSwitch("remote-debugging-port", "9223");

const ERROR_PAGE =
  "data:text/html;charset=utf-8," +
  encodeURIComponent(
    `<body style="background:#141416;color:#ddd;font-family:-apple-system,sans-serif;
        display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
      <div style="max-width:32rem;text-align:center">
        <h2>OpenProgram worker is not running</h2>
        <p>Could not reach <code>${START_URL}</code>.</p>
        <p>Start it manually, then relaunch:</p>
        <pre style="background:#222;padding:0.8em;border-radius:6px">${WORKER_COMMAND}</pre>
      </div>
    </body>`
  );

// ---------------------------------------------------------------- worker boot

function probe(url, timeoutMs) {
  return new Promise((resolve) => {
    const mod = url.startsWith("https:") ? https : http;
    const req = mod.get(url, { timeout: timeoutMs }, (res) => {
      res.resume();
      resolve(true);
    });
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", () => resolve(false));
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function spawnWorker() {
  // ponytail: PATH first, then the known miniconda location; no config for more.
  // 18200 = 开发实例：openprogram 二进制靠 env 切 dev profile（openprogram-dev
  // 只是 shell alias，spawn 不到），否则拉起的是 stable worker、端口对不上。
  const env =
    WEB_PORT === "18200"
      ? {
          ...process.env,
          OPENPROGRAM_PROFILE: "dev",
          OPENPROGRAM_BACKEND_PORT: "18209",
          OPENPROGRAM_WEB_PORT: "18200",
        }
      : process.env;
  const start = (bin, onFail) => {
    const child = spawn(bin, ["worker", "start"], {
      detached: true,
      stdio: "ignore",
      env,
    });
    child.on("error", onFail || (() => {})); // ENOENT arrives async
    child.unref();
  };
  start("openprogram", () => start("/opt/miniconda3/bin/openprogram"));
}

async function resolveStartUrl() {
  for (let i = 0; i < 3; i++) {
    if (await probe(START_URL, 1000)) return START_URL;
  }
  spawnWorker();
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    if (await probe(START_URL, 1000)) return START_URL;
    await sleep(1000);
  }
  return ERROR_PAGE;
}

// ------------------------------------------------------------- window state

const stateFile = () => path.join(app.getPath("userData"), "window-state.json");

function loadWindowState() {
  try {
    const s = JSON.parse(fs.readFileSync(stateFile(), "utf8"));
    if (Number.isFinite(s.width) && Number.isFinite(s.height)) {
      // x/y must land on a live display — after a monitor-layout change
      // a stale position would reopen the window fully off-screen.
      if (Number.isFinite(s.x) && Number.isFinite(s.y)) {
        const { screen } = require("electron");
        const onScreen = screen.getAllDisplays().some((d) => {
          const a = d.workArea;
          return (
            s.x < a.x + a.width - 40 && s.x + s.width > a.x + 40 &&
            s.y >= a.y - 20 && s.y < a.y + a.height - 40
          );
        });
        if (!onScreen) {
          delete s.x;
          delete s.y;
        }
      }
      return s;
    }
  } catch (_e) {
    /* first run */
  }
  return { width: 1440, height: 900 };
}

function saveWindowState(win) {
  try {
    fs.writeFileSync(stateFile(), JSON.stringify(win.getBounds()));
  } catch (_e) {
    /* non-fatal */
  }
}

// ----------------------------------------------------------------- web tabs

function makeWindowContext(id, win) {
  return {
    id,
    win,
    views: new Map(),
    visibleViewIds: new Set(),
    pendingTransferToken: null,
  };
}

const windows = new Map();
const contextsByBrowserWindowId = new Map();
let lastFocusedWindowId = null;

function contextForSender(event) {
  const win = event?.sender
    ? BrowserWindow.fromWebContents(event.sender)
    : null;
  const ctx = win ? contextsByBrowserWindowId.get(win.id) : null;
  return ctx && !ctx.win.isDestroyed() ? ctx : null;
}

function focusedContext() {
  const focused = BrowserWindow.getFocusedWindow();
  const direct = focused ? contextsByBrowserWindowId.get(focused.id) : null;
  if (direct && !direct.win.isDestroyed()) {
    lastFocusedWindowId = direct.id;
    return direct;
  }
  if (focused) return null;
  const recent = lastFocusedWindowId
    ? windows.get(lastFocusedWindowId)
    : null;
  if (recent && !recent.win.isDestroyed()) return recent;
  if (recent) lastFocusedWindowId = null;
  return null;
}

function ownerOf(record) {
  const ctx = record ? windows.get(record.ownerId) : null;
  return ctx
    && !ctx.win.isDestroyed()
    && ctx.views.get(record.id) === record
    ? ctx
    : null;
}

function recordFor(ctx, id) {
  const record = ctx?.views.get(id);
  return record && record.ownerId === ctx.id ? record : null;
}

function sendState(record, extra) {
  const ctx = ownerOf(record);
  if (!ctx) return;
  const wc = record.view.webContents;
  // 加载初期 URL 未 commit 时 getURL()/getTitle() 返回空串——发出去会把
  // 渲染端 store 里的 url 冲成空，导致面板被卸载（白屏竞态）。空则不发。
  const u = wc.getURL();
  const ti = wc.getTitle();
  ctx.win.webContents.send("webtab:state", {
    id: record.id,
    ...(u ? { url: u } : {}),
    ...(ti ? { title: ti } : {}),
    loading: wc.isLoading(),
    canGoBack: wc.navigationHistory.canGoBack(),
    canGoForward: wc.navigationHistory.canGoForward(),
    ...extra,
  });
}

function isWebUrl(u) {
  try {
    const p = new URL(u).protocol;
    return p === "http:" || p === "https:";
  } catch {
    return false;
  }
}

function loadView(record, url) {
  const pending = record.navigation;
  if (pending && pending.url === url) return pending.promise;
  const view = record.view;
  if (
    !pending
    && view.webContents.getURL() === url
    && !view.webContents.isLoading()
  ) {
    return Promise.resolve(record);
  }
  const promise = view.webContents
    .loadURL(url)
    .then(() => record)
    .finally(() => {
      if (record.navigation?.promise === promise) {
        record.navigation = null;
      }
    });
  record.navigation = { url, promise };
  return promise;
}

// create-if-missing; loads url only on CREATION. Re-activating a tab
// re-mounts the renderer pane, which calls ensure again — reloading
// here would throw away scroll/form/SPA state and defeat the whole
// persistent-view design. Explicit navigation goes through navigate.
function ensureView(ctx, id, url) {
  if (!ctx || typeof id !== "string" || !id) return null;
  let record = recordFor(ctx, id);
  if (!record && !ctx.views.has(id)) {
    const view = new WebContentsView({
      webPreferences: { partition: "persist:webtabs" },
    });
    record = { id, view, ownerId: ctx.id, navigation: null };
    ctx.views.set(id, record);
    ctx.win.contentView.addChildView(view);
    view.setVisible(false);
    const wc = view.webContents;
    // Popups navigate the same view instead of opening a window.
    // Same http/https gate as every other egress point.
    wc.setWindowOpenHandler(({ url: popupUrl }) => {
      if (isWebUrl(popupUrl)) {
        const owner = ownerOf(record);
        if (owner) void navigateView(owner, id, popupUrl).catch(() => {});
      }
      return { action: "deny" };
    });
    for (const ev of [
      "did-navigate",
      "did-navigate-in-page",
      "page-title-updated",
      "did-start-loading",
      "did-stop-loading",
    ]) {
      wc.on(ev, () => sendState(record));
    }
    if (url && isWebUrl(url)) void loadView(record, url).catch(() => {});
  }
  return record;
}

async function navigateView(ctx, id, url) {
  if (!url || !isWebUrl(url)) return null;
  const record = recordFor(ctx, id) || ensureView(ctx, id, "");
  return record ? loadView(record, url) : null;
}

function normalizedBounds(bounds) {
  return {
    x: Math.round(Number(bounds?.x)) || 0,
    y: Math.round(Number(bounds?.y)) || 0,
    width: Math.max(0, Math.round(Number(bounds?.width)) || 0),
    height: Math.max(0, Math.round(Number(bounds?.height)) || 0),
  };
}

function syncVisibleViews(ctx, items) {
  if (!ctx || ctx.win.isDestroyed() || !Array.isArray(items)) return false;
  const desired = new Map();
  for (const item of items) {
    if (!item || typeof item.id !== "string") return false;
    const record = recordFor(ctx, item.id);
    if (!record) return false;
    desired.set(item.id, {
      record,
      bounds: normalizedBounds(item.bounds),
    });
  }

  for (const record of ctx.views.values()) {
    if (record.ownerId === ctx.id && !desired.has(record.id)) {
      record.view.setVisible(false);
    }
  }
  for (const { record, bounds } of desired.values()) {
    record.view.setBounds(bounds);
    record.view.setVisible(true);
  }
  ctx.visibleViewIds = new Set(desired.keys());
  return true;
}

function currentVisibleItems(ctx, excludedId = null) {
  const items = [];
  for (const id of ctx.visibleViewIds) {
    if (id === excludedId) continue;
    const record = recordFor(ctx, id);
    if (!record) continue;
    items.push({ id, bounds: record.view.getBounds() });
  }
  return items;
}

function showView(ctx, id) {
  const record = recordFor(ctx, id);
  if (!record) return false;
  const desired = currentVisibleItems(ctx, id);
  desired.push({ id, bounds: record.view.getBounds() });
  return syncVisibleViews(ctx, desired);
}

function hideView(ctx, id) {
  if (!recordFor(ctx, id)) return false;
  return syncVisibleViews(ctx, currentVisibleItems(ctx, id));
}

async function activateView(ctx, id, url) {
  let record;
  if (url) {
    if (!isWebUrl(url)) return null;
    record = recordFor(ctx, id) || ensureView(ctx, id, "");
    if (!record || !showView(ctx, id)) return null;
    record = await navigateView(ctx, id, url);
    if (recordFor(ctx, id) !== record || !ctx.visibleViewIds.has(id)) return null;
  } else {
    record = recordFor(ctx, id);
    if (!record || !showView(ctx, id)) return null;
  }
  return record.view.webContents.getOrCreateDevToolsTargetId();
}

function withView(ctx, id, fn) {
  const record = recordFor(ctx, id);
  if (!record) return false;
  fn(record);
  return true;
}

// reload/navigationHistory calls replace any in-flight loadURL Promise
// without going through loadView. Remove that stale registry entry before
// invoking the native operation, so a following activation cannot reuse a
// Promise Electron is about to reject with ERR_ABORTED.
function runNativeNavigation(ctx, id, navigate) {
  const record = recordFor(ctx, id);
  if (!record) return false;
  record.navigation = null;
  navigate(record.view.webContents);
  return true;
}

function destroyView(ctx, id) {
  const record = recordFor(ctx, id);
  if (!record) return false;
  ctx.visibleViewIds.delete(id);
  record.navigation = null;
  ctx.views.delete(id);
  try {
    ctx.win.contentView.removeChildView(record.view);
  } catch (_e) {
    /* already detached */
  }
  try {
    record.view.webContents.close();
  } catch (_e) {
    /* already closed */
  }
  return true;
}

function clearOwnedViews(ctx) {
  for (const record of [...ctx.views.values()]) {
    if (record.ownerId === ctx.id) destroyView(ctx, record.id);
  }
  ctx.visibleViewIds = new Set();
}

function cleanupWindowContext(ctx) {
  clearOwnedViews(ctx);
  ctx.views.clear();
  ctx.visibleViewIds = new Set();
  if (windows.get(ctx.id) === ctx) windows.delete(ctx.id);
  if (contextsByBrowserWindowId.get(ctx.win.id) === ctx) {
    contextsByBrowserWindowId.delete(ctx.win.id);
  }
  if (lastFocusedWindowId === ctx.id) lastFocusedWindowId = null;
}

function registerWebTabIpc() {
  ipcMain.on("webtab:ensure", (event, id, url) => {
    const ctx = contextForSender(event);
    if (ctx) ensureView(ctx, id, url);
  });
  ipcMain.on("webtab:navigate", (event, id, url) => {
    const ctx = contextForSender(event);
    if (ctx) void navigateView(ctx, id, url).catch(() => {});
  });
  ipcMain.handle("webtab:activate", (event, id, url) => {
    const ctx = contextForSender(event);
    return ctx && typeof id === "string"
      ? activateView(ctx, id, typeof url === "string" ? url : "")
      : null;
  });
  ipcMain.on("webtab:sync-visible", (event, items) => {
    const ctx = contextForSender(event);
    if (ctx) syncVisibleViews(ctx, items);
  });
  ipcMain.on("webtab:set-bounds", (event, id, bounds) => {
    const ctx = contextForSender(event);
    if (ctx && bounds) {
      withView(ctx, id, (record) => {
        record.view.setBounds(normalizedBounds(bounds));
      });
    }
  });
  ipcMain.on("webtab:show", (event, id) => {
    const ctx = contextForSender(event);
    if (ctx) showView(ctx, id);
  });
  ipcMain.on("webtab:hide", (event, id) => {
    const ctx = contextForSender(event);
    if (ctx) hideView(ctx, id);
  });
  ipcMain.on("webtab:destroy", (event, id) => {
    const ctx = contextForSender(event);
    if (ctx) destroyView(ctx, id);
  });
  ipcMain.on("webtab:reload", (event, id) => {
    const ctx = contextForSender(event);
    if (ctx) runNativeNavigation(ctx, id, (wc) => wc.reload());
  });
  ipcMain.on("webtab:go-back", (event, id) => {
    const ctx = contextForSender(event);
    if (ctx) runNativeNavigation(ctx, id, (wc) => wc.navigationHistory.goBack());
  });
  ipcMain.on("webtab:go-forward", (event, id) => {
    const ctx = contextForSender(event);
    if (ctx) runNativeNavigation(ctx, id, (wc) => wc.navigationHistory.goForward());
  });
  ipcMain.on("desktop:open-external", (_e, url) => {
    try {
      const u = new URL(url);
      if (u.protocol === "http:" || u.protocol === "https:") shell.openExternal(url);
    } catch (_err) {
      /* invalid url, ignore */
    }
  });
}

// --------------------------------------------------------------------- menu

function buildMenu() {
  const isMac = process.platform === "darwin";
  const send = (channel) => () => {
    const ctx = focusedContext();
    if (ctx) ctx.win.webContents.send(channel);
  };
  const template = [
    ...(isMac ? [{ role: "appMenu" }] : []),
    {
      label: "File",
      submenu: [
        { label: "New Tab", accelerator: "CmdOrCtrl+T", click: send("menu:new-tab") },
        // Cmd+W goes to the renderer (close tab); window close is Cmd+Shift+W.
        { label: "Close Tab", accelerator: "CmdOrCtrl+W", click: send("menu:close-tab") },
        { type: "separator" },
        { role: "close", accelerator: "Shift+CmdOrCtrl+W" },
      ],
    },
    { role: "editMenu" },
    { role: "viewMenu" },
    { role: "windowMenu" },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// --------------------------------------------------------------------- boot

async function createWindow(options = {}) {
  const state = loadWindowState();
  const win = new BrowserWindow({
    x: state.x,
    y: state.y,
    width: state.width,
    height: state.height,
    backgroundColor: "#141416",
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : undefined,
    // macOS only: center the traffic lights vertically in the 40px tab row.
    trafficLightPosition: { x: 18, y: 13 },
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  const windowId = options.windowId || `window-${win.id}`;
  const ctx = makeWindowContext(windowId, win);
  windows.set(windowId, ctx);
  contextsByBrowserWindowId.set(win.id, ctx);
  win.on("focus", () => { lastFocusedWindowId = ctx.id; });
  win.on("close", () => saveWindowState(win));
  win.on("closed", () => cleanupWindowContext(ctx));
  // External links from the app itself (not web tabs) open in the system browser.
  win.webContents.setWindowOpenHandler(({ url }) => {
    try {
      const u = new URL(url);
      if (u.protocol === "http:" || u.protocol === "https:") shell.openExternal(url);
    } catch (_e) {
      /* ignore */
    }
    return { action: "deny" };
  });
  // The app renderer must never leave the local UI origin: the preload
  // bridge is exposed to whatever document runs there. Remote links
  // (docs footer, message content) go to the system browser instead.
  win.webContents.on("will-navigate", (e, url) => {
    try {
      const dest = new URL(url);
      if (dest.hostname === "127.0.0.1" || dest.hostname === "localhost") return;
      e.preventDefault();
      if (dest.protocol === "http:" || dest.protocol === "https:")
        shell.openExternal(url);
    } catch (_e) {
      e.preventDefault();
    }
  });
  // Renderer reload (Cmd+R) resets the renderer's view bookkeeping —
  // orphaned WebContentsViews would leak until quit. Start clean.
  win.webContents.on("did-navigate", () => clearOwnedViews(ctx));
  win.loadURL(await resolveStartUrl());
  return ctx;
}

app.whenReady().then(() => {
  registerWebTabIpc();
  buildMenu();
  void createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) void createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
