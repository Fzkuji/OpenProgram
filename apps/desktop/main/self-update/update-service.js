"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { Readable } = require("node:stream");
const { replaceAppImage } = require("./app-replace");

const REPOSITORY = "Fzkuji/OpenProgram";
const LATEST_URL = `https://api.github.com/repos/${REPOSITORY}/releases/latest`;
const ALLOWED_HOSTS = new Set([
  "api.github.com",
  "github.com",
  "raw.githubusercontent.com",
  "release-assets.githubusercontent.com",
]);
// Only read when GitHub cannot answer. It can name a new repository and the
// hosts its files come from, and counts only with a signature from one of the
// keys below, the same keys as RELEASE_PUBLIC_KEYS in official/assets.py.
// The address is UPDATE_SOURCE_URL in openprogram/operations/project_site.py.
const UPDATE_SOURCE_URL = "https://fuzzykuji.github.io/openprogram-site/update-source.json";
const UPDATE_SOURCE_HOSTS = new Set(["fuzzykuji.github.io"]);
const RELEASE_PUBLIC_KEYS = ["e40d13b9572ab80002742e74a4243faa3312a8acec0d08a63ce71999e8531381"];
const ED25519_PUBLIC_KEY_PREFIX = Buffer.from("302a300506032b6570032100", "hex");
const GITHUB_WEB_ROOT = "https://github.com";
const REPOSITORY_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}\/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;
const HOST_RE = /^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$/;
const SIGNATURE_RE = /^[0-9a-f]{128}$/;
const VERSION_RE = /^(?:v)?(\d+)\.(\d+)\.(\d+)$/;
const SUCCESS_INTERVAL_MS = 24 * 3600_000;
const FAILURE_INTERVAL_MS = 6 * 3600_000;
const MAX_REDIRECTS = 5;
const REQUEST_TIMEOUT_MS = 30_000;
const DOWNLOAD_IDLE_TIMEOUT_MS = 60_000;
const DOWNLOAD_ATTEMPTS = 5;
const DOWNLOAD_RETRY_DELAY_MS = 1_000;
const DOWNLOAD_RETRY_LIMIT_MS = 30_000;
const RESPONSE_ABORT = Symbol("openprogramUpdateAbort");
const NOT_PUBLISHED = "not-published";
const WAITING_FOR_SHELL = "waiting_for_shell";

/** A release exists, and has no file for this kind of computer yet. */
class DesktopAssetNotPublished extends Error {}

function transient(error) {
  if (error && typeof error === "object") error.transient = true;
  return error;
}

function electronNet() {
  try {
    const electron = require("electron");
    return typeof electron === "object" ? electron.net : null;
  } catch (_error) {
    return null;
  }
}

/**
 * One request through Chromium's network stack, which uses the system proxy.
 * The stack's own fetch fails with "Redirect was cancelled" under a manual
 * redirect policy, and every hop has to be seen here to check its host, so
 * the redirect event is turned into the 30x response fetch would have given.
 */
function chromiumRequest(net, requestUrl, { method = "GET", headers = {}, signal } = {}) {
  return new Promise((resolve, reject) => {
    const request = net.request({
      url: requestUrl, method, redirect: "manual", credentials: "omit", cache: "no-store",
    });
    for (const [name, value] of Object.entries(headers)) request.setHeader(name, value);
    const abort = () => {
      request.abort();
      reject(new DOMException("The operation was aborted.", "AbortError"));
    };
    if (signal?.aborted) {
      abort();
      return;
    }
    signal?.addEventListener("abort", abort, { once: true });
    request.on("redirect", (status, _method, location) => {
      resolve(new Response(null, { status, headers: { location } }));
      request.abort();
    });
    request.on("response", (response) => {
      try {
        const received = new Headers();
        for (const [name, value] of Object.entries(response.headers)) {
          for (const item of Array.isArray(value) ? value : [value]) {
            if (typeof item === "string") received.append(name, item);
          }
        }
        const empty = [204, 205, 304].includes(response.statusCode) || method === "HEAD";
        resolve(new Response(empty ? null : Readable.toWeb(response), {
          status: response.statusCode, statusText: response.statusMessage, headers: received,
        }));
      } catch (error) {
        request.abort();
        reject(error);
      }
    });
    request.on("error", reject);
    request.end();
  });
}

