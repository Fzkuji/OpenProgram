// Idle Pages give up their renderer process.
//
// Every Page of a window is a native WebContentsView with a renderer
// process of its own, whether or not anyone is looking at it: the retained
// Pages an Agent opened over weeks of conversations each keep one. A Page
// nobody has shown, navigated, captured or operated for IDLE_MS closes its
// view and remembers where it was (``ctx.sleepingViews``); the next show,
// ensure, navigation or activation recreates the view at that URL, so the
// tab, its resource row and the Agent's access to it are unchanged. Page
// state such as scroll position and form input is lost at that point, which
// is why a Page in use is never put to sleep: visible, loading, playing
// audio, carrying an Agent's control overlay or action cue, in a picture-
// in-picture layout, mid-transfer or mid-navigation.
function createIdleDiscard({ now, idleMs, destroyView, tabTransfers }) {
  function touch(record) {
    if (record) record.lastUsedAt = now();
  }

  function inUse(ctx, record) {
    if (ctx.visibleViewIds.has(record.id) || record.nativeVisible) return true;
    if (record.controlPayload || record.controlOverlayView || record.actionCueWindow) return true;
    if (record.navigation || record.pipLayoutZoom) return true;
    if (tabTransfers.isLocked(record.id)) return true;
    const contents = record.view?.webContents;
    try {
      if (!contents || contents.isDestroyed?.()) return true;
      if (contents.isLoading?.() || contents.isCurrentlyAudible?.()) return true;
    } catch (_error) {
      return true;
    }
    return false;
  }

  function sleep(ctx, record) {
    const contents = record.view?.webContents;
    let url = "";
    let title = "";
    try {
      url = String(contents?.getURL?.() || "");
      title = String(contents?.getTitle?.() || "");
    } catch (_error) {
      url = "";
    }
    // A Page with no URL (a popup still opening) has nowhere to come back to.
    if (!url) return false;
    if (!destroyView(ctx, record.id)) return false;
    if (!ctx.sleepingViews) ctx.sleepingViews = new Map();
    ctx.sleepingViews.set(record.id, { url, title, sleptAt: now() });
    return true;
  }

  /** Put every idle Page of the window to sleep; returns how many. */
  function sweep(ctx) {
    if (!ctx || ctx.win?.isDestroyed?.()) return 0;
    let count = 0;
    for (const record of [...ctx.views.values()]) {
      if (record.ownerId !== ctx.id) continue;
      if (inUse(ctx, record)) {
        touch(record);
        continue;
      }
      if (now() - (record.lastUsedAt || 0) < idleMs) continue;
      if (sleep(ctx, record)) count += 1;
    }
    return count;
  }

  return { touch, inUse, sleep, sweep };
}

/** Where a sleeping Page was, or null when it is live or unknown. */
function sleepingView(ctx, id) {
  return ctx?.sleepingViews?.get(id) ?? null;
}

module.exports = { createIdleDiscard, sleepingView };
