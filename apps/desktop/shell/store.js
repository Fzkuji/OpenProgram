"use strict";
// What the shell knows about <root> without Python: the mode it starts in, the
// versions on disk, boot records, and the control files around a switch.
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const pointers = require("./pointer");

const ATTEMPT_WINDOW_SECONDS = 600;
const FAILING_ATTEMPTS = 3;
const RECOVERY_AFTER_MS = 120_000;
const RECOVERY_ARGUMENT = "--recovery";
const IDENTITY_FILES = [
  "shell-compat.json", "python-minor", "openprogram-launcher", "openprogram-launcher.exe",
];

function stateDirectory(env = process.env, home = os.homedir()) {
  const profile = env.OPENPROGRAM_PROFILE;
  return path.join(home, profile ? `.openprogram-${profile}` : ".openprogram");
}

function runtimeRoot(env = process.env, home = os.homedir()) {
  return path.join(stateDirectory(env, home), "runtime");
}

function readDeveloper(root) {
  try {
    const value = JSON.parse(fs.readFileSync(path.join(root, "developer.json"), "utf8"));
    if (typeof value.checkout === "string" && path.isAbsolute(value.checkout) &&
        typeof value.python === "string" && path.isAbsolute(value.python)) return value;
  } catch {
    // Absent or unreadable: not developer mode.
  }
  return null;
}

/**
 * Which of the shell's three modes to start in. An unpackaged run is a source
 * checkout and behaves like embedded mode.
 */
function chooseMode({ isPackaged, resourcesPath, root }) {
  if (!isPackaged) return { mode: "embedded" };
  const developer = readDeveloper(root);
  if (developer) return { mode: "developer", developer };
  if (fs.existsSync(path.join(resourcesPath, "instance.json")) ||
      fs.existsSync(path.join(resourcesPath, "runtime"))) {
    return { mode: "embedded" };
  }
  return { mode: "managed" };
}

function repoIsEmpty(root) {
  try {
    return fs.readdirSync(path.join(root, "repo")).length === 0;
  } catch {
    return true;
  }
}

function needsSeed(root) {
  return fs.existsSync(path.join(root, ".seeding")) ||
    (!fs.existsSync(path.join(root, "current")) && repoIsEmpty(root));
}

function readManifest(root, id) {
  try {
    const value = JSON.parse(fs.readFileSync(path.join(root, "versions", id, "version.json"), "utf8"));
    return value && value.id === id ? value : null;
  } catch {
    return null;
  }
}

function withinCompat(manifest, compat) {
  const value = manifest && manifest.shell_compat;
  return Number.isInteger(value) && value >= compat.min && value <= compat.max;
}

function desktopEntry(root, id) {
  return path.join(root, "versions", id, "desktop", "main.js");
}

function failedMarker(root, id) {
  return path.join(root, "boot", `${id}.desktop-failed`);
}

function recordDesktopFailure(root, id, shellVersion, error) {
  fs.mkdirSync(path.join(root, "boot"), { recursive: true });
  fs.writeFileSync(failedMarker(root, id), `${JSON.stringify({
    shell: shellVersion,
    at: Math.floor(Date.now() / 1000),
    error: String(error && error.stack ? error.stack : error).slice(0, 4000),
  })}\n`);
}

/** A marker counts for the shell that wrote it: a newer shell tries again. */
function desktopFailed(root, id, shellVersion) {
  try {
    return JSON.parse(fs.readFileSync(failedMarker(root, id), "utf8")).shell === shellVersion;
  } catch (error) {
    return error instanceof SyntaxError;
  }
}

function recentAttempts(root, id, now = Date.now() / 1000) {
  try {
    return fs.readFileSync(path.join(root, "boot", `${id}.attempts`), "ascii").split(/\s+/)
      .filter((line) => /^\d+$/.test(line) && now - Number(line) < ATTEMPT_WINDOW_SECONDS).length;
  } catch {
    return 0;
  }
}

function usable(root, manifest, compat) {
  return withinCompat(manifest, compat) &&
    fs.existsSync(desktopEntry(root, manifest.id)) &&
    fs.existsSync(path.join(root, "bases", String(manifest.base)));
}

/** The version whose main process to load, or null when none can be. */
function chooseDesktop(root, pointer, compat, shellVersion) {
  const fallback = pointers.fallback(pointer);
  const ids = [pointer.version, ...(fallback ? [fallback.version] : [])];
  for (const id of ids) {
    const manifest = readManifest(root, id);
    if (manifest && usable(root, manifest, compat) && !desktopFailed(root, id, shellVersion)) {
      return { id, manifest };
    }
  }
  return null;
}

