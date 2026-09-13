// Desktop web views. State is owned by the main-process composition.
function createWebViews({
  Buffer,
  Menu,
  WebContentsView,
  app,
  boundsDiffer,
  clearActionCue,
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
      loading: wc.isLoading(),
      canGoBack: wc.navigationHistory.canGoBack(),
      canGoForward: wc.navigationHistory.canGoForward(),
      ...extra,
    });
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

  function handleWebTabShortcut(record, event, input) {
    if (
      input?.type !== "keyDown"
      || (!input.meta && !input.control)
      || tabTransfers.isLocked(record.id)
    ) return false;
    const key = String(input.key || "").toLowerCase();
    const owner = ownerOf(record);
    if (!owner) return false;
    if (key === "f") {
      event.preventDefault();
      owner.win.webContents.send("webtab:command", { id: record.id, command: "find" });
      owner.win.webContents.focus?.();
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

  function sendWebTabPopup(record, popupUrl) {
    if (!record || !isWebUrl(popupUrl) || tabTransfers.isLocked(record.id)) {
      return false;
    }
    const owner = ownerOf(record);
    if (!owner) return false;
    owner.win.webContents.send("webtab:popup", {
      openerId: record.id,
      url: popupUrl,
    });
    return true;
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

  function ensureView(ctx, id, url) {
    if (!ctx || typeof id !== "string" || !id) return null;
    if (tabTransfers.isLocked(id)) return null;
    let record = recordFor(ctx, id);
    if (!record && !ctx.views.has(id)) {
      const view = new WebContentsView({
        webPreferences: { partition: "persist:webtabs" },
      });
      record = { id, view, ownerId: ctx.id, navigation: null, findRequestId: null };
      const nativeSetBounds = view.setBounds.bind(view);
      const nativeSetVisible = view.setVisible.bind(view);
      view.setBounds = (bounds) => {
        const prev = view.getBounds();
        nativeSetBounds(bounds);
        if (boundsDiffer(prev, view.getBounds())) {
          clearActionCue(record, true);
          layoutControlOverlay(record);
        }
      };
      view.setVisible = (visible) => {
        nativeSetVisible(visible);
        if (!visible) {
          clearActionCue(record, true);
          hideControlOverlay(record);
        } else {
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
      // Native popup windows are disabled. A valid web popup is delegated to
      // this record's renderer window, which creates a distinct Browser tab and
      // leaves the opener Page (and any exact-page agent session) unchanged.
      wc.setWindowOpenHandler(({ url: popupUrl }) => {
        sendWebTabPopup(record, popupUrl);
        return { action: "deny" };
      });
      wc.on("context-menu", (_event, params) => {
        showWebTabContextMenu(record, params);
      });
      for (const ev of [
        "did-navigate",
        "did-navigate-in-page",
        "page-title-updated",
        "did-start-loading",
        "did-stop-loading",
      ]) {
        wc.on(ev, () => sendState(record));
      }
      wc.on("did-navigate", () => restorePendingPipZoom(record));
      // Browsing history. The store folds repeat hits on the head URL into one
      // row, so the title/favicon events that follow a navigation enrich the
      // entry instead of appending duplicates.
      const noteVisit = () => {
        if (wc.isDestroyed()) return;
        safeRecordVisit({
          url: wc.getURL(),
          title: wc.getTitle(),
          faviconUrl: record.faviconUrl || "",
          visitedAt: Date.now(),
        });
      };
      wc.on("did-navigate", noteVisit);
      wc.on("did-navigate-in-page", (_event, _url, isMainFrame) => {
        if (isMainFrame) noteVisit();
      });
      wc.on("page-title-updated", noteVisit);
      wc.on("page-favicon-updated", (_event, favicons) => {
        record.faviconUrl = Array.isArray(favicons) ? favicons[0] || "" : "";
        sendState(record, { faviconUrl: record.faviconUrl });
        noteVisit();
      });
      // 新页面没有 favicon 时不会再触发 page-favicon-updated——导航提交时先清
      // 掉上一页的图标，否则 tab 会一直挂着旧站点的 icon。
      wc.on("did-navigate", () => {
        record.faviconUrl = "";
        sendState(record, { faviconUrl: "" });
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
    return record;
  }

  async function navigateView(ctx, id, url) {
    if (!url || !isTabUrl(url)) return null;
    const record = recordFor(ctx, id) || ensureView(ctx, id, "");
    return record ? loadView(record, url) : null;
  }

  const WEBTAB_ZOOM_FACTORS = [0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3];
  const PIP_VIRTUAL_WIDTH = 1920;
  const PIP_ZOOM_MIN = 0.25;

  function pipLayoutZoom(width) {
    // CSS viewport = viewWidth / zoom. CDP/Playwright Input and
    // screenshot(scale="css") already use that CSS space.
    return Math.max(PIP_ZOOM_MIN, Math.min(1, width / PIP_VIRTUAL_WIDTH));
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

  function setPipZoom(ctx, id, width) {
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
      if (typeof width === "number" && width > 0) {
        rememberUserZoom(record);
        const factor = pipLayoutZoom(width);
        record.pendingTransferZoomRestore = false;
        record.pendingPipZoomRestore = false;
        record.pipLayoutZoom = factor;
        wc.setZoomFactor(factor);
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
      record.pipLayoutZoom = null;
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

  async function captureView(ctx, id) {
    const record = recordFor(ctx, id);
    if (!record) return null;
    const contents = record.view.webContents;
    try {
      const image = await contents.capturePage(
        undefined,
        { stayHidden: true },
      );
      if (recordFor(ctx, id) !== record || contents.isDestroyed()) return null;
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

  return { sendState, forwardFindResult, handleWebTabShortcut, isWebUrl, isTabUrl, sendWebTabPopup, showWebTabContextMenu, loadView, ensureView, navigateView, pipLayoutZoom, rememberUserZoom, setPipZoom, requestPipZoomRestore, restorePendingPipZoom, findView, stopFindView, captureView, zoomView, printPdfDefaultName, printView };
}

module.exports = { createWebViews };
