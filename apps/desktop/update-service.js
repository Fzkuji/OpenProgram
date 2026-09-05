"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const REPOSITORY = "Fzkuji/OpenProgram";
const LATEST_URL = `https://api.github.com/repos/${REPOSITORY}/releases/latest`;
const ALLOWED_HOSTS = new Set([
  "api.github.com",
  "github.com",
  "raw.githubusercontent.com",
  "release-assets.githubusercontent.com",
]);
const VERSION_RE = /^(?:v)?(\d+)\.(\d+)\.(\d+)$/;
const SUCCESS_INTERVAL_MS = 24 * 3600_000;
const FAILURE_INTERVAL_MS = 6 * 3600_000;
const MAX_REDIRECTS = 5;
const REQUEST_TIMEOUT_MS = 30_000;
const DOWNLOAD_IDLE_TIMEOUT_MS = 60_000;
const RESPONSE_ABORT = Symbol("openprogramUpdateAbort");

function desktopUpdateFetch(requestUrl, options) {
  return fetch(requestUrl, options);
}

function versionParts(value) {
  const match = VERSION_RE.exec(String(value || ""));
  if (!match) throw new Error(`invalid version: ${value}`);
  return match.slice(1).map(Number);
}

function compareVersions(left, right) {
  const a = versionParts(left);
  const b = versionParts(right);
  for (let index = 0; index < 3; index += 1) {
    if (a[index] > b[index]) return 1;
    if (a[index] < b[index]) return -1;
  }
  return 0;
}

function validateUpdateUrl(value) {
  const parsed = new URL(value);
  if (parsed.protocol !== "https:") throw new Error("update URLs must use HTTPS");
  if (parsed.username || parsed.password) throw new Error("update URLs must not contain credentials");
  if (!ALLOWED_HOSTS.has(parsed.hostname)) {
    throw new Error(`update URL host is not allowed: ${parsed.hostname}`);
  }
  return parsed;
}

function assetMap(release) {
  if (!Array.isArray(release.assets)) throw new Error("release assets are missing");
  const result = new Map();
  for (const asset of release.assets) {
    if (!asset || typeof asset.name !== "string" || !Number.isSafeInteger(asset.size)) {
      throw new Error("release asset metadata is invalid");
    }
    if (result.has(asset.name)) throw new Error(`duplicate release asset: ${asset.name}`);
    result.set(asset.name, asset);
  }
  return result;
}

function manifestMap(manifest) {
  if (!manifest || manifest.schema !== 1 || !Array.isArray(manifest.files)) {
    throw new Error("release manifest is invalid");
  }
  const result = new Map();
  for (const item of manifest.files) {
    if (!item || typeof item.path !== "string") throw new Error("manifest entry is invalid");
    const name = path.posix.basename(item.path.replaceAll("\\", "/"));
    if (!name || result.has(name)) throw new Error(`duplicate manifest basename: ${name}`);
    if (!Number.isSafeInteger(item.bytes) || item.bytes < 0) throw new Error(`invalid size for ${name}`);
    if (!/^[a-f0-9]{64}$/.test(item.sha256 || "")) throw new Error(`invalid sha256 for ${name}`);
    result.set(name, item);
  }
  return result;
}

function releaseAssetUrl(version, name) {
  return `https://github.com/${REPOSITORY}/releases/download/v${version}/${encodeURIComponent(name)}`;
}

function desktopAssetSpec(version, platform, arch) {
  if (platform === "darwin" && (arch === "arm64" || arch === "x64")) {
    return {
      name: `OpenProgram-${version}-mac-${arch}-unsigned.dmg`,
      kind: "dmg",
    };
  }
  if (platform === "win32" && (arch === "x64" || arch === "arm64")) {
    return {
      name: `OpenProgram-${version}-win-${arch}.exe`,
      kind: "windows-installer",
    };
  }
  throw new Error(`unsupported Desktop platform: ${platform}/${arch}`);
}

