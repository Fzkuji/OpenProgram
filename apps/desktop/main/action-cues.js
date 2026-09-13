// Desktop action cues. State is owned by the main-process composition.
function createActionCues({
  ACTION_CUE_MS,
  ACTION_CUE_SIZE,
  BrowserWindow,
  START_URL,
  WEB_PORT,
  __dirname,
  clearTimeout,
  layoutControlOverlay,
  nativeTheme,
  ownerOf,
  path,
  recordFor,
  setTimeout,
}) {
  function viewZoomFactor(record) {
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
      width: bounds.width / zoom,
      height: bounds.height / zoom,
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

  function stopCueMotion(record) {
    if (record.actionCueMotionTimer != null) {
      clearTimeout(record.actionCueMotionTimer);
      record.actionCueMotionTimer = null;
    }
  }

  function hideActionCueWindow(record) {
    stopCueMotion(record);
    const win = record.actionCueWindow;
    if (!win || win.isDestroyed?.()) return;
    try { win.hide(); } catch { /* already gone */ }
  }

  function closeActionCueWindow(record) {
    hideActionCueWindow(record);
    const win = record.actionCueWindow;
    record.actionCueWindow = null;
    record.actionCueReady = false;
    record.actionCueLoaded = false;
    record.pendingCuePayload = null;
    if (!win || win.isDestroyed?.()) return;
    try { win.close(); } catch { /* already closed */ }
  }

  function cueWindowContentOrigin(ctx, record, x, y) {
    const content = ctx.win.getContentBounds ? ctx.win.getContentBounds() : ctx.win.getBounds();
    const page = record.view.getBounds();
    const zoom = viewZoomFactor(record);
    const size = ACTION_CUE_SIZE;
    return {
      x: Math.round(content.x + page.x + x * zoom - size / 2),
      y: Math.round(content.y + page.y + y * zoom - size / 2),
      width: size,
      height: size,
    };
  }

  function sendPendingCue(record) {
    const win = record.actionCueWindow;
    const payload = record.pendingCuePayload;
    if (!payload || !win || win.isDestroyed?.() || !record.actionCueReady) return;
    try { win.webContents.send("webtab:action-cue", payload); } catch { /* overlay not ready */ }
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
      width: ACTION_CUE_SIZE,
      height: ACTION_CUE_SIZE,
      webPreferences: {
        preload: path.join(__dirname, "preload.js"),
        contextIsolation: true,
        nodeIntegration: false,
        additionalArguments: [
          `--openprogram-window-id=${ctx.id}`,
          "--openprogram-surface=action-cue",
        ],
      },
    });
    win.setIgnoreMouseEvents(true, { forward: true });
    if (typeof win.setMenuBarVisibility === "function") win.setMenuBarVisibility(false);
    record.actionCueWindow = win;
    record.actionCueReady = false;
    record.actionCueLoaded = false;
    win.webContents.on("did-finish-load", () => {
      record.actionCueLoaded = true;
      sendPendingCue(record);
    });
    win.loadURL(`${overlayUiOrigin()}/menu-overlay/action-cue`).catch(() => {});
    bindHostOverlayRelayout(ctx);
    return win;
  }

  function bindHostOverlayRelayout(ctx) {
    if (!ctx || ctx.hostOverlayMoveBound) return;
    ctx.hostOverlayMoveBound = true;
    const relayout = () => {
      for (const record of ctx.views.values()) {
        if (record.actionCueHold && record.lastCuePoint) {
          placeActionCueWindow(record, record.lastCuePoint.x, record.lastCuePoint.y);
        }
        layoutControlOverlay(record);
      }
    };
    ctx.win.on("move", relayout);
    ctx.win.on("resize", relayout);
  }

  function placeActionCueWindow(record, x, y) {
    const ctx = ownerOf(record);
    const win = record.actionCueWindow;
    if (!ctx || !win || win.isDestroyed?.()) return;
    win.setBounds(cueWindowContentOrigin(ctx, record, x, y));
    if (typeof win.showInactive === "function") win.showInactive();
    else win.show();
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
      hideActionCueWindow(record);
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
    const reducedMotion = marker.reducedMotion === true
      || !!(nativeTheme && nativeTheme.prefersReducedMotion);
    const last = record.lastCuePoint || null;
    const next = { x, y };
    const animateMove = !!(last && !reducedMotion && (last.x !== next.x || last.y !== next.y));
    const finish = () => {
      if (!actionCueStillCurrent(record, ctx, id, token)) return;
      placeActionCueWindow(record, next.x, next.y);
      record.pendingCuePayload = {
        play: reducedMotion ? "never" : "once",
        playSeq: sequence,
        reducedMotion,
      };
      sendPendingCue(record);
      record.lastCuePoint = next;
      rememberActionCueIdentity(record, sequence, generation, hasGeneration, resourceId);
      record.actionCueTimer = setTimeout(() => {
        if (record.actionCueToken === token) clearActionCue(record);
      }, ACTION_CUE_MS);
    };
    if (!animateMove) {
      placeActionCueWindow(record, next.x, next.y);
      finish();
      return true;
    }
    const dx = next.x - last.x;
    const dy = next.y - last.y;
    const dist = Math.sqrt(dx * dx + dy * dy);
    const duration = Math.min(280, Math.max(80, Math.round(dist * 0.4)));
    const started = Date.now();
    placeActionCueWindow(record, last.x, last.y);
    const step = () => {
      if (!actionCueStillCurrent(record, ctx, id, token)) return;
      const t = Math.min(1, (Date.now() - started) / duration);
      const ease = t * (2 - t);
      placeActionCueWindow(record, last.x + dx * ease, last.y + dy * ease);
      if (t < 1) {
        record.actionCueMotionTimer = setTimeout(step, 16);
        return;
      }
      finish();
    };
    record.actionCueMotionTimer = setTimeout(step, 16);
    return true;
  }

  return { viewZoomFactor, cssViewportSize, finiteNumber, pointInViewport, viewportMatchesMarker, actionCueResourceId, isStaleActionMarker, rememberActionCueIdentity, boundsDiffer, actionCueStillCurrent, overlayUiOrigin, stopCueMotion, hideActionCueWindow, closeActionCueWindow, cueWindowContentOrigin, sendPendingCue, ensureActionCueWindow, bindHostOverlayRelayout, placeActionCueWindow, cleanupCue, clearActionCue, showActionView };
}

module.exports = { createActionCues };
