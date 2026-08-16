const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const EXTENSION_ID_RE = /^[a-p]{32}$/;
const MAX_CRX_BYTES = 64 * 1024 * 1024;
const MAX_CRX_HEADER_BYTES = 1024 * 1024;

function extensionIdFromBytes(bytes) {
  return Buffer.from(bytes).toString("hex").replace(/[0-9a-f]/g, (value) =>
    String.fromCharCode("a".charCodeAt(0) + Number.parseInt(value, 16)));
}

function extensionIdFromPublicKey(publicKey) {
  return extensionIdFromBytes(
    crypto.createHash("sha256").update(publicKey).digest().subarray(0, 16),
  );
}

function parseStoreListing(value) {
  let url;
  try {
    url = new URL(String(value || ""));
  } catch (_error) {
    return null;
  }
  if (url.protocol !== "https:") return null;
  let store = null;
  if (
    url.hostname === "microsoftedge.microsoft.com"
    && url.pathname.toLowerCase().startsWith("/addons/detail/")
  ) {
    store = "edge-addons";
  } else if (
    (url.hostname === "chromewebstore.google.com"
      && url.pathname.toLowerCase().startsWith("/detail/"))
    || (url.hostname === "chrome.google.com"
      && url.pathname.toLowerCase().startsWith("/webstore/detail/"))
  ) {
    store = "chrome-web-store";
  }
  if (!store) return null;
  const extensionId = url.pathname.split("/").filter(Boolean)
    .findLast((part) => EXTENSION_ID_RE.test(part));
  if (!extensionId) return null;
  url.search = "";
  url.hash = "";
  return { store, extensionId, listingUrl: url.href };
}

function storeDownloadUrl(store, extensionId, chromeVersion = "138.0.0.0") {
  if (!EXTENSION_ID_RE.test(extensionId)) throw new Error("invalid_extension_id");
  const endpoints = {
    "edge-addons": "https://edge.microsoft.com/extensionwebstorebase/v1/crx",
    "chrome-web-store": "https://clients2.google.com/service/update2/crx",
  };
  const endpoint = endpoints[store];
  if (!endpoint) throw new Error("unsupported_store");
  const url = new URL(endpoint);
  url.searchParams.set("response", "redirect");
  url.searchParams.set("prodversion", chromeVersion);
  url.searchParams.set("acceptformat", "crx3");
  url.searchParams.set("x", `id=${extensionId}&installsource=ondemand&uc`);
  return url.href;
}

function isAllowedStoreResponseUrl(store, value) {
  let url;
  try {
    url = new URL(String(value || ""));
  } catch (_error) {
    return false;
  }
  if (store === "chrome-web-store") {
    return url.protocol === "https:"
      && new Set(["clients2.google.com", "clients2.googleusercontent.com"]).has(url.hostname);
  }
  if (store === "edge-addons") {
    if (url.protocol === "https:" && url.hostname === "edge.microsoft.com") return true;
    // Edge's signed CRX endpoint currently redirects to Microsoft's HTTP
    // delivery CDN. The exact publisher domain is constrained here and the
    // CRX3 developer signature is verified before any archive is extracted.
    return url.protocol === "http:"
      && url.hostname.endsWith(".dl.delivery.mp.microsoft.com");
  }
  return false;
}

function readVarint(buffer, start) {
  let value = 0;
  let scale = 1;
  let offset = start;
  for (let count = 0; count < 10 && offset < buffer.length; count += 1) {
    const byte = buffer[offset++];
    value += (byte & 0x7f) * scale;
    if ((byte & 0x80) === 0) return { value, offset };
    scale *= 128;
  }
  throw new Error("invalid_crx_header");
}

function protobufByteFields(buffer) {
  const fields = new Map();
  let offset = 0;
  while (offset < buffer.length) {
    const tag = readVarint(buffer, offset);
    offset = tag.offset;
    const field = Math.floor(tag.value / 8);
    const wire = tag.value & 7;
    if (!field) throw new Error("invalid_crx_header");
    if (wire === 2) {
      const size = readVarint(buffer, offset);
      offset = size.offset;
      const end = offset + size.value;
      if (!Number.isSafeInteger(end) || end < offset || end > buffer.length) {
        throw new Error("invalid_crx_header");
      }
      const values = fields.get(field) || [];
      values.push(buffer.subarray(offset, end));
      fields.set(field, values);
      offset = end;
    } else if (wire === 0) {
      offset = readVarint(buffer, offset).offset;
    } else if (wire === 1) {
      offset += 8;
    } else if (wire === 5) {
      offset += 4;
    } else {
      throw new Error("invalid_crx_header");
    }
    if (offset > buffer.length) throw new Error("invalid_crx_header");
  }
  return fields;
}