function resolveDesktopRelease(
  release,
  manifest,
  currentVersion,
  arch,
  platform = "darwin",
) {
  if (!release || release.draft !== false) throw new Error("draft release is not eligible");
  if (release.prerelease !== false) throw new Error("prerelease is not eligible");
  const tagMatch = VERSION_RE.exec(String(release.tag_name || ""));
  if (!tagMatch || !String(release.tag_name).startsWith("v")) throw new Error("release tag is invalid");
  const latestVersion = tagMatch.slice(1).join(".");
  versionParts(currentVersion);
  if (!manifest || manifest.version !== latestVersion) throw new Error("manifest version mismatch");
  const expectedReleaseUrl = `https://github.com/${REPOSITORY}/releases/tag/v${latestVersion}`;
  if (release.html_url !== expectedReleaseUrl) throw new Error("release page URL is invalid");
  const releaseAssets = assetMap(release);
  if (!releaseAssets.has("release-manifest.json")) throw new Error("release manifest asset is missing");
  const manifestFiles = manifestMap(manifest);
  const spec = desktopAssetSpec(latestVersion, platform, arch);
  const { name } = spec;
  const asset = releaseAssets.get(name);
  const entry = manifestFiles.get(name);
  if (!asset || !entry) throw new Error(`complete Desktop asset is missing: ${name}`);
  if (asset.size !== entry.bytes) throw new Error(`asset size metadata mismatch: ${name}`);
  const result = {
    status: compareVersions(latestVersion, currentVersion) > 0 ? "available" : "up-to-date",
    currentVersion,
    latestVersion,
    publishedAt: typeof release.published_at === "string" ? release.published_at : "",
    releaseName: typeof release.name === "string" ? release.name : `OpenProgram ${latestVersion} Release`,
    releaseNotes: typeof release.body === "string" ? release.body.slice(0, 20_000) : "",
    releaseUrl: expectedReleaseUrl,
    artifactKind: spec.kind,
    asset: {
      name,
      bytes: entry.bytes,
      sha256: entry.sha256,
      url: releaseAssetUrl(latestVersion, name),
    },
  };
  return result;
}

function defaultPersistedState() {
  return {
    schema: 1,
    automaticChecks: true,
    lastAttemptAt: 0,
    lastSuccessAt: 0,
    release: null,
  };
}

function normalizePersistedState(value) {
  const defaults = defaultPersistedState();
  if (!value || value.schema !== 1) return defaults;
  return {
    schema: 1,
    automaticChecks: typeof value.automaticChecks === "boolean" ? value.automaticChecks : true,
    lastAttemptAt: Number.isFinite(value.lastAttemptAt) && value.lastAttemptAt >= 0 ? value.lastAttemptAt : 0,
    lastSuccessAt: Number.isFinite(value.lastSuccessAt) && value.lastSuccessAt >= 0 ? value.lastSuccessAt : 0,
    release: value.release && typeof value.release === "object" ? value.release : null,
  };
}

function loadStateFile(filePath) {
  try {
    const parsed = JSON.parse(fs.readFileSync(filePath, "utf8"));
    const state = normalizePersistedState(parsed);
    return { state, repaired: JSON.stringify(parsed) !== JSON.stringify(state) };
  } catch (error) {
    return {
      state: defaultPersistedState(),
      repaired: error?.code !== "ENOENT",
    };
  }
}

function readStateFile(filePath) { return loadStateFile(filePath).state; }

function saveStateFileAtomic(filePath, state) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const temporary = `${filePath}.tmp-${process.pid}-${crypto.randomBytes(4).toString("hex")}`;
  try {
    const options = {
      encoding: "utf8",
    };
    if (process.platform !== "win32") options.mode = 0o600;
    fs.writeFileSync(
      temporary,
      `${JSON.stringify(normalizePersistedState(state), null, 2)}\n`,
      options,
    );
    fs.renameSync(temporary, filePath);
  } finally {
    try { fs.unlinkSync(temporary); } catch (_error) { /* already renamed */ }
  }
}

function publicRelease(release) {
  if (!release) return null;
  const { asset: _asset, ...visible } = release;
  return visible;
}

