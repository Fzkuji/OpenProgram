// Desktop action cues. State is owned by the main-process composition.
function createActionCues({
  ACTION_CUE_MS,
  ACTION_CUE_MARGIN = 24,
  ACTION_CUE_FADE_MS = 360,
  BrowserWindow,
  START_URL,
  WEB_PORT,
  clearTimeout,
  layoutControlOverlay,
  nativeTheme,
  ownerOf,
  path,
  recordFor,
  setTimeout,
}) {
  function viewZoomFactor(record) {
    if (record.pipLayoutZoom) return record.pipLayoutZoom;
    try {
      const zoom = Number(record.view.webContents.getZoomFactor?.());
      return Number.isFinite(zoom) && zoom > 0 ? zoom : 1;
    } catch {
      return 1;
    }
  }

  function cssViewportSize(record) {
    const bounds = record.view.getBounds();
    const zoom = viewZoomFactor(record);
    return {
      width: record.pipViewport?.width ?? bounds.width / zoom,
      height: record.pipViewport?.height ?? bounds.height / zoom,
      zoom,
      bounds,
    };
  }

  function finiteNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function pointInViewport(x, y, width, height) {
    return x >= 0 && y >= 0 && x < width && y < height;
  }

  function viewportMatchesMarker(marker, record) {
    const current = cssViewportSize(record);
    return Math.round(marker.width) === Math.round(current.width)
      && Math.round(marker.height) === Math.round(current.height);
  }

  const ACTION_CUE_RESOURCE_ID_MAX = 256;

  function actionCueResourceId(marker) {
    if (marker == null || !Object.prototype.hasOwnProperty.call(marker, "resourceId")) {
      return "";
    }
    const value = marker.resourceId;
    if (typeof value !== "string" || !value || value.length > ACTION_CUE_RESOURCE_ID_MAX) {
      return null;
    }
    return value;
  }

  function isStaleActionMarker(record, sequence, generation, hasGeneration, resourceId) {
    const lastSeq = record.lastActionSequence;
    const lastGen = record.lastActionGeneration;
    const lastId = record.lastActionResourceId || "";
    const priorId = record.priorActionResourceId || "";
    if (resourceId) {
      if (lastId && resourceId !== lastId) {
        return resourceId === priorId;
      }
    }
    if (hasGeneration) {
      if (lastGen != null) {
        if (generation < lastGen) return true;
        if (generation > lastGen) return false;
        return lastSeq != null && sequence <= lastSeq;
      }
      if (generation > 0) return false;
    }
    return lastSeq != null && sequence <= lastSeq;
  }

  function rememberActionCueIdentity(record, sequence, generation, hasGeneration, resourceId) {
    if (resourceId && resourceId !== (record.lastActionResourceId || "")) {
      if (record.lastActionResourceId) {
        record.priorActionResourceId = record.lastActionResourceId;
      }
      record.lastActionResourceId = resourceId;
      record.lastActionGeneration = hasGeneration ? generation : 0;
      record.lastActionSequence = sequence;
      return;
    }
    record.lastActionSequence = sequence;
    if (hasGeneration) record.lastActionGeneration = generation;
    if (resourceId) record.lastActionResourceId = resourceId;
  }

  function boundsDiffer(a, b) {
    return a.x !== b.x || a.y !== b.y || a.width !== b.width || a.height !== b.height;
  }

  function actionCueStillCurrent(record, ctx, id, token) {
    return record.actionCueToken === token && recordFor(ctx, id) === record;
  }

  function overlayUiOrigin() {
    let origin = "http://127.0.0.1:" + WEB_PORT;
    try { origin = new URL(START_URL).origin; } catch { /* fallback */ }
    return origin;
  }

  // The page's control overlay: the same page that draws over a desktop
  // window the Agent operates (menu-overlay/agent-control, fed like
  // main/worker/agent-control.js), in a transparent click-through window
  // laid over the page's native view with a margin for the cursor. It
  // glows along the inside of the page's edge while the Agent holds the
  // page (setControlGlow), and the agent cursor glides to each step.
  function stopCueMotion(record) {
    if (record.actionCueFadeTimer != null) {
      clearTimeout(record.actionCueFadeTimer);
      record.actionCueFadeTimer = null;
    }
    record.actionCueEnding = false;
  }

  function overlayRadius(record) {
    if (record.pipLayoutZoom) return record.pipCornerRadius || 0;
    const radius = Number(record.corners?.radius);
    const zoom = Number(record.corners?.zoom);
    return Number.isFinite(radius) && radius > 0 ? radius * (Number.isFinite(zoom) && zoom > 0 ? zoom : 1) : 0;
  }

  function cueWindowContentOrigin(ctx, record) {
    const content = ctx.win.getContentBounds ? ctx.win.getContentBounds() : ctx.win.getBounds();
    const page = record.view.getBounds();
    return {
      x: Math.round(content.x + page.x - ACTION_CUE_MARGIN),
      y: Math.round(content.y + page.y - ACTION_CUE_MARGIN),
      width: Math.round(page.width + 2 * ACTION_CUE_MARGIN),
      height: Math.round(page.height + 2 * ACTION_CUE_MARGIN),
    };
  }

  function sendPendingCue(record) {
    const win = record.actionCueWindow;
    const payload = record.pendingCuePayload;
    if (!payload || !win || win.isDestroyed?.() || !record.actionCueReady) return;
    try { win.webContents.send("agent-control:update", payload); } catch { /* overlay not ready */ }
  }

  /** Hide the overlay: the page is out of sight, or nothing is left to draw. */
  function hideActionCueWindow(record) {
    const win = record.actionCueWindow;
    if (!win || win.isDestroyed?.()) return;
    try { win.hide(); } catch { /* already gone */ }
  }

  function closeActionCueWindow(record) {
    stopCueMotion(record);
    hideActionCueWindow(record);
    const win = record.actionCueWindow;
    record.actionCueWindow = null;
    record.actionCueReady = false;
    record.actionCueLoaded = false;
    record.pendingCuePayload = null;
    record.actionCueBounds = null;
    if (!win || win.isDestroyed?.()) return;
    try { win.close(); } catch { /* already closed */ }
  }

  function ensureActionCueWindow(ctx, record) {
    if (record.actionCueWindow && !record.actionCueWindow.isDestroyed?.()) {
      return record.actionCueWindow;
    }
    const win = new BrowserWindow({
      parent: ctx.win,
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
          `--openprogram-window-id=${ctx.id}`,
          "--openprogram-surface=agent-control",
        ],
      },
    });
    win.setIgnoreMouseEvents(true, { forward: true });
    if (typeof win.setMenuBarVisibility === "function") win.setMenuBarVisibility(false);
    record.actionCueWindow = win;
    record.actionCueReady = false;
    record.actionCueLoaded = false;
    record.actionCueBounds = null;
    win.webContents.on("did-finish-load", () => {
      record.actionCueLoaded = true;
      sendPendingCue(record);
    });
    win.loadURL(`${overlayUiOrigin()}/menu-overlay/agent-control`).catch(() => {});
    bindHostOverlayRelayout(ctx);
    return win;
  }

  function bindHostOverlayRelayout(ctx) {
    if (!ctx || ctx.hostOverlayMoveBound) return;
    ctx.hostOverlayMoveBound = true;
    const relayout = () => {
      for (const record of ctx.views.values()) {
        layoutActionCue(record);
        layoutControlOverlay(record);
      }
    };
    ctx.win.on("move", relayout);
    ctx.win.on("resize", relayout);
  }

  /** Lay the overlay over the page as it is now and send what it draws. */
  function layoutActionCue(record) {
    const win = record.actionCueWindow;
    if (!win || win.isDestroyed?.()) return;
    const ctx = ownerOf(record);
    const drawn = record.controlHeld || record.actionCueHold;
    if (!ctx || ctx.win.isDestroyed?.() || record.nativeVisible !== true || !drawn) {
      hideActionCueWindow(record);
      return;
    }
    const bounds = cueWindowContentOrigin(ctx, record);
    if (bounds.width <= 2 * ACTION_CUE_MARGIN || bounds.height <= 2 * ACTION_CUE_MARGIN) {
      hideActionCueWindow(record);
      return;
    }
    if (!record.actionCueBounds || boundsDiffer(record.actionCueBounds, bounds)) {
      win.setBounds(bounds);
      record.actionCueBounds = bounds;
    }
    const point = record.actionCueHold ? record.lastCuePoint : null;
    const zoom = viewZoomFactor(record);
    record.pendingCuePayload = {
      margin: ACTION_CUE_MARGIN,
      width: bounds.width,
      height: bounds.height,
      appName: "",
      buttons: null,
      occluders: [],
      glow: !!record.controlHeld,
      radius: overlayRadius(record),
      ending: !!record.actionCueEnding,
      reducedMotion: !!(nativeTheme && nativeTheme.prefersReducedMotion),
      cursor: point
        ? { x: point.x * zoom, y: point.y * zoom, action: point.action || "click", seq: point.seq }
        : null,
    };
    sendPendingCue(record);
    if (!win.isVisible?.()) {
      if (typeof win.showInactive === "function") win.showInactive();
      else win.show();
    }
  }

  /** Fade the overlay out, then hide it. */
  function fadeActionCue(record) {
    const win = record.actionCueWindow;
    if (!win || win.isDestroyed?.() || !win.isVisible?.() || record.actionCueEnding) {
      if (!record.actionCueEnding) hideActionCueWindow(record);
      return;
    }
    record.actionCueEnding = true;
    if (record.pendingCuePayload) {
      record.pendingCuePayload = { ...record.pendingCuePayload, ending: true };
      sendPendingCue(record);
    }
    record.actionCueFadeTimer = setTimeout(() => {
      record.actionCueFadeTimer = null;
      if (!record.actionCueEnding) return;
      record.actionCueEnding = false;
      hideActionCueWindow(record);
    }, ACTION_CUE_FADE_MS);
  }

  /** The Agent holds the page, or lets it go. */
  function setControlGlow(ctx, id, held) {
    const record = recordFor(ctx, id);
    if (!record) return false;
    record.controlHeld = held === true;
    if (record.controlHeld) {
      stopCueMotion(record);
      try { ensureActionCueWindow(ctx, record); } catch { return false; }
      layoutActionCue(record);
    } else if (record.actionCueHold) {
      // The cursor goes with the glow.
      clearActionCue(record);
    } else {
      fadeActionCue(record);
    }
    return true;
  }

  function placeActionCueWindow(record) {
    stopCueMotion(record);
    layoutActionCue(record);
  }

  async function cleanupCue(record, token) {
    if (token == null || !record) return false;
    const acquired = record.actionCueAcquiredTokens;
    if (!acquired || !acquired.has(token)) return false;
    acquired.delete(token);
    const ownsOverlay = record.actionCueHoldToken === token;
    if (ownsOverlay) {
      record.actionCueHold = false;
      record.actionCueHoldToken = null;
      if (record.controlHeld) layoutActionCue(record);
      else fadeActionCue(record);
    }
    return true;
  }

  function clearActionCue(record, forgetLast) {
    if (!record) return false;
    if (record.actionCueTimer != null) {
      clearTimeout(record.actionCueTimer);
      record.actionCueTimer = null;
    }
    stopCueMotion(record);
    const holdToken = record.actionCueHoldToken;
    record.actionCueToken = (record.actionCueToken || 0) + 1;
    void cleanupCue(record, holdToken);
    if (forgetLast) record.lastCuePoint = null;
    return true;
  }

  async function showActionView(ctx, id, marker) {
    const record = recordFor(ctx, id);
    if (!record) return false;
    if (marker == null) {
      if (record.actionCueTimer != null) {
        clearTimeout(record.actionCueTimer);
        record.actionCueTimer = null;
      }
      const holdToken = record.actionCueHoldToken;
      record.actionCueToken = (record.actionCueToken || 0) + 1;
      await cleanupCue(record, holdToken);
      return true;
    }
    if (typeof marker !== "object") return false;
    const x = marker.x;
    const y = marker.y;
    const width = marker.width;
    const height = marker.height;
    const sequence = marker.sequence;
    const generation = marker.generation;
    const hasGeneration = finiteNumber(generation);
    const resourceId = actionCueResourceId(marker);
    if (resourceId == null) return false;
    if (![x, y, width, height, sequence].every(finiteNumber)) return false;
    if (width <= 0 || height <= 0 || sequence < 0) return false;
    if (hasGeneration && generation < 0) return false;
    if (isStaleActionMarker(record, sequence, generation, hasGeneration, resourceId)) {
      return false;
    }
    if (!viewportMatchesMarker(marker, record)) return false;
    if (!pointInViewport(x, y, width, height)) return false;
    const current = cssViewportSize(record);
    if (!pointInViewport(x, y, current.width, current.height)) return false;

    if (record.actionCueTimer != null) {
      clearTimeout(record.actionCueTimer);
      record.actionCueTimer = null;
    }
    const previousHold = record.actionCueHoldToken;
    const token = (record.actionCueToken = (record.actionCueToken || 0) + 1);
    await cleanupCue(record, previousHold);
    if (!actionCueStillCurrent(record, ctx, id, token)) return false;

    let win;
    try {
      win = ensureActionCueWindow(ctx, record);
    } catch {
      return false;
    }
    if (!win) return false;
    if (!record.actionCueAcquiredTokens) record.actionCueAcquiredTokens = new Set();
    record.actionCueAcquiredTokens.add(token);
    record.actionCueHold = true;
    record.actionCueHoldToken = token;
    const action = typeof marker.action === "string" ? marker.action.slice(0, 40) : "";
    // The overlay page glides the cursor here from where it was.
    record.lastCuePoint = { x, y, action, seq: sequence };
    rememberActionCueIdentity(record, sequence, generation, hasGeneration, resourceId);
    placeActionCueWindow(record);
    // Between steps the cursor stays while the Agent holds the page; a
    // step shown without a hold fades after a while.
    const expire = () => {
      if (record.actionCueToken !== token) return;
      if (record.controlHeld) record.actionCueTimer = setTimeout(expire, ACTION_CUE_MS);
      else clearActionCue(record);
    };
    record.actionCueTimer = setTimeout(expire, ACTION_CUE_MS);
    return true;
  }

  return { viewZoomFactor, cssViewportSize, finiteNumber, pointInViewport, viewportMatchesMarker, actionCueResourceId, isStaleActionMarker, rememberActionCueIdentity, boundsDiffer, actionCueStillCurrent, overlayUiOrigin, stopCueMotion, hideActionCueWindow, closeActionCueWindow, cueWindowContentOrigin, sendPendingCue, ensureActionCueWindow, bindHostOverlayRelayout, layoutActionCue, placeActionCueWindow, setControlGlow, cleanupCue, clearActionCue, showActionView };
}

module.exports = { createActionCues };
