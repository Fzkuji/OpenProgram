// Self-check of main/web-views/browser-profile-import.js against synthetic browser
// profiles in a temporary directory; it never reads a real browser.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");
const {
  MAC_BROWSERS,
  browserDefinitions,
  listBrowserSources,
  readHistory,
  readBookmarks,
  electronCookie,
  importCookies,
  runBrowserImport,
  terminateChild,
} = require("../main/web-views/browser-profile-import");
const assert = require("node:assert");
const dir = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-import-check-"));
const apps = path.join(dir, "Applications");
const home = path.join(dir, "home");
const chromeApp = path.join(apps, MAC_BROWSERS[0].app);
const chromeRoot = path.join(home, MAC_BROWSERS[0].data);
const profile = path.join(chromeRoot, "Default");
fs.mkdirSync(path.dirname(chromeApp), { recursive: true });
fs.writeFileSync(chromeApp, "");
fs.mkdirSync(profile, { recursive: true });
fs.writeFileSync(path.join(chromeRoot, "Local State"), JSON.stringify({ profile: { info_cache: { Default: { name: "Person 1" } } } }));
fs.writeFileSync(path.join(profile, "Bookmarks"), JSON.stringify({ roots: {
  bookmark_bar: { type: "folder", name: "Bookmarks bar", children: [
    { type: "folder", name: "Research", children: [
      { type: "url", name: "Example", url: "https://example.com" },
      { type: "url", name: "Reject", url: "javascript:alert(1)" },
    ] },
  ] },
  other: { type: "folder", name: "Other bookmarks", children: [
    { type: "url", name: "Docs", url: "https://docs.example.com" },
  ] },
} }));
fs.writeFileSync(path.join(profile, "Cookies"), "");
const braveApp = path.join(home, "Applications", MAC_BROWSERS[1].app);
const braveProfile = path.join(home, MAC_BROWSERS[1].data, "Default");
fs.mkdirSync(path.dirname(braveApp), { recursive: true });
fs.writeFileSync(braveApp, "");
fs.mkdirSync(braveProfile, { recursive: true });
fs.writeFileSync(path.join(braveProfile, "History"), "");
const unindexedProfile = path.join(chromeRoot, "Profile 9");
fs.mkdirSync(unindexedProfile, { recursive: true });
fs.writeFileSync(path.join(unindexedProfile, "History"), "");
const { DatabaseSync } = require("node:sqlite");
const historyDb = new DatabaseSync(path.join(profile, "History"));
historyDb.exec("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT); CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER)");
historyDb.prepare("INSERT INTO urls VALUES (?, ?, ?)").run(1, "https://example.com/", "Example");
historyDb.prepare("INSERT INTO visits VALUES (?, ?, ?)").run(1, 1, 13344473600000000n);
historyDb.close();
(async () => {
  try {
    const options = { homeDir: home, applicationsDir: apps, platform: "darwin" };
    assert.deepStrictEqual(
      browserDefinitions({ ...options, platform: "linux" }),
      [],
      "profile discovery stays on supported Desktop platforms",
    );
    const windowsLocal = path.join(dir, "LocalAppData");
    const windowsPrograms = path.join(dir, "ProgramFiles");
    const windowsChrome = path.join(
      windowsPrograms,
      "Google",
      "Chrome",
      "Application",
      "chrome.exe",
    );
    const windowsChromeRoot = path.join(
      windowsLocal,
      "Google",
      "Chrome",
      "User Data",
    );
    fs.mkdirSync(path.dirname(windowsChrome), { recursive: true });
    fs.writeFileSync(windowsChrome, "");
    fs.mkdirSync(path.join(windowsChromeRoot, "Default"), { recursive: true });
    fs.writeFileSync(path.join(windowsChromeRoot, "Default", "History"), "");
    const windowsSources = listBrowserSources({
      platform: "win32",
      homeDir: home,
      localAppDataDir: windowsLocal,
      programFilesDir: windowsPrograms,
      programFilesX86Dir: path.join(dir, "ProgramFilesX86"),
    });
    assert.strictEqual(windowsSources[0].id, "chrome");
    assert.strictEqual(windowsSources[0].profiles[0].id, "Default");
    const listed = listBrowserSources(options);
    assert.strictEqual(listed[0].profiles[0].name, "Person 1");
    assert.strictEqual(listed[0].profiles[0].available.cookies, true);
    assert.ok(
      listed.some((item) => item.id === "brave"),
      "browsers installed under ~/Applications are discovered",
    );
    assert.ok(
      listed[0].profiles.some((item) => item.id === "Profile 9"),
      "on-disk Chromium profiles are discovered even when info_cache is stale",
    );
    const originalStatSync = fs.statSync;
    try {
      fs.statSync = (value) => {
        if (String(value).endsWith("Profile 9")) {
          throw Object.assign(new Error("I/O error"), { code: "EIO" });
        }
        return originalStatSync(value);
      };
      assert.ok(
        listBrowserSources(options)[0].profiles.some((item) => item.id === "Default"),
        "one unreadable profile does not hide valid profiles",
      );
    } finally {
      fs.statSync = originalStatSync;
    }
    assert.deepStrictEqual(readBookmarks(profile), [
      {
        kind: "folder",
        title: "Bookmarks bar",
        children: [{
          kind: "folder",
          title: "Research",
          children: [{ kind: "bookmark", title: "Example", url: "https://example.com/" }],
        }],
      },
      {
        kind: "folder",
        title: "Other bookmarks",
        children: [{ kind: "bookmark", title: "Docs", url: "https://docs.example.com/" }],
      },
    ]);
    assert.strictEqual(readHistory(profile, 1)[0].visitedAt, 1700000000000);

    for (const [input, expected] of [["Strict", "strict"], ["Lax", "lax"], ["None", "no_restriction"], [undefined, "unspecified"]]) {
      assert.strictEqual(electronCookie({ domain: ".example.com", name: "a", value: "b", secure: true, path: "/", sameSite: input }).sameSite, expected);
    }
    assert.strictEqual(
      Object.hasOwn(electronCookie({ domain: "example.com", name: "host", value: "1", path: "/" }), "domain"),
      false,
      "host-only cookies must stay host-only",
    );
    assert.strictEqual(
      electronCookie({ domain: ".example.com", name: "domain", value: "1", path: "/" }).domain,
      ".example.com",
      "domain cookies retain their domain scope",
    );
    assert.strictEqual(
      electronCookie({ domain: "example.com", name: "partitioned", value: "1", partitionKey: { topLevelSite: "https://top.test" } }),
      null,
      "partitioned cookies are not widened into ordinary cookies",
    );
    assert.strictEqual(electronCookie({ domain: "", name: "a", value: "b" }), null);

    const cookieCounts = await importCookies({}, { cookies: { set: async () => {} } }, async () => [
      { domain: ".example.com", name: "private-name", value: "private-value", secure: true, path: "/", sameSite: "Lax" },
    ]);
    assert.deepStrictEqual(cookieCounts, { imported: 1, failed: 0 });
    assert.doesNotMatch(JSON.stringify(cookieCounts), /private-name|private-value/);

    const cookieCancel = new AbortController();
    let cookieWrites = 0;
    await assert.rejects(
      importCookies(
        {},
        { cookies: { set: async () => {
          cookieWrites += 1;
          cookieCancel.abort();
        } } },
        async () => [
          { domain: ".example.com", name: "first", value: "1", path: "/" },
          { domain: ".example.com", name: "second", value: "2", path: "/" },
        ],
        cookieCancel.signal,
      ),
      (error) => error.code === "import_cancelled",
    );
    assert.strictEqual(cookieWrites, 1, "cancellation stops later cookie writes");

    const historyCancel = new AbortController();
    setImmediate(() => historyCancel.abort());
    await assert.rejects(
      runBrowserImport(
        { browserId: "chrome", profileId: "Default", items: ["history"] },
        { options, signal: historyCancel.signal },
      ),
      (error) => error.code === "import_cancelled",
    );

    const { EventEmitter } = require("node:events");
    const stubborn = new EventEmitter();
    stubborn.exitCode = null;
    stubborn.signals = [];
    stubborn.kill = (signal) => {
      stubborn.signals.push(signal);
      if (signal === "SIGKILL") {
        stubborn.exitCode = 0;
        queueMicrotask(() => stubborn.emit("close", 0));
      }
    };
    await terminateChild(stubborn, 1);
    assert.deepStrictEqual(stubborn.signals, ["SIGTERM", "SIGKILL"]);

    const cooperative = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { stdio: "ignore" });
    const killCooperative = cooperative.kill.bind(cooperative);
    const cooperativeSignals = [];
    cooperative.kill = (signal) => {
      cooperativeSignals.push(signal);
      return killCooperative(signal);
    };
    await terminateChild(cooperative, 1_000);
    assert.deepStrictEqual(cooperativeSignals, ["SIGTERM"]);

    fs.writeFileSync(path.join(profile, "Bookmarks"), "{");
    assert.throws(() => readBookmarks(profile), (error) => error.code === "bookmarks_invalid");
    fs.writeFileSync(path.join(profile, "History"), "broken");
    assert.throws(() => readHistory(profile), (error) => error.code === "history_read_failed");
    fs.rmSync(path.join(profile, "Cookies"));
    await assert.rejects(
      runBrowserImport({ browserId: "chrome", profileId: "Default", items: ["cookies"] }, { options }),
      (error) => error.code === "cookies_unavailable",
    );
    console.log("browser profile import self-check passed");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
