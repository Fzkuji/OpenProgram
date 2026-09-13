// Desktop downloads. State is owned by the main-process composition.
function createDownloads({
  DOWNLOAD_STATES,
  activeDownloads,
  app,
  crypto,
  downloads,
  downloadsFile,
  fs,
  path,
  process,
  session,
  windows,
}) {
  function downloadsRoot() {
    const directory = app.getPath("downloads");
    fs.mkdirSync(directory, { recursive: true });
    return fs.realpathSync(directory);
  }

  function pathInside(root, candidate) {
    const relative = path.relative(root, candidate);
    return relative !== "" && !relative.startsWith(`..${path.sep}`) && relative !== ".."
      && !path.isAbsolute(relative);
  }

  function pathInsideOrEqual(root, candidate) {
    return root === candidate || pathInside(root, candidate);
  }

  function allowedDownloadPath(value, mustExist = false) {
    if (typeof value !== "string" || !value) return false;
    try {
      const requestedRoot = path.resolve(app.getPath("downloads"));
      const root = downloadsRoot();
      const resolved = path.resolve(value);
      if (!pathInside(requestedRoot, resolved) && !pathInside(root, resolved)) return false;
      if (fs.existsSync(resolved)) {
        if (fs.lstatSync(resolved).isSymbolicLink()) return false;
        return pathInside(root, fs.realpathSync(resolved));
      }
      if (mustExist) return false;
      return pathInsideOrEqual(root, fs.realpathSync(path.dirname(resolved)));
    } catch (_error) {
      return false;
    }
  }

  function validDownloadEntry(entry) {
    return !!entry
      && typeof entry.id === "string" && entry.id.length > 0
      && typeof entry.path === "string"
      && typeof entry.filename === "string" && entry.filename === path.basename(entry.path)
      && typeof entry.url === "string"
      && DOWNLOAD_STATES.has(entry.state)
      && [entry.receivedBytes, entry.totalBytes, entry.startedAt, entry.updatedAt]
        .every((value) => Number.isFinite(value) && value >= 0)
      && allowedDownloadPath(entry.path);
  }

  function publicDownloadEntry(entry) {
    return { ...entry, active: activeDownloads.has(entry.id) };
  }

  function loadDownloads() {
    downloads.clear();
    try {
      const parsed = JSON.parse(fs.readFileSync(downloadsFile(), "utf8"));
      for (const entry of Array.isArray(parsed?.entries) ? parsed.entries : []) {
        if (!validDownloadEntry(entry)) continue;
        downloads.set(entry.id, {
          ...entry,
          state: entry.state === "progressing" ? "interrupted" : entry.state,
        });
      }
    } catch (_error) {
      /* first run or malformed best-effort history */
    }
  }

  function saveDownloads() {
    const file = downloadsFile();
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const temporary = `${file}.${process.pid}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify({
      version: 1,
      entries: [...downloads.values()]
        .sort((a, b) => b.startedAt - a.startedAt)
        .slice(0, 1000),
    }));
    fs.renameSync(temporary, file);
  }

  function downloadEntry(id) {
    const entry = downloads.get(id);
    return entry ? publicDownloadEntry(entry) : null;
  }

  function broadcastDownload(entry) {
    for (const ctx of windows.values()) {
      if (!ctx.win.isDestroyed()) {
        ctx.win.webContents.send(
          "downloads:changed",
          entry ? publicDownloadEntry(entry) : null,
        );
      }
    }
  }

  function downloadReservationKey(value) {
    const normalized = path.resolve(value).normalize("NFD");
    return process.platform === "darwin" || process.platform === "win32"
      ? normalized.toUpperCase().normalize("NFD")
      : normalized;
  }

  function uniqueDownloadPath(filename) {
    const directory = downloadsRoot();
    const safeName = path.basename(filename || "download") || "download";
    const extension = path.extname(safeName);
    const stem = path.basename(safeName, extension);
    let candidate = path.join(directory, safeName);
    const unavailable = (value) => fs.existsSync(value)
      || [...downloads.values()].some(
        (entry) => activeDownloads.has(entry.id)
          && downloadReservationKey(entry.path) === downloadReservationKey(value),
      );
    for (let index = 1; unavailable(candidate); index += 1) {
      candidate = path.join(directory, `${stem} (${index})${extension}`);
    }
    return candidate;
  }

  function registerDownloads(targetSession = session.fromPartition("persist:webtabs")) {
    loadDownloads();
    targetSession.on("will-download", (_event, item) => {
      const id = crypto.randomUUID();
      const savePath = uniqueDownloadPath(item.getFilename());
      item.setSavePath(savePath);
      const entry = {
        id,
        filename: path.basename(savePath),
        path: savePath,
        url: item.getURL(),
        state: "progressing",
        receivedBytes: 0,
        totalBytes: Math.max(0, item.getTotalBytes()),
        startedAt: Date.now(),
        updatedAt: Date.now(),
      };
      downloads.set(id, entry);
      activeDownloads.set(id, item);
      try { saveDownloads(); } catch (_error) { /* best effort */ }
      broadcastDownload(entry);
      item.on("updated", (_itemEvent, state) => {
        if (!downloads.has(id)) return;
        entry.state = state === "interrupted" ? "interrupted" : "progressing";
        entry.receivedBytes = Math.max(0, item.getReceivedBytes());
        entry.totalBytes = Math.max(0, item.getTotalBytes());
        entry.updatedAt = Date.now();
        broadcastDownload(entry);
      });
      item.once("done", (_itemEvent, state) => {
        entry.state = state;
        entry.receivedBytes = Math.max(0, item.getReceivedBytes());
        entry.totalBytes = Math.max(0, item.getTotalBytes());
        entry.updatedAt = Date.now();
        activeDownloads.delete(id);
        try { saveDownloads(); } catch (_error) { /* best effort */ }
        broadcastDownload(entry);
      });
    });
  }

  return { downloadsRoot, pathInside, pathInsideOrEqual, allowedDownloadPath, validDownloadEntry, publicDownloadEntry, loadDownloads, saveDownloads, downloadEntry, broadcastDownload, downloadReservationKey, uniqueDownloadPath, registerDownloads };
}

module.exports = { createDownloads };
