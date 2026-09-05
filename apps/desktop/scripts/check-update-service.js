const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");

const {
  DesktopUpdateService,
  compareVersions,
  desktopAssetSpec,
  desktopUpdateFetch,
  downloadVerified,
  nextAutomaticCheckAt,
  normalizePersistedState,
  readStateFile,
  requestWithRedirects,
  resolveDesktopRelease,
  saveStateFileAtomic,
  validateUpdateUrl,
  writeAll,
} = require("../update-service");

assert.equal(compareVersions("0.6.7", "0.6.6"), 1);
assert.equal(compareVersions("0.6.6", "0.6.6"), 0);
assert.equal(compareVersions("0.6.6", "0.7.0"), -1);
assert.throws(() => compareVersions("main", "0.6.6"), /version/);

const release = {
  tag_name: "v0.6.7",
  name: "OpenProgram 0.6.7 Release",
  draft: false,
  prerelease: false,
  published_at: "2026-08-15T00:00:00Z",
  body: "Release notes",
  html_url: "https://github.com/Fzkuji/OpenProgram/releases/tag/v0.6.7",
  assets: [
    { name: "release-manifest.json", size: 100 },
    { name: "OpenProgram-0.6.7-mac-arm64-unsigned.dmg", size: 123 },
  ],
};
const manifest = {
  schema: 1,
  version: "0.6.7",
  files: [
    {
      path: "desktop-mac-arm64/OpenProgram-0.6.7-mac-arm64-unsigned.dmg",
      bytes: 123,
      sha256: "a".repeat(64),
    },
  ],
};

assert.deepEqual(desktopAssetSpec("0.6.7", "win32", "x64"), {
  name: "OpenProgram-0.6.7-win-x64.exe",
  kind: "windows-installer",
});
const windowsRelease = {
  ...release,
  assets: [
    ...release.assets,
    { name: "OpenProgram-0.6.7-win-x64.exe", size: 456 },
  ],
};
const windowsManifest = {
  ...manifest,
  files: [
    ...manifest.files,
    {
      path: "desktop-win-x64/OpenProgram-0.6.7-win-x64.exe",
      bytes: 456,
      sha256: "b".repeat(64),
    },
  ],
};
const windowsAvailable = resolveDesktopRelease(
  windowsRelease,
  windowsManifest,
  "0.6.6",
  "x64",
  "win32",
);
assert.equal(windowsAvailable.asset.name, "OpenProgram-0.6.7-win-x64.exe");
assert.equal(windowsAvailable.artifactKind, "windows-installer");
assert.deepEqual(desktopAssetSpec("0.6.7", "win32", "arm64"), {
  name: "OpenProgram-0.6.7-win-arm64.exe",
  kind: "windows-installer",
});

const available = resolveDesktopRelease(release, manifest, "0.6.6", "arm64");
assert.equal(available.status, "available");
assert.equal(available.latestVersion, "0.6.7");
assert.equal(available.asset.bytes, 123);
assert.equal(available.asset.sha256, "a".repeat(64));
assert.match(available.asset.url, /github\.com\/Fzkuji\/OpenProgram\/releases\/download\/v0\.6\.7/);

assert.equal(
  resolveDesktopRelease(release, manifest, "0.6.7", "arm64").status,
  "up-to-date",
);
assert.throws(
  () => resolveDesktopRelease(
    release,
    { ...manifest, files: [...manifest.files, { ...manifest.files[0], path: `duplicate/${manifest.files[0].path.split("/").pop()}` }] },
    "0.6.6",
    "arm64",
  ),
  /duplicate/,
);
assert.throws(
  () => resolveDesktopRelease({ ...release, prerelease: true }, manifest, "0.6.6", "arm64"),
  /prerelease/,
);
assert.throws(
  () => resolveDesktopRelease({ ...release, draft: undefined }, manifest, "0.6.6", "arm64"),
  /draft/,
);
assert.throws(
  () => resolveDesktopRelease({ ...release, prerelease: null }, manifest, "0.6.6", "arm64"),
  /prerelease/,
);

for (const url of [
  "https://api.github.com/repos/Fzkuji/OpenProgram/releases/latest",
  "https://github.com/Fzkuji/OpenProgram/releases/download/v0.6.7/release-manifest.json",
  "https://release-assets.githubusercontent.com/github-production-release-asset/1/file",
]) validateUpdateUrl(url);
assert.throws(() => validateUpdateUrl("http://github.com/file"), /HTTPS/);
assert.throws(() => validateUpdateUrl("https://example.com/file"), /host/);
assert.throws(() => validateUpdateUrl("https://user:pass@github.com/file"), /credentials/);

