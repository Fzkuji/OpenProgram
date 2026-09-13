// Desktop menus. State is owned by the main-process composition.
function createMenus({
  CONTEXT_MENU_CHROME,
  CONTEXT_MENU_ROW_HEIGHT,
  CONTEXT_MENU_WIDTH,
  MAIN_MENU_GUTTER,
  MAIN_MENU_HEIGHT,
  MAIN_MENU_WIDTH,
  MENU_THEME_ID_SET,
  START_URL,
  WEB_PORT,
  WebContentsView,
  __dirname,
  cascadeMenuGeometry,
  clampContextMenuPanel,
  clearTimeout,
  contextForSender,
  path,
  setTimeout,
  windows,
}) {
  function resizeMenuOverlay(ctx, size) {
    const view = ctx && ctx.mainMenuView;
    const anchor = ctx && ctx.mainMenuAnchor;
    if (!view || !anchor || view.webContents.isDestroyed()) return;
    const zoom = Number.isFinite(Number(anchor.zoom)) && Number(anchor.zoom) > 0
      ? Number(anchor.zoom)
      : 1;
    const gutter = MAIN_MENU_GUTTER * zoom;
    const panelW = Math.max(1, Math.round(Number(size && size.width) || 0));
    let panelH = Math.max(1, Math.round(Number(size && size.height) || 0));
    panelH = Math.min(panelH, Math.max(1, anchor.winH - 16 * zoom));
    if (!panelW || !panelH) return;
    const { x, y } = clampContextMenuPanel(anchor, panelW, panelH);
    view.setBounds({
      x: Math.round(x - gutter),
      y: Math.round(y - gutter),
      width: Math.round(panelW + gutter * 2),
      height: Math.round(panelH + gutter * 2),
    });
  }

  function hasNestedMenuItems(items) {
    return Array.isArray(items) && items.some((item) =>
      Array.isArray(item && item.children) && item.children.length > 0,
    );
  }

  function menuOverlayUrl(theme, items, anchor, width, cascade = false) {
    let origin = "http://127.0.0.1:" + WEB_PORT;
    try {
      origin = new URL(START_URL).origin;
    } catch (_e) {
      /* keep fallback */
    }
    const q = new URLSearchParams();
    if (MENU_THEME_ID_SET.has(theme)) q.set("theme", theme);
    if (items) q.set("items", JSON.stringify(items));
    if (anchor) {
      q.set("x", String(anchor.x));
      q.set("y", String(anchor.y));
    }
    if (cascade) q.set("cascade", "1");
    if (Number.isFinite(width) && width > 0) q.set("width", String(width));
    return (
      origin
      + (items ? "/menu-overlay/context-menu?" : "/menu-overlay/main-menu?")
      + q.toString()
    );
  }

  function closeMainMenu(ctx) {
    cancelMainMenuClose(ctx);
    if (!ctx || !ctx.mainMenuView) return;
    const view = ctx.mainMenuView;
    ctx.mainMenuView = null;
    ctx.mainMenuAnchor = null;
    ctx.mainMenuCascade = false;
    ctx.mainMenuPendingUpdate = null;
    try {
      if (!ctx.win.isDestroyed()) ctx.win.contentView.removeChildView(view);
    } catch (_e) {
      /* already detached */
    }
    try {
      view.webContents.close();
    } catch (_e) {
      /* already closed */
    }
    if (ctx.win && !ctx.win.isDestroyed()) {
      ctx.win.webContents.send("main-menu:closed");
    }
  }

  function cancelMainMenuClose(ctx) {
    if (!ctx || !ctx.mainMenuCloseTimer) return;
    clearTimeout(ctx.mainMenuCloseTimer);
    ctx.mainMenuCloseTimer = null;
  }

  function scheduleMainMenuClose(ctx, delay) {
    if (!ctx || !ctx.mainMenuView) return;
    cancelMainMenuClose(ctx);
    const requestedDelay = Number(delay);
    const closeDelay = Number.isFinite(requestedDelay)
      ? Math.min(500, Math.max(0, requestedDelay))
      : 120;
    ctx.mainMenuCloseTimer = setTimeout(() => {
      ctx.mainMenuCloseTimer = null;
      closeMainMenu(ctx);
    }, closeDelay);
  }

  function openMainMenu(ctx, opts, zoom = 1) {
    if (!ctx || ctx.win.isDestroyed()) return;
    cancelMainMenuClose(ctx);
    const menuZoom = Number.isFinite(Number(zoom)) && Number(zoom) > 0
      ? Number(zoom)
      : 1;
    const requestedCascade = Boolean(opts && opts.cascade);
    const requestedItems = Array.isArray(opts && opts.items) ? opts.items : null;
    const requestedAnchor = (opts && opts.anchor) || {};

    // Adjacent bookmark folders share one live overlay. Replacing its data is
    // immediate and preserves already decoded favicons; rebuilding the whole
    // WebContentsView on every mouseenter made each folder look as if its icons
    // were being fetched again.
    if (
      requestedCascade
      && requestedItems
      && ctx.mainMenuCascade
      && ctx.mainMenuView
      && !ctx.mainMenuView.webContents.isDestroyed()
    ) {
      const { width: contentW, height: contentH } = ctx.win.getContentBounds();
      const winW = Number(requestedAnchor.vw) || contentW;
      const winH = Number(requestedAnchor.vh) || contentH;
      const geometry = cascadeMenuGeometry(requestedAnchor, winW, winH, menuZoom);
      const update = {
        items: requestedItems,
        x: geometry.anchor.x,
        y: geometry.anchor.y,
        theme: opts && opts.theme,
        width: Number.isFinite(Number(opts && opts.width))
          ? Number(opts.width) / menuZoom
          : undefined,
      };
      ctx.mainMenuView.setBounds(geometry.bounds);
      if (ctx.mainMenuView.webContents.isLoadingMainFrame()) {
        ctx.mainMenuPendingUpdate = update;
      } else {
        ctx.mainMenuView.webContents.send("main-menu:update", update);
      }
      return;
    }

    closeMainMenu(ctx);
    const gutter = MAIN_MENU_GUTTER * menuZoom;
    const view = new WebContentsView({
      webPreferences: {
        preload: path.join(__dirname, "preload.js"),
        contextIsolation: true,
        nodeIntegration: false,
        transparent: true,
        additionalArguments: [`--openprogram-window-id=${ctx.id}`],
      },
    });
    view.webContents.setZoomFactor(menuZoom);
    view.setBackgroundColor("#00000000");
    ctx.mainMenuView = view;
    ctx.mainMenuCascade = requestedCascade;
    ctx.mainMenuPendingUpdate = null;
    ctx.win.contentView.addChildView(view);

    // Anchor: the panel's right edge sits `rightInset` (8px, the tab-strip
    // gutter) from the window's right; its top edge sits on the strip's bottom
    // divider so the menu covers the content below. The view is GUTTER wider
    // and taller than the panel on every side (transparent room for the drop
    // shadow), so the panel is inset by GUTTER inside the view — offset the
    // view accordingly. The renderer measures against its own viewport, so use
    // the viewport width it reports, not getContentBounds (which can disagree).
    const anchor = (opts && opts.anchor) || {};
    const { width: cbW, height: cbH } = ctx.win.getContentBounds();
    const winW = Number(anchor.vw) || cbW;
    const winH = Number(anchor.vh) || cbH;
    const items = Array.isArray(opts && opts.items) ? opts.items : null;
    const nestedItems = hasNestedMenuItems(items);
    const cascadeMenu = Boolean(opts && opts.cascade);
    const overlayWidth = Number.isFinite(Number(opts && opts.width))
      ? Number(opts.width) / menuZoom
      : null;
    let overlayAnchor = null;
    let panelW;
    let panelH;
    let panelX;
    let panelY;
    let cascadeGeometry = null;
    if (items && cascadeMenu) {
      // Bookmark-folder menus need full horizontal room for submenu portals,
      // but must begin below the triggering bar so adjacent folders still
      // receive hover/click events while the menu is open.
      ctx.mainMenuAnchor = null;
      cascadeGeometry = cascadeMenuGeometry(anchor, winW, winH, menuZoom);
      overlayAnchor = cascadeGeometry.anchor;
      panelW = cascadeGeometry.bounds.width;
      panelH = cascadeGeometry.bounds.height;
      panelX = cascadeGeometry.bounds.x;
      panelY = cascadeGeometry.bounds.y;
    } else if (items && nestedItems) {
      // Cascading bookmark folders need the overlay document to cover the
      // content area so submenu portals are not clipped by root-panel bounds.
      ctx.mainMenuAnchor = null;
      overlayAnchor = {
        x: (Number(anchor.x) || 0) / menuZoom,
        y: (Number(anchor.y) || 0) / menuZoom,
      };
      panelW = winW;
      panelH = winH;
      panelX = 0;
      panelY = 0;
    } else if (items) {
      // Generic context menu: panel top-left at anchor {x, y}, clamped to an
      // 8px margin inside the window (same clamp the DOM tab menu used).
      panelW = Number(opts.width) || CONTEXT_MENU_WIDTH * menuZoom;
      panelH = Math.min(
        Number(opts.height)
          || (items.length * CONTEXT_MENU_ROW_HEIGHT + CONTEXT_MENU_CHROME) * menuZoom,
        Math.max(1, winH - 16 * menuZoom),
      );
      // Remember the anchor + viewport so main-menu:resize can re-clamp the
      // view once the overlay reports its measured panel size.
      const align = anchor.align === "end" && Number.isFinite(Number(anchor.right))
        ? "end"
        : "start";
      ctx.mainMenuAnchor = {
        x: Number(anchor.x) || 0,
        right: Number(anchor.right) || 0,
        align,
        y: Number(anchor.y) || 0,
        winW,
        winH,
        zoom: menuZoom,
      };
      const clamped = clampContextMenuPanel(ctx.mainMenuAnchor, panelW, panelH);
      panelX = clamped.x;
      panelY = clamped.y;
    } else {
      ctx.mainMenuAnchor = null;
      // Main menu: panel right edge sits `rightInset` from the window right,
      // top edge on the strip's bottom divider.
      panelW = MAIN_MENU_WIDTH * menuZoom;
      panelH = MAIN_MENU_HEIGHT * menuZoom;
      const rightInset = Number.isFinite(anchor.rightInset)
        ? anchor.rightInset
        : 8 * menuZoom;
      panelX = Math.max(0, winW - rightInset - panelW);
      panelY = Number.isFinite(anchor.top) ? anchor.top : 40 * menuZoom;
    }
    if (cascadeGeometry) {
      view.setBounds(cascadeGeometry.bounds);
    } else if (nestedItems) {
      view.setBounds({ x: 0, y: 0, width: Math.round(winW), height: Math.round(winH) });
    } else {
      const viewW = panelW + gutter * 2;
      const viewH = panelH + gutter * 2;
      // Panel is inset by GUTTER inside the view (transparent room for the
      // drop shadow) — offset the view accordingly.
      view.setBounds({
        x: Math.round(panelX - gutter),
        y: Math.round(panelY - gutter),
        width: Math.round(viewW),
        height: Math.round(viewH),
      });
    }

    const theme = opts && opts.theme;
    view.webContents
      .loadURL(menuOverlayUrl(theme, items, overlayAnchor, overlayWidth, cascadeMenu))
      .then(() => {
        if (ctx.mainMenuView === view && !view.webContents.isDestroyed()) {
          if (ctx.mainMenuPendingUpdate) {
            view.webContents.send("main-menu:update", ctx.mainMenuPendingUpdate);
            ctx.mainMenuPendingUpdate = null;
          }
          view.webContents.focus();
        }
      })
      .catch(() => {});
    // Outside click steals focus from this view → close.
    view.webContents.on("blur", () => {
      if (ctx.mainMenuView === view) closeMainMenu(ctx);
    });
  }

  // The menu overlay runs in a WebContentsView, whose webContents does NOT
  // resolve via BrowserWindow.fromWebContents — find its owning window by
  // matching the sender against each context's mainMenuView.
  function contextForMenuSender(event) {
    const fromWindow = contextForSender(event);
    if (fromWindow) return fromWindow;
    const sender = event?.sender;
    if (!sender) return null;
    for (const ctx of windows.values()) {
      if (
        ctx.mainMenuView
        && !ctx.win.isDestroyed()
        && ctx.mainMenuView.webContents === sender
      ) {
        return ctx;
      }
    }
    return null;
  }

  return { resizeMenuOverlay, hasNestedMenuItems, menuOverlayUrl, closeMainMenu, cancelMainMenuClose, scheduleMainMenuClose, openMainMenu, contextForMenuSender };
}

module.exports = { createMenus };