function nextAutomaticCheckAt(state) {
  if (state.lastSuccessAt && state.lastSuccessAt >= state.lastAttemptAt) {
    return state.lastSuccessAt + SUCCESS_INTERVAL_MS;
  }
  if (state.lastAttemptAt) return state.lastAttemptAt + FAILURE_INTERVAL_MS;
  return 0;
}

async function requestWithRedirects(fetchImpl, initialUrl, options = {}) {
  let current = validateUpdateUrl(initialUrl);
  for (let redirects = 0; redirects <= MAX_REDIRECTS; redirects += 1) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), options.timeoutMs || REQUEST_TIMEOUT_MS);
    let response;
    try {
      response = await fetchImpl(current.toString(), {
        method: "GET",
        redirect: "manual",
        signal: controller.signal,
        headers: {
          Accept: options.accept || "application/octet-stream",
          "User-Agent": "OpenProgram-Desktop-Updater",
          "X-GitHub-Api-Version": "2022-11-28",
        },
      });
    } finally {
      clearTimeout(timeout);
    }
    if ([301, 302, 303, 307, 308].includes(response.status)) {
      controller.abort();
      if (redirects === MAX_REDIRECTS) throw new Error("update redirect limit exceeded");
      const location = response.headers.get("location");
      if (!location) throw new Error("update redirect is missing a location");
      current = validateUpdateUrl(new URL(location, current).toString());
      continue;
    }
    if (!response.ok) {
      controller.abort();
      throw new Error(`update request failed with HTTP ${response.status}`);
    }
    Object.defineProperty(response, RESPONSE_ABORT, {
      value: () => controller.abort(),
    });
    return response;
  }
  throw new Error("update redirect limit exceeded");
}

async function nextBodyChunk(iterator, timeoutMs) {
  let timeout;
  return Promise.race([
    iterator.next(),
    new Promise((_resolve, reject) => {
      timeout = setTimeout(() => reject(new Error("response body stalled")), timeoutMs);
    }),
  ]).finally(() => clearTimeout(timeout));
}

