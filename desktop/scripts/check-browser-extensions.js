const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const {
  bindManifestIdentity,
  createBrowserExtensionManager,
  extensionIdFromPublicKey,
  inspectExtensionDirectory,
  isAllowedStoreResponseUrl,
  parseAndVerifyCrx3,
  parseStoreListing,
  readResponseBuffer,
  storeDownloadUrl,
} = require("../browser-extension-manager");

function varint(value) {
  const bytes = [];
  let current = value;
  while (current >= 0x80) {
    bytes.push((current & 0x7f) | 0x80);
    current = Math.floor(current / 0x80);
  }
  bytes.push(current);
  return Buffer.from(bytes);
}

function bytesField(number, value) {
  const data = Buffer.from(value);
  return Buffer.concat([varint((number << 3) | 2), varint(data.length), data]);
}

function storedZip(name, contents) {
  const filename = Buffer.from(name);
  const body = Buffer.from(contents);
  const crcTable = storedZip.crcTable || (storedZip.crcTable = Array.from({ length: 256 }, (_, n) => {
    let value = n;
    for (let bit = 0; bit < 8; bit += 1) value = (value >>> 1) ^ ((value & 1) ? 0xedb88320 : 0);
    return value >>> 0;
  }));
  let crc = 0xffffffff;
  for (const byte of body) crc = (crc >>> 8) ^ crcTable[(crc ^ byte) & 0xff];
  crc = (crc ^ 0xffffffff) >>> 0;
  const local = Buffer.alloc(30);
  local.writeUInt32LE(0x04034b50, 0);
  local.writeUInt16LE(20, 4);
  local.writeUInt32LE(crc, 14);
  local.writeUInt32LE(body.length, 18);
  local.writeUInt32LE(body.length, 22);
  local.writeUInt16LE(filename.length, 26);
  const central = Buffer.alloc(46);
  central.writeUInt32LE(0x02014b50, 0);
  central.writeUInt16LE(20, 4);
  central.writeUInt16LE(20, 6);
  central.writeUInt32LE(crc, 16);
  central.writeUInt32LE(body.length, 20);
  central.writeUInt32LE(body.length, 24);
  central.writeUInt16LE(filename.length, 28);
  const centralOffset = local.length + filename.length + body.length;
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(1, 8);
  end.writeUInt16LE(1, 10);
  end.writeUInt32LE(central.length + filename.length, 12);
  end.writeUInt32LE(centralOffset, 16);
  return Buffer.concat([local, filename, body, central, filename, end]);
}

function signedCrx(zip, privateKey, publicKey) {
  const publicDer = publicKey.export({ type: "spki", format: "der" });
  const id = extensionIdFromPublicKey(publicDer);
  const idBytes = crypto.createHash("sha256").update(publicDer).digest().subarray(0, 16);
  const signedHeader = bytesField(1, idBytes);
  const size = Buffer.alloc(4);
  size.writeUInt32LE(signedHeader.length);
  const signedPayload = Buffer.concat([
    Buffer.from("CRX3 SignedData\0"),
    size,
    signedHeader,
    zip,
  ]);
  const signature = crypto.sign("sha256", signedPayload, privateKey);
  const proof = Buffer.concat([
    bytesField(1, publicDer),
    bytesField(2, signature),
  ]);
  const header = Buffer.concat([
    bytesField(2, proof),
    bytesField(10000, signedHeader),
  ]);
  const prefix = Buffer.alloc(12);
  prefix.write("Cr24", 0, "ascii");
  prefix.writeUInt32LE(3, 4);
  prefix.writeUInt32LE(header.length, 8);
  return { id, bytes: Buffer.concat([prefix, header, zip]) };
}

const edgeId = "abcdefghijklmnopabcdefghijklmnop";
assert.deepEqual(
  parseStoreListing(`https://microsoftedge.microsoft.com/addons/detail/sample/${edgeId}`),
  { store: "edge-addons", extensionId: edgeId, listingUrl: `https://microsoftedge.microsoft.com/addons/detail/sample/${edgeId}` },
);
assert.deepEqual(
  parseStoreListing(`https://chromewebstore.google.com/detail/sample/${edgeId}`),
  { store: "chrome-web-store", extensionId: edgeId, listingUrl: `https://chromewebstore.google.com/detail/sample/${edgeId}` },
);
assert.equal(parseStoreListing(`https://example.com/detail/${edgeId}`), null);
assert.equal(parseStoreListing("javascript:alert(1)"), null);