function desktopUpdateFetch(requestUrl, options, net = electronNet()) {
  if (net?.request) return chromiumRequest(net, requestUrl, options);
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

function validateUpdateUrl(value, allowedHosts = ALLOWED_HOSTS) {
  const parsed = new URL(value);
  if (parsed.protocol !== "https:") throw new Error("update URLs must use HTTPS");
  if (parsed.username || parsed.password) throw new Error("update URLs must not contain credentials");
  if (!allowedHosts.has(parsed.hostname)) {
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

function releaseAssetUrl(version, name, repository = REPOSITORY, webRoot = GITHUB_WEB_ROOT) {
  return `${webRoot}/${repository}/releases/download/v${version}/${encodeURIComponent(name)}`;
}

function desktopAssetSpec(version, platform, arch, { linuxFormat = "deb" } = {}) {
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
  if (platform === "linux" && (arch === "x64" || arch === "arm64")) {
    if (linuxFormat === "appimage") {
      return {
        name: `OpenProgram-${version}-linux-${arch === "x64" ? "x86_64" : "arm64"}.AppImage`,
        kind: "appimage",
      };
    }
    return {
      name: `OpenProgram-${version}-linux-${arch === "x64" ? "amd64" : "arm64"}.deb`,
      kind: "deb",
    };
  }
  throw new Error(`unsupported Desktop platform: ${platform}/${arch}`);
}

/** What a chosen file is: a macOS .zip is the App itself, ready to swap in. */
function assetKind(name, specKind) {
  return name && name.endsWith(".zip") ? "app-archive" : specKind;
}

/**
 * The file names that can serve this computer, the preferred one first. A
 * signed Windows App stays on the signed line, so it never looks at an
 * unsigned installer.
 */
function desktopAssetNames(version, platform, arch, { linuxFormat, windowsSignedOnly = true, inPlace = false } = {}) {
  const { name } = desktopAssetSpec(version, platform, arch, { linuxFormat });
  if (platform === "darwin") {
    const images = [name.replace("-unsigned.dmg", ".dmg"), name];
    // An App that replaces itself takes the signed .zip, and the disk image
    // when a release has none.
    return inPlace ? [`OpenProgram-${version}-mac-${arch}.zip`, ...images] : images;
  }
  if (platform === "win32" && !windowsSignedOnly) return [name, name.replace(/\.exe$/, "-unsigned.exe")];
  return [name];
}

function resolveDesktopRelease(
  release,
  manifest,
  currentVersion,
  arch,
  platform = "darwin",
  {
    linuxFormat, windowsSignedOnly = true, versionStore = false, repository = REPOSITORY,
    webRoot = GITHUB_WEB_ROOT, inPlace = false,
  } = {},
) {
  if (!release || release.draft !== false) throw new Error("draft release is not eligible");
  if (release.prerelease !== false) throw new Error("prerelease is not eligible");
  const tagMatch = VERSION_RE.exec(String(release.tag_name || ""));
  if (!tagMatch || !String(release.tag_name).startsWith("v")) throw new Error("release tag is invalid");
  const latestVersion = tagMatch.slice(1).join(".");
  versionParts(currentVersion);
  if (!manifest || manifest.version !== latestVersion) throw new Error("manifest version mismatch");
  const expectedReleaseUrl = `${webRoot}/${repository}/releases/tag/v${latestVersion}`;
  if (release.html_url !== expectedReleaseUrl) throw new Error("release page URL is invalid");
  const releaseAssets = assetMap(release);
  if (!releaseAssets.has("release-manifest.json")) throw new Error("release manifest asset is missing");
  const manifestFiles = manifestMap(manifest);
  let spec = null;
  let names = [];
  try {
    spec = desktopAssetSpec(latestVersion, platform, arch, { linuxFormat });
    names = desktopAssetNames(latestVersion, platform, arch, { linuxFormat, windowsSignedOnly, inPlace });
  } catch (error) {
    if (!versionStore) throw new DesktopAssetNotPublished(error.message);
  }
  const name = names.find((candidate) => releaseAssets.has(candidate) && manifestFiles.has(candidate));
  // A version-store install takes new versions through its own fetcher, so a
  // release with no App file for this computer is still a release for it.
  if (!name && !versionStore) {
    throw new DesktopAssetNotPublished(`complete Desktop asset is missing: ${names[0]}`);
  }
  const asset = name ? releaseAssets.get(name) : null;
  const entry = name ? manifestFiles.get(name) : null;
  if (name && asset.size !== entry.bytes) throw new Error(`asset size metadata mismatch: ${name}`);
  const result = {
    status: compareVersions(latestVersion, currentVersion) > 0 ? "available" : "up-to-date",
    currentVersion,
    latestVersion,
    publishedAt: typeof release.published_at === "string" ? release.published_at : "",
    releaseName: typeof release.name === "string" ? release.name : `OpenProgram ${latestVersion} Release`,
    releaseNotes: typeof release.body === "string" ? release.body.slice(0, 20_000) : "",
    releaseUrl: expectedReleaseUrl,
    artifactKind: spec ? assetKind(name, spec.kind) : null,
    asset: name ? {
      name,
      bytes: entry.bytes,
      sha256: entry.sha256,
      url: releaseAssetUrl(latestVersion, name, repository, webRoot),
    } : null,
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
  const hosts = options.allowedHosts || ALLOWED_HOSTS;
  let current = validateUpdateUrl(initialUrl, hosts);
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
          ...options.headers,
        },
      });
    } catch (error) {
      throw transient(error);
    } finally {
      clearTimeout(timeout);
    }
    if ([301, 302, 303, 307, 308].includes(response.status)) {
      controller.abort();
      if (redirects === MAX_REDIRECTS) throw new Error("update redirect limit exceeded");
      const location = response.headers.get("location");
      if (!location) throw new Error("update redirect is missing a location");
      current = validateUpdateUrl(new URL(location, current).toString(), hosts);
      continue;
    }
    if (!response.ok) {
      controller.abort();
      const error = new Error(`update request failed with HTTP ${response.status}`);
      error.httpStatus = response.status;
      if (response.status === 408 || response.status === 416 || response.status === 429 || response.status >= 500) {
        transient(error);
      }
      throw error;
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
      timeout = setTimeout(() => reject(transient(new Error("response body stalled"))), timeoutMs);
    }),
  ]).finally(() => clearTimeout(timeout));
}

