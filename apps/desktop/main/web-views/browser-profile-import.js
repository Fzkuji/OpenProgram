const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");

const MAX_HISTORY_IMPORT = 5000;
const MAX_BOOKMARK_IMPORT = 10000;
const CDP_TIMEOUT_MS = 12000;

function importError(code, message) {
  return Object.assign(new Error(message), { code });
}

function throwIfCancelled(signal) {
  if (signal?.aborted) throw importError("import_cancelled", "Browser import cancelled");
}

async function cancellationPoint(signal) {
  await new Promise((resolve) => setImmediate(resolve));
  throwIfCancelled(signal);
}

const MAC_BROWSERS = [
  {
    id: "chrome",
    name: "Google Chrome",
    app: "Google Chrome.app/Contents/MacOS/Google Chrome",
    data: "Library/Application Support/Google/Chrome",
  },
  {
    id: "brave",
    name: "Brave",
    app: "Brave Browser.app/Contents/MacOS/Brave Browser",
    data: "Library/Application Support/BraveSoftware/Brave-Browser",
  },
  {
    id: "edge",
    name: "Microsoft Edge",
    app: "Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    data: "Library/Application Support/Microsoft Edge",
  },
  {
    id: "chromium",
    name: "Chromium",
    app: "Chromium.app/Contents/MacOS/Chromium",
    data: "Library/Application Support/Chromium",
  },
];

const WINDOWS_BROWSERS = [
  {
    id: "chrome",
    name: "Google Chrome",
    executables: [
      ["localAppData", "Google", "Chrome", "Application", "chrome.exe"],
      ["programFiles", "Google", "Chrome", "Application", "chrome.exe"],
      ["programFilesX86", "Google", "Chrome", "Application", "chrome.exe"],
    ],
    data: ["Google", "Chrome", "User Data"],
  },
  {
    id: "edge",
    name: "Microsoft Edge",
    executables: [
      ["programFilesX86", "Microsoft", "Edge", "Application", "msedge.exe"],
      ["programFiles", "Microsoft", "Edge", "Application", "msedge.exe"],
      ["localAppData", "Microsoft", "Edge", "Application", "msedge.exe"],
    ],
    data: ["Microsoft", "Edge", "User Data"],
  },
  {
    id: "brave",
    name: "Brave",
    executables: [
      ["programFiles", "BraveSoftware", "Brave-Browser", "Application", "brave.exe"],
      ["programFilesX86", "BraveSoftware", "Brave-Browser", "Application", "brave.exe"],
      ["localAppData", "BraveSoftware", "Brave-Browser", "Application", "brave.exe"],
    ],
    data: ["BraveSoftware", "Brave-Browser", "User Data"],
  },
  {
    id: "chromium",
    name: "Chromium",
    executables: [
      ["localAppData", "Chromium", "Application", "chrome.exe"],
      ["programFiles", "Chromium", "Application", "chrome.exe"],
    ],
    data: ["Chromium", "User Data"],
  },
];

function readJson(filePath, fallback = null) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (_error) {
    return fallback;
  }
}