/** The numbers Settings shows: recorded ones first, then new ones handed out the same way. */
function versionNumbers(root, manifests) {
  let recorded = {};
  try {
    recorded = JSON.parse(fs.readFileSync(path.join(root, "version-numbers.json"), "utf8")) || {};
  } catch {
    // Settings has not been opened yet.
  }
  const known = new Map(Object.entries(recorded.numbers || {})
    .filter(([, number]) => Number.isInteger(number) && number > 0));
  const taken = () => new Set(known.values());
  let following = Math.max(Number.isInteger(recorded.next) ? recorded.next : 1, Math.max(0, ...known.values()) + 1);
  for (const manifest of manifests) {
    if (known.has(manifest.id)) continue;
    if (Number.isInteger(manifest.number) && manifest.number > 0 && !taken().has(manifest.number)) {
      known.set(manifest.id, manifest.number);
      following = Math.max(following, manifest.number + 1);
    } else {
      known.set(manifest.id, following);
      following += 1;
    }
  }
  return known;
}

/** Versions a switch committed, from their pinning refs in the bare repository; null if it cannot say. */
function committedVersions(root) {
  const repo = path.join(root, "repo");
  const names = new Set();
  let readable = false;
  try {
    for (const name of fs.readdirSync(path.join(repo, "refs", "openprogram", "version"))) names.add(name);
    readable = true;
  } catch {
    // No loose refs; they may all be packed.
  }
  try {
    for (const line of fs.readFileSync(path.join(repo, "packed-refs"), "utf8").split("\n")) {
      const match = line.match(/ refs\/openprogram\/version\/(\S+)$/);
      if (match) names.add(match[1]);
    }
    readable = true;
  } catch {
    // Nothing packed yet.
  }
  // A repository with no version refs yet has committed nothing, which is an answer.
  return readable || fs.existsSync(path.join(repo, "HEAD")) ? names : null;
}

/** Retained versions the shell can start, newest first, for the recovery list. */
function listVersions(root, compat, shellVersion, pointer) {
  let names;
  try {
    names = fs.readdirSync(path.join(root, "versions"));
  } catch {
    return [];
  }
  const manifests = names.filter((name) => !name.startsWith(".") && pointers.validId(name))
    .map((name) => readManifest(root, name)).filter(Boolean)
    .sort((left, right) => (String(left.id) < String(right.id) ? -1 : 1));
  const numbers = versionNumbers(root, manifests);
  const committed = committedVersions(root);
  const named = new Set(pointer ? [pointer.version, pointer.last_good, pointer.previous_good] : []);
  // A candidate that never passed its trial is no version to go back to, as in Settings.
  const offered = (manifest) => committed === null || committed.has(manifest.id) || named.has(manifest.id);
  return manifests.filter((manifest) => usable(root, manifest, compat) && offered(manifest)).reverse().map((manifest) => ({
    id: manifest.id,
    base: manifest.base,
    number: numbers.get(manifest.id),
    summary: String(manifest.summary || "").split("\n")[0],
    builtAt: String(manifest.built_at || ""),
    current: pointer ? pointer.version === manifest.id : false,
    failed: desktopFailed(root, manifest.id, shellVersion) ||
      recentAttempts(root, manifest.id) >= FAILING_ATTEMPTS,
  }));
}

function processAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code === "EPERM";
  }
}

/** `switching` holds a pid and a deadline while the controller has the worker down. */
function parseSwitching(text) {
  const trimmed = String(text).trim();
  try {
    const value = JSON.parse(trimmed);
    if (value && typeof value === "object") return { pid: Number(value.pid), deadline: Number(value.deadline) };
  } catch {
    // Not JSON.
  }
  const named = Object.fromEntries([...trimmed.matchAll(/([a-z_]+)=(\S+)/g)].map((match) => [match[1], match[2]]));
  if (named.pid) return { pid: Number(named.pid), deadline: Number(named.deadline) };
  const [pid, deadline] = trimmed.split(/\s+/);
  return { pid: Number(pid), deadline: Number(deadline) };
}

function switchingInProgress(root, { now = Date.now() / 1000, isAlive = processAlive } = {}) {
  let text;
  try {
    text = fs.readFileSync(path.join(root, "switching"), "utf8");
  } catch {
    return false;
  }
  const { pid, deadline } = parseSwitching(text);
  return Number.isInteger(pid) && pid > 0 && Number.isFinite(deadline) && now < deadline && isAlive(pid);
}

function appendRecoveryLog(root, message) {
  const stamp = new Date().toISOString().replace(/\.\d+Z$/, "Z");
  fs.mkdirSync(root, { recursive: true });
  fs.appendFileSync(path.join(root, "recovery.log"), `${stamp} shell: ${message}\n`);
}