async function readLimited(response, maxBytes) {
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
  return Buffer.concat(chunks);
}

async function readJsonLimited(response, maxBytes = 2 * 1024 * 1024) {
  return JSON.parse((await readLimited(response, maxBytes)).toString("utf8"));
}

/** ``signature`` is the hex text of update-source.json.sig, as `sign-file` writes it. */
function signedByReleaseKey(data, signature, keys) {
  const text = signature.toString("ascii").trim();
  if (!SIGNATURE_RE.test(text)) return false;
  const signatureBytes = Buffer.from(text, "hex");
  return keys.some((key) => {
    try {
      const publicKey = crypto.createPublicKey({
        key: Buffer.concat([ED25519_PUBLIC_KEY_PREFIX, Buffer.from(key, "hex")]),
        format: "der",
        type: "spki",
      });
      return crypto.verify(null, data, publicKey, signatureBytes);
    } catch (_error) {
      return false;
    }
  });
}

/** The signed update-source file, or null when it is absent or unverified. */
async function readUpdateSource(fetchImpl, keys = RELEASE_PUBLIC_KEYS) {
  if (!keys.length) return null;
  try {
    const options = { allowedHosts: UPDATE_SOURCE_HOSTS, accept: "application/json" };
    const data = await readLimited(await requestWithRedirects(fetchImpl, UPDATE_SOURCE_URL, options), 64 * 1024);
    const signature = await readLimited(
      await requestWithRedirects(fetchImpl, `${UPDATE_SOURCE_URL}.sig`, options), 1024,
    );
    if (!signedByReleaseKey(data, signature, keys)) return null;
    const source = JSON.parse(data.toString("utf8"));
    if (
      source?.schema !== 1
      || !REPOSITORY_RE.test(source.repository || "")
      || !Array.isArray(source.hosts)
      || source.hosts.length === 0
      || source.hosts.length > 16
      || !source.hosts.every((host) => typeof host === "string" && HOST_RE.test(host))
    ) return null;
    return {
      repository: source.repository,
      webRoot: `https://${source.hosts[0]}`,
      hasApi: false,
      allowedHosts: new Set([...ALLOWED_HOSTS, ...source.hosts]),
    };
  } catch (_error) {
    return null;
  }
}

