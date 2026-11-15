const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");

const {
  DesktopUpdateService,
  compareVersions,
  desktopAssetNames,
  desktopAssetSpec,
  desktopUpdateFetch,
  downloadVerified,
  nextAutomaticCheckAt,
  normalizePersistedState,
  parseUpgradeOutcome,
  readStateFile,
  readUpdateSource,
  replaceAppImage,
  requestWithRedirects,
  resolveDesktopRelease,
  saveStateFileAtomic,
  validateUpdateUrl,
  writeAll,
} = require("../main/self-update/update-service");

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
  html_url: "https://github.com/FuzzyKuji/OpenProgram/releases/tag/v0.6.7",
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
const signedRelease = { ...release, assets: [...release.assets,
  { name: "OpenProgram-0.6.7-mac-arm64.dmg", size: 456 }] };
const signedManifest = { ...manifest, files: [...manifest.files,
  { path: "desktop-mac-arm64/OpenProgram-0.6.7-mac-arm64.dmg", bytes: 456, sha256: "b".repeat(64) }] };
const signedAvailable = resolveDesktopRelease(signedRelease, signedManifest, "0.6.6", "arm64");
assert.equal(signedAvailable.asset.bytes, 456);
assert.equal(signedAvailable.asset.sha256, "b".repeat(64));
assert.equal(available.status, "available");
assert.equal(available.latestVersion, "0.6.7");
assert.equal(available.asset.bytes, 123);
assert.equal(available.asset.sha256, "a".repeat(64));
assert.match(available.asset.url, /github\.com\/FuzzyKuji\/OpenProgram\/releases\/download\/v0\.6\.7/);

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
  "https://api.github.com/repos/FuzzyKuji/OpenProgram/releases/latest",
  "https://github.com/FuzzyKuji/OpenProgram/releases/download/v0.6.7/release-manifest.json",
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