assert.equal(nextAutomaticCheckAt({ lastSuccessAt: 1_000, lastAttemptAt: 1_000 }), 1_000 + 24 * 3600_000);
assert.equal(nextAutomaticCheckAt({ lastSuccessAt: 0, lastAttemptAt: 2_000 }), 2_000 + 6 * 3600_000);

const defaults = normalizePersistedState({ schema: 99, automaticChecks: false });
assert.equal(defaults.schema, 1);
assert.equal(defaults.automaticChecks, true);

const root = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-update-state-"));
try {
  const statePath = path.join(root, "update-state.json");
  saveStateFileAtomic(statePath, {
    schema: 1,
    automaticChecks: false,
    lastAttemptAt: 1,
    lastSuccessAt: 1,
    release: available,
  });
  assert.equal(readStateFile(statePath).automaticChecks, false);
  assert.equal(readStateFile(statePath).release.latestVersion, "0.6.7");
  const upgradedApp = new DesktopUpdateService({
    currentVersion: "0.6.8",
    arch: "arm64",
    statePath,
    fetchImpl: async () => { throw new Error("not used"); },
    chooseSavePath: async () => null,
    openPath: async () => "",
  });
  assert.equal(upgradedApp.getState().status, "idle");
  assert.equal(upgradedApp.getState().release, null);
  assert.equal(JSON.parse(fs.readFileSync(statePath, "utf8")).release, null);
  saveStateFileAtomic(statePath, {
    schema: 1,
    automaticChecks: true,
    lastAttemptAt: 10,
    lastSuccessAt: 10,
    release: null,
  });
  const missingReleaseApp = new DesktopUpdateService({
    currentVersion: "0.6.6",
    arch: "arm64",
    statePath,
    fetchImpl: async () => { throw new Error("not used"); },
    chooseSavePath: async () => null,
    openPath: async () => "",
    now: () => 10,
  });
  assert.equal(missingReleaseApp.automaticCheckDueAt(), 0);
  assert.equal(JSON.parse(fs.readFileSync(statePath, "utf8")).lastSuccessAt, 0);
  saveStateFileAtomic(statePath, {
    schema: 1,
    automaticChecks: true,
    lastAttemptAt: 20,
    lastSuccessAt: 20,
    release: { ...available, releaseNotes: { invalid: true } },
  });
  const invalidReleaseApp = new DesktopUpdateService({
    currentVersion: "0.6.6",
    arch: "arm64",
    statePath,
    fetchImpl: async () => { throw new Error("not used"); },
    chooseSavePath: async () => null,
    openPath: async () => "",
    now: () => 20,
  });
  assert.equal(invalidReleaseApp.getState().release, null);
  saveStateFileAtomic(statePath, {
    schema: 1,
    automaticChecks: true,
    lastAttemptAt: 50,
    lastSuccessAt: 50,
    release: available,
  });
  const futureTimestampApp = new DesktopUpdateService({
    currentVersion: "0.6.6",
    arch: "arm64",
    statePath,
    fetchImpl: async () => { throw new Error("not used"); },
    chooseSavePath: async () => null,
    openPath: async () => "",
    now: () => 40,
  });
  assert.equal(futureTimestampApp.automaticCheckDueAt(), 0);
  fs.writeFileSync(statePath, "not-json");
  assert.equal(readStateFile(statePath).automaticChecks, true);
  new DesktopUpdateService({
    currentVersion: "0.6.6",
    arch: "arm64",
    statePath,
    fetchImpl: async () => { throw new Error("not used"); },
    chooseSavePath: async () => null,
    openPath: async () => "",
  });
  assert.equal(JSON.parse(fs.readFileSync(statePath, "utf8")).schema, 1);
  assert.equal(fs.readdirSync(root).some((name) => name.includes(".tmp-")), false);
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}

console.log("desktop update service checks passed");