const edgeDownload = new URL(storeDownloadUrl("edge-addons", edgeId, "138.0.0.0"));
assert.equal(edgeDownload.origin, "https://edge.microsoft.com");
assert.match(edgeDownload.searchParams.get("x") || "", new RegExp(`id=${edgeId}`));
const chromeDownload = new URL(storeDownloadUrl("chrome-web-store", edgeId, "138.0.0.0"));
assert.equal(chromeDownload.origin, "https://clients2.google.com");
assert.equal(
  isAllowedStoreResponseUrl(
    "edge-addons",
    "http://msedgeextensions.f.tlu.dl.delivery.mp.microsoft.com/files/example.crx",
  ),
  true,
);
assert.equal(isAllowedStoreResponseUrl("edge-addons", "http://delivery.mp.microsoft.com.attacker.test/x"), false);
assert.equal(isAllowedStoreResponseUrl("edge-addons", "http://example.com/x"), false);

const { privateKey, publicKey } = crypto.generateKeyPairSync("rsa", { modulusLength: 2048 });
const zip = storedZip("manifest.json", JSON.stringify({ manifest_version: 3, name: "Fixture", version: "1.0.0" }));
const crx = signedCrx(zip, privateKey, publicKey);
const verified = parseAndVerifyCrx3(crx.bytes, crx.id);
assert.equal(verified.extensionId, crx.id);
assert.deepEqual(verified.archive, zip);
assert.deepEqual(verified.developerPublicKey, publicKey.export({ type: "spki", format: "der" }));

const tampered = Buffer.from(crx.bytes);
tampered[tampered.length - 30] ^= 1;
assert.throws(() => parseAndVerifyCrx3(tampered, crx.id), /invalid_signature/);
assert.throws(() => parseAndVerifyCrx3(crx.bytes, edgeId), /extension_id_mismatch/);

const localeRoot = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-extension-locale-"));
fs.mkdirSync(path.join(localeRoot, "_locales", "en"), { recursive: true });
fs.writeFileSync(path.join(localeRoot, "manifest.json"), JSON.stringify({
  manifest_version: 2,
  name: "__MSG_extensionName__",
  description: "__MSG_extensionDescription__",
  version: "1.0.0",
  default_locale: "en",
}));
fs.writeFileSync(path.join(localeRoot, "_locales", "en", "messages.json"), JSON.stringify({
  extensionName: { message: "Readable extension name" },
  extensionDescription: { message: "Readable description" },
}));
assert.deepEqual(
  {
    name: inspectExtensionDirectory(localeRoot).name,
    description: inspectExtensionDirectory(localeRoot).description,
  },
  { name: "Readable extension name", description: "Readable description" },
);
fs.rmSync(localeRoot, { recursive: true, force: true });

