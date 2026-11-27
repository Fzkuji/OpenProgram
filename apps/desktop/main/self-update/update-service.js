"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { Readable } = require("node:stream");

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
// keys below, the same keys as RELEASE_PUBLIC_KEYS in official/signing.py.
// The address is UPDATE_SOURCE_URL in openprogram/operations/project_site.py.
const UPDATE_SOURCE_URL = "https://fzkuji.github.io/OpenProgram/update-source.json";
const UPDATE_SOURCE_HOSTS = new Set(["fzkuji.github.io"]);
const RELEASE_PUBLIC_KEYS = ["e40d13b9572ab80002742e74a4243faa3312a8acec0d08a63ce71999e8531381"];
const ED25519_PUBLIC_KEY_PREFIX = Buffer.from("302a300506032b6570032100", "hex");
const GITHUB_WEB_ROOT = "https://github.com";
const REPOSITORY_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}\/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;
const HOST_RE = /^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$/;
const SIGNATURE_RE = /^[0-9a-f]{128}$/;
// Three or four numeric parts (1.2.1, 1.2.1.1); a missing fourth counts as 0.
const VERSION_RE = /^(?:v)?(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?$/;
const MAX_REDIRECTS = 5;
const REQUEST_TIMEOUT_MS = 30_000;
const DOWNLOAD_IDLE_TIMEOUT_MS = 60_000;
const DOWNLOAD_ATTEMPTS = 5;
const DOWNLOAD_RETRY_DELAY_MS = 1_000;
const DOWNLOAD_RETRY_LIMIT_MS = 30_000;
const RESPONSE_ABORT = Symbol("openprogramUpdateAbort");
const NOT_PUBLISHED = "not-published";
// The update channel: channel.json on the rolling `updates` release, read by
// its plain download address (no API call). The file carries its signature,
// `{"signed": text, "signature": hex}`, and is trusted only with a signature
// from RELEASE_PUBLIC_KEYS over the UTF-8 bytes of that text. It names announced releases
// only (daily syncs go to latest.json, which Apps never read). A version-store
// App checks only this; the Python updater fetches and builds what it names.
const CHANNEL_TAG = "updates";
const CHANNEL_FILE = "channel.json";
// The signed text names its pointer, so latest.json cannot pass as channel.json.
const CHANNEL_POINTER = "channel";
const CHANNEL_SCHEMA = 2;
const MAX_POINTER_BYTES = 1024 * 1024;
const COMMIT_RE = /^[0-9a-f]{40}$/;
const PLAIN_VERSION_RE = /^\d+\.\d+\.\d+(?:\.\d+)?$/;
const BUILD_RE = /^[0-9a-f]{12}$/;
const WAITING_FOR_SHELL = "waiting_for_shell";
const WAITING_FOR_RUNTIME = "waiting_for_runtime";

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
  return match.slice(1).map((part) => Number(part || 0));
}

/** The version a matched tag names, without the "v". */
function matchedVersion(match) {
  return match.slice(1).filter((part) => part !== undefined).join(".");
}

