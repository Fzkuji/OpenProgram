const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");

const {
  DesktopUpdateService,
  compareVersions,
  hasShellLayer,
  shellLayerVersion,
  desktopAssetNames,
  desktopAssetSpec,
  desktopUpdateFetch,
  downloadVerified,
  normalizePersistedState,
  parseUpgradeOutcome,
  readStateFile,
  readUpdateSource,
  requestWithRedirects,
  resolveDesktopRelease,
  saveStateFileAtomic,
  validateUpdateUrl,
  writeAll,
} = require("../../main/self-update/update-service");
const { POSIX_SWAP_SCRIPT, WINDOWS_SWAP_SCRIPT, shellSwap, stagingRoot } = require("../../main/self-update/app-replace");
const { spawnSync } = require("node:child_process");
const crypto = require("node:crypto");

assert.equal(compareVersions("0.6.7", "0.6.6"), 1);
assert.equal(compareVersions("0.6.6", "0.6.6"), 0);
assert.equal(compareVersions("0.6.6", "0.7.0"), -1);
assert.throws(() => compareVersions("main", "0.6.6"), /version/);
assert.equal(compareVersions("1.2.1.1", "1.2.1"), 1, "a fourth part counts");
assert.equal(compareVersions("1.2.1.0", "1.2.1"), 0);
assert.equal(compareVersions("v1.2.2", "1.2.1.9"), 1);
assert.throws(() => compareVersions("1.2.1.1.1", "1.2.1"), /version/);

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
  assert.equal(missingReleaseApp.getState().release, null);
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
  assert.equal(futureTimestampApp.getState().release?.latestVersion, "0.6.7");
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