/** The `main-ready` + `window-loaded` handshake a controller waits for. */
function writeHandshake(root, id, shellVersion) {
  const directory = path.join(root, "boot");
  fs.mkdirSync(directory, { recursive: true });
  const target = path.join(directory, `${id}.desktop-ready`);
  const temporary = `${target}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify({
    pid: process.pid,
    shell: shellVersion,
    main_ready: true,
    window_loaded: true,
    at: Math.floor(Date.now() / 1000),
  })}\n`);
  fs.renameSync(temporary, target);
}

/** Where the open shell is: the controller reads this for its trial and its relaunch. */
function writeShellRecord(root, shellVersion, versionId, executable = process.execPath) {
  fs.mkdirSync(root, { recursive: true });
  const target = path.join(root, "shell.json");
  const temporary = `${target}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify({ pid: process.pid, shell: shellVersion, versionId, executable })}\n`);
  fs.renameSync(temporary, target);
}

/** Where to start this App again: an AppImage's own path outlives its mount. */
function shellExecutable(env = process.env, executable = process.execPath) {
  return env.APPIMAGE || executable;
}

/**
 * What to hand `app.relaunch`. `--recovery` asks for the recovery page once, so
 * a restart from that page must not bring the page back.
 */
function relaunchOptions(env = process.env, argv = process.argv) {
  const args = argv.slice(1).filter((value) => value !== RECOVERY_ARGUMENT);
  if (env.APPIMAGE) return { execPath: env.APPIMAGE, args };
  return args.length === argv.length - 1 ? undefined : { args };
}

function readShellVersion(root) {
  try {
    return JSON.parse(fs.readFileSync(path.join(root, "shell.json"), "utf8")).shell;
  } catch {
    return null;
  }
}

function sameFile(left, right) {
  try {
    return fs.readFileSync(left).equals(fs.readFileSync(right));
  } catch {
    return false;
  }
}

/** Every file under a directory, by relative path: the content or a link's target. */
function directoryFingerprint(directory) {
  const entries = {};
  const walk = (current, prefix) => {
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const absolute = path.join(current, entry.name);
      const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
      if (entry.isDirectory()) walk(absolute, relative);
      else if (entry.isSymbolicLink()) entries[relative] = `link:${fs.readlinkSync(absolute)}`;
      else entries[relative] = crypto.createHash("sha256").update(fs.readFileSync(absolute)).digest("hex");
    }
  };
  walk(directory, "");
  return JSON.stringify(Object.keys(entries).sort().map((name) => [name, entries[name]]));
}

/** Put a file in place in one step; a running launcher on Windows is moved aside first. */
function replaceFile(source, target) {
  const staged = `${target}.${process.pid}.new`;
  fs.copyFileSync(source, staged);
  fs.chmodSync(staged, fs.statSync(source).mode & 0o777);
  try {
    fs.renameSync(staged, target);
  } catch (error) {
    if (error.code !== "EPERM" && error.code !== "EBUSY" && error.code !== "EACCES") {
      fs.rmSync(staged, { force: true });
      throw error;
    }
    const aside = `${target}.${process.pid}.old`;
    fs.renameSync(target, aside);
    fs.renameSync(staged, target);
    try {
      fs.rmSync(aside, { force: true });
    } catch {
      // Still running; the file goes when a later install clears identity/.
    }
  }
}

/** The Python line a seed's launcher embeds, read from its base's name. */
function seedPythonMinor(seedDirectory) {
  try {
    const { base } = JSON.parse(fs.readFileSync(path.join(seedDirectory, "seed.json"), "utf8"));
    return /^py(\d+\.\d+)-/.exec(base)?.[1] || null;
  } catch {
    return null;
  }
}

/**
 * A newer shell brings its own compatibility range, Python line, launcher and
 * helper, and the version store reads them from `identity/`. The helper is
 * replaced only when its code changed, at the same path with the same
 * identifier, so the system's privacy grants stay with it. A failure leaves the
 * record as it was and the next start tries again.
 */
function refreshIdentity(root, seedDirectory, shellVersion, { log = appendRecoveryLog } = {}) {
  if (readShellVersion(root) === shellVersion || !fs.existsSync(path.join(seedDirectory, "seed.json"))) return false;
  const identity = path.join(root, "identity");
  try {
    fs.mkdirSync(identity, { recursive: true });
    for (const name of IDENTITY_FILES) {
      const source = path.join(seedDirectory, name);
      const target = path.join(identity, name);
      if (fs.existsSync(source) && !sameFile(source, target)) replaceFile(source, target);
    }
    const pythonMinor = seedPythonMinor(seedDirectory);
    if (pythonMinor && !fs.existsSync(path.join(seedDirectory, "python-minor"))) {
      fs.writeFileSync(path.join(identity, "python-minor"), pythonMinor, "ascii");
    }
    const helper = path.join(seedDirectory, "helper", "OpenProgram.app");
    const installed = path.join(identity, "OpenProgram.app");
    if (fs.existsSync(helper) &&
        (!fs.existsSync(installed) || directoryFingerprint(helper) !== directoryFingerprint(installed))) {
      const staged = `${installed}.${process.pid}.new`;
      const aside = `${installed}.${process.pid}.old`;
      fs.rmSync(staged, { recursive: true, force: true });
      fs.cpSync(helper, staged, { recursive: true, verbatimSymlinks: true });
      if (fs.existsSync(installed)) fs.renameSync(installed, aside);
      fs.renameSync(staged, installed);
      fs.rmSync(aside, { recursive: true, force: true });
    }
    return true;
  } catch (error) {
    log(root, `could not refresh the shell's identity: ${error.message}`);
    return false;
  }
}