async function managerChecks() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-extensions-"));
  const loads = [];
  const removals = [];
  const extensions = {
    async loadExtension(directory) {
      const manifest = JSON.parse(fs.readFileSync(path.join(directory, "manifest.json"), "utf8"));
      loads.push(directory);
      const derivedId = extensionIdFromPublicKey(Buffer.from(manifest.key, "base64"));
      return { id: derivedId, name: manifest.name, version: manifest.version };
    },
    removeExtension(id) {
      removals.push(id);
    },
  };
  let fetchedUrl = "";
  const fetch = async (url) => {
    fetchedUrl = url;
    return {
      ok: true,
      status: 200,
      url,
      headers: new Headers({ "content-length": String(crx.bytes.length) }),
      arrayBuffer: async () => crx.bytes,
    };
  };
  const extractArchive = async (_archive, destination) => {
    fs.mkdirSync(destination, { recursive: true });
    fs.writeFileSync(path.join(destination, "icon.png"), Buffer.from([0x89, 0x50, 0x4e, 0x47]));
    fs.writeFileSync(path.join(destination, "manifest.json"), JSON.stringify({
      manifest_version: 3,
      name: "Fixture",
      version: "1.0.0",
      icons: { 16: "icon.png" },
      permissions: ["storage"],
      host_permissions: ["https://example.com/*"],
    }));
  };
  const manager = createBrowserExtensionManager({
    userDataPath: root,
    extensions,
    fetch,
    extractArchive,
    chromeVersion: "138.0.0.0",
    confirm: async (candidate) => {
      assert.equal(candidate.name, "Fixture");
      assert.deepEqual(candidate.permissions, ["storage"]);
      return true;
    },
  });
  const listing = `https://chromewebstore.google.com/detail/fixture/${crx.id}`;
  const installed = await manager.installFromStoreUrl(listing);
  assert.equal(installed.ok, true);
  assert.equal(new URL(fetchedUrl).origin, "https://clients2.google.com");
  assert.equal(loads.length, 1);
  assert.deepEqual(manager.list().map((item) => ({
    id: item.id,
    name: item.name,
    enabled: item.enabled,
    source: item.source,
    hasIcon: item.iconUrl.startsWith("data:image/png;base64,"),
    hasManagedPath: Object.hasOwn(item, "managedPath"),
  })), [{
    id: crx.id,
    name: "Fixture",
    enabled: true,
    source: "chrome-web-store",
    hasIcon: true,
    hasManagedPath: false,
  }]);

  const originalRenameSync = fs.renameSync;
  fs.renameSync = (source, destination) => {
    if (String(destination).endsWith(path.join("browser-extensions", "registry.json"))) {
      throw new Error("registry unavailable");
    }
    return originalRenameSync(source, destination);
  };
  try {
    assert.deepEqual(await manager.setEnabled(installed.extension.key, false), {
      ok: false,
      error: "registry_write_failed",
    });
  } finally {
    fs.renameSync = originalRenameSync;
  }
  assert.equal(manager.list()[0].enabled, true);
  assert.equal(manager.list()[0].loaded, true);

  await manager.setEnabled(installed.extension.key, false);
  assert.deepEqual(removals, [crx.id, crx.id]);
  assert.equal(manager.list()[0].enabled, false);
  assert.deepEqual(await manager.reload(installed.extension.key), {
    ok: false,
    error: "extension_disabled",
  });
  await manager.setEnabled(installed.extension.key, true);
  assert.equal(loads.length, 3);

  const restoredLoads = [];
  const restored = createBrowserExtensionManager({
    userDataPath: root,
    extensions: {
      async loadExtension(directory) {
        restoredLoads.push(directory);
        const manifest = JSON.parse(fs.readFileSync(path.join(directory, "manifest.json"), "utf8"));
        return { id: extensionIdFromPublicKey(Buffer.from(manifest.key, "base64")) };
      },
      removeExtension() {},
    },
    fetch,
    extractArchive,
    confirm: async () => true,
  });
  await restored.restore();
  assert.equal(restoredLoads.length, 1);

  await restored.remove(installed.extension.key);
  assert.deepEqual(restored.list(), []);
  assert.equal(fs.existsSync(path.join(root, "browser-extensions", "packages")), true);
  fs.rmSync(root, { recursive: true, force: true });

  const streamResponse = new Response(new Blob([Buffer.from("ab"), Buffer.from("c")]));
  assert.deepEqual(await readResponseBuffer(streamResponse, 3), Buffer.from("abc"));
  await assert.rejects(
    readResponseBuffer(new Response(new Blob([Buffer.from("abcd")])), 3),
    /invalid_crx_size/,
  );

  const extractionRoot = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-extension-extract-"));
  const extracted = createBrowserExtensionManager({
    userDataPath: extractionRoot,
    extensions: {
      async loadExtension(directory) {
        assert.equal(fs.existsSync(path.join(directory, "manifest.json")), true);
        const manifest = JSON.parse(fs.readFileSync(path.join(directory, "manifest.json"), "utf8"));
        return { id: extensionIdFromPublicKey(Buffer.from(manifest.key, "base64")) };
      },
      removeExtension() {},
    },
    fetch,
    confirm: async () => true,
  });
  assert.equal((await extracted.installFromStoreUrl(listing)).ok, true);
  fs.rmSync(extractionRoot, { recursive: true, force: true });

  const replacementRoot = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-extension-replace-"));
  let replacementVersion = "1.0.0";
  const replacementExtract = async (_archive, destination) => {
    fs.mkdirSync(destination, { recursive: true });
    fs.writeFileSync(path.join(destination, "manifest.json"), JSON.stringify({
      manifest_version: 2,
      name: "Replacement",
      version: replacementVersion,
    }));
  };
  const replacementExtensions = {
    async loadExtension(directory) {
      const manifest = JSON.parse(fs.readFileSync(path.join(directory, "manifest.json"), "utf8"));
      return {
        id: extensionIdFromPublicKey(Buffer.from(manifest.key, "base64")),
        version: manifest.version,
      };
    },
    removeExtension() {},
  };
  const replacementManager = createBrowserExtensionManager({
    userDataPath: replacementRoot,
    extensions: replacementExtensions,
    fetch,
    extractArchive: replacementExtract,
    confirm: async () => true,
  });
  assert.equal((await replacementManager.installFromStoreUrl(listing)).ok, true);
  replacementVersion = "2.0.0";
  const originalRmSync = fs.rmSync;
  fs.rmSync = (target, options) => {
    if (path.basename(String(target)).startsWith("replace-")) throw new Error("prior_cleanup_failed");
    return originalRmSync(target, options);
  };
  try {
    const replacement = await replacementManager.installFromStoreUrl(listing);
    assert.equal(replacement.ok, true);
    assert.equal(replacement.extension.version, "2.0.0");
  } finally {
    fs.rmSync = originalRmSync;
  }
  const replacementRestored = createBrowserExtensionManager({
    userDataPath: replacementRoot,
    extensions: replacementExtensions,
    fetch,
    extractArchive: replacementExtract,
    confirm: async () => true,
  });
  assert.equal((await replacementRestored.restore())[0].version, "2.0.0");
  fs.rmSync(replacementRoot, { recursive: true, force: true });

  const identityRoot = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-extension-identity-"));
  fs.writeFileSync(path.join(identityRoot, "manifest.json"), JSON.stringify({
    manifest_version: 3,
    name: "Identity",
    version: "1.0.0",
  }));
  bindManifestIdentity(identityRoot, verified.developerPublicKey, crx.id);
  const identityManifest = JSON.parse(fs.readFileSync(path.join(identityRoot, "manifest.json"), "utf8"));
  assert.equal(extensionIdFromPublicKey(Buffer.from(identityManifest.key, "base64")), crx.id);
  identityManifest.key = Buffer.from("wrong-key").toString("base64");
  fs.writeFileSync(path.join(identityRoot, "manifest.json"), JSON.stringify(identityManifest));
  assert.throws(
    () => bindManifestIdentity(identityRoot, verified.developerPublicKey, crx.id),
    /manifest_key_invalid|manifest_key_mismatch/,
  );
  fs.rmSync(identityRoot, { recursive: true, force: true });

  const registryRoot = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-extension-registry-"));
  const external = path.join(registryRoot, "do-not-delete");
  fs.mkdirSync(external);
  const registryDirectory = path.join(registryRoot, "browser-extensions");
  fs.mkdirSync(registryDirectory, { recursive: true });
  fs.writeFileSync(path.join(registryDirectory, "registry.json"), JSON.stringify({
    version: 1,
    extensions: [{
      key: "folder:outside",
      name: "Outside",
      version: "1",
      enabled: true,
      managedPath: external,
    }],
  }));
  const guarded = createBrowserExtensionManager({
    userDataPath: registryRoot,
    extensions: { loadExtension: async () => { throw new Error("must_not_load"); }, removeExtension() {} },
  });
  assert.deepEqual(await guarded.restore(), []);
  assert.equal(fs.existsSync(external), true);
  fs.rmSync(registryRoot, { recursive: true, force: true });

  const timeoutRoot = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-extension-timeout-"));
  const timeoutManager = createBrowserExtensionManager({
    userDataPath: timeoutRoot,
    extensions,
    downloadTimeoutMs: 10,
    fetch: async (_url, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
    }),
  });
  assert.deepEqual(await timeoutManager.installFromStoreUrl(listing), {
    ok: false,
    error: "download_timeout",
  });
  fs.rmSync(timeoutRoot, { recursive: true, force: true });

  const slowConfirmRoot = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-extension-confirm-"));
  const slowConfirmManager = createBrowserExtensionManager({
    userDataPath: slowConfirmRoot,
    extensions,
    downloadTimeoutMs: 10,
    fetch,
    extractArchive,
    confirm: async () => {
      await new Promise((resolve) => setTimeout(resolve, 30));
      return false;
    },
  });
  assert.deepEqual(await slowConfirmManager.installFromStoreUrl(listing), {
    ok: false,
    error: "cancelled",
  });
  fs.rmSync(slowConfirmRoot, { recursive: true, force: true });

  const mainSource = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
  const preloadSource = fs.readFileSync(path.join(__dirname, "..", "preload.js"), "utf8");
  const repositoryRoot = path.join(__dirname, "..", "..");
  const controlsSource = fs.readFileSync(path.join(repositoryRoot, "web/components/center-tabs/browser-controls.tsx"), "utf8");
  const paneSource = fs.readFileSync(path.join(repositoryRoot, "web/components/center-tabs/builtin-tab-pane.tsx"), "utf8");
  const paneStyles = fs.readFileSync(path.join(repositoryRoot, "web/components/center-tabs/center-tabs.module.css"), "utf8");
  const webTabSource = fs.readFileSync(path.join(repositoryRoot, "web/components/center-tabs/web-tab-pane.tsx"), "utf8");
  const packageJson = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "package.json"), "utf8"));
  const refreshSource = fs.readFileSync(path.join(repositoryRoot, "scripts/refresh-local-app.sh"), "utf8");
  assert.match(mainSource, /ipcMain\.handle\("extensions:install-current-page"/);
  assert.match(mainSource, /record\.view\.webContents\.getURL\(\)/);
  assert.match(mainSource, /initializeBrowserExtensions\(\)[\s\S]*registerWebTabIpc\(\)[\s\S]*createWindow\(\)/);
  for (const channel of ["list", "install-current-page", "install-store-url", "install-folder", "set-enabled", "reload", "remove"]) {
    assert.match(preloadSource, new RegExp(`extensions:${channel}`));
  }
  assert.match(controlsSource, /openBuiltinTab\("extensions"\)/);
  assert.match(paneSource, /function ExtensionsPage\(\)/);
  assert.match(paneSource, /api\.installStoreUrl\(storeUrl\.trim\(\)\)/);
  assert.match(paneSource, /microsoftedge\.microsoft\.com\/addons\/Microsoft-Edge-Extensions-Home/);
  assert.match(paneSource, /openWebTab\("https:\/\/chromewebstore\.google\.com\/"\)/);
  assert.match(paneSource, /Browse Chrome Web Store/);
  assert.match(paneStyles, /\.builtinHeader\s*\{[^}]*flex-wrap:\s*wrap/);
  assert.match(paneStyles, /\.builtinHeaderActions\s*\{[^}]*flex-wrap:\s*wrap/);
  assert.match(webTabSource, /<InstallExtensionButton bridge=\{bridge\} tabId=\{tabId\} url=\{effectiveUrl\} \/>/);
  assert.match(webTabSource, /api\.installCurrentPage\(tabId\)/);
  assert.match(webTabSource, /showToast\(text\([\s\S]*Extension installed\. Reload this page to apply it\./);
  assert.match(webTabSource, /requestGenerationRef\.current !== requestGeneration/);
  assert.match(webTabSource, /currentUrlRef\.current !== requestUrl/);
  assert.equal(packageJson.dependencies["extract-zip"], "2.0.1");
  assert.equal(packageJson.build.files.includes("browser-extension-manager.js"), true);
  assert.match(refreshSource, /main\.js preload\.js browser-extension-manager\.js packaged-runtime\.js/);
  for (const moduleName of ["extract-zip", "debug", "get-stream", "yauzl"]) {
    assert.match(refreshSource, new RegExp(`(?:^|\\s)${moduleName}(?:\\s|$)`));
  }
}

managerChecks().then(() => {
  console.log("browser extensions checks passed");
}).catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