const mainSource = require("../../scripts/main-source").readMainSource();
const preloadSource = fs.readFileSync(path.join(__dirname, "..", "..", "main", "preload.js"), "utf8");
assert.match(mainSource, /new DesktopUpdateService/);
assert.match(mainSource, /fetchImpl:\s*desktopUpdateFetch/);
assert.doesNotMatch(mainSource, /fetchImpl:[\s\S]{0,200}net\.fetch/);
assert.match(String(desktopUpdateFetch), /\bfetch\(/);
assert.doesNotMatch(String(desktopUpdateFetch), /net\.fetch/);
assert.match(mainSource, /ipcMain\.handle\("updates:get-state"/);
assert.match(mainSource, /ipcMain\.handle\("updates:check"/);
assert.match(mainSource, /UPDATE_INITIAL_DELAY_MS = 30_000/);
assert.match(mainSource, /UPDATE_CHECK_INTERVAL_MS = 3600_000/);
assert.match(mainSource, /setInterval\(run, UPDATE_CHECK_INTERVAL_MS\)/);
assert.match(mainSource, /ipcMain\.handle\("updates:download"/);
assert.doesNotMatch(mainSource, /automaticCheckDueAt|scheduleAutomaticUpdateCheck/);
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
    const now = 1_000;
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
    // The hourly timer in main.js paces checks; each one asks again.
    assert.equal((await retryService.check()).status, "error");
    assert.equal(retryFetches, 4);
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
    ["OpenProgram-1.2.3-mac-arm64.dmg", "OpenProgram-1.2.3-mac-arm64-unsigned.dmg"],
    "a Mac never downloads its App whole: a version-store App takes its shell layer");
}

/**
 * The swap waits for the App to exit, puts the new bundle in place, drops the
 * old one and only then leaves the marker the new App switches on; a failed
 * second rename puts the old App back and leaves no marker.
 */
function checkPosixSwap(directory) {
  const root = path.join(directory, "swap");
  const app = path.join(root, "OpenProgram.app");
  const staged = path.join(stagingRoot(app), "staged", "OpenProgram.app");
  const marker = path.join(root, "activate");
  fs.mkdirSync(path.join(app, "Contents"), { recursive: true });
  fs.mkdirSync(path.join(staged, "Contents"), { recursive: true });
  fs.writeFileSync(path.join(app, "Contents", "version"), "old");
  fs.writeFileSync(path.join(staged, "Contents", "version"), "new");
  const swap = (from) => spawnSync("/bin/sh", ["-c", POSIX_SWAP_SCRIPT, "swap", "999999", app, from, "true", marker, "1.1.0#42"],
    { encoding: "utf8" });
  const result = swap(staged);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.readFileSync(path.join(app, "Contents", "version"), "utf8"), "new");
  assert.equal(fs.existsSync(staged), false);
  assert.equal(fs.readFileSync(marker, "utf8"), "1.1.0#42");
  assert.deepEqual(fs.readdirSync(root).sort(), ["OpenProgram.app", "activate"], "the staging folder is gone");
  fs.rmSync(marker);
  assert.equal(swap(path.join(stagingRoot(app), "staged", "missing.app")).status, 0);
  assert.equal(fs.readFileSync(path.join(app, "Contents", "version"), "utf8"), "new", "the App is put back");
  assert.equal(fs.existsSync(marker), false, "no switch after a failed swap");
  assert.deepEqual(fs.readdirSync(root).sort(), ["OpenProgram.app"]);

  // An AppImage is one file, renamed over the running one and started itself.
  const image = path.join(root, "OpenProgram.AppImage");
  const newImage = path.join(stagingRoot(image), "staged", "OpenProgram.AppImage");
  const ran = path.join(root, "ran");
  fs.writeFileSync(image, "#!/bin/sh\necho old > \"$(dirname \"$0\")/ran\"\n", { mode: 0o755 });
  fs.mkdirSync(path.dirname(newImage), { recursive: true });
  fs.writeFileSync(newImage, "#!/bin/sh\necho new > \"$(dirname \"$0\")/ran\"\n", { mode: 0o755 });
  const result2 = spawnSync("/bin/sh", ["-c", POSIX_SWAP_SCRIPT, "swap", "999999", image, newImage, "", marker, "1.1.0#43"],
    { encoding: "utf8" });
  assert.equal(result2.status, 0, result2.stderr);
  for (let i = 0; i < 50 && !fs.existsSync(ran); i += 1) spawnSync("sleep", ["0.1"]);
  assert.equal(fs.readFileSync(ran, "utf8").trim(), "new", "the new AppImage is started");
  assert.equal(fs.statSync(image).mode & 0o111, 0o111);
  assert.equal(fs.readFileSync(marker, "utf8"), "1.1.0#43");
  assert.equal(fs.existsSync(stagingRoot(image)), false);
}

/**
 * The staged App the updater recorded: taken only when it replaces this App,
 * sits in the runtime's staging folder and is newer; install hands the swap
 * script the release key and quits.
 */
function checkShellSwap(directory) {
  const runtime = path.join(directory, "shell-runtime");
  const bundle = path.join(directory, "Applications", "OpenProgram.app");
  const execPath = path.join(bundle, "Contents", "MacOS", "OpenProgram");
  const staged = path.join(stagingRoot(bundle), "staged", "OpenProgram.app");
  fs.mkdirSync(path.join(runtime, "shell"), { recursive: true });
  fs.mkdirSync(path.dirname(execPath), { recursive: true });
  fs.mkdirSync(path.join(staged, "Contents", "MacOS"), { recursive: true });
  fs.writeFileSync(path.join(staged, "Contents", "MacOS", "OpenProgram"), "new");
  const write = (record) => fs.writeFileSync(path.join(runtime, "shell", "staged.json"), JSON.stringify({
    path: staged, version: "1.1.0", app: bundle, executable: "Contents/MacOS/OpenProgram", ...record,
  }));
  const started = [];
  let quits = 0;
  const swap = shellSwap({
    runtimeRoot: runtime, execPath, platform: "darwin", isNewer: (version) => compareVersions(version, "1.0.0") > 0,
    quit: () => { quits += 1; }, pid: 4242,
    start: (command, args) => { started.push([command, ...args]); return { unref() {} }; },
  });
  assert.equal(swap.staged(), null, "nothing staged");
  write({});
  assert.deepEqual(swap.staged(), { path: staged, version: "1.1.0", executable: "Contents/MacOS/OpenProgram" });
  write({ version: "1.0.0" });
  assert.equal(swap.staged(), null, "not newer than the running App");
  write({ app: path.join(directory, "Elsewhere.app") });
  assert.equal(swap.staged(), null, "staged for another App");
  write({ path: path.join(directory, "OpenProgram.app") });
  assert.equal(swap.staged(), null, "outside the App's staging folder");
  write({});
  assert.equal(swap.install(swap.staged(), "1.1.0#42"), "");
  assert.equal(quits, 1);
  const [[command, , , , pid, app, from, opener, marker, key]] = started;
  assert.equal(command, "/bin/sh");
  assert.deepEqual([pid, app, from, opener, key], ["4242", bundle, staged, "open", "1.1.0#42"]);
  assert.equal(marker, path.join(runtime, "shell", "activate"));
  fs.writeFileSync(marker, "1.1.0#42");
  assert.equal(swap.takeActivation(), "1.1.0#42");
  assert.equal(swap.takeActivation(), null, "taken once");
  assert.equal(shellSwap({ runtimeRoot: runtime, execPath, platform: "linux", isNewer: () => true, quit() {} }), null,
    "a deb replaces nothing itself");

  // An AppImage: the staged file sits beside it and the script starts it itself.
  const image = path.join(directory, "Apps", "OpenProgram-1.0.0.AppImage");
  const stagedImage = path.join(stagingRoot(image), "staged", path.basename(image));
  fs.mkdirSync(path.dirname(stagedImage), { recursive: true });
  fs.writeFileSync(image, "old");
  fs.writeFileSync(stagedImage, "new");
  fs.writeFileSync(path.join(runtime, "shell", "staged.json"), JSON.stringify({
    path: stagedImage, version: "1.1.0", app: image, executable: "",
  }));
  started.length = 0;
  const linux = shellSwap({
    runtimeRoot: runtime, execPath: "/tmp/.mount_x/openprogram", platform: "linux", appImage: image,
    isNewer: (version) => compareVersions(version, "1.0.0") > 0, quit() {},
    start: (command, args) => { started.push([command, ...args]); return { unref() {} }; },
  });
  assert.deepEqual(linux.staged(), { path: stagedImage, version: "1.1.0", executable: "" });
  linux.install(linux.staged(), "1.1.0#42");
  assert.deepEqual(started[0].slice(5, 8), [image, stagedImage, ""], "no opener: the AppImage runs itself");
}

/**
 * The Windows swap undoes only its two renames: a failed registry or marker
 * write after the new App is in place never moves the old one back.
 */
function checkWindowsSwapScript() {
  const script = WINDOWS_SWAP_SCRIPT;
  const swapped = script.indexOf("$Swapped = $true");
  const rollback = script.indexOf("Move-Item -LiteralPath $Old -Destination $App");
  const after = script.indexOf("if ($Swapped) {");
  assert.ok(swapped > 0 && swapped < rollback && rollback < after, "the rollback guards the renames alone");
  assert.ok(script.indexOf("DisplayVersion") > after, "the uninstall entry is written after the swap");
  assert.match(script.slice(after), /try \{ if \(\$Marker\) \{ \[System\.IO\.File\]::WriteAllText\(\$Marker, \$Key\) \} \} catch \{ \}/);
  assert.equal(script.slice(after).includes("-Destination $App"), false, "nothing after the swap moves the App");
}

/**
 * Whether this App swaps its shell is the updater's answer: once it said no,
 * a new shell is the installer for this App, at every check, until the App changes.
 */
function checkShellSwapVerdict(directory) {
  const statePath = path.join(directory, "verdict.json");
  const swap = { id: "/Applications/OpenProgram.app@1.0.0", isNewer: () => true };
  const service = serviceIn(directory, { statePath, versionStore: true, shellSwap: swap });
  assert.equal(service.swapsShell(), swap, "until the updater answers, the App may swap");
  service.noteShellSwap({ ok: false, reason: "waiting_for_shell", shell_swap: false });
  assert.equal(service.swapsShell(), null);
  const again = serviceIn(directory, { statePath, versionStore: true, shellSwap: swap });
  assert.equal(again.swapsShell(), null, "kept across starts: no flip-flop at the next check");
  const updated = serviceIn(directory, { statePath, versionStore: true, shellSwap: { ...swap, id: "/Applications/OpenProgram.app@1.1.0" } });
  assert.equal(updated.swapsShell()?.id, "/Applications/OpenProgram.app@1.1.0", "another App asks again");
  service.noteShellSwap({ ok: true, shell_swap: true });
  assert.equal(service.swapsShell(), swap);
  assert.equal(normalizePersistedState({ schema: 1, shellSwap: { app: 3 } }).shellSwap, null);
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
      html_url: `https://github.com/Fzkuji/OpenProgram/releases/tag/v${version}`,
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
        releaseNotes: "", releaseUrl: "https://github.com/Fzkuji/OpenProgram/releases/tag/v1.1.0", artifactKind: kind,
        asset: {
          name, bytes: 7, sha256: "c".repeat(64),
          url: `https://github.com/Fzkuji/OpenProgram/releases/download/v1.1.0/${name}`,
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
    await checkVersionPrepare(directory);
    await checkChannel(directory);
    await checkResume(directory);
    checkPosixSwap(directory);
    checkShellSwap(directory);
    checkWindowsSwapScript();
    checkShellSwapVerdict(directory);
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
      if (requestUrl.includes("Fzkuji/OpenProgram")) return new Response("gone", { status: 404 });
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

/** With automatic checks on, a version-store update is prepared in the background and switched to on restart. */
async function checkVersionPrepare(directory) {
  const release = releaseWith("1.1.0", ["OpenProgram-1.1.0-mac-arm64.dmg"]);
  let current = "1.0.0";
  const calls = [];
  let relaunched = 0;
  const options = (extra) => ({
    platform: "linux",
    versionStore: true,
    fetchImpl: githubStandIn(release).fetchImpl,
    readCurrentVersion: () => current,
    prepareVersionUpdate: async () => {
      calls.push("prepare");
      return { ok: true, outcome: "prepared", prepared: true, applied: false };
    },
    applyVersionUpdate: async () => {
      calls.push("apply");
      current = "1.1.0";
      return { ok: true, outcome: "activated", applied: true };
    },
    relaunchApp: () => { relaunched += 1; },
    ...extra,
  });
  // A check only finds the update; nothing is fetched until asked.
  const service = serviceIn(directory, options());
  service.setAutomaticChecks(true);
  assert.equal((await service.check({ force: true })).status, "available");
  assert.ok(!service.downloadPromise, "a check downloads nothing");
  assert.deepEqual(calls, []);
  assert.equal((await service.download()).status, "ready", "Download update prepares it");
  const ready = service.getState();
  assert.equal(ready.readyVersion, "1.1.0");
  assert.deepEqual(calls, ["prepare"], "nothing is switched before the restart");
  const installed = await service.install();
  assert.deepEqual(calls, ["prepare", "apply"]);
  assert.equal(relaunched, 1, "the App starts again on the new version");
  assert.equal(installed.currentVersion, "1.1.0");
  assert.equal(installed.readyVersion, null);

  // Automatic checks off: found, shown, nothing fetched until asked.
  current = "1.0.0";
  calls.length = 0;
  const manual = serviceIn(directory, options());
  manual.setAutomaticChecks(false);
  assert.equal((await manual.check({ force: true })).status, "available");
  assert.ok(!manual.downloadPromise, "nothing starts on its own");
  assert.deepEqual(calls, []);
  assert.equal((await manual.download()).status, "ready", "a manual download also stops before the switch");
  assert.deepEqual(calls, ["prepare"]);

  // A failed switch reports it and does not restart.
  relaunched = 0;
  const broken = serviceIn(directory, options({
    applyVersionUpdate: async () => ({ ok: false, outcome: "rolled_back", detail: "the new version did not start" }),
  }));
  broken.setAutomaticChecks(true);
  await broken.check({ force: true });
  await broken.download();
  const failed = await broken.install();
  assert.equal(failed.status, "error");
  assert.match(failed.error, /did not start/);
  assert.equal(relaunched, 0);
}

async function checkChannel(directory) {
  const crypto = require("node:crypto");
  const { publicKey, privateKey } = crypto.generateKeyPairSync("ed25519");
  const rawKey = publicKey.export({ format: "der", type: "spki" }).subarray(-32).toString("hex");
  const channel = {
    schema: 2, pointer: "channel", sequence: 42, published_at: "2026-11-26T08:00:00Z", version: "1.0.1", announce: true,
    build: "d".repeat(12), notes: "release notes", commit: "d".repeat(40), shell_compat: 1, layers: {}, packs: {},
  };
  const asked = [];
  // A pointer file as channel.py's `seal` writes it: the signed text with its signature.
  const seal = (value, signedBy = privateKey) => {
    const text = `${JSON.stringify(value, null, 1)}\n`;
    return { signed: text, signature: crypto.sign(null, Buffer.from(text, "utf8"), signedBy).toString("hex") };
  };
  const standIn = (value, { signedBy = privateKey, stable = releaseWith("1.0.0", []), envelope = null, missing = false } = {}) => {
    const data = Buffer.from(JSON.stringify(envelope || seal(value, signedBy), null, 1));
    const github = githubStandIn(stable);
    return async (requestUrl, options) => {
      asked.push(requestUrl);
      if (requestUrl === "https://github.com/Fzkuji/OpenProgram/releases/download/updates/channel.json") {
        return missing ? new Response("Not Found", { status: 404 }) : new Response(data);
      }
      return github.fetchImpl(requestUrl, options);
    };
  };
  let running = { sequence: 40, shell_compat: 1, shellCompat: { min: 1, max: 1 } };
  let outcome = { ok: true, outcome: "activated", detail: "done", applied: true };
  let applied = 0;
  const statePath = path.join(directory, "channel-state.json");
  const make = (fetchImpl, options = {}) => new DesktopUpdateService({
    currentVersion: "1.0.0", arch: "x64", platform: "linux", statePath, now: () => 1_000,
    chooseSavePath: async () => null, openPath: async () => "", updateSourceKeys: [rawKey],
    versionStore: true, readCurrentVersion: () => "1.0.0", readRunningBuild: () => running,
    applyVersionUpdate: async () => {
      applied += 1;
      if (outcome.applied) running = { ...running, sequence: 42 };
      return outcome;
    },
    fetchImpl, ...options,
  });

  const found = await make(standIn(channel)).check({ force: true });
  assert.equal(found.status, "available", "a higher sequence than the running build is an update");
  assert.equal(found.release.latestVersion, "1.0.1");
  assert.equal(found.release.releaseNotes, "release notes");
  assert.equal(found.release.releaseUrl, "https://github.com/Fzkuji/OpenProgram/releases/tag/v1.0.1");
  assert.equal(asked.some((requestUrl) => requestUrl.includes("api.github.com") || requestUrl.endsWith("/releases/latest")), false,
    "only channel.json is read");
  assert.equal(asked.some((requestUrl) => requestUrl.endsWith(".sig")), false, "the signature travels inside channel.json");
  assert.equal(readStateFile(statePath).channelSequence, 42);
  const reopened = make(standIn(channel));
  assert.equal(reopened.getState().status, "available", "the cached channel update survives a restart");
  const done = await reopened.download();
  assert.equal(applied, 1);
  assert.equal(done.status, "up-to-date");
  assert.equal(make(standIn(channel)).getState().status, "up-to-date");
  assert.equal((await make(standIn(channel)).check({ force: true })).status, "up-to-date", "the running sequence is current");

  // A replayed, older channel is no update and no error.
  running = { ...running, sequence: 30 };
  const replay = await make(standIn({ ...channel, sequence: 41 })).check({ force: true });
  assert.equal(replay.status, "up-to-date");
  assert.equal(replay.error, null);
  assert.equal(readStateFile(statePath).channelSequence, 42, "the highest sequence is kept");

  // A missing channel.json (between replacing uploads) is nothing new, silently.
  const absent = await make(standIn(channel, { missing: true })).check({ force: true });
  assert.equal(absent.status, "up-to-date");
  assert.equal(absent.error, null);
  assert.equal(absent.errorKind, null);

  // A four-part version; an unknown running sequence is older.
  fs.rmSync(statePath, { force: true });
  running = { sequence: null, shell_compat: 1, shellCompat: null };
  const patch = await make(standIn({ ...channel, sequence: 50, version: "1.0.1.1" })).check({ force: true });
  assert.equal(patch.status, "available");
  assert.equal(patch.release.latestVersion, "1.0.1.1");
  assert.equal(patch.release.releaseUrl, "https://github.com/Fzkuji/OpenProgram/releases/tag/v1.0.1.1");
  assert.equal(make(standIn(channel)).getState().release.latestVersion, "1.0.1.1", "a four-part version is cached");

  // Waiting for this computer's runtime: no error, and the next check looks again.
  fs.rmSync(statePath, { force: true });
  running = { sequence: 40, shell_compat: 1, shellCompat: { min: 1, max: 2 } };
  outcome = { ok: true, outcome: "waiting_for_runtime", detail: "not yet", applied: false };
  const waiting = make(standIn(channel));
  assert.equal((await waiting.check({ force: true })).status, "available");
  const after = await waiting.download();
  assert.equal(after.status, "up-to-date");
  assert.equal(after.error, null);
  assert.equal(readStateFile(statePath).release.applied, undefined, "not kept as applied");
  assert.equal((await waiting.check({ force: true })).status, "available", "the next check looks again");

  // Prepared in the background, switched on restart.
  fs.rmSync(statePath, { force: true });
  outcome = { ok: true, outcome: "activated", applied: true };
  let relaunched = 0;
  const prepare = make(standIn(channel), {
    prepareVersionUpdate: async () => ({ ok: true, outcome: "prepared", prepared: true, applied: false }),
    relaunchApp: () => { relaunched += 1; },
  });
  await prepare.check({ force: true });
  await prepare.download();
  assert.equal(prepare.getState().status, "ready");
  assert.equal(prepare.getState().readyVersion, "1.0.1");
  const installed = await prepare.install();
  assert.equal(relaunched, 1);
  assert.equal(installed.status, "up-to-date");

  // Signature and fields are checked.
  running = { sequence: 40, shell_compat: 1, shellCompat: { min: 1, max: 1 } };
  const failed = async (value, options) => {
    fs.rmSync(statePath, { force: true });
    return make(standIn(value, options)).check({ force: true });
  };
  assert.equal((await failed(channel, { signedBy: crypto.generateKeyPairSync("ed25519").privateKey })).status, "error");
  for (const broken of [
    { ...channel, schema: 1 }, { ...channel, pointer: undefined },
    { ...channel, sequence: 60, announce: false }, { ...channel, announce: undefined },
    { ...channel, sequence: 1.5 }, { ...channel, published_at: "soon" },
    { ...channel, version: "v1.0.1" }, { ...channel, build: "xyz" }, { ...channel, notes: "x".repeat(20_001) },
    { ...channel, commit: "abc" }, { ...channel, shell_compat: "1" },
  ]) assert.equal((await failed(broken)).status, "error", JSON.stringify(broken).slice(0, 80));
  // latest.json's signed text served as channel.json is refused.
  assert.equal((await failed({ ...channel, pointer: "latest", sequence: 99 })).status, "error");
  // Signed text changed after signing is refused.
  const sealed = seal(channel);
  const tampered = { ...sealed, signed: sealed.signed.replace('"version": "1.0.1"', '"version": "9.0.0"') };
  assert.notEqual(tampered.signed, sealed.signed);
  assert.equal((await failed(channel, { envelope: tampered })).status, "error");
  // A bare pointer without the envelope is not read.
  assert.equal((await failed(channel, { envelope: channel })).status, "error");

  // Another shell range: the stable release's App file, when it carries this code or newer.
  const bytes = Buffer.from("7 bytes");
  const withShell = releaseWith("1.0.1", ["OpenProgram-1.0.1-linux-amd64.deb"]);
  withShell.manifest.files[0].sha256 = crypto.createHash("sha256").update(bytes).digest("hex");
  const newShell = await failed({ ...channel, shell_compat: 2 }, { stable: withShell });
  assert.equal(newShell.status, "available");
  assert.match(newShell.needsNewShell, /newer OpenProgram App/);
  assert.equal(newShell.release.latestVersion, "1.0.1");
  assert.equal(newShell.release.artifactKind, "deb");
  const noShell = await failed({ ...channel, shell_compat: 2 }, { stable: releaseWith("1.0.0", ["OpenProgram-1.0.0-linux-amd64.deb"]) });
  assert.equal(noShell.status, "not-published", "an older release's App does not carry the update");
  assert.ok(noShell.needsNewShell);

  // With this computer's shell layer and an App that swaps its shell, the updater
  // takes the update, shell and all: no installer.
  const shellLayer = {
    "shell-linux-x86_64": { manifest: "e".repeat(64), pack: "pack-000000000000.pack", offset: 0, length: 1, version: "1.0.0" },
  };
  assert.equal(hasShellLayer({ layers: shellLayer }, "linux", "x64"), true);
  assert.equal(hasShellLayer({ layers: shellLayer }, "darwin", "x64"), false);
  assert.equal(shellLayerVersion({ layers: shellLayer }, "linux", "x64"), "1.0.0");
  const swapping = { isNewer: () => false, staged: () => null, takeActivation: () => null, install: () => "" };
  fs.rmSync(statePath, { force: true });
  const covered = await make(standIn({ ...channel, shell_compat: 2, layers: shellLayer }, { stable: withShell }),
    { shellSwap: swapping }).check({ force: true });
  assert.equal(covered.status, "available");
  assert.equal(covered.needsNewShell, null);
  assert.equal(covered.release.artifactKind, null, "the channel's update, not the installer");
  fs.rmSync(statePath, { force: true });
  const uncovered = await make(standIn({ ...channel, shell_compat: 2 }, { stable: withShell }),
    { shellSwap: swapping }).check({ force: true });
  assert.match(uncovered.needsNewShell, /newer OpenProgram App/, "no shell layer for this computer: the installer");

  // Restart to update with a staged shell: the App is swapped first and the
  // version is switched only by the new App, as it starts.
  fs.rmSync(statePath, { force: true });
  running = { sequence: 40, shell_compat: 1, shellCompat: { min: 1, max: 1 } };
  outcome = { ok: true, outcome: "activated", applied: true };
  applied = 0;
  const swaps = [];
  let marker = null;
  let stagedApp = { path: "/runtime/shell/staged/OpenProgram.app", version: "1.1.0", executable: "x" };
  const swapper = {
    isNewer: () => false,
    staged: () => stagedApp,
    install: (staged, key) => { swaps.push([staged.path, key]); marker = key; stagedApp = null; return ""; },
    takeActivation: () => { const key = marker; marker = null; return key; },
  };
  let restarted = 0;
  const before = make(standIn({ ...channel, shell_compat: 2, layers: shellLayer }), {
    shellSwap: swapper,
    prepareVersionUpdate: async () => ({ ok: true, outcome: "prepared", prepared: true, applied: false }),
    relaunchApp: () => { restarted += 1; },
  });
  await before.check({ force: true });
  await before.download();
  assert.equal(before.getState().status, "ready");
  await before.install();
  assert.deepEqual(swaps, [["/runtime/shell/staged/OpenProgram.app", "1.0.1#42"]]);
  assert.equal(applied, 0, "nothing is switched before the new App runs");
  assert.equal(restarted, 0, "the swap script opens the new App");
  const swappedIn = make(standIn({ ...channel, shell_compat: 2, layers: shellLayer }), {
    shellSwap: swapper, relaunchApp: () => { restarted += 1; },
  });
  const resumed = await swappedIn.resumeAfterShellSwap();
  assert.equal(applied, 1, "the new App switches to the prepared version");
  assert.equal(restarted, 1);
  assert.equal(resumed.status, "up-to-date");
  assert.equal((await swappedIn.resumeAfterShellSwap()).status, "up-to-date");
  assert.equal(applied, 1, "once");

  // A newer shell layer is an update by itself, even when this App runs the
  // channel's code: it is prepared, swapped in, and nothing restarts after.
  fs.rmSync(statePath, { force: true });
  running = { sequence: 42, shell_compat: 1, shellCompat: { min: 1, max: 1 } };
  const newerShell = { "shell-linux-x86_64": { ...shellLayer["shell-linux-x86_64"], version: "1.1.0" } };
  stagedApp = { path: "/Apps/.OpenProgram.AppImage.update/staged/OpenProgram.AppImage", version: "1.1.0", executable: "" };
  swaps.length = 0;
  restarted = 0;
  applied = 0;
  outcome = { ok: true, outcome: "up_to_date", reason: "up_to_date", applied: true };
  const shellOnly = { ...swapper, isNewer: (version) => compareVersions(version, "1.0.0") > 0 };
  const current = await make(standIn(channel), { shellSwap: shellOnly }).check({ force: true });
  assert.equal(current.status, "up-to-date", `the same code and no newer shell: nothing to do (${current.error})`);
  const onlyShell = make(standIn({ ...channel, layers: newerShell }), {
    shellSwap: shellOnly,
    prepareVersionUpdate: async () => ({ ok: true, outcome: "prepared", prepared: true, applied: false }),
    relaunchApp: () => { restarted += 1; },
  });
  const offered = await onlyShell.check({ force: true });
  assert.equal(offered.release.status, "available");
  await onlyShell.download();
  assert.equal(onlyShell.getState().status, "ready");
  await onlyShell.install();
  assert.equal(swaps.length, 1, "the App is swapped");
  const afterSwap = make(standIn({ ...channel, layers: newerShell }), {
    shellSwap: { ...shellOnly, isNewer: () => false }, relaunchApp: () => { restarted += 1; },
  });
  const swappedShell = await afterSwap.resumeAfterShellSwap();
  assert.equal(applied, 0, "the code was running already: nothing to switch");
  assert.equal(restarted, 0, "the new App already runs: no second restart");
  assert.equal(swappedShell.status, "up-to-date");

  // An old daily answer is dropped; an App that is not a version store keeps the stable release.
  saveStateFileAtomic(statePath, {
    schema: 1, automaticChecks: true, lastAttemptAt: 1, lastSuccessAt: 1,
    release: { channel: "daily", commit: "d".repeat(40), latestVersion: "1.0.1 · 2026-11-26", currentVersion: "1.0.0",
      releaseUrl: "https://github.com/Fzkuji/OpenProgram/releases/tag/daily", asset: null, artifactKind: null },
  });
  assert.equal(make(standIn(channel)).getState().release, null);
  asked.length = 0;
  await make(standIn(channel), { versionStore: false }).check({ force: true });
  assert.equal(asked.some((requestUrl) => requestUrl.includes("/download/updates/")), false, "only a version store reads the channel");
}

async function checkResume(directory) {
  const crypto = require("node:crypto");
  const bytes = Buffer.from("0123456789abcdefghij");
  const asset = {
    url: "https://github.com/Fzkuji/OpenProgram/releases/download/v1.1.0/app.dmg",
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