function isInside(parent, child) {
  const relative = path.relative(fs.realpathSync(parent), fs.realpathSync(child));
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function cookieFileForProfile(profilePath) {
  const candidates = [
    path.join(profilePath, "Cookies"),
    path.join(profilePath, "Network", "Cookies"),
  ];
  return candidates.find((candidate) => fs.existsSync(candidate)) || null;
}

function profileDirectories(dataRoot) {
  const state = readJson(path.join(dataRoot, "Local State"), {});
  const info = state?.profile?.info_cache;
  const names = info && typeof info === "object" ? info : {};
  const candidates = new Set(["Default", ...Object.keys(names)]);
  // Local State is an index, not the source of truth. It can lag behind a
  // profile directory created or restored by Chromium, so include ordinary
  // on-disk profiles even when info_cache does not mention them yet.
  try {
    for (const entry of fs.readdirSync(dataRoot, { withFileTypes: true })) {
      if (entry.isDirectory() && /^Profile \d+$/.test(entry.name)) {
        candidates.add(entry.name);
      }
    }
  } catch (_error) {
    /* an unreadable browser root has no additional discoverable profiles */
  }
  return [...candidates].flatMap((id) => {
    try {
      const profilePath = path.join(dataRoot, id);
      if (!fs.existsSync(profilePath) || !fs.statSync(profilePath).isDirectory()) return [];
      if (!isInside(dataRoot, profilePath)) return [];
      const hasHistory = fs.existsSync(path.join(profilePath, "History"));
      const hasBookmarks = fs.existsSync(path.join(profilePath, "Bookmarks"));
      const hasCookies = !!cookieFileForProfile(profilePath);
      if (!hasHistory && !hasBookmarks && !hasCookies) return [];
      return [{
        id,
        name: typeof names[id]?.name === "string" && names[id].name ? names[id].name : id,
        available: {
          history: hasHistory,
          bookmarks: hasBookmarks,
          cookies: hasCookies,
        },
      }];
    } catch (_error) {
      return [];
    }
  });
}

function browserDefinitions({
  homeDir = os.homedir(),
  applicationsDir = "/Applications",
  platform = process.platform,
  localAppDataDir = process.env.LOCALAPPDATA
    || path.join(homeDir, "AppData", "Local"),
  programFilesDir = process.env.ProgramFiles || "C:\\Program Files",
  programFilesX86Dir = process.env["ProgramFiles(x86)"] || "C:\\Program Files (x86)",
} = {}) {
  if (platform === "darwin") {
    const applicationRoots = [...new Set([applicationsDir, path.join(homeDir, "Applications")])];
    return MAC_BROWSERS.map((browser) => ({
      ...browser,
      executable: applicationRoots
        .map((root) => path.join(root, browser.app))
        .find((candidate) => fs.existsSync(candidate))
        || path.join(applicationsDir, browser.app),
      dataRoot: path.join(homeDir, browser.data),
    }));
  }
  if (platform === "win32") {
    const roots = {
      localAppData: localAppDataDir,
      programFiles: programFilesDir,
      programFilesX86: programFilesX86Dir,
    };
    return WINDOWS_BROWSERS.map((browser) => {
      const candidates = browser.executables.map(([root, ...parts]) =>
        path.join(roots[root], ...parts));
      return {
        ...browser,
        executable: candidates.find((candidate) => fs.existsSync(candidate))
          || candidates[0],
        dataRoot: path.join(localAppDataDir, ...browser.data),
      };
    });
  }
  return [];
}

function listBrowserSources(options) {
  return browserDefinitions(options).flatMap((browser) => {
    if (!fs.existsSync(browser.executable) || !fs.existsSync(browser.dataRoot)) return [];
    const profiles = profileDirectories(browser.dataRoot);
    return profiles.length ? [{ id: browser.id, name: browser.name, profiles }] : [];
  });
}

function resolveSource(browserId, profileId, options) {
  const browser = browserDefinitions(options).find((item) => item.id === browserId);
  if (!browser || !fs.existsSync(browser.executable) || !fs.existsSync(browser.dataRoot)) {
    throw Object.assign(new Error("source browser unavailable"), { code: "source_unavailable" });
  }
  const profile = profileDirectories(browser.dataRoot).find((item) => item.id === profileId);
  if (!profile) {
    throw Object.assign(new Error("source profile unavailable"), { code: "profile_unavailable" });
  }
  const profilePath = path.join(browser.dataRoot, profile.id);
  if (!isInside(browser.dataRoot, profilePath)) {
    throw Object.assign(new Error("source profile escaped browser root"), { code: "invalid_profile" });
  }
  return { browser, profile, profilePath };
}

function copyFileIfPresent(source, destination) {
  if (!fs.existsSync(source)) return false;
  const directoryOptions = { recursive: true };
  if (process.platform !== "win32") directoryOptions.mode = 0o700;
  fs.mkdirSync(path.dirname(destination), directoryOptions);
  fs.copyFileSync(source, destination);
  if (process.platform !== "win32") fs.chmodSync(destination, 0o600);
  return true;
}

function withCopiedDatabase(source, fn) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-browser-import-"));
  if (process.platform !== "win32") fs.chmodSync(dir, 0o700);
  const copy = path.join(dir, path.basename(source));
  try {
    for (const suffix of ["", "-wal", "-shm"]) {
      copyFileIfPresent(`${source}${suffix}`, `${copy}${suffix}`);
    }
    return fn(copy);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

function readHistory(profilePath, limit = MAX_HISTORY_IMPORT) {
  const source = path.join(profilePath, "History");
  if (!fs.existsSync(source)) throw importError("history_missing", "History file disappeared");
  try {
    return withCopiedDatabase(source, (copy) => {
      const { DatabaseSync } = require("node:sqlite");
      const db = new DatabaseSync(copy, { readOnly: true });
      try {
        const statement = db.prepare(`
          SELECT u.url AS url, u.title AS title, v.visit_time AS visit_time
          FROM visits v JOIN urls u ON u.id = v.url
          WHERE (u.url LIKE 'http://%' OR u.url LIKE 'https://%')
          ORDER BY v.visit_time DESC LIMIT ?
        `);
        statement.setReadBigInts(true);
        const rows = statement.all(
          BigInt(Math.max(1, Math.min(Number(limit) || MAX_HISTORY_IMPORT, MAX_HISTORY_IMPORT))),
        );
        return rows.map((row) => ({
          url: String(row.url || ""),
          title: String(row.title || "").slice(0, 500),
          faviconUrl: "",
          visitedAt: typeof row.visit_time === "bigint"
            ? Math.max(0, Number((row.visit_time - 11644473600000000n) / 1000n))
            : Math.max(0, Number(row.visit_time) / 1000 - 11644473600000),
        })).filter((row) => row.url && Number.isFinite(row.visitedAt));
      } finally {
        db.close();
      }
    });
  } catch (error) {
    if (error?.code === "history_missing") throw error;
    throw importError("history_read_failed", "History database could not be read");
  }
}

function readBookmarks(profilePath) {
  const bookmarkPath = path.join(profilePath, "Bookmarks");
  if (!fs.existsSync(bookmarkPath)) throw importError("bookmarks_missing", "Bookmarks file disappeared");
  let raw;
  try {
    raw = JSON.parse(fs.readFileSync(bookmarkPath, "utf8"));
  } catch (_error) {
    throw importError("bookmarks_invalid", "Bookmarks file is invalid");
  }
  if (!raw?.roots || typeof raw.roots !== "object") {
    throw importError("bookmarks_invalid", "Bookmarks roots are invalid");
  }
  let bookmarkCount = 0;
  const rootLabels = {
    bookmark_bar: "Bookmarks bar",
    other: "Other bookmarks",
    synced: "Mobile bookmarks",
  };
  const walk = (node, fallbackTitle = "Folder", depth = 0) => {
    if (!node || typeof node !== "object" || bookmarkCount >= MAX_BOOKMARK_IMPORT || depth > 64) {
      return null;
    }
    if (node.type === "url" && typeof node.url === "string") {
      try {
        const url = new URL(node.url);
        if (url.protocol === "http:" || url.protocol === "https:") {
          bookmarkCount += 1;
          return {
            kind: "bookmark",
            title: String(node.name || node.url).slice(0, 500),
            url: url.href,
          };
        }
      } catch (_error) {
        /* invalid imported URL */
      }
      return null;
    }
    if (!Array.isArray(node.children)) return null;
    const children = [];
    for (const child of node.children) {
      const imported = walk(child, "Folder", depth + 1);
      if (imported) children.push(imported);
      if (bookmarkCount >= MAX_BOOKMARK_IMPORT) break;
    }
    if (children.length === 0) return null;
    return {
      kind: "folder",
      title: String(node.name || fallbackTitle).slice(0, 500),
      children,
    };
  };
  return Object.entries(raw.roots)
    .map(([key, node]) => walk(node, rootLabels[key] || key, 0))
    .filter(Boolean);
}

function waitForFile(filePath, child, timeoutMs = CDP_TIMEOUT_MS, signal) {
  return new Promise((resolve, reject) => {
    const started = Date.now();
    let settled = false;
    let pollTimer = null;
    const finish = (callback, value) => {
      if (settled) return;
      settled = true;
      if (pollTimer !== null) clearTimeout(pollTimer);
      child.removeListener("error", onError);
      signal?.removeEventListener("abort", onAbort);
      callback(value);
    };
    const onAbort = () => finish(
      reject,
      importError("import_cancelled", "Browser import cancelled"),
    );
    const onError = () => finish(
      reject,
      importError("browser_start_failed", "source browser failed to start"),
    );
    if (signal?.aborted) return onAbort();
    signal?.addEventListener("abort", onAbort, { once: true });
    child.once("error", onError);
    const poll = () => {
      if (settled) return;
      if (fs.existsSync(filePath)) return finish(resolve);
      if (child.exitCode !== null) {
        return finish(reject, importError("browser_start_failed", "source browser exited before CDP started"));
      }
      if (Date.now() - started >= timeoutMs) {
        return finish(reject, importError("browser_start_timeout", "source browser CDP startup timed out"));
      }
      pollTimer = setTimeout(poll, 75);
    };
    poll();
  });
}

function cdpRequest(webSocketUrl, method, params = {}, timeoutMs = CDP_TIMEOUT_MS, signal) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(webSocketUrl);
    let settled = false;
    const finish = (callback, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      socket.close();
      callback(value);
    };
    const onAbort = () => finish(
      reject,
      importError("import_cancelled", "Browser import cancelled"),
    );
    const timer = setTimeout(() => {
      finish(reject, Object.assign(new Error("CDP request timed out"), { code: "cdp_timeout" }));
    }, timeoutMs);
    if (signal?.aborted) return onAbort();
    signal?.addEventListener("abort", onAbort, { once: true });
    socket.addEventListener("open", () => {
      socket.send(JSON.stringify({ id: 1, method, params }));
    });
    socket.addEventListener("message", (event) => {
      let payload;
      try { payload = JSON.parse(String(event.data)); } catch (_error) { return; }
      if (payload.id !== 1) return;
      if (payload.error) {
        finish(reject, Object.assign(new Error(payload.error.message || "CDP request failed"), { code: "cdp_failed" }));
      } else {
        finish(resolve, payload.result || {});
      }
    });
    socket.addEventListener("error", () => {
      finish(reject, Object.assign(new Error("CDP connection failed"), { code: "cdp_failed" }));
    });
  });
}

