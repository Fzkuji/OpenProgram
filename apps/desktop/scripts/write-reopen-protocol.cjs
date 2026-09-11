"use strict";
// Build-time compatibility metadata. Never execute code from the candidate App.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const asar = require("@electron/asar");

function file(root, relative) {
  if (typeof relative !== "string" || relative.length > 512 || path.posix.isAbsolute(relative) ||
      relative.includes("\\") || relative.split("/").some(p => !p || p === "." || p === "..")) {
    throw new Error("invalid reopen protocol path");
  }
  let current = root;
  for (const name of ["", ...relative.split("/")]) {
    current = path.join(current, name);
    if (fs.lstatSync(current).isSymbolicLink()) throw new Error("symlink in reopen protocol path");
  }
  const stat = fs.statSync(current);
  if (!stat.isFile() || stat.size > 512 * 1024 * 1024) throw new Error("invalid reopen protocol file");
  return current;
}
function text(root, relative) {
  const target = file(root, relative);
  if (fs.statSync(target).size > 16 * 1024 * 1024) throw new Error("reopen protocol source is too large");
  return fs.readFileSync(target, "utf8");
}
async function binding(root, relative) {
  const hash = crypto.createHash("sha256");
  for await (const chunk of fs.createReadStream(file(root, relative))) hash.update(chunk);
  return { path: relative, sha256: hash.digest("hex") };
}