function verifyProof(proofBytes, signedPayload, algorithm) {
  const proof = protobufByteFields(proofBytes);
  const publicKey = proof.get(1)?.[0];
  const signature = proof.get(2)?.[0];
  if (!publicKey || !signature) throw new Error("invalid_crx_header");
  let key;
  try {
    key = crypto.createPublicKey({ key: publicKey, format: "der", type: "spki" });
  } catch (_error) {
    throw new Error("invalid_crx_public_key");
  }
  if (algorithm === "rsa" && key.asymmetricKeyType !== "rsa") {
    throw new Error("invalid_crx_public_key");
  }
  if (algorithm === "ecdsa" && key.asymmetricKeyType !== "ec") {
    throw new Error("invalid_crx_public_key");
  }
  if (!crypto.verify("sha256", signedPayload, key, signature)) {
    throw new Error("invalid_signature");
  }
  return publicKey;
}

function parseAndVerifyCrx3(input, expectedExtensionId) {
  const buffer = Buffer.from(input);
  if (buffer.length < 16 || buffer.length > MAX_CRX_BYTES) {
    throw new Error("invalid_crx_size");
  }
  if (buffer.toString("ascii", 0, 4) !== "Cr24" || buffer.readUInt32LE(4) !== 3) {
    throw new Error("unsupported_crx_format");
  }
  const headerSize = buffer.readUInt32LE(8);
  if (headerSize < 1 || headerSize > MAX_CRX_HEADER_BYTES || 12 + headerSize >= buffer.length) {
    throw new Error("invalid_crx_header");
  }
  const headerBytes = buffer.subarray(12, 12 + headerSize);
  for (const token of [Buffer.from("PK\x05\x06", "binary"), Buffer.from("PK\x06\x07", "binary"), Buffer.from("PK\x06\x06", "binary")]) {
    if (headerBytes.includes(token)) throw new Error("invalid_crx_header");
  }
  const header = protobufByteFields(headerBytes);
  const signedHeader = header.get(10000)?.[0];
  if (!signedHeader) throw new Error("invalid_crx_header");
  const signedData = protobufByteFields(signedHeader);
  const idBytes = signedData.get(1)?.[0];
  if (!idBytes || idBytes.length !== 16) throw new Error("invalid_crx_header");
  const extensionId = extensionIdFromBytes(idBytes);
  if (expectedExtensionId && extensionId !== expectedExtensionId) {
    throw new Error("extension_id_mismatch");
  }
  const archive = buffer.subarray(12 + headerSize);
  if (archive.readUInt32LE(0) !== 0x04034b50) throw new Error("invalid_crx_archive");
  const signedHeaderSize = Buffer.alloc(4);
  signedHeaderSize.writeUInt32LE(signedHeader.length);
  const signedPayload = Buffer.concat([
    Buffer.from("CRX3 SignedData\0"),
    signedHeaderSize,
    signedHeader,
    archive,
  ]);
  const proofs = [
    ...(header.get(2) || []).map((bytes) => [bytes, "rsa"]),
    ...(header.get(3) || []).map((bytes) => [bytes, "ecdsa"]),
  ];
  if (proofs.length === 0) throw new Error("missing_signature");
  let developerProof = false;
  let developerPublicKey = null;
  for (const [proof, algorithm] of proofs) {
    const publicKey = verifyProof(proof, signedPayload, algorithm);
    if (extensionIdFromPublicKey(publicKey) === extensionId) {
      developerProof = true;
      developerPublicKey = Buffer.from(publicKey);
    }
  }
  if (!developerProof) throw new Error("missing_developer_signature");
  return {
    extensionId,
    developerPublicKey,
    archive: Buffer.from(archive),
  };
}

function readJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (_error) {
    return fallback;
  }
}

