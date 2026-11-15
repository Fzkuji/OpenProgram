// The control overlay over a desktop app window an Agent operates.
//
// One transparent, click-through, unfocusable window per conversation,
// laid over the controlled window with a margin for its glow. It sits in
// the window stack directly above the controlled window (moveAbove with
// the window's id), not above every app: a window the user brings in front
// of the controlled one covers the overlay and its cursor too. Each report
// orders it again, since the stack changes whenever the user switches
// apps. Only when the window server will not order it there does it float
// above all windows, with the glow cut out where others cover the window. The worker
// reports the window's place while the Agent holds it (native.control:
// frame, title-bar buttons, the windows in front of it) and each step's
// screen point (native.action). The page (menu-overlay/agent-control)
// draws the glowing edge, the control badge over the title-bar buttons and
// the agent cursor, which stays in the window between steps and glides to
// the next one. Every App window forwards the same events, so steps are
// deduplicated by identity and window reports are simply idempotent.

// The worker leaves windows with this title out of the windows that cover
// the controlled one (window_watch.OVERLAY_TITLE).
const OVERLAY_TITLE = "OpenProgram agent overlay";
// Room around the window for the glow (window_watch.GLOW_MARGIN).
const MARGIN = 32;
const FADE_MS = 360;

function createAgentControl({ BrowserWindow, nativeTheme, overlayUiOrigin, path, setTimeout, clearTimeout }) {
  const overlays = new Map();
  const seen = [];

  const finite = (value) => typeof value === "number" && Number.isFinite(value);
  const rect = (value) => Array.isArray(value) && value.length === 4 && value.every(finite) ? value : null;

  function overlaySender(event) {
    for (const entry of overlays.values()) {
      if (entry.win && !entry.win.isDestroyed?.() && event?.sender === entry.win.webContents) return entry;
    }
    return null;
  }

  function create(sessionId) {
    const win = new BrowserWindow({
      title: OVERLAY_TITLE,
      frame: false,
      transparent: true,
      show: false,
      focusable: false,
      skipTaskbar: true,
      hasShadow: false,
      resizable: false,
      movable: false,
      enableLargerThanScreen: true,
      webPreferences: {
        preload: path.join(__dirname, "..", "preload.js"),
        contextIsolation: true,
        nodeIntegration: false,
        additionalArguments: [
          `--openprogram-window-id=agent-control-${sessionId}`,
          "--openprogram-surface=agent-control",
        ],
      },
    });
    win.setIgnoreMouseEvents(true);
    if (typeof win.setMenuBarVisibility === "function") win.setMenuBarVisibility(false);
    // The page sets its own title; keep the one the worker looks for.
    win.on?.("page-title-updated", (event) => event.preventDefault());
    const entry = { sessionId, win, ready: false, frame: null, bounds: null, place: null, cursor: null, closing: null,
      floating: false };
    win.on("closed", () => {
      if (overlays.get(sessionId) === entry) overlays.delete(sessionId);
    });
    win.loadURL(`${overlayUiOrigin()}/menu-overlay/agent-control`).catch(() => {});
    overlays.set(sessionId, entry);
    return entry;
  }

  function send(entry) {
    if (!entry.ready || !entry.win || entry.win.isDestroyed?.()) return;
    const reducedMotion = !!(nativeTheme && nativeTheme.prefersReducedMotion);
    try {
      entry.win.webContents.send("agent-control:update", { ...entry.place, cursor: entry.cursor, reducedMotion });
    } catch { /* overlay not ready */ }
  }

  function markReady(event) {
    const entry = overlaySender(event);
    if (!entry) return false;
    entry.ready = true;
    send(entry);
    return true;
  }

  function close(entry) {
    if (entry.closing) return;
    entry.place = { ...(entry.place || {}), ending: true };
    send(entry);
    entry.closing = setTimeout(() => {
      if (overlays.get(entry.sessionId) === entry) overlays.delete(entry.sessionId);
      try { entry.win.destroy(); } catch { /* gone */ }
    }, FADE_MS);
  }

  function reopen(entry) {
    if (!entry.closing) return entry;
    clearTimeout(entry.closing);
    entry.closing = null;
    if (entry.place) entry.place = { ...entry.place, ending: false };
    return entry;
  }

  /** Put the overlay right above the controlled window in the stack. */
  function orderAbove(entry, windowId) {
    let placed = false;
    if (Number.isInteger(windowId) && windowId > 0 && typeof entry.win.moveAbove === "function") {
      try {
        entry.win.moveAbove(`window:${windowId}:0`);
        placed = true;
      } catch { /* not a window the server can order against */ }
    }
    if (placed === !entry.floating) return;
    entry.floating = !placed;
    try {
      if (entry.floating) entry.win.setAlwaysOnTop(true, "floating");
      else entry.win.setAlwaysOnTop(false);
    } catch { /* gone */ }
  }

  /** A report of the controlled window's place, or of the end of control. */
  function showControl(data) {
    if (!data || typeof data !== "object" || typeof data.session_id !== "string" || !data.session_id) return false;
    let entry = overlays.get(data.session_id);
    if (data.active !== true) {
      if (entry) close(entry);
      return true;
    }
    const frame = rect(data.frame);
    if (!frame || frame[2] < 1 || frame[3] < 1) {
      // Minimized or on another Space: nothing to draw over until it is back.
      if (entry?.win && !entry.win.isDestroyed?.()) entry.win.hide();
      if (entry) entry.frame = null;
      return true;
    }
    entry = entry ? reopen(entry) : create(data.session_id);
    const [x, y, w, h] = frame;
    const bounds = { x: Math.round(x - MARGIN), y: Math.round(y - MARGIN), width: Math.round(w + 2 * MARGIN), height: Math.round(h + 2 * MARGIN) };
    const moved = !entry.bounds || ["x", "y", "width", "height"].some((key) => entry.bounds[key] !== bounds[key]);
    if (moved) {
      entry.win.setBounds(bounds);
      entry.bounds = bounds;
    }
    entry.frame = frame;
    const buttons = rect(data.buttons);
    const occluders = Array.isArray(data.occluders) ? data.occluders.map(rect).filter(Boolean).slice(0, 32) : [];
    entry.place = {
      margin: MARGIN,
      width: bounds.width,
      height: bounds.height,
      appName: typeof data.app_name === "string" ? data.app_name.slice(0, 120) : "",
      buttons: buttons ? [buttons[0] + MARGIN, buttons[1] + MARGIN, buttons[2], buttons[3]] : null,
      occluders: occluders.map(([ox, oy, ow, oh]) => [ox - bounds.x, oy - bounds.y, ow, oh]),
    };
    if (entry.cursor && entry.cursor.screen) {
      entry.cursor = { ...entry.cursor, x: entry.cursor.screen.x - x, y: entry.cursor.screen.y - y, screen: null };
    }
    send(entry);
    if (!entry.win.isVisible?.()) {
      if (typeof entry.win.showInactive === "function") entry.win.showInactive();
      else entry.win.show();
    }
    orderAbove(entry, data.window_id);
    return true;
  }

  /** One step's screen point: the cursor glides there and plays the action. */
  function showAction(cue) {
    if (!cue || typeof cue !== "object" || typeof cue.session_id !== "string" || !cue.session_id) return false;
    const { x, y, sequence, at } = cue;
    if (![x, y, sequence, at].every(finite)) return false;
    const key = `${cue.session_id}:${sequence}:${at}`;
    if (seen.includes(key)) return false;
    seen.push(key);
    if (seen.length > 64) seen.shift();
    let entry = overlays.get(cue.session_id);
    if (!entry) entry = create(cue.session_id);
    reopen(entry);
    const action = typeof cue.action === "string" ? cue.action.slice(0, 40) : "";
    // Kept relative to the window, so the cursor moves with it.
    entry.cursor = entry.frame
      ? { x: x - entry.frame[0], y: y - entry.frame[1], screen: null, action, seq: sequence }
      : { x: 0, y: 0, screen: { x, y }, action, seq: sequence };
    if (entry.frame) send(entry);
    return true;
  }

  return { showControl, showAction, markReady, overlaySender, OVERLAY_TITLE, MARGIN };
}

module.exports = { createAgentControl, OVERLAY_TITLE, MARGIN };
