// Desktop view layout. State is owned by the main-process composition.
function createViewLayout({
  clearActionCue,
  closeActionCueWindow,
  closeControlOverlay,
  ensureView,
  isTabUrl,
  navigateView,
  recordFor,
  tabTransfers,
  withDebugger,
}) {
  function normalizedBounds(bounds) {
    return {
      x: Math.round(Number(bounds?.x)) || 0,
      y: Math.round(Number(bounds?.y)) || 0,
      width: Math.max(0, Math.round(Number(bounds?.width)) || 0),
      height: Math.max(0, Math.round(Number(bounds?.height)) || 0),
    };
  }

  function rendererZoomFactor(event) {
    const senderZoom = Number(event?.sender?.getZoomFactor?.());
    return Number.isFinite(senderZoom) && senderZoom > 0 ? senderZoom : 1;
  }

  function normalizedRendererBounds(event, bounds) {
    const zoom = rendererZoomFactor(event);
    return normalizedBounds({
      x: Number(bounds?.x) * zoom,
      y: Number(bounds?.y) * zoom,
      width: Number(bounds?.width) * zoom,
      height: Number(bounds?.height) * zoom,
    });
  }

  function normalizedRendererMenuOptions(event, options) {
    const source = options && typeof options === "object" ? options : {};
    const anchor = source.anchor && typeof source.anchor === "object"
      ? { ...source.anchor }
      : {};
    const zoom = rendererZoomFactor(event);
    for (const key of ["x", "right", "y", "rightInset", "top", "vw", "vh"]) {
      const value = Number(anchor[key]);
      if (Number.isFinite(value)) anchor[key] = value * zoom;
    }
    const normalized = { ...source, anchor };
    for (const key of ["width", "height"]) {
      const value = Number(source[key]);
      if (Number.isFinite(value)) normalized[key] = value * zoom;
    }
    return normalized;
  }

  function syncVisibleViews(ctx, items) {
    if (!ctx || ctx.win.isDestroyed() || !Array.isArray(items)) return false;
    const desired = new Map();
    for (const item of items) {
      // Skip unknown/invalid entries instead of aborting the whole sync:
      // aborting would leave previously shown views visible over pages
      // that no longer expect them (e.g. after a route change). A view
      // that exists but is owned elsewhere (or transfer-locked) still
      // aborts — that is a stale cross-window command, not a missing tab.
      if (!item || typeof item.id !== "string") continue;
      const record = recordFor(ctx, item.id);
      if (!record) {
        if (ctx.views.has(item.id) || tabTransfers.isLocked(item.id)) return false;
        continue;
      }
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

  async function devToolsTargetId(webContents) {
    return withDebugger(webContents, async (client) => {
      const result = await client.sendCommand("Target.getTargetInfo");
      const targetId = result?.targetInfo?.targetId;
      return typeof targetId === "string" && targetId ? targetId : null;
    });
  }

  async function activateView(ctx, id, url, requireVisible = false) {
    let record;
    if (url) {
      if (!isTabUrl(url)) return null;
      record = recordFor(ctx, id)
        || (!requireVisible ? ensureView(ctx, id, "") : null);
      if (
        !record
        || (requireVisible
          ? !ctx.visibleViewIds.has(id)
          : !showView(ctx, id))
      ) return null;
      record = await navigateView(ctx, id, url);
      if (recordFor(ctx, id) !== record || !ctx.visibleViewIds.has(id)) return null;
    } else {
      record = recordFor(ctx, id);
      if (
        !record
        || (requireVisible
          ? !ctx.visibleViewIds.has(id)
          : !showView(ctx, id))
      ) return null;
    }
    const targetId = await devToolsTargetId(record.view.webContents);
    if (recordFor(ctx, id) !== record || !ctx.visibleViewIds.has(id)) return null;
    return targetId;
  }

  async function resolveView(ctx, id) {
    const record = recordFor(ctx, id);
    if (!record) return null;
    const targetId = await devToolsTargetId(record.view.webContents);
    return recordFor(ctx, id) === record ? targetId : null;
  }

  async function inspectView(ctx, id) {
    const record = recordFor(ctx, id);
    if (!record) return null;
    const contents = record.view.webContents;
    const targetId = await devToolsTargetId(contents);
    if (!targetId || recordFor(ctx, id) !== record) return null;
    return {
      target_id: targetId,
      url: contents.getURL(),
      title: contents.getTitle(),
    };
  }

  const SURFACE_PREVIEW_SCRIPT = `(() => {
    const visible = (element) => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden"
        && rect.width > 0 && rect.height > 0
        && rect.bottom > 0 && rect.right > 0
        && rect.top < innerHeight && rect.left < innerWidth;
    };
    let bodyText = "";
    if (document.body) {
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      while (walker.nextNode() && bodyText.length <= 2000) {
        const parent = walker.currentNode.parentElement;
        if (!parent || !visible(parent)) continue;
        const text = (walker.currentNode.textContent || "").replace(/\\s+/g, " ").trim();
        if (text) bodyText += (bodyText ? " " : "") + text;
      }
    }
    const landmarkSelector = [
      "header", "nav", "main", "aside", "footer",
      "[role=banner]", "[role=navigation]", "[role=main]",
      "[role=complementary]", "[role=contentinfo]", "[role=search]",
    ].join(",");
    const visibleLandmarks = Array.from(document.querySelectorAll(landmarkSelector))
      .filter(visible);
    const landmarks = visibleLandmarks
      .slice(0, 12)
      .map((element) => ({
        role: element.getAttribute("role") || element.tagName.toLowerCase(),
        name: (
          element.getAttribute("aria-label") || element.getAttribute("title") || ""
        ).replace(/\\s+/g, " ").trim().slice(0, 160),
      }));
    const interactiveSelector = [
      "a[href]", "button", "input", "textarea", "select", "summary",
      "[role=button]", "[role=link]", "[role=checkbox]", "[role=radio]",
      "[role=tab]", "[role=menuitem]", "[contenteditable=true]",
      "[tabindex]:not([tabindex='-1'])",
    ].join(",");
    return {
      visible_text_excerpt: bodyText.slice(0, 2000),
      text_truncated: bodyText.length > 2000,
      aria_landmarks: landmarks,
      landmarks_truncated: visibleLandmarks.length > landmarks.length,
      interactive_count: Array.from(document.querySelectorAll(interactiveSelector))
        .filter(visible).length,
    };
  })()`;

  async function previewView(ctx, id, allowBackground = false) {
    const record = recordFor(ctx, id);
    if (!record) return null;
    if (!allowBackground && !ctx.visibleViewIds.has(id)) return null;
    const wc = record.view.webContents;
    if (wc.isDestroyed?.()) return null;
    try {
      const [preview, targetId] = await Promise.all([
        wc.executeJavaScript(SURFACE_PREVIEW_SCRIPT, true),
        devToolsTargetId(wc),
      ]);
      if (!targetId || recordFor(ctx, id) !== record || wc.isDestroyed?.()) {
        return null;
      }
      if (!allowBackground && !ctx.visibleViewIds.has(id)) return null;
      return {
        tab_id: id,
        target_id: targetId,
        url: wc.getURL(),
        title: wc.getTitle(),
        preview,
      };
    } catch {
      return null;
    }
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

  function destroyView(ctx, id, { strict = false } = {}) {
    const record = recordFor(ctx, id);
    if (!record) return false;
    if (strict) {
      try {
        record.view.webContents.close();
      } catch (_e) {
        return false;
      }
    }
    clearActionCue(record, true);
    closeActionCueWindow(record);
    closeControlOverlay(record);
    ctx.visibleViewIds.delete(id);
    record.navigation = null;
    ctx.views.delete(id);
    try {
      ctx.win.contentView.removeChildView(record.view);
    } catch (_e) {
      /* already detached */
    }
    if (!strict) {
      try {
        record.view.webContents.close();
      } catch (_e) {
        /* already closed */
      }
    }
    return true;
  }

  function clearOwnedViews(ctx) {
    for (const record of [...ctx.views.values()]) {
      if (record.ownerId === ctx.id) destroyView(ctx, record.id);
    }
    ctx.visibleViewIds = new Set();
  }

  return { normalizedBounds, rendererZoomFactor, normalizedRendererBounds, normalizedRendererMenuOptions, syncVisibleViews, currentVisibleItems, showView, hideView, devToolsTargetId, activateView, resolveView, inspectView, previewView, withView, runNativeNavigation, destroyView, clearOwnedViews };
}

module.exports = { createViewLayout };
