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

let mainWindow = null;
const views = new Map(); // id -> WebContentsView
const viewNavigations = new Map(); // id -> { url, promise }
let visibleViewId = null;

function sendState(id, extra) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  const view = views.get(id);
  if (!view) return;
  const wc = view.webContents;
  // 加载初期 URL 未 commit 时 getURL()/getTitle() 返回空串——发出去会把
  // 渲染端 store 里的 url 冲成空，导致面板被卸载（白屏竞态）。空则不发。
  const u = wc.getURL();
  const ti = wc.getTitle();
  mainWindow.webContents.send("webtab:state", {
    id,
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

function loadView(id, view, url) {
  const pending = viewNavigations.get(id);
  if (pending && pending.url === url) return pending.promise;
  if (
    !pending
    && view.webContents.getURL() === url
    && !view.webContents.isLoading()
  ) {
    return Promise.resolve(view);
  }
  const promise = view.webContents
    .loadURL(url)
    .then(() => view)
    .finally(() => {
      if (viewNavigations.get(id)?.promise === promise) {
        viewNavigations.delete(id);
      }
    });
  viewNavigations.set(id, { url, promise });
  return promise;
}

// create-if-missing; loads url only on CREATION. Re-activating a tab
// re-mounts the renderer pane, which calls ensure again — reloading
// here would throw away scroll/form/SPA state and defeat the whole
// persistent-view design. Explicit navigation goes through navigate.
function ensureView(id, url) {
  let view = views.get(id);
  if (!view) {
    view = new WebContentsView({
      webPreferences: { partition: "persist:webtabs" },
    });
    views.set(id, view);
    mainWindow.contentView.addChildView(view);
    view.setVisible(false);
    const wc = view.webContents;
    // Popups navigate the same view instead of opening a window.
    // Same http/https gate as every other egress point.
    wc.setWindowOpenHandler(({ url: popupUrl }) => {
      if (isWebUrl(popupUrl)) {
        void navigateView(id, popupUrl).catch(() => {});
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
      wc.on(ev, () => sendState(id));
    }
    if (url && isWebUrl(url)) void loadView(id, view, url).catch(() => {});
  }
  return view;
}

async function navigateView(id, url) {
  if (!url || !isWebUrl(url)) return null;
  const view = views.get(id) || ensureView(id, "");
  return loadView(id, view, url);
}

function showView(id) {
  if (!views.has(id)) return false;
  for (const [otherId, view] of views) view.setVisible(otherId === id);
  visibleViewId = id;
  return true;
}

function hideView(id) {
  const view = views.get(id);
  if (!view) return;
  view.setVisible(false);
  if (visibleViewId === id) visibleViewId = null;
}

async function activateView(id, url) {
  let view;
  if (url) {
    if (!isWebUrl(url)) return null;
    view = views.get(id) || ensureView(id, "");
    showView(id);
    view = await navigateView(id, url);
    if (!view) return null;
    if (visibleViewId !== id) return null;
  } else {
    view = views.get(id);
    if (!view || !showView(id)) return null;
  }
  return view.webContents.getOrCreateDevToolsTargetId();
}

function withView(id, fn) {
  const view = views.get(id);
  if (view) fn(view);
}

// reload/navigationHistory calls replace any in-flight loadURL Promise
// without going through loadView. Remove that stale registry entry before
// invoking the native operation, so a following activation cannot reuse a
// Promise Electron is about to reject with ERR_ABORTED.
function runNativeNavigation(id, navigate) {
  const view = views.get(id);
  if (!view) return;
  viewNavigations.delete(id);
  navigate(view.webContents);
}

function registerWebTabIpc() {
  ipcMain.on("webtab:ensure", (_e, id, url) => ensureView(id, url));
  ipcMain.on("webtab:navigate", (_e, id, url) => {
    void navigateView(id, url).catch(() => {});
  });
  ipcMain.handle("webtab:activate", (_e, id, url) =>
    typeof id === "string"
      ? activateView(id, typeof url === "string" ? url : "")
      : null
  );
  ipcMain.on("webtab:set-bounds", (_e, id, b) => {
    if (!b) return;
    const r = {
      x: Math.round(Number(b.x)) || 0,
      y: Math.round(Number(b.y)) || 0,
      width: Math.max(0, Math.round(Number(b.width)) || 0),
      height: Math.max(0, Math.round(Number(b.height)) || 0),
    };
    withView(id, (v) => v.setBounds(r));
  });
  ipcMain.on("webtab:show", (_e, id) => showView(id));
  ipcMain.on("webtab:hide", (_e, id) => hideView(id));
  ipcMain.on("webtab:destroy", (_e, id) =>
    withView(id, (v) => {
      if (visibleViewId === id) visibleViewId = null;
      mainWindow.contentView.removeChildView(v);
      v.webContents.close();
      viewNavigations.delete(id);
      views.delete(id);
    })
  );
  ipcMain.on("webtab:reload", (_e, id) =>
    runNativeNavigation(id, (wc) => wc.reload())
  );
  ipcMain.on("webtab:go-back", (_e, id) =>
    runNativeNavigation(id, (wc) => wc.navigationHistory.goBack())
  );
  ipcMain.on("webtab:go-forward", (_e, id) =>
    runNativeNavigation(id, (wc) => wc.navigationHistory.goForward())
  );
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
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send(channel);
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

async function createWindow() {
  const state = loadWindowState();
  mainWindow = new BrowserWindow({
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
  mainWindow.on("close", () => saveWindowState(mainWindow));
  mainWindow.on("closed", () => {
    views.clear();
    viewNavigations.clear();
    visibleViewId = null;
    mainWindow = null;
  });
  // External links from the app itself (not web tabs) open in the system browser.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
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
  mainWindow.webContents.on("will-navigate", (e, url) => {
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
  mainWindow.webContents.on("did-navigate", () => {
    for (const [id, view] of views) {
      try {
        mainWindow.contentView.removeChildView(view);
        view.webContents.close();
      } catch (_e) {
        /* already gone */
      }
      views.delete(id);
    }
    viewNavigations.clear();
    visibleViewId = null;
  });
  mainWindow.loadURL(await resolveStartUrl());
}

app.whenReady().then(() => {
  registerWebTabIpc();
  buildMenu();
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
