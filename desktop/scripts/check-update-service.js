const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const {
  compareVersions,
  downloadVerified,
  nextAutomaticCheckAt,
  normalizePersistedState,
  readStateFile,
  requestWithRedirects,
  resolveDesktopRelease,
  saveStateFileAtomic,
  validateUpdateUrl,
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
  fs.writeFileSync(statePath, "not-json");
  assert.equal(readStateFile(statePath).automaticChecks, true);
  assert.equal(fs.readdirSync(root).some((name) => name.includes(".tmp-")), false);
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}

console.log("desktop update service checks passed");

const mainSource = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
const preloadSource = fs.readFileSync(path.join(__dirname, "..", "preload.js"), "utf8");
assert.match(mainSource, /new DesktopUpdateService/);
assert.match(mainSource, /ipcMain\.handle\("updates:get-state"/);
assert.match(mainSource, /ipcMain\.handle\("updates:check"/);
assert.match(mainSource, /ipcMain\.handle\("updates:download"/);
assert.match(mainSource, /powerMonitor\.on\("resume"/);
assert.match(preloadSource, /updates:\s*\{/);
assert.match(preloadSource, /getState:\s*\(\)\s*=>\s*ipcRenderer\.invoke\("updates:get-state"\)/);
assert.doesNotMatch(preloadSource, /updates:download"\s*,/);

async function checkNetworkBoundaries() {
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

  await assert.rejects(
    requestWithRedirects(
      async () => new Response(null, { status: 302, headers: { location: "https://example.com/file" } }),
      "https://github.com/Fzkuji/OpenProgram/releases/download/v0.6.7/file",
    ),
    /host/,
  );

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

    const badTarget = path.join(root, "bad.dmg");
    await assert.rejects(
      downloadVerified(async () => new Response(bytes), { ...asset, sha256: "0".repeat(64) }, badTarget),
      /checksum/,
    );
    assert.equal(fs.existsSync(badTarget), false);
    assert.equal(fs.readdirSync(root).some((name) => name.includes(".part-")), false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

checkNetworkBoundaries().then(
  () => console.log("desktop update network checks passed"),
  (error) => { console.error(error); process.exitCode = 1; },
);