const mainSource = require("../scripts/main-source").readMainSource();
const preloadSource = fs.readFileSync(path.join(__dirname, "..", "main", "preload.js"), "utf8");
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
    "https://github.com/FuzzyKuji/OpenProgram/releases/download/v0.6.7/file",
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
      "https://github.com/FuzzyKuji/OpenProgram/releases/download/v0.7.1/release-manifest.json",
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
      "https://github.com/FuzzyKuji/OpenProgram/releases/download/v0.6.7/file",
    ),
    /host/,
  );
  await assert.rejects(
    requestWithRedirects(
      async () => new Response(null, { status: 302, headers: { location: "http://github.com/file" } }),
      "https://github.com/FuzzyKuji/OpenProgram/releases/download/v0.6.7/file",
    ),
    /HTTPS/,
  );
  await assert.rejects(
    requestWithRedirects(
      async () => new Response(null, { status: 302, headers: { location: "/next" } }),
      "https://github.com/FuzzyKuji/OpenProgram/releases/download/v0.6.7/file",
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
      "https://github.com/FuzzyKuji/OpenProgram/releases/download/v0.6.7/file",
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
      updateSourceKeys: [],
      fetchImpl: async () => {
        retryFetches += 1;
        throw new Error("offline");
      },
      chooseSavePath: async () => null,
      openPath: async () => "",
      now: () => now,
    });
    assert.equal((await retryService.check({ force: true })).status, "error");
    assert.equal(retryFetches, 2, "the API, then the latest-release page");
    assert.equal((await retryService.check()).status, "error");
    assert.equal(retryFetches, 2);
    now += 6 * 3600_000;
    await retryService.check();
    assert.equal(retryFetches, 4);
  } finally {
    fs.rmSync(retryRoot, { recursive: true, force: true });
  }

  const root = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-update-download-"));
  try {
    const bytes = Buffer.from("verified update");
    const target = path.join(root, "update.dmg");
    const asset = {
      url: "https://github.com/FuzzyKuji/OpenProgram/releases/download/v0.6.7/update.dmg",
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
        () => {},
        { retryDelayMs: 0 },
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

function checkAssetNames() {
  assert.deepEqual(desktopAssetSpec("1.2.3", "linux", "x64", { linuxFormat: "appimage" }), {
    name: "OpenProgram-1.2.3-linux-x86_64.AppImage", kind: "appimage",
  });
  assert.deepEqual(desktopAssetSpec("1.2.3", "linux", "arm64", { linuxFormat: "appimage" }), {
    name: "OpenProgram-1.2.3-linux-arm64.AppImage", kind: "appimage",
  });
  assert.deepEqual(desktopAssetSpec("1.2.3", "linux", "x64"), {
    name: "OpenProgram-1.2.3-linux-amd64.deb", kind: "deb",
  });
  assert.deepEqual(desktopAssetSpec("1.2.3", "linux", "arm64"), {
    name: "OpenProgram-1.2.3-linux-arm64.deb", kind: "deb",
  });
  assert.deepEqual(desktopAssetNames("1.2.3", "win32", "x64"), ["OpenProgram-1.2.3-win-x64.exe"],
    "a signed Windows App never looks at an unsigned installer");
  assert.deepEqual(desktopAssetNames("1.2.3", "win32", "arm64", { windowsSignedOnly: false }),
    ["OpenProgram-1.2.3-win-arm64.exe", "OpenProgram-1.2.3-win-arm64-unsigned.exe"]);
  assert.deepEqual(desktopAssetNames("1.2.3", "darwin", "arm64"),
    ["OpenProgram-1.2.3-mac-arm64.dmg", "OpenProgram-1.2.3-mac-arm64-unsigned.dmg"]);
}

function releaseWith(version, names) {
  return {
    release: {
      tag_name: `v${version}`,
      name: `OpenProgram ${version} Release`,
      draft: false,
      prerelease: false,
      published_at: "2026-11-09T00:00:00Z",
      body: "Notes from the API",
      html_url: `https://github.com/FuzzyKuji/OpenProgram/releases/tag/v${version}`,
      assets: [{ name: "release-manifest.json", size: 10 }, ...names.map((name) => ({ name, size: 7 }))],
    },
    manifest: {
      schema: 1,
      version,
      files: names.map((name) => ({ path: `d/${name}`, bytes: 7, sha256: "c".repeat(64) })),
    },
  };
}

/** A GitHub stand-in: the API answers `api`, the latest page and the manifest are fixed. */
function githubStandIn({ release, manifest }, { api = 200, page = 302 } = {}) {
  const calls = [];
  const fetchImpl = async (requestUrl) => {
    calls.push(requestUrl);
    if (requestUrl.includes("api.github.com")) {
      return api === 200 ? new Response(JSON.stringify(release)) : new Response("limited", { status: api });
    }
    if (requestUrl.endsWith("/releases/latest")) {
      return page === 302
        ? new Response(null, { status: 302, headers: { location: release.html_url } })
        : new Response("down", { status: page });
    }
    if (requestUrl.endsWith("release-manifest.json")) return new Response(JSON.stringify(manifest));
    throw new Error(`unexpected request: ${requestUrl}`);
  };
  return { calls, fetchImpl };
}

function serviceIn(directory, options) {
  return new DesktopUpdateService({
    currentVersion: "1.0.0",
    arch: "x64",
    statePath: path.join(directory, `${Math.random()}.json`),
    chooseSavePath: async () => null,
    openPath: async () => "",
    now: () => 1_000,
    ...options,
  });
}

async function checkPlatformsAndFallbacks() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-update-platforms-"));
  try {
    // Linux picks by how it was installed.
    const linuxFiles = releaseWith("1.1.0", [
      "OpenProgram-1.1.0-linux-x86_64.AppImage", "OpenProgram-1.1.0-linux-amd64.deb",
    ]);
    const appImage = await serviceIn(directory, {
      platform: "linux", linuxFormat: "appimage", fetchImpl: githubStandIn(linuxFiles).fetchImpl,
    }).check({ force: true });
    assert.equal(appImage.status, "available");
    assert.equal(appImage.release.artifactKind, "appimage");
    const deb = await serviceIn(directory, {
      platform: "linux", fetchImpl: githubStandIn(linuxFiles).fetchImpl,
    }).check({ force: true });
    assert.equal(deb.release.artifactKind, "deb");

    // A release with nothing for this computer is its own answer, not a failure.
    const macOnly = releaseWith("1.1.0", ["OpenProgram-1.1.0-mac-arm64.dmg"]);
    const missing = serviceIn(directory, { platform: "linux", fetchImpl: githubStandIn(macOnly).fetchImpl });
    const missingState = await missing.check({ force: true });
    assert.equal(missingState.status, "not-published");
    assert.equal(missingState.error, null);
    assert.equal(missing.automaticCheckDueAt(), 1_000 + 6 * 3600_000, "checked again sooner than a full day");
    const unknownComputer = await serviceIn(directory, {
      platform: "linux", arch: "ppc64", fetchImpl: githubStandIn(macOnly).fetchImpl,
    }).check({ force: true });
    assert.equal(unknownComputer.status, "not-published");

    // Windows: the App's own build decides which installer counts.
    const unsignedWindows = releaseWith("1.1.0", ["OpenProgram-1.1.0-win-x64-unsigned.exe"]);
    const unsignedLine = await serviceIn(directory, {
      platform: "win32", windowsLine: async () => ({ signed: false }), fetchImpl: githubStandIn(unsignedWindows).fetchImpl,
    }).check({ force: true });
    assert.equal(unsignedLine.status, "available");
    const signedLine = await serviceIn(directory, {
      platform: "win32", windowsLine: async () => ({ signed: true }), fetchImpl: githubStandIn(unsignedWindows).fetchImpl,
    }).check({ force: true });
    assert.equal(signedLine.status, "not-published", "a signed App is not offered the unsigned installer");
    const bothWindows = releaseWith("1.1.0", [
      "OpenProgram-1.1.0-win-x64.exe", "OpenProgram-1.1.0-win-x64-unsigned.exe",
    ]);
    const preferSigned = serviceIn(directory, {
      platform: "win32", windowsLine: async () => ({ signed: false }), fetchImpl: githubStandIn(bothWindows).fetchImpl,
    });
    await preferSigned.check({ force: true });
    assert.equal(preferSigned.persisted.release.asset.name, "OpenProgram-1.1.0-win-x64.exe");

    // A saved answer survives a restart under either published name.
    for (const [platform, arch, name, kind] of [
      ["win32", "x64", "OpenProgram-1.1.0-win-x64-unsigned.exe", "windows-installer"],
      ["win32", "x64", "OpenProgram-1.1.0-win-x64.exe", "windows-installer"],
      ["darwin", "arm64", "OpenProgram-1.1.0-mac-arm64.dmg", "dmg"],
      ["darwin", "arm64", "OpenProgram-1.1.0-mac-arm64-unsigned.dmg", "dmg"],
      ["linux", "x64", "OpenProgram-1.1.0-linux-amd64.deb", "deb"],
    ]) {
      const statePath = path.join(directory, `cached-${name}.json`);
      const cached = {
        status: "available", currentVersion: "1.0.0", latestVersion: "1.1.0", publishedAt: "", releaseName: "x",
        releaseNotes: "", releaseUrl: "https://github.com/FuzzyKuji/OpenProgram/releases/tag/v1.1.0", artifactKind: kind,
        asset: {
          name, bytes: 7, sha256: "c".repeat(64),
          url: `https://github.com/FuzzyKuji/OpenProgram/releases/download/v1.1.0/${name}`,
        },
      };
      saveStateFileAtomic(statePath, { schema: 1, automaticChecks: true, lastAttemptAt: 1, lastSuccessAt: 1, release: cached });
      const restored = serviceIn(directory, { platform, arch, statePath, fetchImpl: async () => { throw new Error("unused"); } });
      assert.equal(restored.getState().release?.latestVersion, "1.1.0", name);
      saveStateFileAtomic(statePath, {
        schema: 1, automaticChecks: true, lastAttemptAt: 1, lastSuccessAt: 1,
        release: { ...cached, asset: { ...cached.asset, name: "something-else.exe" } },
      });
      assert.equal(
        serviceIn(directory, { platform, arch, statePath, fetchImpl: async () => { throw new Error("unused"); } }).getState().release,
        null,
      );
    }

    // A rate-limited API: the release page and manifest still answer. Only notes are lost.
    const standIn = githubStandIn(linuxFiles, { api: 403 });
    const limited = await serviceIn(directory, { platform: "linux", fetchImpl: standIn.fetchImpl }).check({ force: true });
    assert.equal(limited.status, "available");
    assert.equal(limited.release.latestVersion, "1.1.0");
    assert.equal(limited.release.releaseNotes, "");
    assert.equal(standIn.calls.length, 3);
    const overloaded = await serviceIn(directory, {
      platform: "linux", fetchImpl: githubStandIn(linuxFiles, { api: 503 }).fetchImpl,
    }).check({ force: true });
    assert.equal(overloaded.status, "available");

    // With the page gone too, the user is told plainly.
    const nothing = await serviceIn(directory, {
      platform: "linux", fetchImpl: githubStandIn(linuxFiles, { api: 403, page: 500 }).fetchImpl,
    }).check({ force: true });
    assert.equal(nothing.status, "error");
    assert.equal(nothing.errorKind, "rate-limited");
    assert.match(nothing.error, /Try again in about an hour/);
    const offline = await serviceIn(directory, {
      platform: "linux", fetchImpl: async () => { throw new TypeError("fetch failed"); },
    }).check({ force: true });
    assert.equal(offline.errorKind, "network");
    assert.match(offline.error, /Check your internet connection/);

    await checkUpdateSource(directory, linuxFiles);
    await checkVersionStore(directory);
    await checkResume(directory);
    checkChromiumPath();
    await checkChromiumRequest();
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }

  assert.deepEqual(parseUpgradeOutcome('noise\n{"ok":true,"reason":"activated"}\n', 0), { ok: true, reason: "activated" });
  assert.equal(parseUpgradeOutcome("Traceback", 1).ok, false);
  assert.match(parseUpgradeOutcome("Traceback", 1).detail, /Try again from Settings/);
}

async function checkUpdateSource(directory, files) {
  const crypto = require("node:crypto");
  const { publicKey, privateKey } = crypto.generateKeyPairSync("ed25519");
  const rawKey = publicKey.export({ format: "der", type: "spki" }).subarray(-32).toString("hex");
  const body = Buffer.from(JSON.stringify({ schema: 1, repository: "NewHome/OpenProgram", hosts: ["downloads.example.org"] }));
  const hexSignature = (data, key) => `${crypto.sign(null, data, key).toString("hex")}\n`;
  const fetchSource = (data, signature) => async (requestUrl) => {
    if (requestUrl.endsWith("/update-source.json")) return new Response(data);
    if (requestUrl.endsWith("/update-source.json.sig")) return new Response(signature);
    throw new Error(`unexpected request: ${requestUrl}`);
  };
  const signed = await readUpdateSource(fetchSource(body, hexSignature(body, privateKey)), [rawKey]);
  assert.equal(signed.repository, "NewHome/OpenProgram");
  assert.equal(signed.webRoot, "https://downloads.example.org");
  assert.equal(signed.allowedHosts.has("downloads.example.org"), true);
  assert.equal(signed.allowedHosts.has("github.com"), true);

  assert.equal(await readUpdateSource(fetchSource(body, "00".repeat(64)), [rawKey]), null, "an unverified file is ignored");
  assert.equal(await readUpdateSource(fetchSource(body, crypto.sign(null, body, privateKey)), [rawKey]), null, "only the hex form counts");
  const noHosts = Buffer.from(JSON.stringify({ schema: 1, repository: "NewHome/OpenProgram", hosts: [] }));
  assert.equal(await readUpdateSource(fetchSource(noHosts, hexSignature(noHosts, privateKey)), [rawKey]), null, "a source must name a host");
  const other = crypto.generateKeyPairSync("ed25519").privateKey;
  assert.equal(await readUpdateSource(fetchSource(body, hexSignature(body, other)), [rawKey]), null);
  assert.equal(await readUpdateSource(async () => new Response("", { status: 404 }), [rawKey]), null, "an absent file is ignored");
  let asked = 0;
  assert.equal(await readUpdateSource(async () => { asked += 1; return new Response(""); }, []), null);
  assert.equal(asked, 0, "with no trusted key nothing is fetched");

  // Asked only when GitHub fails, and then the new repository is used.
  const signature = hexSignature(body, privateKey);
  const moved = releaseWith("1.1.0", ["OpenProgram-1.1.0-linux-amd64.deb"]);
  const movedPage = "https://downloads.example.org/NewHome/OpenProgram/releases";
  const calls = [];
  const state = await serviceIn(directory, {
    platform: "linux",
    updateSourceKeys: [rawKey],
    fetchImpl: async (requestUrl) => {
      calls.push(requestUrl);
      if (requestUrl.endsWith("/update-source.json")) return new Response(body);
      if (requestUrl.endsWith("/update-source.json.sig")) return new Response(signature);
      if (requestUrl.includes("FuzzyKuji/OpenProgram")) return new Response("gone", { status: 404 });
      if (requestUrl === `${movedPage}/latest`) {
        return new Response(null, { status: 302, headers: { location: `${movedPage}/tag/v1.1.0` } });
      }
      if (requestUrl === `${movedPage}/download/v1.1.0/release-manifest.json`) {
        return new Response(JSON.stringify(moved.manifest));
      }
      throw new Error(`unexpected request: ${requestUrl}`);
    },
  }).check({ force: true });
  assert.equal(state.status, "available");
  assert.equal(state.release.releaseUrl, `${movedPage}/tag/v1.1.0`);
  assert.equal(calls.some((requestUrl) => requestUrl.includes("api.github.com/repos/NewHome")), false, "only the named host is asked");
  const healthy = githubStandIn(files);
  await serviceIn(directory, { platform: "linux", updateSourceKeys: [rawKey], fetchImpl: healthy.fetchImpl }).check({ force: true });
  assert.equal(healthy.calls.some((requestUrl) => requestUrl.includes("update-source")), false);
  assert.ok(calls.some((requestUrl) => requestUrl.includes("update-source")));
}

async function checkVersionStore(directory) {
  // The App's own file is not needed for an ordinary update.
  const noShellFile = releaseWith("1.1.0", ["OpenProgram-1.1.0-mac-arm64.dmg"]);
  let runs = 0;
  let current = "1.0.0";
  const service = serviceIn(directory, {
    platform: "linux",
    versionStore: true,
    fetchImpl: githubStandIn(noShellFile).fetchImpl,
    readCurrentVersion: () => current,
    applyVersionUpdate: async () => {
      runs += 1;
      current = "1.1.0";
      return { ok: true, reason: "activated", detail: "done" };
    },
  });
  assert.equal((await service.check({ force: true })).status, "available", "compared with the running release");
  const updated = await service.download();
  assert.equal(runs, 1);
  assert.equal(updated.status, "up-to-date");
  assert.equal(updated.currentVersion, "1.1.0");

  // The shell's own version is not what is compared.
  current = "1.1.0";
  const current110 = serviceIn(directory, {
    platform: "linux", versionStore: true, currentVersion: "1.0.0",
    fetchImpl: githubStandIn(noShellFile).fetchImpl, readCurrentVersion: () => current,
  });
  assert.equal((await current110.check({ force: true })).status, "up-to-date");

  // Only "needs a new shell" leads to the App's file.
  const withShell = releaseWith("1.1.0", ["OpenProgram-1.1.0-linux-amd64.deb"]);
  const bytes = Buffer.from("7 bytes");
  withShell.manifest.files[0].sha256 = require("node:crypto").createHash("sha256").update(bytes).digest("hex");
  const standIn = githubStandIn(withShell);
  const target = path.join(directory, "new-shell.deb");
  const opened = [];
  let applied = 0;
  const waiting = serviceIn(directory, {
    platform: "linux",
    versionStore: true,
    fetchImpl: async (requestUrl, options) => (requestUrl.endsWith(".deb") ? new Response(bytes) : standIn.fetchImpl(requestUrl, options)),
    readCurrentVersion: () => "1.0.0",
    chooseSavePath: async () => target,
    openPath: async (filePath) => { opened.push(filePath); return ""; },
    applyVersionUpdate: async () => {
      applied += 1;
      return { ok: false, reason: "waiting_for_shell", detail: "release 1.1.0 needs a newer signed shell" };
    },
  });
  await waiting.check({ force: true });
  const needs = await waiting.download();
  assert.equal(needs.status, "available");
  assert.match(needs.needsNewShell, /newer signed shell/);
  assert.deepEqual(opened, []);
  const afterShell = await waiting.download();
  assert.equal(afterShell.status, "downloaded");
  assert.equal(applied, 1, "the shell is fetched instead of asking again");
  assert.deepEqual(opened, [target]);

  const failing = serviceIn(directory, {
    platform: "linux", versionStore: true, fetchImpl: githubStandIn(noShellFile).fetchImpl, readCurrentVersion: () => "1.0.0",
    applyVersionUpdate: async () => ({ ok: false, reason: "release-refused", detail: "the signature does not match" }),
  });
  await failing.check({ force: true });
  const failed = await failing.download();
  assert.equal(failed.status, "error");
  assert.match(failed.error, /signature does not match/);
}

async function checkResume(directory) {
  const crypto = require("node:crypto");
  const bytes = Buffer.from("0123456789abcdefghij");
  const asset = {
    url: "https://github.com/FuzzyKuji/OpenProgram/releases/download/v1.1.0/app.dmg",
    bytes: bytes.length,
    sha256: crypto.createHash("sha256").update(bytes).digest("hex"),
  };
  const target = path.join(directory, "resumed.dmg");
  const ranges = [];
  let requests = 0;
  await downloadVerified(async (_url, options) => {
    requests += 1;
    ranges.push(options.headers.Range || null);
    if (requests === 1) {
      return new Response(new ReadableStream({
        start(controller) { controller.enqueue(bytes.subarray(0, 8)); },
        pull(controller) { controller.error(new TypeError("connection reset")); },
      }));
    }
    return new Response(bytes.subarray(8), {
      status: 206, headers: { "content-range": `bytes 8-${bytes.length - 1}/${bytes.length}` },
    });
  }, asset, target, () => {}, { retryDelayMs: 0 });
  assert.deepEqual(ranges, [null, "bytes=8-"], "the second request continues where the first stopped");
  assert.deepEqual(fs.readFileSync(target), bytes);
  assert.equal(fs.readdirSync(directory).some((name) => name.startsWith("resumed.dmg.") && name.endsWith(".part")), false);

  // A server that ignores the range sends everything, which replaces the partial file.
  const restartTarget = path.join(directory, "restarted.dmg");
  fs.writeFileSync(`${restartTarget}.${asset.sha256.slice(0, 16)}.part`, bytes.subarray(0, 5));
  await downloadVerified(async () => new Response(bytes), asset, restartTarget);
  assert.deepEqual(fs.readFileSync(restartTarget), bytes);

  // A partial file left by an earlier run counts; a wrong one is thrown away.
  const laterTarget = path.join(directory, "later.dmg");
  const laterPartial = `${laterTarget}.${asset.sha256.slice(0, 16)}.part`;
  fs.writeFileSync(laterPartial, bytes.subarray(0, 12));
  const seen = [];
  await downloadVerified(async (_url, options) => {
    seen.push(options.headers.Range);
    return new Response(bytes.subarray(12), {
      status: 206, headers: { "content-range": `bytes 12-${bytes.length - 1}/${bytes.length}` },
    });
  }, asset, laterTarget);
  assert.deepEqual(seen, ["bytes=12-"]);
  const damagedTarget = path.join(directory, "damaged.dmg");
  const damagedPartial = `${damagedTarget}.${asset.sha256.slice(0, 16)}.part`;
  fs.writeFileSync(damagedPartial, Buffer.from("XXXXXXXXXXXX"));
  await assert.rejects(downloadVerified(async () => new Response(bytes.subarray(12), {
    status: 206, headers: { "content-range": `bytes 12-${bytes.length - 1}/${bytes.length}` },
  }), asset, damagedTarget), /checksum/);
  assert.equal(fs.existsSync(damagedPartial), false, "a file that fails the check is not continued");

  // Refusals are final: no retry, nothing kept.
  let refused = 0;
  await assert.rejects(downloadVerified(async () => {
    refused += 1;
    return new Response("nope", { status: 404 });
  }, asset, path.join(directory, "missing.dmg"), () => {}, { retryDelayMs: 0 }), /HTTP 404/);
  assert.equal(refused, 1);

  // Retries end, and the partial file waits for the next try.
  const stubbornTarget = path.join(directory, "stubborn.dmg");
  let tries = 0;
  await assert.rejects(downloadVerified(async () => {
    tries += 1;
    throw new TypeError("fetch failed");
  }, asset, stubbornTarget, () => {}, { retryDelayMs: 0, attempts: 3 }), /fetch failed/);
  assert.equal(tries, 3);

  // An AppImage replaces the running file and stays runnable.
  const running = path.join(directory, "OpenProgram.AppImage");
  fs.writeFileSync(running, "old");
  const downloaded = path.join(directory, ".new.download");
  fs.writeFileSync(downloaded, "new", { mode: 0o600 });
  replaceAppImage(downloaded, running);
  assert.equal(fs.readFileSync(running, "utf8"), "new");
  assert.equal(fs.statSync(running).mode & 0o111, 0o111);
}

function checkChromiumPath() {
  // Every App request goes through the system-proxy stack when it is there.
  assert.match(String(desktopUpdateFetch), /net\?\.request/);
}

function fakeNet(script) {
  const made = [];
  return {
    made,
    request(options) {
      const { EventEmitter } = require("node:events");
      const emitter = new EventEmitter();
      const request = Object.assign(emitter, {
        options,
        headers: {},
        aborted: false,
        setHeader(name, value) { this.headers[name] = value; },
        abort() { this.aborted = true; },
        end() { setImmediate(() => script(this)); },
      });
      made.push(request);
      return request;
    },
  };
}

async function checkChromiumRequest() {
  const { Readable } = require("node:stream");
  const redirecting = fakeNet((request) => request.emit("redirect", 302, "GET", "https://release-assets.githubusercontent.com/x", {}));
  const redirect = await desktopUpdateFetch("https://github.com/x", {
    method: "GET", redirect: "manual", headers: { Range: "bytes=4-" },
  }, redirecting);
  assert.equal(redirect.status, 302, "the hop comes back to be checked instead of failing");
  assert.equal(redirect.headers.get("location"), "https://release-assets.githubusercontent.com/x");
  assert.equal(redirecting.made[0].options.redirect, "manual");
  assert.equal(redirecting.made[0].options.credentials, "omit");
  assert.equal(redirecting.made[0].headers.Range, "bytes=4-");
  assert.equal(redirecting.made[0].aborted, true);

  const streaming = fakeNet((request) => {
    const body = Readable.from([Buffer.from("he"), Buffer.from("llo")]);
    body.statusCode = 206;
    body.statusMessage = "Partial Content";
    body.headers = { "content-range": "bytes 0-4/5", "set-cookie": ["a=1", "b=2"] };
    request.emit("response", body);
  });
  const streamed = await desktopUpdateFetch("https://github.com/x", { method: "GET" }, streaming);
  assert.equal(streamed.status, 206);
  assert.equal(streamed.headers.get("content-range"), "bytes 0-4/5");
  assert.equal(await streamed.text(), "hello");

  const failing = fakeNet((request) => request.emit("error", new Error("ERR_PROXY_CONNECTION_FAILED")));
  await assert.rejects(desktopUpdateFetch("https://github.com/x", {}, failing), /PROXY/);

  const controller = new AbortController();
  const hanging = fakeNet(() => {});
  const pending = desktopUpdateFetch("https://github.com/x", { signal: controller.signal }, hanging);
  controller.abort();
  await assert.rejects(pending, /aborted/);
  assert.equal(hanging.made[0].aborted, true);

  // Through the service, a proxy that refuses the connection is a network error.
  const statePath = path.join(os.tmpdir(), `openprogram-proxy-${process.pid}.json`);
  try {
    const state = await new DesktopUpdateService({
      currentVersion: "1.0.0",
      arch: "x64",
      platform: "linux",
      statePath,
      fetchImpl: (requestUrl, options) => desktopUpdateFetch(requestUrl, options, fakeNet((request) => {
        request.emit("error", new Error("net::ERR_PROXY_CONNECTION_FAILED"));
      })),
      chooseSavePath: async () => null,
      openPath: async () => "",
    }).check({ force: true });
    assert.equal(state.errorKind, "network");
  } finally {
    fs.rmSync(statePath, { force: true });
  }
}

checkAssetNames();

const watchdog = setTimeout(() => {
  console.error("desktop update network checks timed out");
  process.exit(1);
}, 10_000);
checkNetworkBoundaries().then(checkPlatformsAndFallbacks).then(
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