async function writeProtocol(resources) {
  if (!path.isAbsolute(resources) || !fs.lstatSync(resources).isDirectory()) throw new Error("invalid resources directory");
  const archive = file(resources, "app.asar");
  asar.uncache(archive);
  function archiveText(name) {
    const stat = asar.statFile(archive, name, false);
    if (stat.link || stat.unpacked || !Number.isSafeInteger(stat.size) || stat.size > 2 * 1024 * 1024) {
      throw new Error("invalid packaged reopen module");
    }
    return asar.extractFile(archive, name, false).toString("utf8");
  }
  if (!/^const REOPEN_PROTOCOL = 1;$/m.test(archiveText("self-update-reopen.js")) ||
      !archiveText("main.js").includes("selfUpdateReopen.resolveStartUrl") ||
      !archiveText("preload.js").includes('"self-update:session-loaded"')) {
    throw new Error("Desktop reopen protocol is unavailable");
  }
  const manifest = JSON.parse(text(resources, "runtime/runtime-manifest.json"));
  if (manifest.schema !== 2 || typeof manifest.python !== "string") throw new Error("invalid runtime manifest");
  file(resources, `runtime/${manifest.python}`);
  const prefix = path.posix.dirname(path.posix.dirname(`runtime/${manifest.python}`));
  const candidates = fs.readdirSync(path.join(resources, prefix, "lib"))
    .filter(name => /^python\d+\.\d+$/.test(name))
    .map(name => `${prefix}/lib/${name}/site-packages/`)
    .filter(site => fs.existsSync(path.join(resources, site, "openprogram/self_update/reopen.py")));
  if (candidates.length !== 1) throw new Error("ambiguous backend reopen package");
  const site = candidates[0];
  const backend = `${site}openprogram/self_update/reopen.py`;
  const routes = `${site}openprogram_server/_webui/routes/self_updates.py`;
  if (!/^REOPEN_PROTOCOL = 1$/m.test(text(resources, backend)) ||
      !text(resources, routes).includes('/desktop-reopen/ack"')) throw new Error("backend reopen protocol is unavailable");
  if (!text(resources, "update/install-app.sh").includes("--reopen-update=")) throw new Error("installer reopen protocol is unavailable");
  const chunkRoot = `${site}openprogram_server/_webui/_frontend/_next/static/chunks/`;
  const chunks = fs.readdirSync(path.join(resources, chunkRoot)).sort().filter(name => /^[A-Za-z0-9._-]+\.js$/.test(name));
  const frontend = chunks.map(name => chunkRoot + name).find(relative => {
    const source = text(resources, relative);
    return source.includes("selfUpdateReopen") && source.includes("sessionLoaded");
  });
  if (!frontend) throw new Error("compiled Web reopen protocol is unavailable");
  const paths = { desktop: "app.asar", installer: "update/install-app.sh",
    runtime_manifest: "runtime/runtime-manifest.json", backend, routes, frontend };
  const bindings = Object.fromEntries(await Promise.all(Object.entries(paths).map(async ([role, name]) =>
    [role, await binding(resources, name)])));
  const target = path.join(resources, "update/reopen-protocol.json");
  const temporary = `${target}.${crypto.randomUUID()}.tmp`;
  const fd = fs.openSync(temporary, "wx", 0o600);
  try { fs.writeFileSync(fd, JSON.stringify({ schema: 1, protocol: 1, bindings }) + "\n"); fs.fsyncSync(fd); }
  finally { fs.closeSync(fd); }
  fs.renameSync(temporary, target);
  // Older complete packages keep their reopen-only protocol. Capture capability
  // is advertised only when all three actual packaged consumers are present.
  const uiBackend = `${site}openprogram/self_update/ui_checks.py`;
  const uiFrontend = chunks.map(name => chunkRoot + name).find(relative => text(resources, relative).includes("selfUpdateCapture"));
  const uiTarget = path.join(resources, "update/ui-verification-protocol.json");
  const hasCapture = ["/self-update-ui.js", "/self-update-ui-guard.js", "/self-update-ui-scroll.js"]
    .every(name => asar.listPackage(archive).includes(name));
  if (hasCapture && fs.existsSync(path.join(resources, uiBackend)) && uiFrontend &&
      /^UI_PROTOCOL = 1$/m.test(text(resources, uiBackend)) &&
      archiveText("main.js").includes("registerUiVerificationIpc") &&
      archiveText("main.js").includes("guard: uiVerificationGuard") &&
      archiveText("self-update-ui.js").includes("guard.acquire(wc, nonce)") &&
      archiveText("preload.js").includes('"self-update:ui-capture"') &&
      text(resources, routes).includes("/desktop-verification/{nonce}")) {
    const uiBindings = { desktop: bindings.desktop, routes: bindings.routes, runtime_manifest: bindings.runtime_manifest,
      backend: await binding(resources, uiBackend), frontend: await binding(resources, uiFrontend) };
    let protocol = 1;
    const server = `${site}openprogram_server/server.py`;
    const ownerAuth = `${site}openprogram_server/_webui/owner_auth.py`;
    const scrollFrontend = chunks.map(name => chunkRoot + name).find(relative => text(resources, relative).includes("data-self-update-verification"));
    if (asar.listPackage(archive).includes("/self-update-ui-scroll.js") && scrollFrontend &&
        /^UI_INTERACTION_PROTOCOL = 1$/m.test(text(resources, uiBackend)) &&
        fs.existsSync(path.join(resources, server)) && text(resources, server).includes("permits_ws_command") &&
        fs.existsSync(path.join(resources, ownerAuth)) && text(resources, ownerAuth).includes("x-openprogram-ui-check")) {
      protocol = 2;
      Object.assign(uiBindings, { server: await binding(resources, server), owner_auth: await binding(resources, ownerAuth),
        scroll_frontend: await binding(resources, scrollFrontend) });
      const viewFrontend = chunks.map(name => chunkRoot + name).find(relative => {
        const source = text(resources, relative);
        return source.includes("sessionPerspectiveToggle") && source.includes("data-self-update-view");
      });
      const viewControls = chunks.map(name => chunkRoot + name).find(relative => {
        const source = text(resources, relative);
        return source.includes("sessionPerspectiveToggle") && source.includes("data-tab-id") && source.includes("aria-pressed");
      });
      if (/^UI_VIEW_PROTOCOL = 1$/m.test(text(resources, uiBackend)) && viewFrontend && viewControls &&
          archiveText("self-update-ui-scroll.js").includes("view_restore_failed")) {
        protocol = 3;
        uiBindings.view_frontend = await binding(resources, viewFrontend);
        uiBindings.view_controls = await binding(resources, viewControls);
      }
    }
    const uiTemporary = `${uiTarget}.${crypto.randomUUID()}.tmp`;
    const uiFd = fs.openSync(uiTemporary, "wx", 0o600);
    try { fs.writeFileSync(uiFd, JSON.stringify({ schema: 1, protocol, bindings: uiBindings }) + "\n"); fs.fsyncSync(uiFd); }
    finally { fs.closeSync(uiFd); }
    fs.renameSync(uiTemporary, uiTarget);
  } else if (fs.existsSync(uiTarget)) {
    fs.unlinkSync(uiTarget); // Do not retain a stale capability after rebuilding.
  }
  const directory = fs.openSync(path.dirname(target), "r");
  try { fs.fsyncSync(directory); } finally { fs.closeSync(directory); }
}

exports.default = async context => {
  if (context.electronPlatformName === "darwin") await writeProtocol(context.packager.getResourcesDir(context.appOutDir));
};
exports.writeProtocol = writeProtocol;
if (require.main === module) {
  if (process.argv.length !== 4 || process.argv[2] !== "--resources") {
    throw new Error("usage: write-reopen-protocol.cjs --resources /absolute/App/Contents/Resources");
  }
  writeProtocol(process.argv[3]).catch(error => { console.error(error.message); process.exitCode = 1; });
}
