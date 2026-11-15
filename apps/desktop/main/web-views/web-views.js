// Desktop web views. State is owned by the main-process composition.
function createWebViews({
  Buffer,
  Menu,
  WebContentsView,
  app,
  boundsDiffer,
  clearActionCue,
  hideActionCueWindow,
  layoutActionCue,
  clipboard,
  crypto,
  dialog,
  fs,
  hideControlOverlay,
  layoutControlOverlay,
  ownerOf,
  path,
  process,
  recordFor,
  safeRecordVisit,
  withDebugger,
  tabTransfers,
}) {
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
      faviconUrl: record.faviconUrl || "",
      loading: wc.isLoading(),
      canGoBack: wc.navigationHistory.canGoBack(),
      canGoForward: wc.navigationHistory.canGoForward(),
      ...extra,
    });
  }

  // Read through the page's session without a public arbitrary-URL IPC. Inline
  // bytes let the renderer inspect alpha without a cross-origin canvas read.
  async function readFavicon(session, url, signal) {
    const limit = 256 * 1024;
    if (/^data:image\/[a-z0-9.+-]+;base64,/i.test(url)) {
      return url.length <= Math.ceil(limit * 4 / 3) + 128 ? url : "";
    }
    if (!isWebUrl(url)) return "";
    const response = await session.fetch(url, { signal, credentials: "omit" });
    if (!response.ok || Number(response.headers.get("content-length")) > limit) {
      await response.body?.cancel();
      return "";
    }
    const mime = response.headers.get("content-type")?.split(";")[0].trim() || "image/x-icon";
    if (!/^image\/[a-z0-9.+-]+$/i.test(mime) && mime !== "application/octet-stream") {
      await response.body?.cancel();
      return "";
    }
    const reader = response.body?.getReader();
    if (!reader) return "";
    const chunks = [];
    let size = 0;
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > limit) { await reader.cancel(); return ""; }
        chunks.push(Buffer.from(value));
      }
    } finally { reader.releaseLock(); }
    return size ? `data:${mime === "application/octet-stream" ? "image/x-icon" : mime};base64,${Buffer.concat(chunks).toString("base64")}` : "";
  }

  function forwardFindResult(record, result) {
    if (tabTransfers.isLocked(record.id) || result?.requestId !== record.findRequestId) return false;
    const owner = ownerOf(record);
    if (!owner) return false;
    owner.win.webContents.send("webtab:find-result", {
      id: record.id,
      activeMatchOrdinal: Number(result?.activeMatchOrdinal) || 0,
      matches: Number(result?.matches) || 0,
      finalUpdate: Boolean(result?.finalUpdate),
    });
    return true;
  }

  /** Whether the user is looking at this page: it is on screen in the window they are in. */
  function watched(record, owner) {
    return record.nativeVisible === true && owner.win.isFocused?.() === true;
  }

  function handleWebTabShortcut(record, event, input) {
    if (
      input?.type !== "keyDown"
      || (!input.meta && !input.control)
      || tabTransfers.isLocked(record.id)
    ) return false;
    const key = String(input.key || "").toLowerCase();
    const owner = ownerOf(record);
    if (!owner) return false;
    const wc = record.view.webContents;
    // Keys an Agent sends to a page arrive here too. The ones that focus the
    // App or open its UI are the user's only when they typed them into it.
    if ((key === "f" || key === "l" || key === "k" || key === "p")
        && !(watched(record, owner) && wc.isFocused?.() === true)) return false;
    if (key === "f" || key === "l") {
      event.preventDefault();
      owner.win.webContents.send("webtab:command", {
        id: record.id,
        command: key === "f" ? "find" : "focus-address",
      });
      owner.win.webContents.focus?.();
    } else if (key === "k" && !input.shift && !input.alt) {
      // The App's command palette, not whatever the page binds Cmd+K to.
      event.preventDefault();
      owner.win.webContents.send("webtab:command", { id: record.id, command: "palette" });
      owner.win.webContents.focus?.();
    } else if (key === "r") {
      // Before the menu sees it: Cmd+R here reloads the page, not the App.
      event.preventDefault();
      record.navigation = null;
      if (input.shift) wc.reloadIgnoringCache();
      else wc.reload();
    } else if (key === "[" || key === "]") {
      event.preventDefault();
      record.navigation = null;
      if (key === "[") {
        if (wc.navigationHistory.canGoBack()) wc.navigationHistory.goBack();
      } else if (wc.navigationHistory.canGoForward()) {
        wc.navigationHistory.goForward();
      }
    } else if (key === "p") {
      event.preventDefault();
      void printView(owner, record.id);
    } else if (key === "+" || key === "=") {
      event.preventDefault();
      zoomView(owner, record.id, "in");
    } else if (key === "-") {
      event.preventDefault();
      zoomView(owner, record.id, "out");
    } else if (key === "0") {
      event.preventDefault();
      zoomView(owner, record.id, "reset");
    } else {
      return false;
    }
    return true;
  }

  function isWebUrl(u) {
    try {
      const p = new URL(u).protocol;
      return p === "http:" || p === "https:";
    } catch {
      return false;
    }
  }

  // 地址栏导航（Chrome 式）还允许 file://——输入本地路径直接打开本地
  // 文件/目录。弹窗（setWindowOpenHandler）仍只放行 web，网页不能把
  // 视图带去本地文件。
  function isTabUrl(u) {
    try {
      const p = new URL(u).protocol;
      return p === "http:" || p === "https:" || p === "file:";
    } catch {
      return false;
    }
  }

  function sendWebTabPopup(record, popupUrl, popupId = "") {
    if (!record || !isWebUrl(popupUrl) || tabTransfers.isLocked(record.id)) {
      return false;
    }
    const owner = ownerOf(record);
    if (!owner) return false;
    owner.win.webContents.send("webtab:popup", {
      openerId: record.id,
      url: popupUrl,
      ...(popupId ? { id: popupId } : {}),
    });
    return true;
  }

  // Same shape as the renderer's popup tab ids, so either side can mint one.
  let nativePopupSeq = 0;
  function nativePopupId(popupUrl) {
    return `w:${popupUrl}:popup:${Date.now().toString(36)}:n${(nativePopupSeq++).toString(36)}`;
  }

  function showWebTabContextMenu(record, params = {}) {
    const owner = ownerOf(record);
    if (!owner || tabTransfers.isLocked(record.id)) return false;
    const ownerId = owner.id;
    const wc = record.view.webContents;
    const exact = (action) => () => {
      const current = ownerOf(record);
      if (
        !current
        || current.id !== ownerId
        || tabTransfers.isLocked(record.id)
        || wc.isDestroyed()
      ) return;
      action();
    };
    const template = [];
    const linkUrl = isWebUrl(params.linkURL) ? params.linkURL : "";
    if (linkUrl) {
      template.push(
        {
          label: "Open Link in New Tab",
          click: exact(() => { sendWebTabPopup(record, linkUrl); }),
        },
        {
          label: "Copy Link Address",
          click: exact(() => { clipboard.writeText(linkUrl); }),
        },
      );
    }

    const editFlags = params.editFlags || {};
    if (params.isEditable) {
      if (template.length) template.push({ type: "separator" });
      template.push(
        { label: "Undo", enabled: !!editFlags.canUndo, click: exact(() => wc.undo()) },
        { label: "Redo", enabled: !!editFlags.canRedo, click: exact(() => wc.redo()) },
        { type: "separator" },
        { label: "Cut", enabled: !!editFlags.canCut, click: exact(() => wc.cut()) },
        { label: "Copy", enabled: !!editFlags.canCopy, click: exact(() => wc.copy()) },
        { label: "Paste", enabled: !!editFlags.canPaste, click: exact(() => wc.paste()) },
        { label: "Select All", enabled: !!editFlags.canSelectAll, click: exact(() => wc.selectAll()) },
      );
    } else {
      if (params.selectionText) {
        if (template.length) template.push({ type: "separator" });
        template.push({
          label: "Copy",
          enabled: editFlags.canCopy !== false,
          click: exact(() => wc.copy()),
        });
      }
      if (template.length) template.push({ type: "separator" });
      template.push(
        {
          label: "Back",
          enabled: wc.navigationHistory.canGoBack(),
          click: exact(() => {
            wc.navigationHistory.goBack();
          }),
        },
        {
          label: "Forward",
          enabled: wc.navigationHistory.canGoForward(),
          click: exact(() => {
            wc.navigationHistory.goForward();
          }),
        },
        {
          label: "Reload",
          click: exact(() => {
            wc.reload();
          }),
        },
      );
    }

    Menu.buildFromTemplate(template).popup({
      window: owner.win,
      ...(params.frame ? { frame: params.frame } : {}),
      ...(params.menuSourceType ? { sourceType: params.menuSourceType } : {}),
    });
    return true;
  }

  function loadView(record, url) {
    record.lastUsedAt = Date.now();
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
  const HIDDEN_WEBTAB_BOUNDS = { x: 0, y: 0, width: 1920, height: 1080 };

  function ensureView(ctx, id, url, viewOptions = null) {
    if (!ctx || typeof id !== "string" || !id) return null;
    if (tabTransfers.isLocked(id)) return null;
    let record = recordFor(ctx, id);
    if (!record && !ctx.views.has(id)) {
      // A Page put to sleep (idle-discard.js) comes back where it was when
      // the caller names no URL of its own.
      const asleep = ctx.sleepingViews?.get(id);
      if (asleep) {
        ctx.sleepingViews.delete(id);
        if (!url) url = asleep.url;
      }
      // A script-opened popup arrives with the WebContents Chromium already
      // made for it (viewOptions.webContents), which keeps window.opener.
      const view = new WebContentsView(viewOptions || {
        webPreferences: { partition: "persist:webtabs" },
      });
      record = { id, view, ownerId: ctx.id, navigation: null, captureNavigationGeneration: 0, findRequestId: null, lastUsedAt: Date.now() };
      const nativeSetBounds = view.setBounds.bind(view);
      const nativeSetVisible = view.setVisible.bind(view);
      view.setBounds = (bounds) => {
        const prev = view.getBounds();
        if (!boundsDiffer(prev, bounds)) return;
        nativeSetBounds(bounds);
        const current = view.getBounds();
        if (record.pipLayoutZoom && (prev.width !== current.width || prev.height !== current.height)) {
          applyPipViewport(record, current);
        }
        if (boundsDiffer(prev, current)) {
          // A resized page has moved what the cursor pointed at.
          if (prev.width !== current.width || prev.height !== current.height) clearActionCue(record, true);
          layoutActionCue(record);
          layoutControlOverlay(record);
        }
      };
      let lastVisible;
      view.setVisible = (visible) => {
        if (lastVisible === visible) return;
        nativeSetVisible(visible);
        lastVisible = visible;
        record.nativeVisible = visible;
        if (!visible) {
          hideActionCueWindow(record);
          hideControlOverlay(record);
        } else {
          layoutActionCue(record);
          layoutControlOverlay(record);
        }
      };
      ctx.views.set(id, record);
      ctx.win.contentView.addChildView(view);
      // A never-shown Page otherwise has a 0x0 viewport, so neither Electron
      // nor CDP can capture it. Keep a real CSS viewport while the native view
      // stays hidden; visible layouts replace these bounds before showing it.
      view.setBounds(HIDDEN_WEBTAB_BOUNDS);
      view.setVisible(false);
      const wc = view.webContents;
      // The Agent works in Pages the user is not looking at. Chromium would
      // otherwise stop drawing a hidden Page, and clicks wait on its frames.
      wc.setBackgroundThrottling(false);
      // Pinch out magnifies the page as in Safari; pinch in at normal size
      // (reported by webtab-preload.js) opens the App's window overview.
      const allowPinchZoom = () => { Promise.resolve(wc.setVisualZoomLevelLimits?.(1, 3)).catch(() => {}); };
      allowPinchZoom();
      wc.on("dom-ready", allowPinchZoom);
      wc.ipc?.on("webtab:pinch-overview", () => {
        const owner = ownerOf(record);
        if (owner && !record.pipLayoutZoom) owner.win.webContents.send("webtab:command", { id: record.id, command: "overview" });
      });
      // Native popup windows are disabled. A valid web popup becomes a hidden
      // Page of this window, built on the WebContents Chromium made for it so
      // window.opener still works: sites such as ImmiAccount finish a flow by
      // messaging the page that opened it. The renderer adopts it as a tab by
      // id and leaves the opener Page (and any exact-page agent session) as is.
      wc.setWindowOpenHandler(({ url: popupUrl }) => {
        const owner = ownerOf(record);
        if (!owner || !isWebUrl(popupUrl) || tabTransfers.isLocked(record.id)) {
          return { action: "deny" };
        }
        const popupId = nativePopupId(popupUrl);
        return {
          action: "allow",
          outlivesOpener: true,
          createWindow: (options) => {
            const popup = ensureView(owner, popupId, "", options);
            if (!popup) throw new Error("popup Page unavailable");
            sendWebTabPopup(record, popupUrl, popupId);
            return popup.view.webContents;
          },
        };
      });
      wc.on("will-prevent-unload", (event) => {
        const owner = ownerOf(record);
        if (!owner || recordFor(owner, id) !== record || wc.isDestroyed() || record.confirmingUnload) return;
        if (!watched(record, owner)) {
          // Nobody is at this page to answer, and a dialog would bring the
          // App in front of what the user is doing: the navigation goes on.
          event.preventDefault();
          return;
        }
        record.confirmingUnload = true;
        try {
          const choice = dialog.showMessageBoxSync(owner.win, {
            type: "question",
            title: "Leave this page?",
            message: "Changes you made may not be saved.",
            buttons: ["Stay on page", "Leave page"],
            defaultId: 0,
            cancelId: 0,
            noLink: true,
          });
          // For this Electron event, preventDefault permits the blocked unload.
          if (choice === 1 && !wc.isDestroyed() && ownerOf(record) === owner && recordFor(owner, id) === record) {
            event.preventDefault();
          }
        } catch {
          // A failed confirmation must never discard the page's unsaved work.
        } finally {
          record.confirmingUnload = false;
        }
      });
      wc.on("context-menu", (_event, params) => {
        showWebTabContextMenu(record, params);
      });
      // Any visible frame can change capture pixels. Advance at both start and
      // commit: a capture may begin while navigation is already in progress.
      // Never reset this generation when pending navigation returns to null.
      const invalidateNavigationCapture = () => { record.captureNavigationGeneration += 1; };
      wc.on("did-start-navigation", invalidateNavigationCapture);
      wc.on("did-frame-navigate", invalidateNavigationCapture);
      wc.on("did-navigate-in-page", invalidateNavigationCapture);
      const clearFavicon = () => {
        record.faviconRequest?.abort();
        record.faviconRequest = null;
        record.faviconSourceUrl = "";
        record.faviconCandidates = "";
        record.faviconUrl = "";
      };
      // Clear before sending navigation state or recording the new history row.
      wc.on("did-navigate", clearFavicon);
      wc.on("destroyed", clearFavicon);
      for (const ev of [
        "did-navigate",
        "did-navigate-in-page",
        "page-title-updated",
        "did-start-loading",
        "did-stop-loading",
      ]) {
        wc.on(ev, () => sendState(record));
      }
      // Detaching any final shared debugger holder resets Chromium emulation.
      // Restore the current preview, never the geometry saved by a capture.
      wc.debugger.on("detach", () => restorePipViewport(record));
      wc.on("did-navigate", () => {
        if (record.pipLayoutZoom) {
          applyPipViewport(record, view.getBounds(), true);
          setPipScrollbarStyle(record, true, true);
        } else restorePendingPipZoom(record);
      });
      // Browsing history. The store folds repeat hits on the head URL into one
      // row, so the title/favicon events that follow a navigation enrich the
      // entry instead of appending duplicates.
      const noteVisit = () => {
        if (wc.isDestroyed()) return;
        safeRecordVisit({
          url: wc.getURL(),
          title: wc.getTitle(),
          faviconUrl: record.faviconSourceUrl || "",
          visitedAt: Date.now(),
        });
      };
      wc.on("did-navigate", noteVisit);
      wc.on("did-navigate-in-page", (_event, _url, isMainFrame) => {
        if (isMainFrame) noteVisit();
      });
      wc.on("page-title-updated", noteVisit);
      wc.on("page-favicon-updated", (_event, favicons) => {
        const sources = (Array.isArray(favicons) ? favicons.slice(0, 4) : []).filter(url => typeof url === "string");
        const candidates = JSON.stringify(sources);
        if (record.faviconUrl && record.faviconCandidates === candidates) return;
        clearFavicon();
        record.faviconCandidates = candidates;
        sendState(record);
        const request = new AbortController();
        record.faviconRequest = request;
        const timeout = setTimeout(() => request.abort(), 5_000);
        void (async () => {
          for (const url of sources) {
            if (typeof url !== "string" || request.signal.aborted) continue;
            let icon = "";
            try { icon = await readFavicon(wc.session, url, request.signal); }
            catch { /* Missing/blocked icons use the renderer's Lucide fallback. */ }
            if (request.signal.aborted || record.faviconRequest !== request || wc.isDestroyed()) return;
            if (!icon) continue;
            record.faviconSourceUrl = url;
            record.faviconUrl = icon;
            sendState(record);
            noteVisit();
            return;
          }
        })().finally(() => clearTimeout(timeout));
      });
      wc.on("found-in-page", (_event, result) => forwardFindResult(record, result));
      wc.on("did-navigate", () => clearActionCue(record, true));
      wc.on("did-navigate-in-page", (_event, _url, isMainFrame) => {
        if (isMainFrame) clearActionCue(record, true);
      });
      wc.on("before-input-event", (event, input) => {
        if (handleWebTabShortcut(record, event, input)) return;
      });
      if (url && isTabUrl(url)) void loadView(record, url).catch(() => {});
    }
    if (record) sendState(record);
    return record;
  }

  async function navigateView(ctx, id, url) {
    if (!url || !isTabUrl(url)) return null;
    const record = recordFor(ctx, id) || ensureView(ctx, id, "");
    return record ? loadView(record, url) : null;
  }

  const WEBTAB_ZOOM_FACTORS = [0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3];
  const PIP_VIRTUAL_WIDTH = 1920;
  const PIP_VIRTUAL_HEIGHT = 1080;

  // The page lays out 1920 wide and as tall as the preview's shape makes
  // it, so it fills the preview edge to edge whatever the shape.
  function pipLayoutZoom(width) {
    return width / PIP_VIRTUAL_WIDTH;
  }

  // Electron's enableDeviceEmulation and disableDeviceEmulation reach the
  // main frame's RenderWidgetHostView without checking it, and a page has
  // none before its first document commits (getURL() is still empty) or
  // after its renderer died: either call then is a SIGSEGV of the whole App.
  // The emulation lives in that renderer anyway; did-navigate reapplies the
  // preview viewport once a document has committed.
  function pageFrameLive(wc) {
    try {
      return !wc.isDestroyed() && !wc.isCrashed?.() && !!wc.getURL();
    } catch (_error) {
      return false;
    }
  }

  function applyPipViewport(record, { width, height }, force = false) {
    if (!(width > 0 && height > 0)) return;
    const wc = record.view.webContents;
    // Page zoom changes layout; device emulation scales presentation separately.
    const scale = pipLayoutZoom(width);
    const viewport = { width: PIP_VIRTUAL_WIDTH, height: Math.round(height / scale) };
    if (!force && record.pipLayoutZoom === scale && record.pipViewport?.height === viewport.height) return;
    if (wc.getZoomFactor() !== 1) wc.setZoomFactor(1);
    if (pageFrameLive(wc)) {
      wc.enableDeviceEmulation({
        screenPosition: "desktop",
        viewSize: viewport,
        deviceScaleFactor: 0,
        scale,
      });
    }
    record.pipLayoutZoom = scale;
    record.pipViewport = viewport;
  }

  // Only the root tracks are suppressed. Overflow, wheel and keyboard scrolling
  // remain native; normal tabs get their original CSS back, even after a late insert.
  function setPipScrollbarStyle(record, enabled, replace = false) {
    const wc = record.view.webContents;
    const previous = record.pipScrollbarStyle;
    if (enabled && previous && !replace) return;
    const remove = (key) => {
      if (!wc.isDestroyed()) void wc.removeInsertedCSS(key).catch(() => {});
    };
    record.pipScrollbarStyle = null;
    if (previous?.key) remove(previous.key);
    if (!enabled || wc.isDestroyed()) return;
    const pending = {};
    record.pipScrollbarStyle = pending;
    void wc.insertCSS(
      ":root { scrollbar-width: none !important; } :root::-webkit-scrollbar { display: none !important; }",
      // Electron removal uses Blink's default author origin, even when insertion
      // requested user origin. Keep insertion/removal in the same origin.
      { cssOrigin: "author" },
    ).then(key => {
      if (record.pipScrollbarStyle !== pending) remove(key);
      else pending.key = key;
    }).catch(() => {
      if (record.pipScrollbarStyle === pending) record.pipScrollbarStyle = null;
    });
  }

  function restorePipViewport(record) {
    try {
      if (record.pipLayoutZoom && !record.view.webContents.isDestroyed()) {
        applyPipViewport(record, record.view.getBounds(), true);
      }
    } catch { /* renderer or native view closed during cleanup */ }
  }

  function rememberUserZoom(record) {
    if (record.userZoomFactor != null) return;
    try {
      record.userZoomFactor = record.pipLayoutZoom
        ? 1
        : record.view.webContents.getZoomFactor();
    } catch (_error) {
      record.userZoomFactor = 1;
    }
  }

  function setPipZoom(ctx, id, width, height = width * PIP_VIRTUAL_HEIGHT / PIP_VIRTUAL_WIDTH) {
    let record = recordFor(ctx, id);
    if (!record && width == null && tabTransfers.isLocked(id)) {
      const lockedRecord = ctx?.views.get(id);
      if (lockedRecord?.ownerId === ctx.id) {
        lockedRecord.pendingTransferZoomRestore = true;
        return true;
      }
    }
    if (!record) return false;
    const wc = record.view.webContents;
    try {
      if (Number.isFinite(width) && width > 0 && Number.isFinite(height) && height > 0) {
        rememberUserZoom(record);
        record.pendingTransferZoomRestore = false;
        record.pendingPipZoomRestore = false;
        applyPipViewport(record, { width, height });
        setPipScrollbarStyle(record, true);
        record.view.setBackgroundColor("#00000000");
        const radius = Math.max(1, Math.round(10 * (ctx.win.webContents.getZoomFactor?.() ?? 1)));
        if (record.pipCornerRadius !== radius) {
          record.view.setBorderRadius(radius);
          record.pipCornerRadius = radius;
        }
        return true;
      }
      record.pendingTransferZoomRestore = false;
      return requestPipZoomRestore(record);
    } catch (_error) {
      return false;
    }
  }

  function requestPipZoomRestore(record) {
    try {
      const wc = record.view.webContents;
      if (pageFrameLive(wc)) wc.disableDeviceEmulation();
      record.pipLayoutZoom = null;
      record.pipViewport = null;
      setPipScrollbarStyle(record, false);
      record.view.setBackgroundColor("#ffffff");
      if (record.pipCornerRadius) {
        record.view.setBorderRadius(0);
        record.pipCornerRadius = 0;
      }
      record.pendingPipZoomRestore = !wc.getURL() || !!record.navigation;
      wc.setZoomFactor(record.userZoomFactor ?? 1);
      return true;
    } catch (_error) {
      return false;
    }
  }

  function restorePendingPipZoom(record) {
    if (!record?.pendingPipZoomRestore || record.pipLayoutZoom) return false;
    try {
      record.view.webContents.setZoomFactor(record.userZoomFactor ?? 1);
      record.pendingPipZoomRestore = false;
      return true;
    } catch (_error) {
      return false;
    }
  }

  function findView(ctx, id, query, options) {
    const record = recordFor(ctx, id);
    const needle = typeof query === "string" ? query.slice(0, 512) : "";
    if (!record || !needle) return false;
    try {
      record.findRequestId = record.view.webContents.findInPage(needle, {
        forward: options?.forward !== false,
        findNext: options?.findNext === true,
      });
      return true;
    } catch (_error) {
      return false;
    }
  }

  function stopFindView(ctx, id, action) {
    const record = recordFor(ctx, id);
    if (!record || !["clearSelection", "keepSelection", "activateSelection"].includes(action)) {
      return false;
    }
    try {
      record.findRequestId = null;
      record.view.webContents.stopFindInPage(action);
      return true;
    } catch (_error) {
      return false;
    }
  }

  // Comment mode: the user points at one element of the page and the chat
  // gets a description of it. The picker runs in its own isolated world, so
  // the page can neither see nor call it; it outlines what is under the
  // pointer, swallows the click that picks, and Escape cancels.
  const PICKER_WORLD = 1207;
  const PICKER_SOURCE = `(() => new Promise((resolve) => {
    window.__openprogramPick?.cancel();
    const root = document.documentElement;
    const box = document.createElement("div");
    box.style.cssText = "position:fixed;z-index:2147483647;pointer-events:none;display:none;"
      + "border:2px solid #3b82f6;background:rgba(59,130,246,.12);border-radius:3px;box-sizing:border-box";
    root.appendChild(box);
    // The page's own style attribute comes back exactly as it was.
    const style = root.getAttribute("style");
    root.style.cursor = "crosshair";
    const selector = (el) => {
      const parts = [];
      for (let node = el; node && node.nodeType === 1 && parts.length < 5; node = node.parentElement) {
        let part = node.tagName.toLowerCase();
        if (node.id && /^[A-Za-z][\\w-]*$/.test(node.id)) { parts.unshift(part + "#" + node.id); break; }
        const parent = node.parentElement;
        if (parent) {
          const same = Array.from(parent.children).filter((c) => c.tagName === node.tagName);
          if (same.length > 1) part += ":nth-of-type(" + (same.indexOf(node) + 1) + ")";
        }
        parts.unshift(part);
      }
      return parts.join(" > ");
    };
    const describe = (el) => ({
      tag: el.tagName.toLowerCase(),
      selector: selector(el),
      text: (el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim().slice(0, 300),
      label: el.getAttribute("aria-label") || el.getAttribute("alt") || el.getAttribute("title") || "",
      href: el.closest("a") ? el.closest("a").href : "",
    });
    const place = (el) => {
      const r = el.getBoundingClientRect();
      Object.assign(box.style, { display: "block", top: r.top + "px", left: r.left + "px",
        width: r.width + "px", height: r.height + "px" });
    };
    const stop = (e) => { e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation(); };
    const over = (e) => { if (e.target instanceof Element && e.target !== box) place(e.target); };
    const click = (e) => { stop(e); finish(e.target instanceof Element ? describe(e.target) : null); };
    const key = (e) => { if (e.key === "Escape") { stop(e); finish(null); } };
    const swallow = ["pointerdown", "pointerup", "mousedown", "mouseup", "auxclick", "dblclick"];
    const finish = (value) => {
      window.removeEventListener("mouseover", over, true);
      window.removeEventListener("click", click, true);
      window.removeEventListener("keydown", key, true);
      for (const type of swallow) window.removeEventListener(type, stop, true);
      box.remove();
      if (style === null) root.removeAttribute("style");
      else root.setAttribute("style", style);
      delete window.__openprogramPick;
      resolve(value);
    };
    window.addEventListener("mouseover", over, true);
    window.addEventListener("click", click, true);
    window.addEventListener("keydown", key, true);
    for (const type of swallow) window.addEventListener(type, stop, true);
    window.__openprogramPick = { cancel: () => finish(null) };
  }))()`;

  function pickedText(value, limit) {
    return typeof value === "string" ? value.slice(0, limit) : "";
  }

  async function pickElementInView(ctx, id) {
    const record = recordFor(ctx, id);
    if (!record) return null;
    const contents = record.view.webContents;
    try {
      contents.focus();
      const picked = await contents.executeJavaScriptInIsolatedWorld(
        PICKER_WORLD, [{ code: PICKER_SOURCE }], true,
      );
      if (!picked || typeof picked !== "object" || recordFor(ctx, id) !== record || contents.isDestroyed()) {
        return null;
      }
      return {
        id,
        url: contents.getURL(),
        title: contents.getTitle(),
        tag: pickedText(picked.tag, 40),
        selector: pickedText(picked.selector, 400),
        text: pickedText(picked.text, 300),
        label: pickedText(picked.label, 200),
        href: pickedText(picked.href, 2048),
      };
    } catch (_error) {
      return null;
    }
  }

  function cancelPickInView(ctx, id) {
    const record = recordFor(ctx, id);
    // The pane cancels its pickers as it unmounts, which is also what
    // happens when the page closed itself (a sign-in popup after the
    // redirect): the record is still there but its webContents is gone.
    const contents = record?.view?.webContents;
    if (!contents || contents.isDestroyed?.()) return false;
    try {
      contents.executeJavaScriptInIsolatedWorld(
        PICKER_WORLD, [{ code: "window.__openprogramPick?.cancel()" }],
      ).catch(() => {});
    } catch (_error) {
      return false;
    }
    return true;
  }

  // Region screenshot: the user drags a rectangle over the page and the
  // chat gets those pixels. The overlay lives in the picker's isolated world
  // and shares its cancel hook, so Escape or Cancel ends either mode. It is
  // removed and two frames painted (or 150 ms passed, on a page that is not
  // painting) before the rectangle is returned, so the capture never
  // contains it.
  const REGION_SOURCE = `(() => new Promise((resolve) => {
    window.__openprogramPick?.cancel();
    const root = document.documentElement;
    const layer = document.createElement("div");
    layer.style.cssText = "position:fixed;inset:0;z-index:2147483647;cursor:crosshair;background:transparent";
    const box = document.createElement("div");
    box.style.cssText = "position:fixed;display:none;pointer-events:none;border:1.5px solid #3b82f6;"
      + "background:rgba(59,130,246,.12);border-radius:3px;box-sizing:border-box";
    const size = document.createElement("div");
    size.style.cssText = "position:fixed;display:none;pointer-events:none;padding:1px 6px;border-radius:4px;"
      + "background:#1e293b;color:#fff;font:11px/16px -apple-system,system-ui,sans-serif";
    layer.append(box, size);
    root.appendChild(layer);
    let start = null;
    let rect = null;
    const stop = (e) => { e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation(); };
    const draw = (e) => {
      const x = Math.max(0, Math.min(start.x, e.clientX));
      const y = Math.max(0, Math.min(start.y, e.clientY));
      const right = Math.min(innerWidth, Math.max(start.x, e.clientX));
      const bottom = Math.min(innerHeight, Math.max(start.y, e.clientY));
      rect = { x, y, width: right - x, height: bottom - y };
      Object.assign(box.style, { display: "block", left: x + "px", top: y + "px",
        width: rect.width + "px", height: rect.height + "px" });
      size.textContent = Math.round(rect.width) + " × " + Math.round(rect.height);
      Object.assign(size.style, { display: "block", left: x + "px",
        top: (bottom + 22 < innerHeight ? bottom + 4 : Math.max(0, y - 20)) + "px" });
    };
    const down = (e) => { if (e.button !== 0) return; stop(e); start = { x: e.clientX, y: e.clientY }; draw(e); };
    const move = (e) => { if (start) { stop(e); draw(e); } };
    const up = (e) => {
      if (!start) return;
      stop(e);
      // A click without a drag starts over rather than capturing a sliver.
      if (!rect || rect.width < 8 || rect.height < 8) { start = null; rect = null; box.style.display = "none"; size.style.display = "none"; return; }
      finish(rect);
    };
    const key = (e) => { if (e.key === "Escape") { stop(e); finish(null); } };
    const finish = (value) => {
      window.removeEventListener("mousedown", down, true);
      window.removeEventListener("mousemove", move, true);
      window.removeEventListener("mouseup", up, true);
      window.removeEventListener("keydown", key, true);
      window.removeEventListener("click", stop, true);
      layer.remove();
      delete window.__openprogramPick;
      if (!value) { resolve(null); return; }
      // A page that is not painting runs no frames; the timer stands in.
      let done = false;
      const settle = () => { if (!done) { done = true; resolve(value); } };
      requestAnimationFrame(() => requestAnimationFrame(settle));
      setTimeout(settle, 150);
    };
    window.addEventListener("mousedown", down, true);
    window.addEventListener("mousemove", move, true);
    window.addEventListener("mouseup", up, true);
    window.addEventListener("keydown", key, true);
    window.addEventListener("click", stop, true);
    window.__openprogramPick = { cancel: () => finish(null) };
  }))()`;

  async function pickRegionInView(ctx, id) {
    const record = recordFor(ctx, id);
    if (!record || record.pipLayoutZoom) return null;
    const contents = record.view.webContents;
    try {
      contents.focus();
      const navigation = record.navigation;
      const url = contents.getURL();
      const picked = await contents.executeJavaScriptInIsolatedWorld(
        PICKER_WORLD, [{ code: REGION_SOURCE }], true,
      );
      if (!picked || typeof picked !== "object" || recordFor(ctx, id) !== record || contents.isDestroyed()
          || record.navigation !== navigation || contents.getURL() !== url) {
        return null;
      }
      const numbers = ["x", "y", "width", "height"].map((key) => Number(picked[key]));
      if (!numbers.every(Number.isFinite)) return null;
      // Page CSS pixels are view pixels times the page zoom.
      const zoom = contents.getZoomFactor() || 1;
      const [x, y, width, height] = numbers.map((value) => Math.round(value * zoom));
      if (width < 1 || height < 1) return null;
      const image = await contents.capturePage({ x, y, width, height }, { stayHidden: true });
      if (!image || (typeof image.isEmpty === "function" && image.isEmpty())) return null;
      const pixels = image.getSize();
      return {
        id,
        url,
        title: contents.getTitle(),
        width: pixels.width,
        height: pixels.height,
        dataUrl: image.toDataURL(),
      };
    } catch (_error) {
      return null;
    }
  }

  async function capturePipView(ctx, record) {
    if (record.pipCapture) return record.pipCapture;
    const contents = record.view.webContents;
    const bounds = record.view.getBounds();
    const scale = record.pipLayoutZoom;
    const viewport = record.pipViewport;
    const url = contents.getURL();
    const navigation = record.navigation;
    const generation = record.captureNavigationGeneration;
    const current = () => recordFor(ctx, record.id) === record
      && !contents.isDestroyed() && record.pipLayoutZoom === scale && record.pipViewport?.height === viewport?.height
      && !boundsDiffer(bounds, record.view.getBounds())
      && contents.getURL() === url && record.navigation === navigation
      && record.captureNavigationGeneration === generation;
    const capture = (async () => {
      try {
        // Native capturePage returns the scaled display bitmap. CDP must know
        // the same emulation state before capturing, or captureScreenshot can
        // temporarily reflow the document to the native view's small size.
        const result = await withDebugger(contents, async (client) => {
          const metrics = await contents.executeJavaScript(`({
            dpr: devicePixelRatio, x: scrollX, y: scrollY,
            screenWidth: screen.width, screenHeight: screen.height
          })`);
          if (!current() || !(metrics.dpr > 0)) return null;
          try {
            await client.sendCommand("Emulation.setDeviceMetricsOverride", {
              width: viewport.width, height: viewport.height,
              deviceScaleFactor: metrics.dpr, mobile: false,
              screenWidth: metrics.screenWidth, screenHeight: metrics.screenHeight,
              dontSetVisibleSize: true, scale,
            });
            if (!current()) return null;
            return await client.sendCommand("Page.captureScreenshot", {
              format: "png", fromSurface: true, captureBeyondViewport: true,
              clip: { x: metrics.x, y: metrics.y, width: viewport.width,
                height: viewport.height, scale: 1 / metrics.dpr },
            });
          } finally {
            try { await client.sendCommand("Emulation.clearDeviceMetricsOverride"); }
            finally { restorePipViewport(record); }
          }
        });
        if (!current() || !result?.data) return null;
        return `data:image/png;base64,${result.data}`;
      } finally {
        // withDebugger can detach here or after another concurrent holder.
        // The detach listener also reapplies the latest preview geometry.
        restorePipViewport(record);
      }
    })();
    record.pipCapture = capture;
    try { return await capture; }
    finally { if (record.pipCapture === capture) record.pipCapture = null; }
  }

  async function captureView(ctx, id, mode) {
    const record = recordFor(ctx, id);
    if (!record) return null;
    const contents = record.view.webContents;
    try {
      if (record.pipLayoutZoom && mode !== "presentation") return await capturePipView(ctx, record);
      const generation = record.captureNavigationGeneration;
      const url = contents.getURL();
      const navigation = record.navigation;
      const image = await contents.capturePage(
        undefined,
        { stayHidden: true },
      );
      if (recordFor(ctx, id) !== record || contents.isDestroyed()
          || record.captureNavigationGeneration !== generation
          || contents.getURL() !== url || record.navigation !== navigation) return null;
      if (!image || (typeof image.isEmpty === "function" && image.isEmpty())) {
        return null;
      }
      return image.toDataURL();
    } catch (_error) {
      return null;
    }
  }

  function zoomView(ctx, id, action) {
    const record = recordFor(ctx, id);
    if (!record || !["in", "out", "reset"].includes(action)) return null;
    try {
      const wc = record.view.webContents;
      rememberUserZoom(record);
      const current = record.userZoomFactor ?? wc.getZoomFactor();
      const nearest = WEBTAB_ZOOM_FACTORS.reduce(
        (best, value, index) => Math.abs(value - current) < Math.abs(WEBTAB_ZOOM_FACTORS[best] - current)
          ? index
          : best,
        0,
      );
      const index = action === "reset"
        ? WEBTAB_ZOOM_FACTORS.indexOf(1)
        : Math.max(
            0,
            Math.min(WEBTAB_ZOOM_FACTORS.length - 1, nearest + (action === "in" ? 1 : -1)),
          );
      const factor = WEBTAB_ZOOM_FACTORS[index];
      record.userZoomFactor = factor;
      if (!record.pipLayoutZoom) wc.setZoomFactor(factor);
      return Math.round(factor * 100);
    } catch (_error) {
      return null;
    }
  }

  function printPdfDefaultName(title) {
    const stem = String(title || "page")
      .normalize("NFC")
      .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_")
      .replace(/[.\s]+$/g, "")
      .slice(0, 120);
    return `${stem || "page"}.pdf`;
  }

  async function printView(ctx, id) {
    const record = recordFor(ctx, id);
    if (!record) return false;
    const wc = record.view.webContents;
    let destroyed = false;
    const printResult = await new Promise((resolve) => {
      let settled = false;
      const finish = (success, failureReason = "") => {
        if (settled) return;
        settled = true;
        wc.removeListener("destroyed", onDestroyed);
        resolve({ success: Boolean(success), failureReason: String(failureReason || "") });
      };
      const onDestroyed = () => {
        destroyed = true;
        finish(false);
      };
      wc.once("destroyed", onDestroyed);
      try {
        wc.print(
          { silent: false, printBackground: true },
          finish,
        );
      } catch (_error) {
        finish(false);
      }
    });
    if (printResult.success) return true;
    if (printResult.failureReason === "Print job canceled") return false;
    if (
      destroyed
      || wc.isDestroyed()
      || ctx.win.isDestroyed()
      || recordFor(ctx, id) !== record
    ) return false;
    let stagedPdfPath = "";
    try {
      const selected = await dialog.showSaveDialog(ctx.win, {
        title: "Save page as PDF",
        defaultPath: path.join(app.getPath("downloads"), printPdfDefaultName(wc.getTitle())),
        filters: [{ name: "PDF", extensions: ["pdf"] }],
      });
      if (selected.canceled || !selected.filePath) return false;
      if (wc.isDestroyed() || ctx.win.isDestroyed() || recordFor(ctx, id) !== record) return false;
      const pdf = await wc.printToPDF({ printBackground: true, preferCSSPageSize: true });
      if (!Buffer.isBuffer(pdf)) return false;
      if (wc.isDestroyed() || ctx.win.isDestroyed() || recordFor(ctx, id) !== record) return false;
      stagedPdfPath = path.join(
        path.dirname(selected.filePath),
        `.${path.basename(selected.filePath)}.${process.pid}.${crypto.randomUUID()}.tmp`,
      );
      await fs.promises.writeFile(stagedPdfPath, pdf, { flag: "wx", mode: 0o600 });
      if (wc.isDestroyed() || ctx.win.isDestroyed() || recordFor(ctx, id) !== record) return false;
      await fs.promises.rename(stagedPdfPath, selected.filePath);
      stagedPdfPath = "";
      return true;
    } catch (_error) {
      return false;
    } finally {
      if (stagedPdfPath) {
        try {
          await fs.promises.unlink(stagedPdfPath);
        } catch (_error) {
          // Best effort: the selected destination remains untouched.
        }
      }
    }
  }

  return { sendState, forwardFindResult, handleWebTabShortcut, isWebUrl, isTabUrl, sendWebTabPopup, showWebTabContextMenu, loadView, ensureView, navigateView, pipLayoutZoom, rememberUserZoom, setPipZoom, requestPipZoomRestore, restorePendingPipZoom, findView, stopFindView, pickElementInView, pickRegionInView, cancelPickInView, captureView, zoomView, printPdfDefaultName, printView };
}

module.exports = { createWebViews };