async function terminateChild(child, timeoutMs = 1_500) {
  if (!child || child.exitCode !== null || child.signalCode) return;
  const waitForClose = () => new Promise((resolve) => {
    if (child.exitCode !== null || child.signalCode) return resolve(true);
    const onClose = () => {
      clearTimeout(timer);
      resolve(true);
    };
    const timer = setTimeout(() => {
      child.removeListener("close", onClose);
      resolve(false);
    }, timeoutMs);
    child.once("close", onClose);
  });
  child.kill("SIGTERM");
  const closed = await waitForClose();
  if (!closed) {
    child.kill("SIGKILL");
    await waitForClose();
  }
}

async function readCookies(source, signal) {
  throwIfCancelled(signal);
  const cookieSource = cookieFileForProfile(source.profilePath);
  if (!cookieSource) throw importError("cookies_missing", "Cookies file disappeared");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-cookie-import-"));
  if (process.platform !== "win32") fs.chmodSync(root, 0o700);
  const profileCopy = path.join(root, source.profile.id);
  const cookieCopy = path.join(profileCopy, path.relative(source.profilePath, cookieSource));
  let child = null;
  try {
    copyFileIfPresent(path.join(source.browser.dataRoot, "Local State"), path.join(root, "Local State"));
    copyFileIfPresent(path.join(source.profilePath, "Preferences"), path.join(profileCopy, "Preferences"));
    for (const suffix of ["", "-wal", "-shm"]) {
      copyFileIfPresent(`${cookieSource}${suffix}`, `${cookieCopy}${suffix}`);
    }
    if (!fs.existsSync(cookieCopy)) throw importError("cookies_missing", "Cookies file disappeared");
    child = spawn(source.browser.executable, [
      `--user-data-dir=${root}`,
      `--profile-directory=${source.profile.id}`,
      "--headless=new",
      "--remote-debugging-port=0",
      "--disable-background-networking",
      "--disable-component-update",
      "--disable-default-apps",
      "--disable-extensions",
      "--disable-sync",
      "--metrics-recording-only",
      "--no-default-browser-check",
      "--no-first-run",
      "about:blank",
    ], { stdio: "ignore" });
    const activePort = path.join(root, "DevToolsActivePort");
    await waitForFile(activePort, child, CDP_TIMEOUT_MS, signal);
    throwIfCancelled(signal);
    const [port, browserPath] = fs.readFileSync(activePort, "utf8").trim().split(/\r?\n/);
    if (!/^\d+$/.test(port) || !browserPath?.startsWith("/")) {
      throw Object.assign(new Error("invalid CDP endpoint"), { code: "cdp_failed" });
    }
    const result = await cdpRequest(
      `ws://127.0.0.1:${port}${browserPath}`,
      "Storage.getCookies",
      {},
      CDP_TIMEOUT_MS,
      signal,
    );
    throwIfCancelled(signal);
    return Array.isArray(result.cookies) ? result.cookies : [];
  } catch (error) {
    if (["import_cancelled", "cookies_missing", "browser_start_failed", "browser_start_timeout", "cdp_timeout", "cdp_failed"].includes(error?.code)) {
      throw error;
    }
    throw importError("cookies_read_failed", "Cookies could not be read");
  } finally {
    await terminateChild(child);
    fs.rmSync(root, { recursive: true, force: true });
  }
}