function writeJsonAtomic(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temporary = `${file}.${process.pid}.${crypto.randomUUID()}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  fs.renameSync(temporary, file);
}

function pathInside(root, candidate) {
  if (typeof candidate !== "string" || !candidate) return false;
  const relative = path.relative(root, path.resolve(candidate));
  return relative !== "" && relative !== ".." && !relative.startsWith(`..${path.sep}`)
    && !path.isAbsolute(relative);
}

async function readResponseBuffer(response, maxBytes = MAX_CRX_BYTES) {
  const reader = response?.body?.getReader?.();
  if (!reader) {
    const buffer = Buffer.from(await response.arrayBuffer());
    if (buffer.length > maxBytes) throw new Error("invalid_crx_size");
    return buffer;
  }
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunk = Buffer.from(value);
      size += chunk.length;
      if (size > maxBytes) throw new Error("invalid_crx_size");
      chunks.push(chunk);
    }
    return Buffer.concat(chunks, size);
  } catch (error) {
    await reader.cancel?.().catch?.(() => {});
    throw error;
  } finally {
    reader.releaseLock?.();
  }
}

function safeCode(error) {
  const code = String(error?.message || error?.code || "extension_install_failed");
  return /^[a-z][a-z0-9_]{0,63}$/.test(code) ? code : "extension_install_failed";
}

function manifestCompatibility(manifest) {
  const incompatible = [];
  const warnings = [];
  if (![2, 3].includes(manifest.manifest_version)) incompatible.push("manifest_version");
  if (manifest.background?.service_worker) incompatible.push("background.service_worker");
  const permissions = Array.isArray(manifest.permissions) ? manifest.permissions : [];
  if (permissions.includes("declarativeNetRequest") || manifest.declarative_net_request) {
    incompatible.push("declarativeNetRequest");
  }
  if (manifest.action || manifest.browser_action || manifest.page_action) {
    warnings.push("toolbar_action");
  }
  return {
    status: incompatible.length ? "incompatible" : warnings.length ? "limited" : "compatible",
    incompatible,
    warnings,
  };
}

function manifestMessages(manifest, directory) {
  const localeNames = [];
  if (typeof manifest.default_locale === "string") localeNames.push(manifest.default_locale);
  localeNames.push("en", "en_US");
  try {
    localeNames.push(...fs.readdirSync(path.join(directory, "_locales")));
  } catch (_error) {
    // A manifest without locales can still use literal labels.
  }
  for (const locale of [...new Set(localeNames)]) {
    if (!/^[A-Za-z0-9_@-]+$/.test(locale)) continue;
    const file = path.join(directory, "_locales", locale, "messages.json");
    try {
      const stat = fs.lstatSync(file);
      if (!stat.isFile() || stat.size > 1024 * 1024) continue;
      const messages = readJson(file, null);
      if (messages && typeof messages === "object") return messages;
    } catch (_error) {
      // Try the next safe locale.
    }
  }
  return null;
}

function resolveManifestText(value, messages, fallback) {
  if (typeof value !== "string" || !value.trim()) return fallback;
  const lookup = messages && typeof messages === "object"
    ? new Map(Object.entries(messages).map(([key, entry]) => [key.toLowerCase(), entry]))
    : new Map();
  const resolved = value.replace(/__MSG_([A-Za-z0-9_@-]+)__/gi, (token, key) => {
    const message = lookup.get(String(key).toLowerCase())?.message;
    return typeof message === "string" && message.trim() ? message : token;
  }).trim();
  return /__MSG_[A-Za-z0-9_@-]+__/i.test(resolved) ? fallback : resolved;
}

function manifestIconPath(manifest, directory) {
  if (!manifest.icons || typeof manifest.icons !== "object") return "";
  const candidates = Object.entries(manifest.icons)
    .filter(([size, value]) => /^\d+$/.test(size) && typeof value === "string")
    .sort(([left], [right]) => Number(right) - Number(left));
  for (const [, relative] of candidates) {
    const file = path.resolve(directory, relative);
    if (!pathInside(directory, file)) continue;
    try {
      const stat = fs.lstatSync(file);
      if (stat.isFile() && stat.size <= 1024 * 1024) return path.relative(directory, file);
    } catch (_error) {
      // Continue to a smaller declared icon.
    }
  }
  return "";
}

function inspectExtensionDirectory(directory) {
  const manifestFile = path.join(directory, "manifest.json");
  let stat;
  try {
    stat = fs.statSync(manifestFile);
  } catch (_error) {
    throw new Error("manifest_missing");
  }
  if (!stat.isFile() || stat.size > 1024 * 1024) throw new Error("manifest_invalid");
  const manifest = readJson(manifestFile, null);
  if (!manifest || typeof manifest !== "object") throw new Error("manifest_invalid");
  if (typeof manifest.name !== "string" || !manifest.name.trim()) throw new Error("manifest_invalid");
  if (typeof manifest.version !== "string" || !manifest.version.trim()) throw new Error("manifest_invalid");
  const messages = manifestMessages(manifest, directory);
  const permissions = Array.isArray(manifest.permissions)
    ? manifest.permissions.filter((value) => typeof value === "string")
    : [];
  const hostPermissions = [
    ...(Array.isArray(manifest.host_permissions) ? manifest.host_permissions : []),
    ...(Array.isArray(manifest.content_scripts)
      ? manifest.content_scripts.flatMap((script) => Array.isArray(script?.matches) ? script.matches : [])
      : []),
  ].filter((value, index, values) => typeof value === "string" && values.indexOf(value) === index);
  return {
    name: resolveManifestText(manifest.name, messages, "Browser extension"),
    description: resolveManifestText(manifest.description, messages, ""),
    version: manifest.version.trim(),
    permissions,
    hostPermissions,
    iconRelativePath: manifestIconPath(manifest, directory),
    compatibility: manifestCompatibility(manifest),
  };
}

function bindManifestIdentity(directory, publicKey, expectedExtensionId) {
  const manifestFile = path.join(directory, "manifest.json");
  const manifest = readJson(manifestFile, null);
  if (!manifest || typeof manifest !== "object") throw new Error("manifest_invalid");
  if (typeof manifest.key === "string" && manifest.key) {
    const declaredKey = Buffer.from(manifest.key, "base64");
    try {
      crypto.createPublicKey({ key: declaredKey, format: "der", type: "spki" });
    } catch (_error) {
      throw new Error("manifest_key_invalid");
    }
    if (extensionIdFromPublicKey(declaredKey) !== expectedExtensionId) {
      throw new Error("manifest_key_mismatch");
    }
    return;
  }
  manifest.key = Buffer.from(publicKey).toString("base64");
  writeJsonAtomic(manifestFile, manifest);
}

function validateExtractedTree(root, limits = {}) {
  const maxFiles = limits.maxFiles || 10_000;
  const maxBytes = limits.maxBytes || 256 * 1024 * 1024;
  let files = 0;
  let bytes = 0;
  const visit = (directory) => {
    for (const name of fs.readdirSync(directory)) {
      const file = path.join(directory, name);
      const stat = fs.lstatSync(file);
      if (stat.isSymbolicLink()) throw new Error("extension_symlink_rejected");
      if (stat.isDirectory()) visit(file);
      else if (stat.isFile()) {
        files += 1;
        bytes += stat.size;
        if (files > maxFiles || bytes > maxBytes) throw new Error("extension_too_large");
      } else {
        throw new Error("extension_entry_rejected");
      }
    }
  };
  visit(root);
}

async function defaultExtractArchive(archive, destination) {
  // Lazy require keeps pure CRX verification usable in environments that do
  // not install the desktop runtime dependencies.
  const extract = require("extract-zip");
  const archiveFile = path.join(path.dirname(destination), "package.zip");
  fs.writeFileSync(archiveFile, archive, { mode: 0o600 });
  try {
    let files = 0;
    let bytes = 0;
    await extract(archiveFile, {
      dir: destination,
      onEntry(entry) {
        files += 1;
        bytes += Number(entry.uncompressedSize || 0);
        if (files > 10_000 || bytes > 256 * 1024 * 1024) {
          throw new Error("extension_too_large");
        }
      },
    });
    validateExtractedTree(destination);
  } finally {
    fs.rmSync(archiveFile, { force: true });
  }
}

function extensionIconDataUrl(entry) {
  const relative = entry.iconRelativePath;
  if (!relative || !entry.managedPath) return "";
  const file = path.resolve(entry.managedPath, relative);
  if (!pathInside(entry.managedPath, file)) return "";
  const mime = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
  }[path.extname(file).toLowerCase()];
  if (!mime) return "";
  try {
    const stat = fs.lstatSync(file);
    if (!stat.isFile() || stat.size > 1024 * 1024) return "";
    return `data:${mime};base64,${fs.readFileSync(file).toString("base64")}`;
  } catch (_error) {
    return "";
  }
}

function publicEntry(entry, loadedKeys) {
  return {
    key: entry.key,
    id: entry.id || "",
    name: entry.name,
    description: entry.description || "",
    version: entry.version,
    source: entry.source,
    sourceUrl: entry.sourceUrl || "",
    iconUrl: extensionIconDataUrl(entry),
    enabled: entry.enabled === true,
    loaded: loadedKeys.has(entry.key),
    permissions: [...(entry.permissions || [])],
    hostPermissions: [...(entry.hostPermissions || [])],
    compatibility: entry.compatibility,
    error: entry.error || "",
  };
}

function createBrowserExtensionManager(options) {
  const userDataPath = path.resolve(options.userDataPath);
  const root = path.join(userDataPath, "browser-extensions");
  const packagesRoot = path.join(root, "packages");
  const stagingRoot = path.join(root, "staging");
  const registryFile = path.join(root, "registry.json");
  const extensionApi = options.extensions;
  const fetchImpl = options.fetch;
  const extractArchive = options.extractArchive || defaultExtractArchive;
  const defaultConfirm = options.confirm || (async () => false);
  const chromeVersion = options.chromeVersion || "138.0.0.0";
  const downloadTimeoutMs = options.downloadTimeoutMs || 30_000;
  const rawRegistry = readJson(registryFile, { version: 1, extensions: [] });
  let entries = Array.isArray(rawRegistry?.extensions)
    ? rawRegistry.extensions.filter((entry) => (
      entry
      && typeof entry.key === "string"
      && pathInside(packagesRoot, entry.managedPath)
    ))
    : [];
  const loadedKeys = new Set();
  fs.rmSync(stagingRoot, { recursive: true, force: true });

  const persist = () => {
    try {
      writeJsonAtomic(registryFile, { version: 1, extensions: entries });
    } catch (_error) {
      throw new Error("registry_write_failed");
    }
  };
  const find = (key) => entries.find((entry) => entry.key === key);
  const unload = (entry) => {
    if (entry?.id && loadedKeys.has(entry.key)) extensionApi.removeExtension(entry.id);
    loadedKeys.delete(entry?.key);
  };
  const load = async (entry) => {
    if (!pathInside(packagesRoot, entry?.managedPath)) throw new Error("invalid_managed_path");
    const loaded = await extensionApi.loadExtension(entry.managedPath, { allowFileAccess: false });
    if (entry.id && loaded.id !== entry.id) {
      extensionApi.removeExtension(loaded.id);
      throw new Error("extension_id_mismatch");
    }
    entry.id = loaded.id;
    loadedKeys.add(entry.key);
    entry.error = "";
  };

  async function commitPrepared(unpackedDirectory, source, sourceUrl, expectedId, metadata) {
    const key = expectedId ? `${source}:${expectedId}` : `local:${crypto.randomUUID()}`;
    const prior = find(key);
    const packageKey = crypto.createHash("sha256").update(key).digest("hex").slice(0, 24);
    const targetParent = path.join(packagesRoot, packageKey);
    const target = path.join(targetParent, `${metadata.version}-${crypto.randomUUID()}`);
    fs.mkdirSync(targetParent, { recursive: true });
    fs.renameSync(unpackedDirectory, target);
    const entry = {
      key,
      id: expectedId || "",
      name: metadata.name,
      description: metadata.description,
      version: metadata.version,
      source,
      sourceUrl,
      enabled: metadata.compatibility.status !== "incompatible",
      permissions: metadata.permissions,
      hostPermissions: metadata.hostPermissions,
      iconRelativePath: metadata.iconRelativePath,
      compatibility: metadata.compatibility,
      managedPath: target,
      error: "",
    };
    const previousEntries = entries;
    let priorRemovalPath = "";
    try {
      if (prior) unload(prior);
      if (entry.enabled) await load(entry);
      if (prior?.managedPath && prior.managedPath !== target && fs.existsSync(prior.managedPath)) {
        fs.mkdirSync(stagingRoot, { recursive: true });
        priorRemovalPath = path.join(stagingRoot, `replace-${crypto.randomUUID()}`);
        fs.renameSync(prior.managedPath, priorRemovalPath);
      }
      entries = [...entries.filter((item) => item.key !== key), entry];
      persist();
      if (priorRemovalPath) {
        try {
          fs.rmSync(priorRemovalPath, { recursive: true, force: true });
        } catch (_cleanupError) {
          // Startup cleanup removes committed replacement orphans.
        }
      }
      return publicEntry(entry, loadedKeys);
    } catch (error) {
      entries = previousEntries;
      try {
        unload(entry);
      } catch (_unloadError) {
        loadedKeys.delete(key);
      }
      fs.rmSync(target, { recursive: true, force: true });
      if (priorRemovalPath && fs.existsSync(priorRemovalPath) && prior?.managedPath) {
        fs.mkdirSync(path.dirname(prior.managedPath), { recursive: true });
        fs.renameSync(priorRemovalPath, prior.managedPath);
      }
      if (prior?.enabled && !loadedKeys.has(prior.key)) {
        try {
          await load(prior);
        } catch (_restoreError) {
          prior.error = "restore_failed";
        }
      }
      throw error;
    }
  }

  async function prepareAndInstall(stage, source, sourceUrl, expectedId, confirmInstall) {
    const unpacked = path.join(stage, "unpacked");
    const metadata = inspectExtensionDirectory(unpacked);
    const accepted = await confirmInstall({
      ...metadata,
      source,
      sourceUrl,
      extensionId: expectedId || "",
    });
    if (!accepted) throw new Error("cancelled");
    return commitPrepared(unpacked, source, sourceUrl, expectedId, metadata);
  }

  return {
    list() {
      return entries.map((entry) => publicEntry(entry, loadedKeys));
    },

    async installFromStoreUrl(value, installOptions = {}) {
      const listing = parseStoreListing(value);
      if (!listing) return { ok: false, error: "invalid_store_url" };
      if (typeof fetchImpl !== "function") return { ok: false, error: "download_unavailable" };
      const stage = path.join(stagingRoot, crypto.randomUUID());
      const downloadController = new AbortController();
      let downloadTimedOut = false;
      const downloadTimer = setTimeout(() => {
        downloadTimedOut = true;
        downloadController.abort();
      }, downloadTimeoutMs);
      try {
        fs.mkdirSync(stage, { recursive: true });
        const downloadUrl = storeDownloadUrl(listing.store, listing.extensionId, chromeVersion);
        const response = await fetchImpl(downloadUrl, {
          redirect: "follow",
          signal: downloadController.signal,
        });
        if (!response?.ok) throw new Error("download_failed");
        const finalUrl = new URL(response.url || downloadUrl);
        if (!isAllowedStoreResponseUrl(listing.store, finalUrl)) {
          throw new Error("download_redirect_rejected");
        }
        const declaredSize = Number(response.headers?.get?.("content-length") || 0);
        if (declaredSize > MAX_CRX_BYTES) throw new Error("invalid_crx_size");
        const bytes = await readResponseBuffer(response);
        const verified = parseAndVerifyCrx3(bytes, listing.extensionId);
        await extractArchive(verified.archive, path.join(stage, "unpacked"));
        validateExtractedTree(path.join(stage, "unpacked"));
        bindManifestIdentity(
          path.join(stage, "unpacked"),
          verified.developerPublicKey,
          listing.extensionId,
        );
        const extension = await prepareAndInstall(
          stage,
          listing.store,
          listing.listingUrl,
          listing.extensionId,
          installOptions.confirm || defaultConfirm,
        );
        return { ok: true, extension };
      } catch (error) {
        return { ok: false, error: downloadTimedOut ? "download_timeout" : safeCode(error) };
      } finally {
        clearTimeout(downloadTimer);
        fs.rmSync(stage, { recursive: true, force: true });
      }
    },

    async installFromFolder(directory, installOptions = {}) {
      const source = path.resolve(String(directory || ""));
      const stage = path.join(stagingRoot, crypto.randomUUID());
      try {
        if (!fs.statSync(source).isDirectory()) throw new Error("invalid_extension_folder");
        fs.mkdirSync(stage, { recursive: true });
        fs.cpSync(source, path.join(stage, "unpacked"), {
          recursive: true,
          dereference: false,
          errorOnExist: true,
        });
        validateExtractedTree(path.join(stage, "unpacked"));
        const extension = await prepareAndInstall(
          stage,
          "folder",
          "",
          "",
          installOptions.confirm || defaultConfirm,
        );
        return { ok: true, extension };
      } catch (error) {
        return { ok: false, error: safeCode(error) };
      } finally {
        fs.rmSync(stage, { recursive: true, force: true });
      }
    },

    async restore() {
      for (const entry of entries) {
        if (!entry.enabled) continue;
        try {
          await load(entry);
        } catch (error) {
          entry.error = safeCode(error);
        }
      }
      persist();
      return this.list();
    },

    async setEnabled(key, enabled) {
      const entry = find(String(key || ""));
      if (!entry) return { ok: false, error: "extension_not_found" };
      const wasEnabled = entry.enabled;
      const wasLoaded = loadedKeys.has(entry.key);
      let registryWriteStarted = false;
      try {
        if (enabled) {
          if (entry.compatibility?.status === "incompatible") {
            return { ok: false, error: "extension_incompatible" };
          }
          await load(entry);
        } else {
          unload(entry);
        }
        entry.enabled = Boolean(enabled);
        registryWriteStarted = true;
        persist();
        return { ok: true, extension: publicEntry(entry, loadedKeys) };
      } catch (error) {
        const failureCode = safeCode(error);
        entry.enabled = wasEnabled;
        try {
          if (wasLoaded && !loadedKeys.has(entry.key)) await load(entry);
          if (!wasLoaded && loadedKeys.has(entry.key)) unload(entry);
        } catch (_restoreError) {
          entry.error = "restore_failed";
          return { ok: false, error: entry.error };
        }
        entry.error = failureCode;
        if (!registryWriteStarted) {
          try {
            persist();
          } catch (_persistError) {
            // A load/unload failure remains visible for this process.
          }
        }
        return { ok: false, error: entry.error };
      }
    },

    async reload(key) {
      const entry = find(String(key || ""));
      if (!entry) return { ok: false, error: "extension_not_found" };
      if (!entry.enabled) return { ok: false, error: "extension_disabled" };
      try {
        unload(entry);
        await load(entry);
        entry.enabled = true;
        persist();
        return { ok: true, extension: publicEntry(entry, loadedKeys) };
      } catch (error) {
        entry.error = safeCode(error);
        try {
          persist();
        } catch (_persistError) {
          // The prior enabled registry entry remains authoritative.
        }
        return { ok: false, error: entry.error };
      }
    },

    async remove(key) {
      const entry = find(String(key || ""));
      if (!entry) return { ok: false, error: "extension_not_found" };
      if (!pathInside(packagesRoot, entry.managedPath)) {
        return { ok: false, error: "invalid_managed_path" };
      }
      const priorEntries = entries;
      const removalPath = path.join(stagingRoot, `remove-${crypto.randomUUID()}`);
      let registryCommitted = false;
      try {
        fs.mkdirSync(stagingRoot, { recursive: true });
        unload(entry);
        fs.renameSync(entry.managedPath, removalPath);
        entries = entries.filter((item) => item.key !== entry.key);
        persist();
        registryCommitted = true;
        fs.rmSync(removalPath, { recursive: true, force: true });
        return { ok: true };
      } catch (error) {
        if (registryCommitted) return { ok: true };
        entries = priorEntries;
        let rollbackFailed = false;
        try {
          if (fs.existsSync(removalPath) && !fs.existsSync(entry.managedPath)) {
            fs.mkdirSync(path.dirname(entry.managedPath), { recursive: true });
            fs.renameSync(removalPath, entry.managedPath);
          }
        } catch (_rollbackError) {
          rollbackFailed = true;
        }
        const failureCode = safeCode(error);
        if (entry.enabled) {
          try {
            await load(entry);
          } catch (_restoreError) {
            rollbackFailed = true;
          }
        }
        entry.error = rollbackFailed ? "restore_failed" : failureCode;
        try {
          persist();
        } catch (_persistError) {
          // The original registry was not replaced, so the disk state remains authoritative.
        }
        return { ok: false, error: entry.error };
      }
    },
  };
}

module.exports = {
  createBrowserExtensionManager,
  bindManifestIdentity,
  extensionIdFromPublicKey,
  inspectExtensionDirectory,
  isAllowedStoreResponseUrl,
  parseAndVerifyCrx3,
  parseStoreListing,
  readResponseBuffer,
  storeDownloadUrl,
};