const mainSource = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
const preloadSource = fs.readFileSync(path.join(__dirname, "..", "preload.js"), "utf8");
assert.match(mainSource, /new DesktopUpdateService/);
assert.match(mainSource, /fetchImpl:\s*desktopUpdateFetch/);
assert.doesNotMatch(mainSource, /fetchImpl:[\s\S]{0,200}net\.fetch/);
assert.match(String(desktopUpdateFetch), /\bfetch\(/);
assert.doesNotMatch(String(desktopUpdateFetch), /net\.fetch/);
assert.match(mainSource, /ipcMain\.handle\("updates:get-state"/);
assert.match(mainSource, /ipcMain\.handle\("updates:check"/);
assert.match(mainSource, /updates:check"[\s\S]*scheduleAutomaticUpdateCheck\(\)/);
assert.match(mainSource, /ipcMain\.handle\("updates:download"/);
assert.match(mainSource, /powerMonitor\.on\("resume"/);
assert.match(mainSource, /finally\s*{\s*scheduleAutomaticUpdateCheck\(\)/);
assert.match(mainSource, /webContents\.isDestroyed\(\)/);
assert.match(preloadSource, /updates:\s*\{/);
assert.match(preloadSource, /getState:\s*\(\)\s*=>\s*ipcRenderer\.invoke\("updates:get-state"\)/);
assert.doesNotMatch(preloadSource, /updates:download"\s*,/);

async function checkNetworkBoundaries() {
  const publicStateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-public-update-state-"));
  try {
    const publicStatePath = path.join(publicStateRoot, "update-state.json");
    saveStateFileAtomic(publicStatePath, {
      schema: 1,
      automaticChecks: true,
      lastAttemptAt: 1,
      lastSuccessAt: 1,
      release: available,
    });
    const service = new DesktopUpdateService({
      currentVersion: "0.6.6",
      arch: "arm64",
      statePath: publicStatePath,
      fetchImpl: async () => { throw new Error("not used"); },
      chooseSavePath: async () => null,
      openPath: async () => "",
      now: () => 2,
    });
    const publicState = service.getState();
    assert.equal("asset" in publicState.release, false);
    assert.equal(JSON.stringify(publicState).includes("sha256"), false);
    assert.equal(JSON.stringify(publicState).includes("releases/download"), false);
  } finally {
    fs.rmSync(publicStateRoot, { recursive: true, force: true });
  }

  const preferenceRoot = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-update-preference-"));
  try {
    const preferencePath = path.join(preferenceRoot, "state.json");
    saveStateFileAtomic(preferencePath, {
      schema: 1,
      automaticChecks: true,
      lastAttemptAt: 0,
      lastSuccessAt: 0,
      release: null,
    });
    const service = new DesktopUpdateService({
      currentVersion: "0.6.6",
      arch: "arm64",
      statePath: preferencePath,
      fetchImpl: async () => { throw new Error("not used"); },
      chooseSavePath: async () => null,
      openPath: async () => "",
    });
    const originalRename = fs.renameSync;
    try {
      fs.renameSync = () => { throw new Error("disk unavailable"); };
      assert.throws(() => service.setAutomaticChecks(false), /disk unavailable/);
    } finally {
      fs.renameSync = originalRename;
    }
    assert.equal(service.getState().automaticChecks, true);
    service.persist();
    assert.equal(JSON.parse(fs.readFileSync(preferencePath, "utf8")).automaticChecks, true);
  } finally {
    fs.rmSync(preferenceRoot, { recursive: true, force: true });
  }

  const allowedCalls = [];
  const allowedFetch = async (url) => {
    allowedCalls.push(url);
    if (allowedCalls.length === 1) {
      return new Response(null, {
        status: 302,
        headers: { location: "https://release-assets.githubusercontent.com/github-production-release-asset/1/file" },
      });
    }
    return new Response("ok", { status: 200 });
  };
  const response = await requestWithRedirects(
    allowedFetch,
    "https://github.com/Fzkuji/OpenProgram/releases/download/v0.6.7/file",
  );
  assert.equal(await response.text(), "ok");
  assert.equal(allowedCalls.length, 2);

  const redirectServer = http.createServer((req, res) => {
    if (req.url === "/start") {
      res.writeHead(302, {
        Location: "https://release-assets.githubusercontent.com/github-production-release-asset/1/file",
      });
      res.end();
      return;
    }
    res.writeHead(404);
    res.end();
  });
  await new Promise((resolve) => redirectServer.listen(0, "127.0.0.1", resolve));
  try {
    const address = redirectServer.address();
    const hop = await requestWithRedirects(
      async (requestUrl, options) => {
        assert.equal(options.redirect, "manual");
        if (new URL(requestUrl).hostname === "github.com") {
          return desktopUpdateFetch(`http://127.0.0.1:${address.port}/start`, options);
        }
        return new Response("ok", { status: 200 });
      },
      "https://github.com/Fzkuji/OpenProgram/releases/download/v0.7.1/release-manifest.json",
    );
    assert.equal(await hop.text(), "ok");
    const manual = await desktopUpdateFetch(`http://127.0.0.1:${address.port}/start`, {
      method: "GET",
      redirect: "manual",
    });
    assert.equal(manual.status, 302);
    assert.equal(
      manual.headers.get("location"),
      "https://release-assets.githubusercontent.com/github-production-release-asset/1/file",
    );
  } finally {
    await new Promise((resolve) => redirectServer.close(resolve));
  }

  await assert.rejects(
    requestWithRedirects(
      async () => new Response(null, { status: 302, headers: { location: "https://example.com/file" } }),
      "https://github.com/Fzkuji/OpenProgram/releases/download/v0.6.7/file",
    ),
    /host/,
  );
  await assert.rejects(
    requestWithRedirects(
      async () => new Response(null, { status: 302, headers: { location: "http://github.com/file" } }),
      "https://github.com/Fzkuji/OpenProgram/releases/download/v0.6.7/file",
    ),
    /HTTPS/,
  );
  await assert.rejects(
    requestWithRedirects(
      async () => new Response(null, { status: 302, headers: { location: "/next" } }),
      "https://github.com/Fzkuji/OpenProgram/releases/download/v0.6.7/file",
    ),
    /limit/,
  );
  let httpErrorAborted = false;
  await assert.rejects(
    requestWithRedirects(
      async (_url, options) => {
        options.signal.addEventListener("abort", () => { httpErrorAborted = true; });
        return new Response("error", { status: 500 });
      },
      "https://github.com/Fzkuji/OpenProgram/releases/download/v0.6.7/file",
    ),
    /HTTP 500/,
  );
  assert.equal(httpErrorAborted, true);

  const checkRoot = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-concurrent-check-"));
  try {
    let checkFetches = 0;
    const service = new DesktopUpdateService({
      currentVersion: "0.6.6",
      arch: "arm64",
      statePath: path.join(checkRoot, "state.json"),
      fetchImpl: async () => {
        checkFetches += 1;
        return new Response(JSON.stringify(checkFetches === 1 ? release : manifest));
      },
      chooseSavePath: async () => null,
      openPath: async () => "",
      now: () => 100,
    });
    const [left, right] = await Promise.all([
      service.check({ force: true }),
      service.check({ force: true }),
    ]);
    assert.equal(checkFetches, 2);
    assert.equal(left.status, "available");
    assert.deepEqual(left, right);
  } finally {
    fs.rmSync(checkRoot, { recursive: true, force: true });
  }

  const retryRoot = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-check-retry-"));
  try {
    let now = 1_000;
    let retryFetches = 0;
    const retryService = new DesktopUpdateService({
      currentVersion: "0.6.6",
      arch: "arm64",
      statePath: path.join(retryRoot, "state.json"),
      fetchImpl: async () => {
        retryFetches += 1;
        throw new Error("offline");
      },
      chooseSavePath: async () => null,
      openPath: async () => "",
      now: () => now,
    });
    assert.equal((await retryService.check({ force: true })).status, "error");
    assert.equal((await retryService.check()).status, "error");
    assert.equal(retryFetches, 1);
    now += 6 * 3600_000;
    await retryService.check();
    assert.equal(retryFetches, 2);
  } finally {
    fs.rmSync(retryRoot, { recursive: true, force: true });
  }

  const root = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-update-download-"));
  try {
    const bytes = Buffer.from("verified update");
    const target = path.join(root, "update.dmg");
    const asset = {
      url: "https://github.com/Fzkuji/OpenProgram/releases/download/v0.6.7/update.dmg",
      bytes: bytes.length,
      sha256: require("node:crypto").createHash("sha256").update(bytes).digest("hex"),
    };
    await downloadVerified(async () => new Response(bytes), asset, target);
    assert.deepEqual(fs.readFileSync(target), bytes);

    const blockedParent = path.join(root, "blocked-parent");
    fs.writeFileSync(blockedParent, "file");
    let openFailureAborted = false;
    await assert.rejects(
      downloadVerified(
        async (_url, options) => {
          options.signal.addEventListener("abort", () => { openFailureAborted = true; });
          return new Response(bytes);
        },
        asset,
        path.join(blockedParent, "update.dmg"),
      ),
    );
    assert.equal(openFailureAborted, true);

    const shortWriteTarget = path.join(root, "short-write.dmg");
    const handle = await fs.promises.open(shortWriteTarget, "w", 0o600);
    await writeAll({
      write: (buffer, offset, length) => handle.write(buffer, offset, Math.min(length, 3)),
    }, bytes);
    await handle.close();
    assert.deepEqual(fs.readFileSync(shortWriteTarget), bytes);

    const badTarget = path.join(root, "bad.dmg");
    await assert.rejects(
      downloadVerified(async () => new Response(bytes), { ...asset, sha256: "0".repeat(64) }, badTarget),
      /checksum/,
    );
    await assert.rejects(
      downloadVerified(
        async () => new Response(bytes),
        { ...asset, bytes: bytes.length + 1 },
        path.join(root, "bad-size.dmg"),
      ),
      /size/,
    );
    assert.equal(fs.existsSync(badTarget), false);
    assert.equal(fs.readdirSync(root).some((name) => name.includes(".part-")), false);

    const signatureStatePath = path.join(root, "signature-state.json");
    saveStateFileAtomic(signatureStatePath, {
      schema: 1,
      automaticChecks: true,
      lastAttemptAt: 1,
      lastSuccessAt: 1,
      release: {
        ...available,
        asset: {
          ...available.asset,
          bytes: bytes.length,
          sha256: asset.sha256,
        },
      },
    });
    const signatureTarget = path.join(root, "invalid-signature.dmg");
    let signatureOpenCount = 0;
    const signatureService = new DesktopUpdateService({
      currentVersion: "0.6.6",
      arch: "arm64",
      statePath: signatureStatePath,
      fetchImpl: async () => new Response(bytes),
      chooseSavePath: async () => signatureTarget,
      verifyArtifact: async () => { throw new Error("invalid signature"); },
      openPath: async () => { signatureOpenCount += 1; return ""; },
      now: () => 2,
    });
    assert.equal((await signatureService.download()).status, "error");
    assert.equal(signatureOpenCount, 0);
    assert.equal(fs.existsSync(signatureTarget), false);

    const originalSetTimeout = global.setTimeout;
    let serviceBodyCancelled = false;
    const serviceStalledBody = new ReadableStream({
      pull: () => new Promise(() => {}),
      cancel: () => { serviceBodyCancelled = true; },
    });
    const stalledService = new DesktopUpdateService({
      currentVersion: "0.6.6",
      arch: "arm64",
      statePath: path.join(root, "stalled-state.json"),
      fetchImpl: async (_url, options) => {
        options.signal.addEventListener("abort", () => { serviceBodyCancelled = true; });
        return new Response(serviceStalledBody);
      },
      chooseSavePath: async () => null,
      openPath: async () => "",
      now: () => 3,
    });
    try {
      global.setTimeout = (callback, _delay, ...args) => originalSetTimeout(callback, 5, ...args);
      assert.equal((await stalledService.check({ force: true })).status, "error");
    } finally {
      global.setTimeout = originalSetTimeout;
    }
    assert.equal(serviceBodyCancelled, true);

    const retryStatePath = path.join(root, "retry-state.json");
    const verifiedRelease = {
      ...available,
      asset: {
        ...asset,
        name: available.asset.name,
        url: available.asset.url,
      },
    };
    saveStateFileAtomic(retryStatePath, {
      schema: 1,
      automaticChecks: true,
      lastAttemptAt: 1,
      lastSuccessAt: 1,
      release: verifiedRelease,
    });
    let downloadFetches = 0;
    let opened = 0;
    const retryService = new DesktopUpdateService({
      currentVersion: "0.6.6",
      arch: "arm64",
      statePath: retryStatePath,
      fetchImpl: async () => {
        downloadFetches += 1;
        return new Response(downloadFetches === 1 ? Buffer.alloc(bytes.length) : bytes);
      },
      chooseSavePath: async () => path.join(root, "retry.dmg"),
      openPath: async () => { opened += 1; return ""; },
      now: () => 2,
    });
    const failedState = await retryService.download();
    assert.equal(failedState.status, "error");
    assert.equal(failedState.release.status, "available");
    assert.equal(opened, 0);
    const [completedState] = await Promise.all([
      retryService.download(),
      retryService.download(),
    ]);
    assert.equal(completedState.status, "downloaded");
    assert.equal(downloadFetches, 2);
    assert.equal(opened, 1);

    const cancelService = new DesktopUpdateService({
      currentVersion: "0.6.6",
      arch: "arm64",
      statePath: retryStatePath,
      fetchImpl: async () => { throw new Error("cancel must not fetch"); },
      chooseSavePath: async () => null,
      openPath: async () => { throw new Error("cancel must not open"); },
      now: () => 2,
    });
    assert.equal((await cancelService.download()).status, "available");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

const watchdog = setTimeout(() => {
  console.error("desktop update network checks timed out");
  process.exit(1);
}, 10_000);
checkNetworkBoundaries().then(
  () => {
    clearTimeout(watchdog);
    console.log("desktop update network checks passed");
  },
  (error) => {
    clearTimeout(watchdog);
    console.error(error);
    process.exitCode = 1;
  },
);