function electronCookie(raw) {
  const domain = String(raw?.domain || "").replace(/^\./, "");
  if (!domain || typeof raw?.name !== "string" || typeof raw?.value !== "string") return null;
  // Electron 37 cannot express Chromium partition keys. Importing one as an
  // ordinary cookie would change which requests receive it, so skip it.
  if (raw.partitionKey || raw.partitionKeyOpaque) return null;
  const sameSite = raw.sameSite === "Strict"
    ? "strict"
    : raw.sameSite === "Lax"
      ? "lax"
      : raw.sameSite === "None"
        ? "no_restriction"
        : "unspecified";
  const cookie = {
    url: `${raw.secure ? "https" : "http"}://${domain}${raw.path || "/"}`,
    name: raw.name,
    value: raw.value,
    path: raw.path || "/",
    secure: !!raw.secure,
    httpOnly: !!raw.httpOnly,
    sameSite,
  };
  // Omitting `domain` preserves host-only cookies. A leading dot is the CDP
  // representation of a domain cookie and is safe to pass through.
  if (String(raw.domain).startsWith(".")) cookie.domain = raw.domain;
  if (Number(raw.expires) > 0) cookie.expirationDate = Number(raw.expires);
  return cookie;
}

async function importCookies(source, targetSession, read = readCookies, signal) {
  throwIfCancelled(signal);
  if (!targetSession?.cookies?.set) {
    throw Object.assign(new Error("target browser session unavailable"), { code: "target_unavailable" });
  }
  const sourceCookies = await read(source, signal);
  throwIfCancelled(signal);
  let imported = 0;
  let failed = 0;
  for (const raw of sourceCookies) {
    throwIfCancelled(signal);
    const cookie = electronCookie(raw);
    if (!cookie) { failed += 1; continue; }
    try {
      await targetSession.cookies.set(cookie);
      imported += 1;
    } catch (_error) {
      throwIfCancelled(signal);
      failed += 1;
    }
    throwIfCancelled(signal);
  }
  return { imported, failed };
}