async function readJsonLimited(response, maxBytes = 2 * 1024 * 1024) {
  const chunks = [];
  let total = 0;
  const iterator = response.body[Symbol.asyncIterator]();
  try {
    while (true) {
      const next = await nextBodyChunk(iterator, REQUEST_TIMEOUT_MS);
      if (next.done) break;
      const buffer = Buffer.from(next.value);
      total += buffer.length;
      if (total > maxBytes) throw new Error("update metadata exceeds size limit");
      chunks.push(buffer);
    }
  } catch (error) {
    response[RESPONSE_ABORT]?.();
    if (typeof iterator.return === "function") {
      try { iterator.return().catch(() => {}); } catch (_cancelError) { /* best effort */ }
    }
    throw error;
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

async function writeAll(file, buffer) {
  let offset = 0;
  while (offset < buffer.length) {
    const result = await file.write(buffer, offset, buffer.length - offset);
    if (!result || !Number.isSafeInteger(result.bytesWritten) || result.bytesWritten <= 0) {
      throw new Error("update file write made no progress");
    }
    offset += result.bytesWritten;
  }
}

async function downloadVerified(fetchImpl, asset, targetPath, onProgress = () => {}) {
  const response = await requestWithRedirects(fetchImpl, asset.url);
  const temporary = `${targetPath}.part-${crypto.randomBytes(8).toString("hex")}`;
  const hash = crypto.createHash("sha256");
  let total = 0;
  let file;
  try {
    fs.mkdirSync(path.dirname(targetPath), { recursive: true });
    file = process.platform === "win32"
      ? await fs.promises.open(temporary, "wx")
      : await fs.promises.open(temporary, "wx", 0o600);
    const iterator = response.body[Symbol.asyncIterator]();
    try {
      while (true) {
        const next = await nextBodyChunk(iterator, DOWNLOAD_IDLE_TIMEOUT_MS);
        if (next.done) break;
        const buffer = Buffer.from(next.value);
        if (total + buffer.length > asset.bytes) throw new Error("download exceeds expected size");
        await writeAll(file, buffer);
        total += buffer.length;
        hash.update(buffer);
        onProgress(total, asset.bytes);
      }
    } catch (error) {
      response[RESPONSE_ABORT]?.();
      if (typeof iterator.return === "function") {
        try { iterator.return().catch(() => {}); } catch (_cancelError) { /* best effort */ }
      }
      throw error;
    }
    await file.close();
    file = null;
    if (total !== asset.bytes) throw new Error("download size mismatch");
    if (hash.digest("hex") !== asset.sha256) throw new Error("download checksum mismatch");
    await fs.promises.rename(temporary, targetPath);
  } catch (error) {
    response[RESPONSE_ABORT]?.();
    if (file) await file.close().catch(() => {});
    await fs.promises.unlink(temporary).catch(() => {});
    throw error;
  }
}

class DesktopUpdateService {
  constructor({
    currentVersion,
    arch,
    platform = "darwin",
    statePath,
    fetchImpl,
    chooseSavePath,
    verifyArtifact = async () => {},
    openPath,
    emit,
    now = Date.now,
  }) {
    this.currentVersion = currentVersion;
    this.arch = arch;
    this.platform = platform;
    this.statePath = statePath;
    this.fetchImpl = fetchImpl;
    this.chooseSavePath = chooseSavePath;
    this.verifyArtifact = verifyArtifact;
    this.openPath = openPath;
    this.emit = emit || (() => {});
    this.now = now;
    const loaded = loadStateFile(statePath);
    this.persisted = loaded.state;
    let repairedState = loaded.repaired;
    const startupNow = this.now();
    if (
      this.persisted.lastAttemptAt > startupNow
      || this.persisted.lastSuccessAt > startupNow
      || this.persisted.lastSuccessAt > this.persisted.lastAttemptAt
    ) {
      this.persisted.lastAttemptAt = 0;
      this.persisted.lastSuccessAt = 0;
      repairedState = true;
    }
    const cached = this.persisted.release;
    if (cached === null && this.persisted.lastSuccessAt > 0) {
      this.persisted.lastAttemptAt = 0;
      this.persisted.lastSuccessAt = 0;
      repairedState = true;
    }
    if (cached !== null) {
      try {
        const expectedAsset = desktopAssetSpec(cached.latestVersion, platform, arch);
        if (
          ![
            cached.currentVersion,
            cached.latestVersion,
            cached.publishedAt,
            cached.releaseName,
            cached.releaseNotes,
            cached.releaseUrl,
          ].every((value) => typeof value === "string")
          || cached.currentVersion !== currentVersion
          || cached.releaseNotes.length > 20_000
          || cached.asset?.name !== expectedAsset.name
          || cached.artifactKind !== expectedAsset.kind
          || cached.asset?.url !== releaseAssetUrl(cached.latestVersion, cached.asset.name)
          || cached.releaseUrl !== `https://github.com/${REPOSITORY}/releases/tag/v${cached.latestVersion}`
          || !Number.isSafeInteger(cached.asset?.bytes)
          || cached.asset.bytes < 0
          || !/^[a-f0-9]{64}$/.test(cached.asset?.sha256 || "")
        ) throw new Error("cached release is invalid");
        this.persisted.release = {
          ...cached,
          status: compareVersions(cached.latestVersion, currentVersion) > 0 ? "available" : "up-to-date",
        };
      } catch (_error) {
        this.persisted.release = null;
        this.persisted.lastAttemptAt = 0;
        this.persisted.lastSuccessAt = 0;
        repairedState = true;
      }
    }
    if (repairedState) {
      try { saveStateFileAtomic(this.statePath, this.persisted); } catch (_error) { /* retry later */ }
    }
    this.publicState = {
      status: this.persisted.release?.status || "idle",
      currentVersion,
      automaticChecks: this.persisted.automaticChecks,
      checkedAt: this.persisted.lastSuccessAt || null,
      release: publicRelease(this.persisted.release),
      progress: null,
      error: null,
    };
    this.checkPromise = null;
    this.downloadPromise = null;
  }

  getState() { return structuredClone(this.publicState); }

  publish(patch) {
    Object.assign(this.publicState, patch);
    this.emit(this.getState());
  }

  persist() { saveStateFileAtomic(this.statePath, this.persisted); }

  setAutomaticChecks(enabled) {
    const next = {
      ...this.persisted,
      automaticChecks: Boolean(enabled),
    };
    saveStateFileAtomic(this.statePath, next);
    this.persisted = next;
    this.publish({ automaticChecks: next.automaticChecks });
    return this.getState();
  }

  automaticCheckDueAt() { return nextAutomaticCheckAt(this.persisted); }

  async check({ force = false } = {}) {
    if (this.checkPromise) return this.checkPromise;
    const now = this.now();
    const dueAt = this.automaticCheckDueAt();
    if (!force && dueAt && now < dueAt) {
      return this.getState();
    }
    this.checkPromise = this.runCheck(now).finally(() => { this.checkPromise = null; });
    return this.checkPromise;
  }

  async runCheck(now) {
    try {
      this.persisted.lastAttemptAt = now;
      this.persist();
      this.publish({ status: "checking", error: null });
      const latestResponse = await requestWithRedirects(this.fetchImpl, LATEST_URL, {
        accept: "application/vnd.github+json",
      });
      const release = await readJsonLimited(latestResponse);
      const tag = String(release.tag_name || "");
      const match = VERSION_RE.exec(tag);
      if (!match || !tag.startsWith("v")) throw new Error("release tag is invalid");
      const version = match.slice(1).join(".");
      const manifestResponse = await requestWithRedirects(
        this.fetchImpl,
        releaseAssetUrl(version, "release-manifest.json"),
      );
      const manifest = await readJsonLimited(manifestResponse);
      const resolved = resolveDesktopRelease(
        release,
        manifest,
        this.currentVersion,
        this.arch,
        this.platform,
      );
      this.persisted.lastSuccessAt = now;
      this.persisted.release = resolved;
      this.persist();
      this.publish({
        status: resolved.status,
        checkedAt: now,
        release: publicRelease(resolved),
        error: null,
      });
    } catch (error) {
      try { this.persist(); } catch (_persistError) { /* state remains in memory */ }
      this.publish({ status: "error", error: error instanceof Error ? error.message : String(error) });
    }
    return this.getState();
  }

  async download() {
    if (this.downloadPromise) return this.downloadPromise;
    const release = this.persisted.release;
    if (!release || release.status !== "available" || !release.asset) {
      throw new Error("no verified Desktop update is available");
    }
    this.downloadPromise = this.runDownload(release).finally(() => { this.downloadPromise = null; });
    return this.downloadPromise;
  }

  async runDownload(release) {
    const targetPath = await this.chooseSavePath(release.asset.name);
    if (!targetPath) return this.getState();
    this.publish({ status: "downloading", progress: { downloaded: 0, total: release.asset.bytes }, error: null });
    try {
      let lastProgressAt = 0;
      await downloadVerified(this.fetchImpl, release.asset, targetPath, (downloaded, total) => {
        const now = Date.now();
        if (downloaded === total || now - lastProgressAt >= 250) {
          lastProgressAt = now;
          this.publish({ progress: { downloaded, total } });
        }
      });
      try {
        await this.verifyArtifact(targetPath, release.asset);
      } catch (error) {
        await fs.promises.unlink(targetPath).catch(() => {});
        throw error;
      }
      const openError = await this.openPath(targetPath);
      if (openError) throw new Error(openError);
      this.publish({ status: "downloaded", progress: null });
    } catch (error) {
      this.publish({ status: "error", progress: null, error: error instanceof Error ? error.message : String(error) });
    }
    return this.getState();
  }
}

module.exports = {
  DesktopUpdateService,
  FAILURE_INTERVAL_MS,
  LATEST_URL,
  SUCCESS_INTERVAL_MS,
  compareVersions,
  desktopAssetSpec,
  desktopUpdateFetch,
  downloadVerified,
  nextAutomaticCheckAt,
  normalizePersistedState,
  readStateFile,
  readJsonLimited,
  requestWithRedirects,
  resolveDesktopRelease,
  saveStateFileAtomic,
  validateUpdateUrl,
  writeAll,
};