/** With no API answer, the latest-release page still says which tag it is. */
async function readTagFromLatestRedirect(fetchImpl, repository, allowedHosts, webRoot = GITHUB_WEB_ROOT) {
  let current = validateUpdateUrl(`${webRoot}/${repository}/releases/latest`, allowedHosts);
  for (let redirects = 0; redirects <= MAX_REDIRECTS; redirects += 1) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    let response;
    try {
      response = await fetchImpl(current.toString(), {
        method: "GET",
        redirect: "manual",
        signal: controller.signal,
        headers: { Accept: "text/html", "User-Agent": "OpenProgram-Desktop-Updater" },
      });
    } catch (error) {
      throw transient(error);
    } finally {
      clearTimeout(timeout);
    }
    controller.abort();
    if (![301, 302, 303, 307, 308].includes(response.status)) {
      const error = new Error(`the latest release page answered HTTP ${response.status}`);
      error.httpStatus = response.status;
      throw error;
    }
    const location = response.headers.get("location");
    if (!location) throw new Error("update redirect is missing a location");
    current = validateUpdateUrl(new URL(location, current).toString(), allowedHosts);
    const tag = /\/releases\/tag\/(v\d+\.\d+\.\d+)$/.exec(current.pathname);
    if (tag) return tag[1];
  }
  throw new Error("update redirect limit exceeded");
}

function githubFailed(error) {
  return Boolean(error?.transient || error?.httpStatus);
}

/** What the API would have said, as far as the release's own manifest knows. */
function releaseFromManifest(repository, version, manifest, webRoot = GITHUB_WEB_ROOT) {
  const files = [...manifestMap(manifest).entries()].map(([name, item]) => ({ name, size: item.bytes }));
  return {
    tag_name: `v${version}`,
    name: `OpenProgram ${version} Release`,
    draft: false,
    prerelease: false,
    html_url: `${webRoot}/${repository}/releases/tag/v${version}`,
    published_at: "",
    body: "",
    assets: [{ name: "release-manifest.json", size: 0 }, ...files],
  };
}

