// Desktop control overlays. State is owned by the main-process composition.
function createControlOverlays({
  Menu,
  UI_ORIGIN,
  WebContentsView,
  __dirname,
  bindHostOverlayRelayout,
  contextForSender,
  finiteNumber,
  overlayUiOrigin,
  ownerOf,
  path,
  recordFor,
  rendererZoomFactor,
  require,
  windows,
}) {
  function controlOverlayOwner(event) {
    const sender = event?.sender;
    if (!sender) return null;
    try {
      if (new URL(sender.getURL()).origin !== UI_ORIGIN) return null;
    } catch {
      return null;
    }
    for (const ctx of windows.values()) {
      if (ctx.win.isDestroyed()) continue;
      for (const record of ctx.views.values()) {
        if (record.ownerId !== ctx.id) continue;
        if (record.controlOverlayView && record.controlOverlayView.webContents === sender) {
          return { ctx, record };
        }
      }
    }
    return null;
  }

  function actionCueOwner(event) {
    const sender = event?.sender;
    if (!sender) return null;
    for (const ctx of windows.values()) {
      if (ctx.win.isDestroyed()) continue;
      for (const record of ctx.views.values()) {
        if (record.actionCueWindow && record.actionCueWindow.webContents === sender) {
          return { ctx, record };
        }
      }
    }
    return null;
  }

  function hideControlOverlay(record) {
    const view = record.controlOverlayView;
    if (!view || view.webContents?.isDestroyed?.()) return;
    try { view.setVisible(false); } catch { /* gone */ }
  }

  function closeControlOverlay(record) {
    const ctx = ownerOf(record);
    const view = record.controlOverlayView;
    record.controlOverlayView = null;
    record.controlReady = false;
    record.controlPayload = null;
    if (!view) return;
    if (ctx && !ctx.win.isDestroyed()) {
      try { ctx.win.contentView.removeChildView(view); } catch { /* detached */ }
    }
    try { view.webContents.close(); } catch { /* closed */ }
  }

  function clampControlLayout(record, width, height) {
    const page = record.view.getBounds();
    const collapsed = record.controlCollapsed !== false;
    const sizeW = Math.max(1, Math.round(width || (collapsed ? 36 : 280)));
    const sizeH = Math.max(1, Math.round(height || (collapsed ? 36 : 44)));
    const maxLeft = Math.max(4, page.width - sizeW - 4);
    const maxTop = Math.max(4, page.height - sizeH - 4);
    let left = Number.isFinite(record.controlLeft) ? record.controlLeft : Math.max(8, page.width - sizeW - 12);
    let top = Number.isFinite(record.controlTop) ? record.controlTop : Math.max(8, page.height - sizeH - 12);
    left = Math.min(Math.max(4, left), maxLeft);
    top = Math.min(Math.max(4, top), maxTop);
    record.controlLeft = left;
    record.controlTop = top;
    record.controlWidth = sizeW;
    record.controlHeight = sizeH;
    return {
      x: Math.round(page.x + left),
      y: Math.round(page.y + top),
      width: sizeW,
      height: sizeH,
    };
  }

  function sendControlOverlayUpdate(record) {
    const view = record.controlOverlayView;
    const payload = record.controlPayload;
    if (!payload || !view || view.webContents.isDestroyed() || !record.controlReady) return;
    try {
      view.webContents.send("webtab:control-overlay-update", {
        ...payload,
        collapsed: record.controlCollapsed !== false,
      });
    } catch { /* overlay not ready */ }
  }

  function layoutControlOverlay(record) {
    const view = record.controlOverlayView;
    if (!view || view.webContents?.isDestroyed?.()) return;
    if (!record.controlPayload) {
      hideControlOverlay(record);
      return;
    }
    const ctx = ownerOf(record);
    if (!ctx || !ctx.visibleViewIds.has(record.id)) {
      hideControlOverlay(record);
      return;
    }
    view.setBounds(clampControlLayout(record, record.controlWidth, record.controlHeight));
    view.setVisible(true);
    sendControlOverlayUpdate(record);
  }

  function setControlOverlay(ctx, id, payload) {
    const record = recordFor(ctx, id);
    if (!record) return false;
    if (payload == null) {
      record.controlPayload = null;
      hideControlOverlay(record);
      return true;
    }
    if (typeof payload !== "object") return false;
    if (typeof payload.resourceId !== "string" || !payload.resourceId) return false;
    if (!finiteNumber(payload.generation) || payload.generation < 0) return false;
    record.controlPayload = payload;
    if (record.controlCollapsed === undefined) record.controlCollapsed = true;
    let view = record.controlOverlayView;
    if (!view || view.webContents.isDestroyed()) {
      view = new WebContentsView({
        webPreferences: {
          preload: path.join(__dirname, "preload.js"),
          contextIsolation: true,
          nodeIntegration: false,
          transparent: true,
          additionalArguments: [
            `--openprogram-window-id=${ctx.id}`,
            "--openprogram-surface=browser-control",
          ],
        },
      });
      view.setBackgroundColor("#00000000");
      record.controlOverlayView = view;
      record.controlReady = false;
      ctx.win.contentView.addChildView(view);
      view.webContents.on("did-finish-load", () => {
        sendControlOverlayUpdate(record);
      });
      view.webContents.loadURL(`${overlayUiOrigin()}/menu-overlay/browser-control`).catch(() => {});
    }
    bindHostOverlayRelayout(ctx);
    layoutControlOverlay(record);
    return true;
  }

  function handleControlOverlayEvent(event, payload) {
    const owner = controlOverlayOwner(event);
    if (!owner || !payload || typeof payload !== "object") return;
    const { ctx, record } = owner;
    const type = payload.type;
    if (type === "ready") {
      record.controlReady = true;
      sendControlOverlayUpdate(record);
      return;
    }
    if (type === "layout") {
      const zoom = rendererZoomFactor(event);
      const nextCollapsed = typeof payload.collapsed === "boolean"
        ? payload.collapsed
        : record.controlCollapsed !== false;
      const nextWidth = finiteNumber(payload.width) ? payload.width * zoom : record.controlWidth;
      const nextHeight = finiteNumber(payload.height) ? payload.height * zoom : record.controlHeight;
      const same = nextCollapsed === (record.controlCollapsed !== false)
        && Math.round(nextWidth || 0) === Math.round(record.controlWidth || 0)
        && Math.round(nextHeight || 0) === Math.round(record.controlHeight || 0);
      record.controlCollapsed = nextCollapsed;
      if (finiteNumber(payload.width)) record.controlWidth = nextWidth;
      if (finiteNumber(payload.height)) record.controlHeight = nextHeight;
      if (same) return;
      layoutControlOverlay(record);
      return;
    }
    if (type === "move") {
      if (!finiteNumber(payload.dx) || !finiteNumber(payload.dy)) return;
      if (payload.id !== record.controlPayload?.resourceId) return;
      if (payload.generation !== record.controlPayload?.generation) return;
      record.controlLeft = (record.controlLeft || 0) + payload.dx;
      record.controlTop = (record.controlTop || 0) + payload.dy;
      layoutControlOverlay(record);
      return;
    }
    if (type === "history") {
      if (payload.id !== record.controlPayload?.resourceId) return;
      if (payload.generation !== record.controlPayload?.generation) return;
      const items = Array.isArray(record.controlPayload.historyItems)
        ? record.controlPayload.historyItems
        : [{ id: "empty", label: record.controlPayload.historyLabel || "Operation history", disabled: true }];
      const page = record.view.getBounds();
      void nativeContextMenus.popup(ctx.win, ctx.win.webContents, {
        requestId: `overlay-history:${record.id}:${Date.now()}`,
        x: page.x + (record.controlLeft || 0),
        y: page.y + (record.controlTop || 0) + (record.controlHeight || 36),
        items,
      }).catch(() => {});
      return;
    }
    if (type !== "pause" && type !== "resume" && type !== "reveal" && type !== "toggle-show") return;
    if (payload.id !== record.controlPayload?.resourceId) {
      ctx.win.webContents.send("webtab:control-overlay-event", {
        type: "stale",
        id: payload.id,
        generation: payload.generation,
      });
      return;
    }
    if (payload.generation !== record.controlPayload?.generation) {
      ctx.win.webContents.send("webtab:control-overlay-event", {
        type: "stale",
        id: payload.id,
        generation: payload.generation,
      });
      return;
    }
    ctx.win.webContents.send("webtab:control-overlay-event", {
      type,
      id: payload.id,
      generation: payload.generation,
    });
  }

  const nativeContextMenus = require("./native-context-menu").createNativeContextMenus(Menu);
  function nativeMenuOwner(event) {
    const ctx = contextForSender(event);
    if (!ctx || !event.senderFrame || event.sender !== ctx.win.webContents || event.senderFrame !== event.sender.mainFrame) return null;
    try { return new URL(event.sender.getURL()).origin === UI_ORIGIN ? ctx : null; }
    catch { return null; }
  }

  return { controlOverlayOwner, actionCueOwner, hideControlOverlay, closeControlOverlay, clampControlLayout, sendControlOverlayUpdate, layoutControlOverlay, setControlOverlay, handleControlOverlayEvent, nativeMenuOwner };
}

module.exports = { createControlOverlays };
