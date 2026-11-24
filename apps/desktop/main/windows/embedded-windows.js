// Another application's window inside a tab of an App window.
//
// The worker does the platform work (computer_use/desktop/embedding):
// on Windows and Linux it makes the window a child of the App window; on
// macOS, where a window cannot leave its process, the tab shows a live
// capture of the window and the worker posts the tab's input to it. This
// module gives the page what only the App has:
//
// * `embedded-window:host` — the App window's native handle (the parent on
//   Windows and Linux), the main process id (left out of the window list),
//   the page's zoom and the display's scale, and the window's content bounds,
//   so the page can turn the tab's rectangle into screen points or device
//   pixels of the client area.
// * `embedded-window:host-changed` — the same description, sent to a page
//   that asked for its host whenever its App window moves or is resized, so
//   a window kept behind a tab on macOS follows the App window without the
//   page looking again.
// * `embedded-window:source` — the capture source id of a window, which the
//   page opens with getUserMedia (`chromeMediaSource: "desktop"`), the way
//   Electron's desktopCapturer is used; it needs no click, so a tab restored
//   after a reload shows its window again by itself.
// * Closing: a window with embedded windows asks its page to give them back
//   before it closes (a child window would otherwise close with its parent),
//   and closes once the page says so or after `RELEASE_MS`.

const RELEASE_MS = 2000;

function nativeHandle(buffer) {
  if (!buffer || typeof buffer.length !== "number") return "0";
  if (buffer.length >= 8 && typeof buffer.readBigUInt64LE === "function") return buffer.readBigUInt64LE(0).toString();
  if (buffer.length >= 4) return String(buffer.readUInt32LE(0));
  return "0";
}

// "window:<id>:<n>" is how Chromium names a window capture source.
function sourceFor(sources, windowId) {
  const wanted = String(windowId);
  return (sources || []).find((source) => String(source.id || "").split(":")[0] === "window"
    && String(source.id).split(":")[1] === wanted) || null;
}

function createEmbeddedWindows({ ipcMain, desktopCapturer, screen, contextForSender, platform, pid, setTimeout, clearTimeout }) {
  const counts = new Map();    // webContents id -> embedded windows in that page
  const waiting = new Map();   // webContents id -> finish()

  const ownPage = (event) => {
    const ctx = contextForSender(event);
    return ctx && event.sender === ctx.win.webContents && event.senderFrame === event.sender.mainFrame ? ctx : null;
  };

  const followed = new WeakSet(); // App windows whose moves are sent to their page

  function describe(win, contents) {
    let scale = 1;
    try { scale = screen.getDisplayMatching(win.getBounds()).scaleFactor || 1; } catch (_error) { /* one display */ }
    return {
      platform,
      pid,
      handle: platform === "darwin" ? "0" : nativeHandle(win.getNativeWindowHandle()),
      zoom: contents.getZoomFactor ? contents.getZoomFactor() : 1,
      scale,
      content: win.getContentBounds(),
    };
  }

  function followHost(win) {
    if (followed.has(win) || typeof win.on !== "function") return;
    followed.add(win);
    const changed = () => {
      if (win.isDestroyed() || win.webContents.isDestroyed?.()) return;
      try { win.webContents.send("embedded-window:host-changed", describe(win, win.webContents)); } catch (_error) { /* closing */ }
    };
    win.on("move", changed);
    win.on("resize", changed);
  }

  ipcMain.handle("embedded-window:host", (event) => {
    const ctx = ownPage(event);
    if (!ctx) return null;
    followHost(ctx.win);
    return describe(ctx.win, event.sender);
  });

  ipcMain.handle("embedded-window:source", async (event, windowId) => {
    if (!ownPage(event) || !Number.isSafeInteger(windowId) || windowId <= 0) return null;
    try {
      const sources = await desktopCapturer.getSources({
        types: ["window"], thumbnailSize: { width: 0, height: 0 }, fetchWindowIcons: false,
      });
      const source = sourceFor(sources, windowId);
      return source ? source.id : null;
    } catch (_error) {
      return null;
    }
  });

  ipcMain.on("embedded-window:count", (event, count) => {
    if (!ownPage(event)) return;
    const value = Number.isSafeInteger(count) && count > 0 ? count : 0;
    if (value) counts.set(event.sender.id, value);
    else counts.delete(event.sender.id);
  });

  ipcMain.on("embedded-window:released", (event) => {
    const finish = waiting.get(event.sender.id);
    if (finish) finish();
  });

  // From the window's "close" event: true when it held the close.
  function windowClosing(ctx, event, closeAgain) {
    if (!ctx || !ctx.win || ctx.win.isDestroyed()) return false;
    const id = ctx.win.webContents.id;
    if (!counts.get(id) || ctx.embeddedReleased) return false;
    event.preventDefault();
    if (waiting.has(id)) return true;
    let timer = null;
    const finish = () => {
      if (!waiting.has(id)) return;
      waiting.delete(id);
      counts.delete(id);
      clearTimeout(timer);
      ctx.embeddedReleased = true;
      closeAgain();
    };
    waiting.set(id, finish);
    timer = setTimeout(finish, RELEASE_MS);
    try { ctx.win.webContents.send("embedded-window:release"); } catch (_error) { finish(); }
    return true;
  }

  return { windowClosing };
}

module.exports = { createEmbeddedWindows, nativeHandle, sourceFor };