function compareVersions(left, right) {
  const a = versionParts(left);
  const b = versionParts(right);
  for (let index = 0; index < 4; index += 1) {
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

/**
 * The file names that can serve this computer, the preferred one first. A
 * signed Windows App stays on the signed line, so it never looks at an
 * unsigned installer.
 */
function desktopAssetNames(version, platform, arch, { linuxFormat, windowsSignedOnly = true } = {}) {
  const { name } = desktopAssetSpec(version, platform, arch, { linuxFormat });
  if (platform === "darwin") return [name.replace("-unsigned.dmg", ".dmg"), name];
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
    webRoot = GITHUB_WEB_ROOT,
  } = {},
) {
  if (!release || release.draft !== false) throw new Error("draft release is not eligible");
  if (release.prerelease !== false) throw new Error("prerelease is not eligible");
  const tagMatch = VERSION_RE.exec(String(release.tag_name || ""));
  if (!tagMatch || !String(release.tag_name).startsWith("v")) throw new Error("release tag is invalid");
  const latestVersion = matchedVersion(tagMatch);
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
    names = desktopAssetNames(latestVersion, platform, arch, { linuxFormat, windowsSignedOnly });
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
    artifactKind: spec ? spec.kind : null,
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
    // The highest channel sequence seen; a lower one is a replay, ignored.
    channelSequence: 0,
    // The updater's answer to whether this App can swap in a new App shell:
    // { app: shellSwap.id, value }. It decides; the App only asks.
    shellSwap: null,
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
    channelSequence: Number.isSafeInteger(value.channelSequence) && value.channelSequence >= 0
      ? value.channelSequence : 0,
    shellSwap: value.shellSwap && typeof value.shellSwap.app === "string" && typeof value.shellSwap.value === "boolean"
      ? { app: value.shellSwap.app, value: value.shellSwap.value } : null,
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
    const tag = /\/releases\/tag\/(v\d+\.\d+\.\d+(?:\.\d+)?)$/.exec(current.pathname);
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

function channelAssetUrl(name) {
  return `${GITHUB_WEB_ROOT}/${REPOSITORY}/releases/download/${CHANNEL_TAG}/${name}`;
}

function channelReleaseUrl(version) {
  return `${GITHUB_WEB_ROOT}/${REPOSITORY}/releases/tag/v${version}`;
}

/**
 * The signed channel.json: one file, `{"signed": "<pointer JSON text>",
 * "signature": "<hex Ed25519 of that text>"}`. Throws when the signature or
 * fields do not check out; null when the file is missing (between the delete
 * and upload that replace it; the next check finds it). channel.json must
 * name an announced release, like channel.py's `parse`.
 */
async function readChannel(fetchImpl, keys = RELEASE_PUBLIC_KEYS) {
  let blob;
  try {
    blob = await readLimited(await requestWithRedirects(fetchImpl, channelAssetUrl(CHANNEL_FILE)), MAX_POINTER_BYTES);
  } catch (error) {
    if (error.httpStatus === 404) return null;
    throw error;
  }
  let envelope;
  try {
    envelope = JSON.parse(blob.toString("utf8"));
  } catch (_error) {
    throw new Error("the update channel is unreadable");
  }
  if (typeof envelope?.signed !== "string" || typeof envelope.signature !== "string") {
    throw new Error("this App does not read this update channel");
  }
  const data = Buffer.from(envelope.signed, "utf8");
  if (!signedByReleaseKey(data, Buffer.from(envelope.signature, "ascii"), keys)) {
    throw new Error("the update channel's signature does not verify");
  }
  let channel;
  try {
    channel = JSON.parse(envelope.signed);
  } catch (_error) {
    throw new Error("the update channel is unreadable");
  }
  if (
    channel?.schema !== CHANNEL_SCHEMA
    || channel.pointer !== CHANNEL_POINTER
    || channel.announce !== true
    || !Number.isSafeInteger(channel.sequence) || channel.sequence < 0
    || typeof channel.published_at !== "string" || Number.isNaN(Date.parse(channel.published_at))
    || typeof channel.version !== "string" || !PLAIN_VERSION_RE.test(channel.version)
    || typeof channel.notes !== "string" || channel.notes.length > 20_000
    || typeof channel.commit !== "string" || !COMMIT_RE.test(channel.commit)
    || !Number.isSafeInteger(channel.shell_compat)
    || typeof channel.build !== "string" || !BUILD_RE.test(channel.build)
  ) throw new Error("the update channel is invalid");
  return channel;
}

/** Newer than the running build; an unknown running sequence counts as older. */
function sequenceStatus(sequence, running) {
  const have = running?.sequence;
  return Number.isSafeInteger(have) && sequence <= have ? "up-to-date" : "available";
}

/** Whether the running shell can host code of `shellCompat`; unknown counts as yes. */
function shellFits(running, shellCompat) {
  if (!running) return true;
  const range = running.shellCompat;
  return range ? shellCompat >= range.min && shellCompat <= range.max : shellCompat === running.shell_compat;
}

/** The channel as a release the rest of the service handles. */
function channelRelease(channel, running, currentVersion) {
  return {
    channel: CHANNEL_TAG,
    sequence: channel.sequence,
    version: channel.version,
    commit: channel.commit,
    shellCompat: channel.shell_compat,
    status: sequenceStatus(channel.sequence, running),
    currentVersion,
    latestVersion: channel.version,
    publishedAt: channel.published_at,
    releaseName: `OpenProgram ${channel.version} Release`,
    releaseNotes: channel.notes,
    releaseUrl: channelReleaseUrl(channel.version),
    artifactKind: null,
    asset: null,
  };
}

function validChannelRelease(cached, currentVersion) {
  return cached.channel === CHANNEL_TAG
    && Number.isSafeInteger(cached.sequence)
    && typeof cached.version === "string" && PLAIN_VERSION_RE.test(cached.version)
    && typeof cached.commit === "string" && COMMIT_RE.test(cached.commit)
    && Number.isSafeInteger(cached.shellCompat)
    && typeof cached.latestVersion === "string" && cached.latestVersion.length <= 64
    && typeof cached.publishedAt === "string" && typeof cached.releaseName === "string"
    && typeof cached.releaseNotes === "string" && cached.releaseNotes.length <= 20_000
    && cached.releaseUrl === channelReleaseUrl(cached.version)
    && cached.asset === null && cached.artifactKind === null
    && (cached.applied === undefined || typeof cached.applied === "boolean")
    && cached.currentVersion === currentVersion;
}

/** What a prepared update is matched by: a channel version can repeat across sequences. */
function releaseKey(release) {
  return release?.channel === CHANNEL_TAG ? `${release.latestVersion}#${release.sequence}` : release?.latestVersion;
}

function waitingFor(outcome, reason) {
  return outcome?.reason === reason || outcome?.outcome === reason;
}

/** This computer's name in layer names: shell-macos-arm64, shell-windows-x86_64, shell-linux-x86_64. */
function layerPlatform(platform, arch) {
  const system = { darwin: "macos", win32: "windows", linux: "linux" }[platform];
  const machine = { x64: "x86_64", arm64: "arm64" }[arch];
  return system && machine ? `${system}-${machine}` : null;
}

/** Whether the channel carries an App shell layer for this computer. */
function hasShellLayer(channel, platform, arch) {
  return shellLayerVersion(channel, platform, arch) !== null;
}

/** The App version of the channel's shell layer for this computer, or null. */
function shellLayerVersion(channel, platform, arch) {
  const name = layerPlatform(platform, arch);
  const layers = channel?.layers;
  if (!name || !layers || typeof layers !== "object" || !Object.hasOwn(layers, `shell-${name}`)) return null;
  const version = layers[`shell-${name}`]?.version;
  return typeof version === "string" && PLAIN_VERSION_RE.test(version) ? version : null;
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
    prepareVersionUpdate = null,
    relaunchApp = () => {},
    updateSourceKeys = RELEASE_PUBLIC_KEYS,
    shellSwap = null,
    readRunningBuild = null,
  }) {
    // A version-store App that can replace itself (macOS, Windows, an
    // AppImage) takes a new App shell from its layer: the updater stages it,
    // Restart to update swaps it in (app-replace.js). Elsewhere a new shell is
    // an installer the user opens.
    this.shellSwap = versionStore ? shellSwap : null;
    this.currentVersion = currentVersion;
    this.arch = arch;
    this.platform = platform;
    this.linuxFormat = linuxFormat;
    this.windowsLine = windowsLine;
    this.versionStore = versionStore;
    this.readCurrentVersion = readCurrentVersion;
    this.applyVersionUpdate = applyVersionUpdate;
    // Prepares a version-store update without switching: "ready" then offers
    // Restart to update, which switches and starts the App again.
    this.prepareVersionUpdate = prepareVersionUpdate;
    this.relaunchApp = relaunchApp;
    this.updateSourceKeys = updateSourceKeys;
    // A version-store App's running build: { sequence, shell_compat,
    // shellCompat: { min, max } }, or null when it cannot be read. With it the
    // App checks the update channel instead of the stable release.
    this.readRunningBuild = versionStore ? readRunningBuild : null;
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
    const cached = this.persisted.release;
    if (cached !== null && cached.channel !== undefined) {
      if (this.readRunningBuild && validChannelRelease(cached, currentVersion)) {
        this.persisted.release = { ...cached, status: this.channelStatus(cached) };
      } else {
        this.persisted.release = null;
        this.persisted.lastAttemptAt = 0;
        this.persisted.lastSuccessAt = 0;
        repairedState = true;
      }
    } else if (cached !== null) {
      try {
        const assetOptions = { linuxFormat, windowsSignedOnly: false };
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
          || cached.artifactKind !== expectedAsset.kind
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
    // The prepared version-store update: { version, key, kind: "version" }.
    this.prepared = null;
    this.checkPromise = null;
    this.downloadPromise = null;
  }

  getState() {
    // A version-store update, new App shell included, installs on restart; anything else opens an installer.
    return { ...structuredClone(this.publicState), installsInPlace: this.takesVersion() };
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

  runningBuild() {
    try {
      return this.readRunningBuild?.() || null;
    } catch (_error) {
      return null;
    }
  }

  /** `applied` marks a channel update the updater took or found already held. */
  channelStatus(release) {
    return release.applied ? "up-to-date" : sequenceStatus(release.sequence, this.runningBuild());
  }

  /** One check; main.js runs one ~30 s after start and then every hour. */
  async check() {
    if (this.checkPromise) return this.checkPromise;
    this.checkPromise = this.runCheck(this.now()).finally(() => { this.checkPromise = null; });
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
      version = matchedVersion(match);
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

  /** The newest stable release as this App's update; throws DesktopAssetNotPublished when it has no file here. */
  async resolveStable() {
    const { source, manifest, release } = await this.readLatestRelease();
    const line = this.platform === "win32" ? await this.windowsLine() : null;
    const stable = resolveDesktopRelease(
      release, manifest, this.currentVersion, this.arch, this.platform, {
        linuxFormat: this.linuxFormat,
        windowsSignedOnly: line ? line.signed : true,
        versionStore: this.versionStore,
        repository: source.repository,
        webRoot: source.webRoot,
      },
    );
    return { source, release: stable };
  }

  /**
   * A version-store App's check: channel.json only. A missing channel, or one
   * older than one already seen (a replay), means no update. An update that
   * needs a newer shell brings it as a shell layer, which the updater stages
   * beside it on an App that can replace itself. Elsewhere (Linux, a
   * read-only App) the stable release's installer is offered instead, if that
   * release carries the channel's code or newer.
   */
  async resolveChannel() {
    const channel = await readChannel(this.fetchImpl, this.updateSourceKeys);
    if (!channel || channel.sequence < this.persisted.channelSequence) {
      const kept = this.persisted.release;
      return { release: kept && { ...kept, status: "up-to-date" }, status: "up-to-date", needsNewShell: null };
    }
    this.persisted.channelSequence = channel.sequence;
    const running = this.runningBuild();
    let release = channelRelease(channel, running, this.currentVersion);
    const previous = this.persisted.release;
    if (previous?.channel === CHANNEL_TAG && previous.applied && previous.sequence === channel.sequence) {
      release = { ...release, applied: true, status: "up-to-date" };
    }
    // The shell is compared by its own layer: a newer one is an update even
    // when this App already runs the channel's code.
    const shellVersion = shellLayerVersion(channel, this.platform, this.arch);
    const swap = this.swapsShell();
    if (release.status !== "available" && shellVersion && swap?.isNewer(shellVersion)) {
      release = { ...release, applied: undefined, status: "available" };
    }
    if (
      release.status !== "available" || shellFits(running, channel.shell_compat)
      || (swap && hasShellLayer(channel, this.platform, this.arch))
    ) {
      return { release, status: release.status, needsNewShell: null };
    }
    const needsNewShell = "This update needs a newer OpenProgram App.";
    let stable = null;
    try {
      stable = await this.resolveStable();
    } catch (error) {
      if (!(error instanceof DesktopAssetNotPublished)) throw error;
    }
    if (stable?.release.asset && compareVersions(stable.release.latestVersion, channel.version) >= 0) {
      this.source = stable.source;
      return { release: { ...stable.release, status: "available" }, status: "available", needsNewShell };
    }
    return { release, status: NOT_PUBLISHED, needsNewShell };
  }

  async runCheck(now) {
    try {
      this.persisted.lastAttemptAt = now;
      this.persist();
      if (this.readCurrentVersion) this.currentVersion = this.readCurrentVersion() || this.currentVersion;
      this.publish({
        status: "checking", currentVersion: this.currentVersion, error: null, errorKind: null, needsNewShell: null,
      });
      let resolved;
      if (this.readRunningBuild) {
        resolved = await this.resolveChannel();
      } else {
        const { source, release } = await this.resolveStable();
        this.source = source;
        resolved = { release, status: release.status, needsNewShell: null };
      }
      const { release } = resolved;
      this.persisted.lastSuccessAt = now;
      this.persisted.release = release;
      this.persist();
      const stillReady = resolved.status === "available" && this.prepared?.key === releaseKey(release);
      if (!stillReady) this.prepared = null;
      this.publish({
        status: stillReady ? "ready" : resolved.status,
        readyVersion: stillReady ? release.latestVersion : null,
        checkedAt: now,
        release: publicRelease(release),
        needsNewShell: resolved.needsNewShell,
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
   * With automatic checks on, a version-store release is fetched and prepared
   * without asking; "ready" then offers the restart. Called after
   * each check and once at start, for a release found before the last quit.
   */
  prepareInBackground() {
    const release = this.persisted.release;
    if (!this.persisted.automaticChecks || release?.status !== "available") return;
    if (this.prepared?.key === releaseKey(release) || this.downloadPromise) return;
    if (!this.takesVersion()) return;
    void this.download().catch(() => {});
  }

  /**
   * The shell swap, unless the updater said this App cannot swap its shell (a
   * locally signed or read-only App): then a new shell is the installer, as
   * before, until the App changes.
   */
  swapsShell() {
    const verdict = this.persisted.shellSwap;
    if (!this.shellSwap) return null;
    return verdict && verdict.app === this.shellSwap.id && verdict.value === false ? null : this.shellSwap;
  }

  /** Keep the updater's answer about the shell swap, from any of its reports. */
  noteShellSwap(outcome) {
    if (!this.shellSwap || typeof outcome?.shell_swap !== "boolean") return;
    const verdict = { app: this.shellSwap.id, value: outcome.shell_swap };
    const known = this.persisted.shellSwap;
    if (known && known.app === verdict.app && known.value === verdict.value) return;
    this.persisted.shellSwap = verdict;
    try { this.persist(); } catch (_error) { /* kept in memory */ }
  }

  /** The prepared state: Restart to update applies ``release`` (swapping in a staged App shell first). */
  markPrepared(release) {
    this.prepared = { version: release.latestVersion, key: releaseKey(release), kind: "version" };
    this.publish({ status: "ready", readyVersion: release.latestVersion, progress: null, updateKind: null });
    return this.getState();
  }

  /** Whether this App's update is a version it prepares and then switches to. */
  takesVersion() {
    return Boolean(this.versionStore && this.applyVersionUpdate && this.prepareVersionUpdate
      && !this.publicState.needsNewShell);
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
      ? (this.prepareVersionUpdate ? this.runVersionPrepare(release) : this.runVersionUpdate(release))
      : this.runDownload(release);
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
      this.noteShellSwap(outcome);
      if (waitingFor(outcome, WAITING_FOR_SHELL)) {
        this.publish({
          status: release.asset ? "available" : NOT_PUBLISHED,
          updateKind: null,
          needsNewShell: outcome.detail || "This update needs a newer OpenProgram App.",
        });
        return this.getState();
      }
      if (!outcome.ok) throw new Error(outcome.detail || "The update could not be applied.");
      // The code needs the new App staged beside this one: it runs after Restart to update.
      if (outcome.prepared === true) return this.markPrepared(release);
      this.settleVersionOutcome(release, outcome);
    } catch (error) {
      this.publish({ status: "error", updateKind: null, ...updateErrorFields(error, "update") });
    }
    return this.getState();
  }

  /**
   * Download, build and try the version-store update without switching to it;
   * "ready" then offers Restart to update.
   */
  async runVersionPrepare(release) {
    this.publish({ status: "downloading", progress: null, updateKind: "version", error: null, errorKind: null });
    try {
      const outcome = await this.prepareVersionUpdate();
      this.noteShellSwap(outcome);
      if (waitingFor(outcome, WAITING_FOR_SHELL)) {
        this.publish({
          status: release.asset ? "available" : NOT_PUBLISHED,
          updateKind: null,
          needsNewShell: outcome.detail || "This update needs a newer OpenProgram App.",
        });
        return this.getState();
      }
      if (!outcome.ok) throw new Error(outcome.detail || "The update could not be prepared.");
      if (outcome.prepared === true) return this.markPrepared(release);
      // Nothing to prepare: already running it, or this computer's runtime is not published yet.
      this.settleVersionOutcome(release, outcome);
    } catch (error) {
      this.publish({ status: "error", progress: null, updateKind: null, ...updateErrorFields(error, "download") });
    }
    return this.getState();
  }

  /**
   * The state after the updater took, held or passed over ``release``. Waiting
   * for this computer's runtime is no error: it shows as current, and the next
   * check looks again.
   */
  settleVersionOutcome(release, outcome) {
    const waiting = waitingFor(outcome, WAITING_FOR_RUNTIME);
    const channel = release.channel === CHANNEL_TAG;
    const kept = waiting || channel ? this.currentVersion : release.latestVersion;
    this.currentVersion = (this.readCurrentVersion && this.readCurrentVersion()) || kept;
    const held = outcome.applied === true || waitingFor(outcome, "up_to_date");
    const updated = channel
      ? { ...release, ...(held && !waiting ? { applied: true } : {}), currentVersion: this.currentVersion, status: "up-to-date" }
      : {
        ...release,
        currentVersion: this.currentVersion,
        status: !waiting && compareVersions(release.latestVersion, this.currentVersion) > 0 ? "available" : "up-to-date",
      };
    this.persisted.release = updated;
    this.persist();
    this.publish({
      status: updated.status, currentVersion: this.currentVersion, release: publicRelease(updated),
      updateKind: null, needsNewShell: null,
    });
  }

  /**
   * Switch to the prepared version, then start the App again on it. With a
   * newer App shell staged, the App is swapped first: it quits, the swap
   * script puts the new App in place and opens it, and the new App switches
   * to the version as it starts (resumeAfterShellSwap), so the new code first
   * runs under the shell it was tried with.
   */
  async installVersion(release) {
    this.publish({ status: "installing", error: null, errorKind: null });
    const staged = this.shellSwap?.staged() || null;
    if (staged) {
      let failure;
      try {
        failure = await this.shellSwap.install(staged, releaseKey(release));
      } catch (error) {
        failure = error instanceof Error ? error.message : String(error);
      }
      if (failure) {
        this.prepared = null;
        this.publish({ status: "error", readyVersion: null, errorKind: "failed", error: failure });
      }
      return this.getState();
    }
    let failure = null;
    let switched = true;
    try {
      const outcome = await this.applyVersionUpdate();
      this.noteShellSwap(outcome);
      // A new App was staged only now (the one prepared earlier went missing): Restart to update swaps it in.
      if (outcome.ok && outcome.prepared === true && outcome.applied === false) return this.markPrepared(release);
      if (!outcome.ok) failure = outcome.detail || "The update could not be applied.";
      else if (outcome.applied === false) failure = outcome.detail || "The prepared update was not applied.";
      else {
        // Up to date: a swap brought only a new App shell, which is running already.
        switched = !waitingFor(outcome, "up_to_date");
        this.settleVersionOutcome(release, { ...outcome, applied: true });
      }
    } catch (error) {
      failure = error instanceof Error ? error.message : String(error);
    }
    this.prepared = null;
    if (failure) {
      this.publish({ status: "error", readyVersion: null, errorKind: "failed", error: failure });
      return this.getState();
    }
    this.publish({ readyVersion: null });
    if (switched) this.relaunchApp();
    return this.getState();
  }

  /**
   * Once at start: after a swap to a new App shell, switch to the version
   * prepared with it. The marker names the prepared update; one that no
   * longer matches the verified release is dropped.
   */
  async resumeAfterShellSwap() {
    const key = this.shellSwap?.takeActivation() || null;
    const release = this.persisted.release;
    if (!key || !release || key !== releaseKey(release) || release.status !== "available") return this.getState();
    this.prepared = { version: release.latestVersion, key, kind: "version" };
    return this.installVersion(release);
  }

  /** Restart to update: switch to the prepared version, swapping in a staged App shell first. */
  async install() {
    const release = this.persisted.release;
    if (!this.prepared || this.prepared.key !== releaseKey(release)) {
      throw new Error("no prepared update is ready to install");
    }
    return this.installVersion(release);
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
  hasShellLayer,
  shellLayerVersion,
  LATEST_URL,
  compareVersions,
  desktopAssetNames,
  desktopAssetSpec,
  desktopUpdateFetch,
  downloadVerified,
  normalizePersistedState,
  parseUpgradeOutcome,
  readChannel,
  readStateFile,
  readJsonLimited,
  readUpdateSource,
  requestWithRedirects,
  resolveDesktopRelease,
  saveStateFileAtomic,
  validateUpdateUrl,
  writeAll,
};