/** The release of the version `current` points at, or null when it can't be read. */
function runningRelease(root) {
  try {
    const { version } = pointers.readOrRecover(root);
    return JSON.parse(fs.readFileSync(path.join(root, "versions", version, "desktop", "package.json"), "utf8")).version || null;
  } catch {
    return null;
  }
}

/** Whether a controller asked this shell to relaunch; the request is consumed. */
function takeRelaunchRequest(root) {
  try {
    fs.unlinkSync(path.join(root, "shell-relaunch"));
    return true;
  } catch {
    return false;
  }
}

function launcherPath(root, platform = process.platform) {
  if (platform === "darwin") return path.join(root, "identity", "OpenProgram.app", "Contents", "MacOS", "OpenProgram");
  return path.join(root, "identity", platform === "win32" ? "openprogram-launcher.exe" : "openprogram-launcher");
}

function venvPython(root, id, platform = process.platform) {
  return platform === "win32"
    ? path.join(root, "versions", id, "venv", "Scripts", "python.exe")
    : path.join(root, "versions", id, "venv", "bin", "python");
}

/**
 * How to start the worker in managed mode: always through the launcher, so the
 * version guard counts the start. Short commands whose output the shell reads
 * use the version's own Python, because they are not starts of the worker.
 */
function resolveWorker(root, compat, platform = process.platform) {
  const pointer = pointers.readOrRecover(root);
  const manifest = readManifest(root, pointer.version);
  if (manifest && !withinCompat(manifest, compat)) {
    throw new Error(`version ${pointer.version} needs a different OpenProgram App`);
  }
  return {
    command: launcherPath(root, platform),
    args: ["-u", "-m", "openprogram", "worker", "run"],
    authCommand: venvPython(root, pointer.version, platform),
    authArgs: ["-I", "-B", "-m", "openprogram"],
    logFile: path.join(path.dirname(root), "worker.log"),
    env: {},
  };
}

/** Developer mode starts the worker from the checkout, as the launcher would. */
function resolveDeveloperWorker(developer, root) {
  return {
    command: developer.python,
    args: ["-u", "-m", "openprogram", "worker", "run"],
    authCommand: developer.python,
    authArgs: ["-m", "openprogram"],
    logFile: path.join(path.dirname(root), "worker.log"),
    env: { PYTHONPATH: developer.checkout },
  };
}

/** Whether the version is still migrating its stores: its marker moved in the last 15 s. */
function migrating(root, id, now = Date.now()) {
  try {
    return now - fs.statSync(path.join(root, "boot", `${id}.migrating`)).mtimeMs < 15_000;
  } catch {
    return false;
  }
}

/**
 * The recovery page takes over after two minutes with no answer. A hung worker
 * never exits, so the launcher's guard does not see it. While a version is on
 * probation the controller or the guard still has a move to make, unless there
 * is nowhere to move to.
 */
function recoveryDue(root, unreachableMs) {
  if (unreachableMs < RECOVERY_AFTER_MS || switchingInProgress(root)) return false;
  let pointer;
  try {
    pointer = pointers.read(root);
  } catch {
    return true;
  }
  if (migrating(root, pointer.version)) return false;
  return !pointer.probation || pointers.fallback(pointer) === null;
}

module.exports = {
  RECOVERY_AFTER_MS,
  RECOVERY_ARGUMENT,
  appendRecoveryLog,
  chooseDesktop,
  chooseMode,
  desktopEntry,
  desktopFailed,
  failedMarker,
  listVersions,
  launcherPath,
  needsSeed,
  parseSwitching,
  processAlive,
  readDeveloper,
  readManifest,
  recordDesktopFailure,
  recentAttempts,
  recoveryDue,
  refreshIdentity,
  relaunchOptions,
  repoIsEmpty,
  resolveDeveloperWorker,
  resolveWorker,
  runningRelease,
  runtimeRoot,
  shellExecutable,
  stateDirectory,
  switchingInProgress,
  usable,
  venvPython,
  withinCompat,
  writeHandshake,
  writeShellRecord,
  takeRelaunchRequest,
};