async function runBrowserImport(
  { browserId, profileId, items },
  { targetSession, options, signal } = {},
) {
  throwIfCancelled(signal);
  const selected = new Set(Array.isArray(items) ? items : []);
  if (selected.size === 0 || [...selected].some((item) => !["history", "bookmarks", "cookies"].includes(item))) {
    throw Object.assign(new Error("unsupported import item"), { code: "invalid_items" });
  }
  const source = resolveSource(browserId, profileId, options);
  throwIfCancelled(signal);
  for (const item of selected) {
    if (!source.profile.available[item]) {
      throw importError(`${item}_unavailable`, `${item} is unavailable in the selected profile`);
    }
  }
  const result = {
    source: { browserId, profileId, label: `${source.browser.name} · ${source.profile.name}` },
    history: [],
    bookmarks: [],
    cookies: { imported: 0, failed: 0 },
  };
  if (selected.has("history")) {
    result.history = readHistory(source.profilePath);
    await cancellationPoint(signal);
  }
  if (selected.has("bookmarks")) {
    result.bookmarks = readBookmarks(source.profilePath);
    await cancellationPoint(signal);
  }
  if (selected.has("cookies")) {
    result.cookies = await importCookies(source, targetSession, readCookies, signal);
    await cancellationPoint(signal);
  }
  return result;
}

module.exports = {
  MAX_HISTORY_IMPORT,
  MAX_BOOKMARK_IMPORT,
  MAC_BROWSERS,
  browserDefinitions,
  listBrowserSources,
  readHistory,
  readBookmarks,
  electronCookie,
  importCookies,
  runBrowserImport,
  terminateChild,
};