function updateErrorFields(error, activity) {
  const message = error instanceof Error ? error.message : String(error);
  if (error?.httpStatus === 403 || error?.httpStatus === 429) {
    return {
      errorKind: "rate-limited",
      error: "GitHub is limiting update checks from this network right now. Try again in about an hour.",
    };
  }
  if (error?.transient) {
    return activity === "download"
      ? {
        errorKind: "network",
        error: "The download stopped before it finished. Start it again and it will continue where it left off.",
      }
      : {
        errorKind: "network",
        error: "OpenProgram couldn't reach the update server. Check your internet connection and try again.",
      };
  }
  return { errorKind: "failed", error: message };
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

function partialDownloadPath(targetPath, asset) {
  return `${targetPath}.${asset.sha256.slice(0, 16)}.part`;
}

/** What an earlier attempt left: its length and a hash already fed with it. */
async function readPartialDownload(partial, asset) {
  const hash = crypto.createHash("sha256");
  let info;
  try {
    info = await fs.promises.lstat(partial);
  } catch (_error) {
    return { bytes: 0, hash };
  }
  if (!info.isFile() || info.size > asset.bytes) {
    await fs.promises.rm(partial, { recursive: true, force: true });
    return { bytes: 0, hash };
  }
  for await (const chunk of fs.createReadStream(partial)) hash.update(chunk);
  return { bytes: info.size, hash };
}

function continuesAt(response, offset, totalBytes) {
  const range = /^bytes (\d+)-(\d+)\/(\d+)$/.exec(response.headers.get("content-range") || "");
  return Boolean(range) && Number(range[1]) === offset && Number(range[3]) === totalBytes;
}

async function downloadAttempt(fetchImpl, asset, targetPath, partial, onProgress, allowedHosts) {
  let { bytes: total, hash } = await readPartialDownload(partial, asset);
  let response = null;
  if (total < asset.bytes) {
    try {
      response = await requestWithRedirects(fetchImpl, asset.url, {
        headers: total > 0 ? { Range: `bytes=${total}-` } : {},
        allowedHosts,
      });
    } catch (error) {
      if (error.httpStatus === 416) await fs.promises.unlink(partial).catch(() => {});
      throw error;
    }
  }
  let file = null;
  try {
    if (response) {
      if (total > 0 && response.status === 206 && !continuesAt(response, total, asset.bytes)) {
        throw Object.assign(transient(new Error("download range mismatch")), { discardPartial: true });
      }
      if (total > 0 && response.status !== 206) {
        total = 0;
        hash = crypto.createHash("sha256");
      }
      fs.mkdirSync(path.dirname(targetPath), { recursive: true });
      if (total === 0) await fs.promises.unlink(partial).catch(() => {});
      const flags = total > 0 ? "a" : "wx";
      file = process.platform === "win32"
        ? await fs.promises.open(partial, flags)
        : await fs.promises.open(partial, flags, 0o600);
      onProgress(total, asset.bytes);
      const iterator = response.body[Symbol.asyncIterator]();
      try {
        while (true) {
          let next;
          try {
            next = await nextBodyChunk(iterator, DOWNLOAD_IDLE_TIMEOUT_MS);
          } catch (error) {
            throw transient(error);
          }
          if (next.done) break;
          const buffer = Buffer.from(next.value);
          if (total + buffer.length > asset.bytes) {
            throw Object.assign(new Error("download exceeds expected size"), { discardPartial: true });
          }
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
    }
    if (total !== asset.bytes) throw transient(new Error("download size mismatch"));
    if (hash.digest("hex") !== asset.sha256) {
      throw Object.assign(new Error("download checksum mismatch"), { discardPartial: true });
    }
    await fs.promises.rename(partial, targetPath);
  } catch (error) {
    response?.[RESPONSE_ABORT]?.();
    if (file) await file.close().catch(() => {});
    if (error.discardPartial) await fs.promises.unlink(partial).catch(() => {});
    throw error;
  }
}

/**
 * Fetch an asset to `targetPath`, trying again after a network failure from
 * where the last attempt stopped. The partial file stays beside the target, so
 * the next try continues too, even after the App was closed.
 */
async function downloadVerified(fetchImpl, asset, targetPath, onProgress = () => {}, {
  allowedHosts,
  attempts = DOWNLOAD_ATTEMPTS,
  retryDelayMs = DOWNLOAD_RETRY_DELAY_MS,
} = {}) {
  const partial = partialDownloadPath(targetPath, asset);
  for (let attempt = 1; ; attempt += 1) {
    try {
      await downloadAttempt(fetchImpl, asset, targetPath, partial, onProgress, allowedHosts);
      return;
    } catch (error) {
      if (!error?.transient || attempt >= attempts) throw error;
      const delay = Math.min(retryDelayMs * 2 ** (attempt - 1), DOWNLOAD_RETRY_LIMIT_MS);
      await new Promise((resolve) => { setTimeout(resolve, delay); });
    }
  }
}

/** Whether a finished file is already there with the release's size and hash. */
async function fileMatches(filePath, asset) {
  try {
    const info = await fs.promises.stat(filePath);
    if (!info.isFile() || info.size !== asset.bytes) return false;
    const hash = crypto.createHash("sha256");
    for await (const chunk of fs.createReadStream(filePath)) hash.update(chunk);
    return hash.digest("hex") === asset.sha256;
  } catch {
    return false;
  }
}

/** What `openprogram upgrade --json` reported: its last line of output. */
function parseUpgradeOutcome(output, exitCode) {
  const line = String(output || "").trim().split(/\r?\n/).at(-1);
  try {
    const outcome = JSON.parse(line);
    if (outcome && typeof outcome === "object") return outcome;
  } catch (_error) {
    // Not the JSON line: described below.
  }
  return {
    ok: exitCode === 0,
    detail: exitCode === 0 ? "" : `The update stopped with code ${exitCode}. Try again from Settings.`,
  };
}

function githubSource() {
  return { repository: REPOSITORY, webRoot: GITHUB_WEB_ROOT, hasApi: true, allowedHosts: ALLOWED_HOSTS };
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
    linuxFormat = "deb",
    windowsLine = async () => ({ signed: true }),
    versionStore = false,
    readCurrentVersion = null,
    applyVersionUpdate = null,
    updateSourceKeys = RELEASE_PUBLIC_KEYS,
    inPlace = null,
  }) {
    // How this App replaces itself (app-replace.js), or null when an update
    // is a file the user opens.
    this.inPlace = inPlace;
    this.currentVersion = currentVersion;
    this.arch = arch;
    this.platform = platform;
    this.linuxFormat = linuxFormat;
    this.windowsLine = windowsLine;
    this.versionStore = versionStore;
    this.readCurrentVersion = readCurrentVersion;
    this.applyVersionUpdate = applyVersionUpdate;
    this.updateSourceKeys = updateSourceKeys;
    this.source = githubSource();
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
        const assetOptions = { linuxFormat, windowsSignedOnly: false, inPlace: Boolean(inPlace) };
        const expectedAsset = desktopAssetSpec(cached.latestVersion, platform, arch, assetOptions);
        const expectedNames = desktopAssetNames(cached.latestVersion, platform, arch, assetOptions);
        const hasNoAsset = versionStore && cached.asset === null;
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
          || cached.artifactKind !== assetKind(cached.asset?.name, expectedAsset.kind)
          || cached.releaseUrl !== `https://github.com/${REPOSITORY}/releases/tag/v${cached.latestVersion}`
          || (!hasNoAsset && (
            !expectedNames.includes(cached.asset?.name)
            || cached.asset?.url !== releaseAssetUrl(cached.latestVersion, cached.asset.name)
            || !Number.isSafeInteger(cached.asset?.bytes)
            || cached.asset.bytes < 0
            || !/^[a-f0-9]{64}$/.test(cached.asset?.sha256 || "")
          ))
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
      errorKind: null,
      updateKind: null,
      needsNewShell: null,
      // The version prepared to swap in on restart; the sidebar's badge.
      readyVersion: null,
    };
    // The prepared file of an App that replaces itself: { version, file }.
    this.prepared = null;
    this.checkPromise = null;
    this.downloadPromise = null;
  }

  getState() {
    return { ...structuredClone(this.publicState), installsInPlace: this.installsInPlace() };
  }

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

  /**
   * The newest release of one source: GitHub's API, or the latest-release page
   * when the API cannot answer. A signed update source has only the page.
   */
  async readRelease(source) {
    const { repository, allowedHosts, webRoot, hasApi } = source;
    let release = null;
    let version;
    try {
      if (!hasApi) throw transient(new Error("this source has no release API"));
      const latestResponse = await requestWithRedirects(
        this.fetchImpl,
        `https://api.github.com/repos/${repository}/releases/latest`,
        { accept: "application/vnd.github+json", allowedHosts },
      );
      release = await readJsonLimited(latestResponse);
      const tag = String(release.tag_name || "");
      const match = VERSION_RE.exec(tag);
      if (!match || !tag.startsWith("v")) throw new Error("release tag is invalid");
      version = match.slice(1).join(".");
    } catch (error) {
      if (!githubFailed(error)) throw error;
      release = null;
      try {
        version = (await readTagFromLatestRedirect(this.fetchImpl, repository, allowedHosts, webRoot)).slice(1);
      } catch (_fallbackError) {
        throw error;
      }
    }
    const manifestResponse = await requestWithRedirects(
      this.fetchImpl,
      releaseAssetUrl(version, "release-manifest.json", repository, webRoot),
      { allowedHosts },
    );
    const manifest = await readJsonLimited(manifestResponse);
    return { source, manifest, release: release || releaseFromManifest(repository, version, manifest, webRoot) };
  }

  async readLatestRelease() {
    const github = githubSource();
    try {
      return await this.readRelease(github);
    } catch (error) {
      if (!githubFailed(error)) throw error;
      const source = await readUpdateSource(this.fetchImpl, this.updateSourceKeys);
      if (!source) throw error;
      try {
        return await this.readRelease(source);
      } catch (_sourceError) {
        throw error;
      }
    }
  }

  async runCheck(now) {
    try {
      this.persisted.lastAttemptAt = now;
      this.persist();
      if (this.readCurrentVersion) this.currentVersion = this.readCurrentVersion() || this.currentVersion;
      this.publish({
        status: "checking", currentVersion: this.currentVersion, error: null, errorKind: null, needsNewShell: null,
      });
      const { source, manifest, release } = await this.readLatestRelease();
      const line = this.platform === "win32" ? await this.windowsLine() : null;
      const resolved = resolveDesktopRelease(release, manifest, this.currentVersion, this.arch, this.platform, {
        linuxFormat: this.linuxFormat,
        windowsSignedOnly: line ? line.signed : true,
        versionStore: this.versionStore,
        repository: source.repository,
        webRoot: source.webRoot,
        inPlace: Boolean(this.inPlace),
      });
      this.source = source;
      this.persisted.lastSuccessAt = now;
      this.persisted.release = resolved;
      this.persist();
      const stillReady = resolved.status === "available" && this.prepared?.version === resolved.latestVersion;
      if (!stillReady) this.prepared = null;
      this.publish({
        status: stillReady ? "ready" : resolved.status,
        readyVersion: stillReady ? resolved.latestVersion : null,
        checkedAt: now,
        release: publicRelease(resolved),
        error: null,
        errorKind: null,
      });
      this.prepareInBackground();
    } catch (error) {
      if (error instanceof DesktopAssetNotPublished) {
        this.persisted.release = null;
        try { this.persist(); } catch (_persistError) { /* state remains in memory */ }
        this.publish({ status: NOT_PUBLISHED, checkedAt: now, release: null, error: null, errorKind: null });
        return this.getState();
      }
      try { this.persist(); } catch (_persistError) { /* state remains in memory */ }
      this.publish({ status: "error", ...updateErrorFields(error, "check") });
    }
    return this.getState();
  }

  /**
   * Whether the verified release is one this App installs itself: its file
   * matches how this App replaces itself.
   */
  installsInPlace(release = this.persisted.release) {
    return Boolean(this.inPlace && release?.asset && release.artifactKind === this.inPlace.kind);
  }

  /**
   * With automatic checks on, a release this App installs itself is fetched
   * and prepared without asking; "ready" then offers the restart. Called after
   * each check and once at start, for a release found before the last quit.
   */
  prepareInBackground() {
    const release = this.persisted.release;
    if (!this.persisted.automaticChecks || release?.status !== "available" || !this.installsInPlace(release)) return;
    if (this.prepared?.version === release.latestVersion || this.versionStore && !this.publicState.needsNewShell) return;
    void this.download().catch(() => {});
  }

  async download() {
    if (this.downloadPromise) return this.downloadPromise;
    const release = this.persisted.release;
    if (!release || release.status !== "available") {
      throw new Error("no verified Desktop update is available");
    }
    const takesVersion = this.versionStore && this.applyVersionUpdate && !this.publicState.needsNewShell;
    if (!takesVersion && !release.asset) {
      this.publish({ status: NOT_PUBLISHED, error: null, errorKind: null });
      return this.getState();
    }
    const run = takesVersion
      ? this.runVersionUpdate(release)
      : this.installsInPlace(release) ? this.runPrepare(release) : this.runDownload(release);
    this.downloadPromise = run
      .finally(() => { this.downloadPromise = null; });
    return this.downloadPromise;
  }

  /**
   * A version-store install updates through its own fetcher. The App's file is
   * offered only when that says the installed shell cannot host the release.
   */
  async runVersionUpdate(release) {
    this.publish({ status: "downloading", progress: null, updateKind: "version", error: null, errorKind: null });
    try {
      const outcome = await this.applyVersionUpdate();
      if (outcome.reason === WAITING_FOR_SHELL) {
        this.publish({
          status: release.asset ? "available" : NOT_PUBLISHED,
          updateKind: null,
          needsNewShell: outcome.detail || "This update needs a newer OpenProgram App.",
        });
        return this.getState();
      }
      if (!outcome.ok) throw new Error(outcome.detail || "The update could not be applied.");
      if (this.readCurrentVersion) this.currentVersion = this.readCurrentVersion() || release.latestVersion;
      else this.currentVersion = release.latestVersion;
      const updated = {
        ...release,
        currentVersion: this.currentVersion,
        status: compareVersions(release.latestVersion, this.currentVersion) > 0 ? "available" : "up-to-date",
      };
      this.persisted.release = updated;
      this.persist();
      this.publish({
        status: updated.status,
        currentVersion: this.currentVersion,
        release: publicRelease(updated),
        updateKind: null,
        needsNewShell: null,
      });
    } catch (error) {
      this.publish({ status: "error", updateKind: null, ...updateErrorFields(error, "update") });
    }
    return this.getState();
  }

  /** Fetch the release file to the App's own folder, check it and get it ready to swap in. */
  async runPrepare(release) {
    const targetPath = this.inPlace.downloadPath(release.asset.name);
    this.publish({
      status: "downloading",
      progress: { downloaded: 0, total: release.asset.bytes },
      updateKind: "app",
      error: null,
      errorKind: null,
    });
    try {
      if (!await fileMatches(targetPath, release.asset)) {
        let lastProgressAt = 0;
        await downloadVerified(this.fetchImpl, release.asset, targetPath, (downloaded, total) => {
          const now = Date.now();
          if (downloaded === total || now - lastProgressAt >= 250) {
            lastProgressAt = now;
            this.publish({ progress: { downloaded, total } });
          }
        }, { allowedHosts: this.source.allowedHosts });
      }
      try {
        await this.verifyArtifact(targetPath, release.asset);
        await this.inPlace.prepare(targetPath, release);
      } catch (error) {
        await fs.promises.unlink(targetPath).catch(() => {});
        throw error;
      }
      this.prepared = { version: release.latestVersion, file: targetPath };
      this.publish({ status: "ready", readyVersion: release.latestVersion, progress: null, updateKind: null });
    } catch (error) {
      this.publish({ status: "error", progress: null, updateKind: null, ...updateErrorFields(error, "download") });
    }
    return this.getState();
  }

  /** Quit, swap in the prepared copy and start it. */
  async install() {
    const release = this.persisted.release;
    if (!this.prepared || this.prepared.version !== release?.latestVersion) {
      throw new Error("no prepared update is ready to install");
    }
    this.publish({ status: "installing", error: null, errorKind: null });
    let failure;
    try {
      failure = await this.inPlace.install(this.prepared.file, release);
    } catch (error) {
      failure = error instanceof Error ? error.message : String(error);
    }
    if (failure) {
      this.prepared = null;
      this.publish({ status: "error", readyVersion: null, errorKind: "failed", error: failure });
    }
    return this.getState();
  }

  async runDownload(release) {
    const targetPath = await this.chooseSavePath(release.asset.name);
    if (!targetPath) return this.getState();
    this.publish({
      status: "downloading",
      progress: { downloaded: 0, total: release.asset.bytes },
      updateKind: "app",
      error: null,
      errorKind: null,
    });
    try {
      let lastProgressAt = 0;
      await downloadVerified(this.fetchImpl, release.asset, targetPath, (downloaded, total) => {
        const now = Date.now();
        if (downloaded === total || now - lastProgressAt >= 250) {
          lastProgressAt = now;
          this.publish({ progress: { downloaded, total } });
        }
      }, { allowedHosts: this.source.allowedHosts });
      try {
        await this.verifyArtifact(targetPath, release.asset);
      } catch (error) {
        await fs.promises.unlink(targetPath).catch(() => {});
        throw error;
      }
      const openError = await this.openPath(targetPath);
      if (openError) throw new Error(openError);
      this.publish({ status: "downloaded", progress: null, updateKind: null });
    } catch (error) {
      this.publish({ status: "error", progress: null, updateKind: null, ...updateErrorFields(error, "download") });
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
  desktopAssetNames,
  desktopAssetSpec,
  desktopUpdateFetch,
  downloadVerified,
  nextAutomaticCheckAt,
  normalizePersistedState,
  parseUpgradeOutcome,
  readStateFile,
  readJsonLimited,
  readUpdateSource,
  replaceAppImage,
  requestWithRedirects,
  resolveDesktopRelease,
  saveStateFileAtomic,
  validateUpdateUrl,
  writeAll,
};
