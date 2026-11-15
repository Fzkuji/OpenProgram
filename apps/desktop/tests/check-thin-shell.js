"use strict";

// The thin shell: its three modes, the pointer protocol, the version list, the
// first-run seed and the recovery actions, all against temporary directories
// and a stand-in Electron. Nothing here starts an App.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const zlib = require("node:zlib");
const Module = require("node:module");

const pointers = require("../shell/pointer");
const store = require("../shell/store");
const seedModule = require("../shell/seed");
const recovery = require("../shell/recovery");

const COMPAT = { min: 1, max: 1 };
const BASE = "py3.12-0123456789abcdef";

function temporaryRoot() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "thin-shell-"));
  return path.join(directory, "state", "runtime");
}

function writeVersion(root, id, extra = {}) {
  const directory = path.join(root, "versions", id);
  fs.mkdirSync(path.join(directory, "desktop"), { recursive: true });
  fs.mkdirSync(path.join(root, "bases", BASE), { recursive: true });
  fs.writeFileSync(path.join(directory, "desktop", "main.js"), "exports.start = () => Promise.resolve();\n");
  fs.writeFileSync(path.join(directory, "version.json"), JSON.stringify({
    id, base: BASE, shell_compat: 1, summary: `Summary of ${id}`, built_at: "2026-11-08T10:42:00Z", ...extra,
  }));
}

function writePointer(root, values = {}) {
  fs.mkdirSync(root, { recursive: true });
  fs.writeFileSync(path.join(root, "current"), pointers.render({
    generation: 5, version: "v2", base: BASE,
    last_good: "v1", last_good_base: BASE, previous_good: "", previous_good_base: "",
    probation: "", probation_deadline: "", writer: "controller", extras: [], ...values,
  }));
}

// --- pointer protocol -------------------------------------------------------
{
  const text = [
    "schema=1", "generation=17", "version=20261108T153012Z-3b08fd06e123", "base=py3.12-9f2c41aa77d04c11",
    "last_good=20261101T101500Z-aa11bb22cc33", "last_good_base=py3.12-9f2c41aa77d04c11",
    "previous_good=20261020T091100Z-77ee11aa22bb", "previous_good_base=py3.12-9f2c41aa77d04c11",
    "probation=u_7f3a9c", "probation_deadline=1794134010", "writer=controller", "",
  ].join("\n");
  const parsed = pointers.parse(text);
  assert.equal(parsed.generation, 17);
  assert.equal(pointers.render(parsed), text, "the format round-trips byte for byte");
  assert.deepEqual(pointers.fallback(parsed), { version: "20261101T101500Z-aa11bb22cc33", base: "py3.12-9f2c41aa77d04c11" });
  assert.deepEqual(pointers.fallback({ ...parsed, version: parsed.last_good }),
    { version: parsed.previous_good, base: parsed.previous_good_base });
  assert.throws(() => pointers.parse("schema=2\n"), pointers.PointerError);
  assert.throws(() => pointers.parse("schema=1\ngeneration=x\nversion=a\nbase=b\n"), pointers.PointerError);
  assert.throws(() => pointers.parse("schema=1\ngeneration=1\nversion=../a\nbase=b\n"), pointers.PointerError);

  const root = temporaryRoot();
  writePointer(root);
  const moved = pointers.compareAndSet(root, 5, "shell", { version: "v3" });
  assert.equal(moved.generation, 6);
  assert.equal(pointers.read(root).writer, "shell");
  assert.equal(pointers.parse(fs.readFileSync(path.join(root, "current.prev"), "ascii")).version, "v2");
  assert.throws(() => pointers.compareAndSet(root, 5, "shell", { version: "v9" }), pointers.PointerConflict);
  assert.equal(fs.existsSync(path.join(root, "current.lock")), false, "the lock is released");

  fs.writeFileSync(path.join(root, "current"), "garbage");
  assert.equal(pointers.readOrRecover(root).version, "v2", "a damaged pointer comes back from current.prev");
}

// --- the three modes --------------------------------------------------------
{
  const root = temporaryRoot();
  const resourcesPath = fs.mkdtempSync(path.join(os.tmpdir(), "thin-resources-"));
  assert.equal(store.chooseMode({ isPackaged: false, resourcesPath, root }).mode, "embedded");
  assert.equal(store.chooseMode({ isPackaged: true, resourcesPath, root }).mode, "managed");
  fs.mkdirSync(path.join(resourcesPath, "runtime"));
  assert.equal(store.chooseMode({ isPackaged: true, resourcesPath, root }).mode, "embedded");
  fs.rmSync(path.join(resourcesPath, "runtime"), { recursive: true });
  fs.writeFileSync(path.join(resourcesPath, "instance.json"), "{}");
  assert.equal(store.chooseMode({ isPackaged: true, resourcesPath, root }).mode, "embedded");

  fs.mkdirSync(root, { recursive: true });
  fs.writeFileSync(path.join(root, "developer.json"), JSON.stringify({ checkout: "/checkout", python: "/usr/bin/python3" }));
  const developer = store.chooseMode({ isPackaged: true, resourcesPath, root });
  assert.equal(developer.mode, "developer", "developer.json wins over everything");
  assert.equal(developer.developer.checkout, "/checkout");
}

// --- when to seed -----------------------------------------------------------
{
  const root = temporaryRoot();
  assert.equal(store.needsSeed(root), true, "a new root is seeded");
  fs.mkdirSync(path.join(root, "repo", "objects"), { recursive: true });
  assert.equal(store.needsSeed(root), false, "a lost pointer with code present is rebuilt, never reseeded");
  writePointer(root);
  assert.equal(store.needsSeed(root), false);
  fs.writeFileSync(path.join(root, ".seeding"), "1\n");
  assert.equal(store.needsSeed(root), true, "an interrupted seed restarts");
}

// --- `switching` ------------------------------------------------------------
{
  const root = temporaryRoot();
  fs.mkdirSync(root, { recursive: true });
  const options = { now: 1000, isAlive: (pid) => pid === 42 };
  assert.equal(store.switchingInProgress(root, options), false, "no file, no switch");
  for (const text of ['{"pid":42,"deadline":2000}', "pid=42\ndeadline=2000\n", "42 2000\n"]) {
    fs.writeFileSync(path.join(root, "switching"), text);
    assert.equal(store.switchingInProgress(root, options), true, text);
  }
  fs.writeFileSync(path.join(root, "switching"), "42 900\n");
  assert.equal(store.switchingInProgress(root, options), false, "past its deadline");
  fs.writeFileSync(path.join(root, "switching"), "41 2000\n");
  assert.equal(store.switchingInProgress(root, options), false, "the controller is dead");
  fs.writeFileSync(path.join(root, "switching"), "not a record");
  assert.equal(store.switchingInProgress(root, options), false);
}

// --- which version's main process, and the recovery list --------------------
{
  const root = temporaryRoot();
  writeVersion(root, "v1", { number: 12 });
  writeVersion(root, "v2", { number: 13 });
  writeVersion(root, "v3", { number: 14, shell_compat: 99 });
  writePointer(root);
  const pointer = pointers.read(root);
  assert.equal(store.chooseDesktop(root, pointer, COMPAT, "1.0.0").id, "v2");
  store.recordDesktopFailure(root, "v2", "1.0.0", new Error("broken"));
  assert.equal(store.chooseDesktop(root, pointer, COMPAT, "1.0.0").id, "v1", "a failed desktop falls back");
  assert.equal(store.chooseDesktop(root, pointer, COMPAT, "1.0.1").id, "v2", "a newer shell tries it again");
  store.recordDesktopFailure(root, "v1", "1.0.0", new Error("broken"));
  assert.equal(store.chooseDesktop(root, pointer, COMPAT, "1.0.0"), null, "both failed: recovery page");

  const listed = store.listVersions(root, COMPAT, "1.0.0", pointer);
  assert.deepEqual(listed.map((version) => version.id), ["v2", "v1"], "incompatible versions are not offered");
  assert.equal(listed[0].number, 13);
  assert.equal(listed[0].current, true);
  assert.equal(listed[0].failed, true);
  assert.equal(listed[0].summary, "Summary of v2");

  const attempts = temporaryRoot();
  writeVersion(attempts, "v1");
  writePointer(attempts, { version: "v1" });
  fs.mkdirSync(path.join(attempts, "boot"), { recursive: true });
  const now = Math.floor(Date.now() / 1000);
  fs.writeFileSync(path.join(attempts, "boot", "v1.attempts"), `${now}\n${now}\n`);
  assert.equal(store.listVersions(attempts, COMPAT, "1.0.0", pointers.read(attempts))[0].failed, false);
  fs.appendFileSync(path.join(attempts, "boot", "v1.attempts"), `${now}\n`);
  assert.equal(store.listVersions(attempts, COMPAT, "1.0.0", pointers.read(attempts))[0].failed, true);
  assert.equal(store.listVersions(attempts, COMPAT, "1.0.0", pointers.read(attempts))[0].number, 1,
    "a version with no stored number is numbered by age");
}

// --- the recovery list agrees with Settings ---------------------------------
{
  const root = temporaryRoot();
  writeVersion(root, "v1", { number: 7 });
  writeVersion(root, "v2", { number: 8 });
  writeVersion(root, "v3", { number: 8 });
  writeVersion(root, "v4", { number: 1 });
  writePointer(root, { version: "v3", last_good: "v2" });
  fs.writeFileSync(path.join(root, "version-numbers.json"), JSON.stringify({ next: 10, numbers: { v2: 8, v3: 9 } }));
  const numbers = Object.fromEntries(store.listVersions(root, COMPAT, "1.0.0", pointers.read(root)).map((row) => [row.id, row.number]));
  assert.deepEqual(numbers, { v1: 7, v2: 8, v3: 9, v4: 1 }, "recorded numbers win; the rest are handed out unique");

  // A candidate that never committed is not offered, as in Settings.
  const refs = path.join(root, "repo", "refs", "openprogram", "version");
  fs.mkdirSync(refs, { recursive: true });
  fs.writeFileSync(path.join(refs, "v1"), "a".repeat(40));
  fs.writeFileSync(path.join(root, "repo", "packed-refs"), `${"b".repeat(40)} refs/openprogram/version/v2\n`);
  const ids = store.listVersions(root, COMPAT, "1.0.0", pointers.read(root)).map((row) => row.id);
  assert.deepEqual(ids, ["v3", "v2", "v1"], "v4 failed its trial; v3 is named by the pointer; v2 is packed");

  // A repository that has committed nothing yet still hides a failed candidate.
  const fresh = temporaryRoot();
  writeVersion(fresh, "v1");
  writeVersion(fresh, "v2");
  writePointer(fresh, { version: "v1", last_good: "v1" });
  fs.mkdirSync(path.join(fresh, "repo"), { recursive: true });
  fs.writeFileSync(path.join(fresh, "repo", "HEAD"), "ref: refs/heads/local\n");
  assert.deepEqual(store.listVersions(fresh, COMPAT, "1.0.0", pointers.read(fresh)).map((row) => row.id), ["v1"]);
}

// --- when the recovery page takes over --------------------------------------
{
  const root = temporaryRoot();
  writePointer(root);
  assert.equal(store.recoveryDue(root, 119_000), false);
  assert.equal(store.recoveryDue(root, 120_000), true, "a hung worker never exits, so no guard will move");
  writePointer(root, { probation: "su_1", probation_deadline: "9999999999" });
  assert.equal(store.recoveryDue(root, 120_000), false, "on probation the controller or the guard still has a move");
  writePointer(root);
  fs.mkdirSync(path.join(root, "boot"), { recursive: true });
  fs.writeFileSync(path.join(root, "boot", "v2.migrating"), "");
  assert.equal(store.recoveryDue(root, 120_000), false, "a store still migrating is not a hang");
  fs.rmSync(path.join(root, "boot", "v2.migrating"));
  writePointer(root, { writer: "launcher" });
  assert.equal(store.recoveryDue(root, 120_000), true, "the fallback is failing too");
  writePointer(root, { last_good: "", last_good_base: "" });
  assert.equal(store.recoveryDue(root, 120_000), true, "there is no fallback to wait for");
  fs.writeFileSync(path.join(root, "switching"), `${process.pid} ${Math.floor(Date.now() / 1000) + 60}\n`);
  assert.equal(store.recoveryDue(root, 600_000), false, "never during a switch");
}

// --- the managed resolver ---------------------------------------------------
{
  const root = temporaryRoot();
  writeVersion(root, "v1");
  writePointer(root, { version: "v1" });
  const launch = store.resolveWorker(root, COMPAT, "darwin");
  assert.equal(launch.command, path.join(root, "identity", "OpenProgram.app", "Contents", "MacOS", "OpenProgram"));
  assert.deepEqual(launch.args, ["-u", "-m", "openprogram", "worker", "run"]);
  assert.equal(launch.authCommand, path.join(root, "versions", "v1", "venv", "bin", "python"));
  assert.equal(store.resolveWorker(root, COMPAT, "win32").authCommand,
    path.join(root, "versions", "v1", "venv", "Scripts", "python.exe"));
  assert.equal(store.resolveWorker(root, COMPAT, "win32").command, path.join(root, "identity", "openprogram-launcher.exe"));
  assert.throws(() => store.resolveWorker(root, { min: 2, max: 3 }, "darwin"), /different OpenProgram App/);
}

// --- first-run seed ---------------------------------------------------------
function tarEntry(name, type, body = Buffer.alloc(0), { link = "", mode = 0o644, mtime = 1_700_000_000 } = {}) {
  const header = Buffer.alloc(512);
  header.write(name, 0, 100, "utf8");
  header.write(mode.toString(8).padStart(7, "0"), 100);
  header.write("0".repeat(7), 108);
  header.write("0".repeat(7), 116);
  header.write(body.length.toString(8).padStart(11, "0"), 124);
  header.write(mtime.toString(8).padStart(11, "0"), 136);
  header.write(" ".repeat(8), 148);
  header.write(type, 156);
  header.write(link, 157, 100, "utf8");
  header.write("ustar\0" + "00", 257, "latin1");
  let sum = 0;
  for (const byte of header) sum += byte;
  header.write(`${sum.toString(8).padStart(6, "0")}\0 `, 148, "latin1");
  const padding = Buffer.alloc((512 - (body.length % 512)) % 512);
  return Buffer.concat([header, body, padding]);
}

function archive(file, entries) {
  fs.writeFileSync(file, zlib.zstdCompressSync(Buffer.concat([...entries, Buffer.alloc(1024)])));
}

function paxEntry(name, value) {
  const record = (key, text) => {
    const body = ` ${key}=${text}\n`;
    let length = body.length + 2;
    while (String(length).length + body.length !== length) length = String(length).length + body.length;
    return `${length}${body}`;
  };
  return tarEntry("PaxHeader", "x", Buffer.from(record(name, value)));
}

async function checkExtraction() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "thin-archive-"));
  const file = path.join(directory, "a.tar.zst");
  const long = `${"long/".repeat(30)}name.txt`;
  archive(file, [
    tarEntry("top/", "5"),
    tarEntry("top/hello.txt", "0", Buffer.from("hello"), { mode: 0o755 }),
    tarEntry("top/empty", "0"),
    tarEntry("top/link", "2", Buffer.alloc(0), { link: "hello.txt" }),
    tarEntry("top/venv-python", "2", Buffer.alloc(0), { link: "/build/root/bases/x/python" }),
    tarEntry("top/hard", "1", Buffer.alloc(0), { link: "top/hello.txt" }),
    paxEntry("path", long),
    tarEntry("ignored", "0", Buffer.from("long body")),
  ]);
  const destination = path.join(directory, "out");
  let bytes = 0;
  await seedModule.extractArchive(file, destination, { builtRoot: "/build/root", onBytes: (count) => { bytes += count; } });
  assert.equal(fs.readFileSync(path.join(destination, "top", "hello.txt"), "utf8"), "hello");
  assert.equal(fs.statSync(path.join(destination, "top", "hello.txt")).mode & 0o777, 0o755);
  assert.equal(Math.floor(fs.statSync(path.join(destination, "top", "hello.txt")).mtimeMs / 1000), 1_700_000_000);
  assert.equal(fs.statSync(path.join(destination, "top", "empty")).size, 0);
  assert.equal(fs.readlinkSync(path.join(destination, "top", "link")), "hello.txt");
  assert.equal(fs.readlinkSync(path.join(destination, "top", "venv-python")), "/build/root/bases/x/python");
  assert.equal(fs.readFileSync(path.join(destination, "top", "hard"), "utf8"), "hello");
  assert.equal(fs.readFileSync(path.join(destination, long), "utf8"), "long body", "pax long names work");
  assert.equal(bytes, fs.statSync(file).size);

  for (const entries of [
    [tarEntry("../escape", "0", Buffer.from("x"))],
    [tarEntry("/absolute", "0", Buffer.from("x"))],
    [tarEntry("a/link", "2", Buffer.alloc(0), { link: "../../outside" })],
    [tarEntry("a/link", "2", Buffer.alloc(0), { link: "/etc/passwd" })],
  ]) {
    archive(file, entries);
    await assert.rejects(seedModule.extractArchive(file, path.join(directory, "bad"), { builtRoot: "/build/root" }),
      seedModule.SeedError);
  }
}

// Windows' tar writes a link under the build's root as `C:/…` while the seed
// records that root as `C:\…`; a symbolic link there needs a privilege most
// accounts lack, so a directory becomes a junction and a file a second name.
async function checkWindowsLinks() {
  const windows = (target, member = "C:\\Users\\u\\rt\\versions\\v1\\venv\\Scripts\\python.exe") => seedModule.resolveLink({
    root: "C:\\Users\\u\\rt", member, target, builtRoot: "D:\\a\\seed\\store\\runtime", paths: path.win32,
  });
  assert.equal(windows("D:/a/seed/store/runtime/bases/x/python.exe"), "C:\\Users\\u\\rt\\bases\\x\\python.exe");
  assert.equal(windows("d:\\A\\seed\\store\\runtime\\bases\\x"), "C:\\Users\\u\\rt\\bases\\x");
  assert.equal(windows("..\\..\\..\\..\\bases\\x"), "C:\\Users\\u\\rt\\bases\\x");
  assert.equal(windows("../../../../../outside"), null);
  assert.equal(windows("D:/a/seed/store/runtime-other/x"), null);
  assert.equal(windows("D:/a/seed/store/runtime/../x"), null);
  assert.equal(windows("C:/Windows/System32/cmd.exe"), null);
  assert.equal(windows("\\\\server\\share\\x"), null);

  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "thin-links-"));
  const seedDirectory = path.join(directory, "seed");
  fs.mkdirSync(path.join(seedDirectory, "bootstrap"), { recursive: true });
  fs.writeFileSync(path.join(seedDirectory, "bootstrap", "install_seed.py"), "");
  fs.writeFileSync(path.join(seedDirectory, "seed.json"), JSON.stringify({
    schema: 1, version: "v1", base: BASE, commit: "a".repeat(40), built_root: "D:\\a\\seed\\store\\runtime",
  }));
  archive(path.join(seedDirectory, "base.tar.zst"), [
    tarEntry(`bases/${BASE}/python/python.exe`, "0", Buffer.from("MZ")),
    tarEntry(`bases/${BASE}/python/again.exe`, "2", Buffer.alloc(0), { link: "alias.exe" }),
    tarEntry(`bases/${BASE}/python/alias.exe`, "2", Buffer.alloc(0), { link: "python.exe" }),
    tarEntry(`bases/${BASE}/tools`, "2", Buffer.alloc(0), { link: "python" }),
  ]);
  archive(path.join(seedDirectory, "version.tar.zst"), [
    tarEntry("versions/v1/version.json", "0", Buffer.from("{}")),
    tarEntry("versions/v1/python.exe", "2", Buffer.alloc(0), {
      link: `D:/a/seed/store/runtime/bases/${BASE}/python/python.exe`,
    }),
     tarEntry("pristine-frontend/abc.zip", "0", Buffer.from("zip")),
  ]);
  const root = path.join(directory, "state", "runtime");
  const calls = [];
  await seedModule.seed({ root, seedDirectory, platform: "win32", spawnProcess: fakeInstaller(root, calls) });
  assert.equal(calls[0].command, path.join(root, "bases", BASE, "python", "python.exe"));
  assert.deepEqual(calls[0].args.slice(0, 5),
    ["-I", "-B", "-X", "utf8", path.join(seedDirectory, "bootstrap", "install_seed.py")],
    "Windows runs the installer in UTF-8 mode, as the launcher runs everything else");
  const base = path.join(root, "bases", BASE);
  for (const file of [path.join(base, "python", "alias.exe"), path.join(base, "python", "again.exe"),
    path.join(root, "versions", "v1", "python.exe")]) {
    assert.equal(fs.lstatSync(file).isSymbolicLink(), false, "a file link is a second name, not a symbolic link");
    assert.equal(fs.readFileSync(file, "utf8"), "MZ");
  }
  assert.equal(fs.statSync(path.join(base, "python", "python.exe")).nlink, 4);
  // Off Windows a junction is made as a plain link; either way it holds the final place.
  assert.equal(fs.realpathSync(path.join(base, "tools")), fs.realpathSync(path.join(base, "python")));
  assert.equal(fs.existsSync(path.join(root, ".seed-links")), false);

  // Interrupted after the move into place: the next run still makes the links.
  const interrupted = path.join(directory, "interrupted", "runtime");
  let stopped = false;
  await assert.rejects(seedModule.seed({
    root: interrupted, seedDirectory, platform: "win32", spawnProcess: fakeInstaller(interrupted, []),
    onProgress(percent) {
      // Everything is unpacked at 92; a file where a later directory goes stops the move after the base.
      if (percent >= 92 && !stopped) {
        stopped = true;
        fs.writeFileSync(path.join(interrupted, "pristine-frontend"), "in the way");
      }
    },
  }));
  assert.ok(fs.existsSync(path.join(interrupted, "bases", BASE, "python", "python.exe")), "the base was moved");
  assert.equal(fs.existsSync(path.join(interrupted, "bases", BASE, "tools")), false);
  fs.rmSync(path.join(interrupted, "pristine-frontend"));
  await seedModule.seed({
    root: interrupted, seedDirectory, platform: "win32", spawnProcess: fakeInstaller(interrupted, []),
  });
  assert.equal(fs.realpathSync(path.join(interrupted, "bases", BASE, "tools")),
    fs.realpathSync(path.join(interrupted, "bases", BASE, "python")));
  assert.equal(fs.readFileSync(path.join(interrupted, "bases", BASE, "python", "again.exe"), "utf8"), "MZ");

  // A link whose target is not in the seed stops the install by name.
  archive(path.join(seedDirectory, "version.tar.zst"), [
    tarEntry("versions/v1/version.json", "0", Buffer.from("{}")),
    tarEntry("versions/v1/dangling", "2", Buffer.alloc(0), { link: "nothing-here" }),
  ]);
  const dangling = path.join(directory, "dangling", "runtime");
  await assert.rejects(
    seedModule.seed({ root: dangling, seedDirectory, platform: "win32", spawnProcess: fakeInstaller(dangling, []) }),
    (error) => error instanceof seedModule.SeedError && /points at nothing: versions\/v1\/dangling/.test(error.message),
  );
}

function fakeSeed() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "thin-seed-"));
  fs.mkdirSync(path.join(directory, "bootstrap"));
  fs.writeFileSync(path.join(directory, "bootstrap", "install_seed.py"), "");
  fs.writeFileSync(path.join(directory, "seed.json"), JSON.stringify({
    schema: 1, version: "v1", base: BASE, commit: "a".repeat(40), built_root: "/build/root",
  }));
  archive(path.join(directory, "base.tar.zst"), [
    tarEntry(`bases/${BASE}/python/bin/python3`, "0", Buffer.from("#!/bin/sh\n"), { mode: 0o755 }),
  ]);
  archive(path.join(directory, "version.tar.zst"), [
    tarEntry("versions/v1/version.json", "0", Buffer.from("{}")),
    tarEntry("pristine-frontend/abc.zip", "0", Buffer.from("zip")),
  ]);
  return directory;
}

function fakeInstaller(root, calls, exitCode = 0) {
  return (command, args) => {
    calls.push({ command, args });
    const { EventEmitter } = require("node:events");
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    setImmediate(() => {
      if (exitCode === 0) fs.rmSync(path.join(root, ".seeding"), { force: true });
      else child.stderr.emit("data", "disk full");
      child.emit("close", exitCode);
    });
    return child;
  };
}

async function checkSeed() {
  const seedDirectory = fakeSeed();
  const root = temporaryRoot();
  const calls = [];
  const progress = [];
  const manifest = await seedModule.seed({
    root, seedDirectory, platform: "darwin", onProgress: (value) => progress.push(value),
    spawnProcess: fakeInstaller(root, calls),
  });
  assert.equal(manifest.version, "v1");
  assert.ok(fs.existsSync(path.join(root, "bases", BASE, "python", "bin", "python3")));
  assert.ok(fs.existsSync(path.join(root, "versions", "v1", "version.json")));
  assert.ok(fs.existsSync(path.join(root, "pristine-frontend", "abc.zip")));
  assert.equal(fs.existsSync(path.join(root, ".seed-work")), false);
  assert.equal(calls[0].command, path.join(root, "bases", BASE, "python", "bin", "python3"));
  assert.deepEqual(calls[0].args.slice(0, 3), ["-I", "-B", path.join(seedDirectory, "bootstrap", "install_seed.py")]);
  assert.deepEqual([...progress].sort((a, b) => a - b), progress, "progress only moves forward");
  assert.equal(progress.at(-1), 100);

  // A flat runtime or anything else already there is left alone.
  const occupied = temporaryRoot();
  fs.mkdirSync(path.join(occupied, "bin"), { recursive: true });
  await assert.rejects(seedModule.seed({ root: occupied, seedDirectory, spawnProcess: fakeInstaller(occupied, []) }),
    /not a version store \(bin\)\. Move them somewhere else, then try again\./);
  // An older command-line install under cli/ is not in the way.
  const withCli = temporaryRoot();
  fs.mkdirSync(path.join(withCli, "cli"), { recursive: true });
  await seedModule.seed({ root: withCli, seedDirectory, platform: "darwin", spawnProcess: fakeInstaller(withCli, []) });

  // An interrupted seed starts over, including its half-finished work.
  const interrupted = temporaryRoot();
  fs.mkdirSync(path.join(interrupted, ".seed-work", "bases", "partial"), { recursive: true });
  fs.writeFileSync(path.join(interrupted, ".seeding"), "1\n");
  await seedModule.seed({ root: interrupted, seedDirectory, platform: "darwin", spawnProcess: fakeInstaller(interrupted, []) });
  assert.equal(fs.existsSync(path.join(interrupted, "bases", "partial")), false);
  assert.equal(fs.existsSync(path.join(interrupted, ".seeding")), false);

  // A failed installer says why, and leaves .seeding so the next start retries.
  const failing = temporaryRoot();
  await assert.rejects(
    seedModule.seed({ root: failing, seedDirectory, platform: "darwin", spawnProcess: fakeInstaller(failing, [], 1) }),
    /disk full/,
  );
  assert.equal(fs.existsSync(path.join(failing, ".seeding")), true);

  // Restore never overwrites code that is there.
  const kept = temporaryRoot();
  fs.mkdirSync(path.join(kept, "repo", "objects"), { recursive: true });
  const refused = await seedModule.restoreBuiltIn({ root: kept, seedDirectory, spawnProcess: fakeInstaller(kept, []) });
  assert.equal(refused.restored, false);
  assert.equal(fs.existsSync(path.join(kept, "versions")), false);
  const empty = temporaryRoot();
  fs.mkdirSync(path.join(empty, "repo"), { recursive: true });
  const installerCalls = [];
  const restored = await seedModule.restoreBuiltIn({
    root: empty, seedDirectory, platform: "darwin", spawnProcess: fakeInstaller(empty, installerCalls),
  });
  assert.equal(restored.restored, true);
  assert.ok(installerCalls[0].args.includes("--point-at-seed"));
}

// --- using a version from the recovery page ---------------------------------
async function checkSeedSafeguards() {
  // Another program holding a file: retried, and a real failure is still one.
  let tries = 0;
  await seedModule.whenUnlocked(() => {
    tries += 1;
    if (tries < 3) throw Object.assign(new Error("locked"), { code: tries === 1 ? "EBUSY" : "EPERM" });
  }, { delayMs: 1 });
  assert.equal(tries, 3);
  tries = 0;
  await assert.rejects(seedModule.whenUnlocked(() => {
    tries += 1;
    throw Object.assign(new Error("locked"), { code: "EACCES" });
  }, { attempts: 2, delayMs: 1 }), /locked/);
  assert.equal(tries, 2);
  tries = 0;
  await assert.rejects(seedModule.whenUnlocked(() => {
    tries += 1;
    throw Object.assign(new Error("gone"), { code: "ENOENT" });
  }, { delayMs: 1 }), /gone/);
  assert.equal(tries, 1, "only a held file is worth waiting for");

  // The real seed path: a rename that is refused twice still finishes.
  {
    const seedDirectory = fakeSeed();
    const root = temporaryRoot();
    const originalRename = fs.renameSync;
    let refused = 0;
    fs.renameSync = (source, target) => {
      if (String(target).includes(path.join("bases", BASE)) && refused < 2) {
        refused += 1;
        throw Object.assign(new Error("held by a scanner"), { code: "EBUSY" });
      }
      return originalRename(source, target);
    };
    try {
      await seedModule.seed({
        root, seedDirectory, spawnProcess: fakeInstaller(root, []), retry: { delayMs: 1 },
      });
    } finally {
      fs.renameSync = originalRename;
    }
    assert.equal(refused, 2);
    assert.ok(fs.existsSync(path.join(root, "bases", BASE)));
  }

  // A full disk stops before anything is written, and says what to do.
  {
    const seedDirectory = fakeSeed();
    const root = temporaryRoot();
    await assert.rejects(
      seedModule.seed({
        root, seedDirectory, spawnProcess: fakeInstaller(root, []),
        statfs: () => ({ bavail: 1024, bsize: 4096 }),
      }),
      (error) => {
        assert.ok(error instanceof seedModule.SeedError);
        assert.match(error.message, /needs about .* GB of free disk space/);
        assert.match(error.message, / free, (\d+ MB|[\d.]+ GB|less than 1 MB) short\./);
        assert.match(error.message, /Free up some space, then try again\./);
        return true;
      },
    );
    assert.equal(fs.existsSync(root), false, "nothing was started");

    // The setup window logs that failure into the root (recovery.js runSetup),
    // and Try again, or the next launch, still gets through once there is room.
    store.appendRecoveryLog(root, "first-run setup failed: needs about 3.3 GB of free disk space");
    assert.equal(store.needsSeed(root), true);
    await seedModule.seed({
      root, seedDirectory, platform: "darwin", spawnProcess: fakeInstaller(root, []),
      statfs: () => ({ bavail: 2 ** 30, bsize: 4096 }),
    });
    assert.ok(fs.existsSync(path.join(root, "versions", "v1", "version.json")), "the retry unpacked the seed");
    assert.ok(fs.existsSync(path.join(root, "recovery.log")), "the log of the failed attempt is kept");

    const sized = JSON.parse(fs.readFileSync(path.join(seedDirectory, "seed.json"), "utf8"));
    sized.unpacked_bytes = 100;
    const roomy = { statfs: () => ({ bavail: 2 ** 20, bsize: 4096 }) };
    seedModule.assertDiskSpace(root, sized, [path.join(seedDirectory, "seed.json")], roomy);
    assert.equal(seedModule.freeBytes(path.join(root, "not", "yet"), () => { throw new Error("no statfs"); }), null,
      "not being able to tell does not stop the install");
  }
}

function checkIdentityRefresh() {
  const seedDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "thin-new-shell-"));
  const helper = path.join(seedDirectory, "helper", "OpenProgram.app", "Contents", "MacOS");
  fs.mkdirSync(helper, { recursive: true });
  fs.writeFileSync(path.join(helper, "OpenProgram"), "new helper");
  fs.writeFileSync(path.join(seedDirectory, "seed.json"), JSON.stringify({ base: "py3.13-0123456789abcdef" }));
  fs.writeFileSync(path.join(seedDirectory, "shell-compat.json"), '{"min":1,"max":2}\n');
  fs.writeFileSync(path.join(seedDirectory, "openprogram-launcher"), "new launcher");
  fs.chmodSync(path.join(seedDirectory, "openprogram-launcher"), 0o755);

  const root = temporaryRoot();
  const identity = path.join(root, "identity");
  const installedHelper = path.join(identity, "OpenProgram.app", "Contents", "MacOS", "OpenProgram");
  fs.mkdirSync(path.dirname(installedHelper), { recursive: true });
  fs.writeFileSync(installedHelper, "old helper");
  fs.writeFileSync(path.join(identity, "shell-compat.json"), '{"min":1,"max":1}\n');
  fs.writeFileSync(path.join(identity, "python-minor"), "3.12");
  fs.writeFileSync(path.join(identity, "openprogram-launcher"), "old launcher");
  store.writeShellRecord(root, "1.0.0", "v1", "/old/shell");

  assert.equal(store.refreshIdentity(root, seedDirectory, "1.0.0"), false, "the same shell changes nothing");
  assert.equal(fs.readFileSync(path.join(identity, "shell-compat.json"), "utf8"), '{"min":1,"max":1}\n');

  assert.equal(store.refreshIdentity(root, seedDirectory, "1.1.0"), true);
  assert.equal(fs.readFileSync(path.join(identity, "shell-compat.json"), "utf8"), '{"min":1,"max":2}\n');
  assert.equal(fs.readFileSync(path.join(identity, "python-minor"), "ascii"), "3.13");
  assert.equal(fs.readFileSync(path.join(identity, "openprogram-launcher"), "utf8"), "new launcher");
  assert.equal(fs.statSync(path.join(identity, "openprogram-launcher")).mode & 0o111, 0o111, "still runnable");
  assert.equal(fs.readFileSync(installedHelper, "utf8"), "new helper", "the helper keeps its path");
  assert.deepEqual(fs.readdirSync(identity).filter((name) => /\.(new|old)$/.test(name)), [], "nothing left behind");

  // Unchanged code leaves the helper's files alone, so its grants stay.
  store.writeShellRecord(root, "1.1.0", "v1", "/old/shell");
  const before = fs.statSync(installedHelper).ino;
  assert.equal(store.refreshIdentity(root, seedDirectory, "1.2.0"), true);
  assert.equal(fs.statSync(installedHelper).ino, before);

  // A failure is logged and left for the next start.
  const messages = [];
  const brokenRoot = temporaryRoot();
  fs.mkdirSync(brokenRoot, { recursive: true });
  fs.writeFileSync(path.join(brokenRoot, "identity"), "not a directory");
  assert.equal(store.refreshIdentity(brokenRoot, seedDirectory, "1.2.0", {
    log: (_root, message) => messages.push(message),
  }), false);
  assert.match(messages[0], /could not refresh/);
  assert.equal(store.refreshIdentity(temporaryRoot(), path.join(seedDirectory, "absent"), "1.2.0"), false,
    "an App without a built-in copy has nothing to refresh from");
}

function checkRelaunchPaths() {
  assert.equal(store.shellExecutable({ APPIMAGE: "/home/a/OpenProgram.AppImage" }, "/tmp/.mount_x/openprogram-desktop"),
    "/home/a/OpenProgram.AppImage", "an AppImage is recorded by its own path, not its mount");
  assert.equal(store.shellExecutable({}, "/opt/OpenProgram/openprogram-desktop"), "/opt/OpenProgram/openprogram-desktop");
  const root = temporaryRoot();
  store.writeShellRecord(root, "1.0.0", "v1", store.shellExecutable({ APPIMAGE: "/home/a/OpenProgram.AppImage" }, "/tmp/.mount_x/app"));
  assert.equal(JSON.parse(fs.readFileSync(path.join(root, "shell.json"), "utf8")).executable, "/home/a/OpenProgram.AppImage");

  assert.deepEqual(store.relaunchOptions({ APPIMAGE: "/home/a/App.AppImage" }, ["/tmp/.mount_x/app", "--flag"]),
    { execPath: "/home/a/App.AppImage", args: ["--flag"] });
  assert.equal(store.relaunchOptions({}, ["/opt/app", "--flag"]), undefined, "elsewhere the default relaunch is right");
  assert.deepEqual(store.relaunchOptions({}, ["/opt/app", "--recovery", "--flag"]), { args: ["--flag"] },
    "a restart from the recovery page does not open it again");
  assert.deepEqual(store.relaunchOptions({ APPIMAGE: "/a.AppImage" }, ["/x", "--recovery"]), { execPath: "/a.AppImage", args: [] });
}

function checkRunningRelease() {
  const root = temporaryRoot();
  writeVersion(root, "v2");
  writePointer(root);
  fs.writeFileSync(path.join(root, "versions", "v2", "desktop", "package.json"), JSON.stringify({ version: "1.2.3" }));
  assert.equal(store.runningRelease(root), "1.2.3", "the version store's running release, not the shell's");
  assert.equal(store.runningRelease(temporaryRoot()), null);
}

async function checkUseVersion() {
  const root = temporaryRoot();
  writeVersion(root, "v1");
  writeVersion(root, "v2");
  writeVersion(root, "old", { shell_compat: 0 });
  writePointer(root, { version: "v2", probation: "u1", probation_deadline: "9" });
  fs.mkdirSync(path.join(root, "boot"), { recursive: true });
  fs.writeFileSync(path.join(root, "boot", "v1.attempts"), "1\n");
  store.recordDesktopFailure(root, "v1", "1.0.0", new Error("x"));

  await recovery.useVersion({ root, compat: COMPAT, id: "v1", shellVersion: "1.0.0", platform: "darwin" });
  const pointer = pointers.read(root);
  assert.equal(pointer.version, "v1");
  assert.equal(pointer.writer, "shell");
  assert.equal(pointer.probation, "");
  assert.equal(fs.existsSync(path.join(root, "boot", "v1.attempts")), false, "the guard starts counting afresh");
  assert.equal(store.desktopFailed(root, "v1", "1.0.0"), false);
  assert.match(fs.readFileSync(path.join(root, "recovery.log"), "utf8"), /moved to v1/);

  await assert.rejects(recovery.useVersion({ root, compat: COMPAT, id: "old", shellVersion: "1.0.0" }), /can't be started/);
  await assert.rejects(recovery.useVersion({ root, compat: COMPAT, id: "../x", shellVersion: "1.0.0" }), /isn't a version/);

  fs.writeFileSync(path.join(root, "switching"), `${process.pid} ${Math.floor(Date.now() / 1000) + 60}\n`);
  await assert.rejects(recovery.useVersion({ root, compat: COMPAT, id: "v2", shellVersion: "1.0.0" }), /switching versions/);
  fs.rmSync(path.join(root, "switching"));

  // A lost pointer can still be replaced from the list.
  fs.rmSync(path.join(root, "current"));
  fs.rmSync(path.join(root, "current.prev"));
  await recovery.useVersion({ root, compat: COMPAT, id: "v2", shellVersion: "1.0.0", platform: "darwin" });
  assert.equal(pointers.read(root).version, "v2");
}

// --- the loader, against a stand-in Electron --------------------------------
function fakeElectron({ isPackaged = true, windowsLoaded = false, single = true } = {}) {
  const events = { relaunched: 0, exited: [], quit: 0 };
  const windows = [];
  class BrowserWindow {
    constructor(options) {
      this.options = options;
      this.webContents = { isLoading: () => !windowsLoaded, send() {} };
      windows.push(this);
    }
    loadFile(file, options) { this.file = file; this.query = options?.query; return Promise.resolve(); }
    on() {}
    show() {}
    focus() {}
    isDestroyed() { return false; }
    static getAllWindows() { return windows; }
  }
  const app = {
    isPackaged,
    getVersion: () => "1.0.0",
    getAppPath: () => "/app.asar",
    getPath: () => os.tmpdir(),
    requestSingleInstanceLock: () => single,
    whenReady: () => Promise.resolve(),
    on() {},
    quit() { events.quit += 1; },
    relaunch() { events.relaunched += 1; },
    exit(code) { events.exited.push(code); },
  };
  return {
    electron: {
      app, BrowserWindow, clipboard: { writeText() {} },
      ipcMain: { handle() {}, on() {} },
      Menu: { setApplicationMenu() {}, buildFromTemplate: (value) => value },
    },
    events,
    windows,
  };
}

async function checkMoveToApplications(moveOfferNeeded, offerMoveToApplications) {
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), "thin-move-"));
  const executable = path.join(userData, "Downloads", "OpenProgram.app", "Contents", "MacOS", "OpenProgram");
  const standIn = (inApplications = false) => ({
    isPackaged: true,
    getPath: () => userData,
    isInApplicationsFolder: () => inApplications,
  });
  const dialog = (response, shown = []) => ({
    showMessageBox: async (options) => { shown.push(options); return { response }; },
  });
  assert.equal(moveOfferNeeded(standIn(), dialog(0), "darwin", executable), true);
  assert.equal(moveOfferNeeded(standIn(true), dialog(0), "darwin", executable), false,
    "/Applications and ~/Applications both count as installed");
  assert.equal(moveOfferNeeded(standIn(), dialog(0), "linux", executable), false);
  assert.equal(moveOfferNeeded({ ...standIn(), isPackaged: false }, dialog(0), "darwin", executable), false);
  assert.equal(moveOfferNeeded({ isPackaged: true }, dialog(0), "darwin", executable), false, "an older Electron API");

  // "Not Now" is remembered for this copy only, and starting goes on.
  const shown = [];
  assert.equal(await offerMoveToApplications({ app: standIn(), dialog: dialog(1, shown) }, { executable }), "declined");
  assert.equal(shown[0].buttons[1], "Not Now");
  assert.equal(moveOfferNeeded(standIn(), dialog(0), "darwin", executable), false);
  assert.equal(moveOfferNeeded(standIn(), dialog(0), "darwin", executable.replace("Downloads", "Desktop")), true);

  // Someone who can write /Applications gets Electron's own move.
  let moved = 0;
  const moving = { ...standIn(), moveToApplicationsFolder: () => { moved += 1; return false; } };
  assert.equal(await offerMoveToApplications({ app: moving, dialog: dialog(0) }, {
    executable, sharedFolderWritable: true,
    copyBundle: () => { throw new Error("not used"); },
  }), "cancelled");
  assert.equal(moved, 1);

  // Someone who can't gets their own Applications folder, and no password prompt.
  const home = path.join(userData, "home");
  const copies = [];
  const relaunched = [];
  const exited = [];
  const personal = {
    ...standIn(),
    moveToApplicationsFolder: () => { throw new Error("would ask for an administrator"); },
    relaunch: (options) => relaunched.push(options),
    exit: (code) => exited.push(code),
  };
  void offerMoveToApplications({ app: personal, dialog: dialog(0) }, {
    executable, home, sharedFolderWritable: false, copyBundle: (source, target) => copies.push([source, target]),
  });
  await new Promise((resolve) => setTimeout(resolve, 10));
  const target = path.join(home, "Applications", "OpenProgram.app");
  assert.deepEqual(copies, [[path.join(userData, "Downloads", "OpenProgram.app"), target]]);
  assert.equal(relaunched[0].execPath, path.join(target, "Contents", "MacOS", "OpenProgram"));
  assert.deepEqual(exited, [0]);

  // A failed move is explained and the App keeps running where it is.
  const warned = [];
  assert.equal(await offerMoveToApplications({
    app: { ...standIn(), moveToApplicationsFolder: () => { throw new Error("no room"); } },
    dialog: { showMessageBox: async (options) => { warned.push(options); return { response: 0 }; } },
  }, { executable, sharedFolderWritable: true }), "failed");
  assert.match(warned[1].message, /couldn't be moved/);
  assert.match(warned[1].detail, /keep running from here/);
}

async function checkLoader() {
  globalThis.__OPENPROGRAM_SHELL_TEST__ = true;
  const load = Module._load;
  const standIn = fakeElectron();
  Module._load = function (request, ...rest) {
    if (request === "electron") return standIn.electron;
    return load.call(this, request, ...rest);
  };
  const { runShell, shiftHeld, addArchiveModules, moveOfferNeeded, offerMoveToApplications } = require("../shell/loader");
  Module._load = load;
  await checkMoveToApplications(moveOfferNeeded, offerMoveToApplications);

  const home = fs.mkdtempSync(path.join(os.tmpdir(), "thin-home-"));
  const previousHome = process.env.HOME;
  process.env.HOME = home;
  const root = path.join(home, ".openprogram", "runtime");
  const resources = fs.mkdtempSync(path.join(os.tmpdir(), "thin-resources-"));
  const previousResources = process.resourcesPath;
  process.resourcesPath = resources;
  const noShift = () => false;
  const settle = () => new Promise((resolve) => setTimeout(resolve, 20));
  try {
    // Embedded: today's main.js, which starts itself, and <root> is not read.
    const embedded = fakeElectron({ isPackaged: false });
    const loads = [];
    const realLoad = Module._load;
    Module._load = function (request, ...rest) {
      if (request.endsWith(`${path.sep}main.js`)) {
        loads.push({ request, startsItself: !globalThis.__OPENPROGRAM_SHELL_LOADER__ });
        return {};
      }
      return realLoad.call(this, request, ...rest);
    };
    assert.equal(runShell(embedded.electron, { shiftHeld: () => { throw new Error("not asked"); } }).mode, "embedded");
    Module._load = realLoad;
    assert.deepEqual(loads, [{ request: path.join(__dirname, "..", "main.js"), startsItself: true }]);
    assert.equal(embedded.windows.length, 0);
    assert.equal(fs.existsSync(root), false, "an embedded App never creates the version store");

    // A new root opens the setup page and does not load any version.
    const first = fakeElectron();
    await settle();
    assert.equal(runShell(first.electron, { shiftHeld: noShift }).phase, "setup");
    await settle();
    assert.match(first.windows[0].file, /setup\.html$/);

    // On Windows every window of the App, the setup page included, carries the
    // installer's application id, or toasts are dropped and a pin doubles.
    const identities = [];
    const onWindows = fakeElectron();
    onWindows.electron.app.setAppUserModelId = (id) => identities.push(id);
    assert.equal(runShell(onWindows.electron, { shiftHeld: noShift, platform: "win32" }).phase, "setup");
    await settle();
    const appId = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "package.json"), "utf8")).build.appId;
    assert.deepEqual(identities, [appId], "the id is the one the NSIS shortcut is written with");
    for (const other of ["darwin", "linux"]) {
      const elsewhere = fakeElectron();
      elsewhere.electron.app.setAppUserModelId = (id) => identities.push(`${other}:${id}`);
      runShell(elsewhere.electron, { shiftHeld: noShift, platform: other });
      await settle();
    }
    assert.equal(identities.length, 1, "macOS and Linux have no application id to set");

    // A second instance leaves.
    writeVersion(root, "v2");
    writeVersion(root, "v1");
    writePointer(root);
    const second = fakeElectron({ single: false });
    runShell(second.electron, { shiftHeld: noShift });
    assert.equal(second.events.quit, 1);

    // Holding Shift stops at the recovery page before any version loads.
    fs.writeFileSync(store.desktopEntry(root, "v2"), 'throw new Error("must not load");\n');
    const shifted = fakeElectron();
    assert.equal(runShell(shifted.electron, { shiftHeld: () => true }).phase, "shift");
    await settle();
    assert.match(shifted.windows[0].file, /recovery\.html$/);

    // A version that throws while loading is marked, and the App starts over.
    const broken = fakeElectron();
    assert.equal(runShell(broken.electron, { shiftHeld: noShift }).phase, "failed");
    assert.equal(broken.events.relaunched, 1);
    assert.equal(store.desktopFailed(root, "v2", "1.0.0"), true);

    // The next start loads the fallback, with the contract, and reports it.
    fs.writeFileSync(store.desktopEntry(root, "v1"), `
      exports.start = (context) => { globalThis.__startedWith = context; return Promise.resolve(); };
    `);
    const fallback = fakeElectron();
    const outcome = runShell(fallback.electron, { shiftHeld: noShift });
    assert.equal(outcome.version, "v1");
    await settle();
    const context = globalThis.__startedWith;
    assert.equal(context.runtimeRoot, root);
    assert.equal(context.versionId, "v1");
    assert.equal(context.shellVersion, "1.0.0");
    assert.equal(typeof context.openRecovery, "function");
    assert.equal(typeof context.resolveWorker, "function");
    const handshake = JSON.parse(fs.readFileSync(path.join(root, "boot", "v1.desktop-ready"), "utf8"));
    assert.equal(handshake.main_ready, true);
    assert.equal(handshake.window_loaded, true);

    // The shell says where it is, and a controller's request is taken once.
    const record = JSON.parse(fs.readFileSync(path.join(root, "shell.json"), "utf8"));
    assert.equal(record.pid, process.pid);
    assert.equal(record.executable, process.execPath);
    fs.writeFileSync(path.join(root, "shell-relaunch"), "v2\n");
    assert.equal(store.takeRelaunchRequest(root), true);
    assert.equal(store.takeRelaunchRequest(root), false);

    // A trial's desktop check loads the candidate's main process and reports.
    const candidate = path.join(root, "candidate-main.js");
    fs.writeFileSync(candidate, "exports.start = async (context) => { globalThis.__TRIAL__ = context.versionId; };\n");
    process.env.OPENPROGRAM_TRIAL = "1";
    process.env.OPENPROGRAM_TRIAL_DESKTOP_MAIN = candidate;
    const trial = fakeElectron();
    trial.electron.app.setPath = () => {};
    assert.equal(runShell(trial.electron, { shiftHeld: noShift }).mode, "desktop-trial");
    await settle();
    assert.deepEqual(trial.events.exited, [0]);
    assert.equal(globalThis.__TRIAL__, "trial");
    delete process.env.OPENPROGRAM_TRIAL;
    delete process.env.OPENPROGRAM_TRIAL_DESKTOP_MAIN;

    // `--recovery` opens the page the same way Shift does, where Shift can't be read.
    process.argv.push("--recovery");
    const asked = fakeElectron();
    assert.equal(runShell(asked.electron, { shiftHeld: noShift }).phase, "shift");
    process.argv.pop();
    await settle();
    assert.match(asked.windows[0].file, /recovery\.html$/);

    // Nothing can be loaded: the recovery page, with no relaunch loop.
    store.recordDesktopFailure(root, "v1", "1.0.0", new Error("x"));
    const nothing = fakeElectron();
    assert.equal(runShell(nothing.electron, { shiftHeld: noShift }).phase, "failed");
    assert.equal(nothing.events.relaunched, 0);
    await settle();
    assert.match(nothing.windows[0].file, /recovery\.html$/);
  } finally {
    process.env.HOME = previousHome;
    process.resourcesPath = previousResources;
  }

  assert.equal(shiftHeld("darwin", () => ({ stdout: `${1 << 17}\n` })), true);
  assert.equal(shiftHeld("darwin", () => ({ stdout: "0\n" })), false);
  assert.equal(shiftHeld("darwin", () => ({ stdout: "" })), false, "not being able to tell means no");
  assert.equal(shiftHeld("win32", () => ({ status: 7 })), true);
  assert.equal(shiftHeld("win32", () => ({ status: 0 })), false);
  assert.equal(shiftHeld("linux", () => { throw new Error("unused"); }), false);

  const moduleRoot = fs.mkdtempSync(path.join(os.tmpdir(), "thin-asar-"));
  fs.mkdirSync(path.join(moduleRoot, "node_modules", "thin-shell-probe"), { recursive: true });
  fs.writeFileSync(path.join(moduleRoot, "node_modules", "thin-shell-probe", "index.js"), "module.exports = 7;\n");
  const before = process.env.NODE_PATH;
  addArchiveModules(moduleRoot);
  assert.equal(process.env.NODE_PATH, before, "the worker's environment is not changed");
  assert.equal(require("thin-shell-probe"), 7, "a version finds the modules in the archive");
}

// --- what the pages and the menu say ----------------------------------------
{
  const shell = path.join(__dirname, "..", "shell");
  const page = fs.readFileSync(path.join(shell, "recovery-page.js"), "utf8");
  const html = fs.readFileSync(path.join(shell, "recovery.html"), "utf8");
  const setup = fs.readFileSync(path.join(shell, "setup.html"), "utf8");
  for (const text of ["OpenProgram can't start right now", "Go back to a previous version", "Didn't start", "Use this version"]) {
    assert.ok(page.includes(text), text);
  }
  for (const text of ["Pick a version to start with", "If none of these work", "Restore the built-in copy", "Copy path"]) {
    assert.ok(html.includes(text), text);
  }
  assert.ok(setup.includes("Setting up OpenProgram..."));
  assert.ok(setup.includes('role="progressbar"'));
  assert.ok(!/<script>/.test(html) && html.includes("script-src 'self'"), "no inline script");

  const main = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
  const about = main.indexOf('{ role: "about", label:');
  const item = main.indexOf("goBackMenuItem()", about);
  assert.ok(about > 0 && item > about && item - about < 120, "the item sits directly under About");
  assert.match(main, /!isMac && shellContext\?\.openRecovery \? \[\{ type: "separator" \}, goBackMenuItem\(\)\]/,
    "Windows and Linux have it too, with no application menu to put it in");
  assert.match(main, /\.\.\.\(shellContext\?\.openRecovery \? \[goBackMenuItem\(\)\] : \[\]\)/,
    "embedded Apps have nothing to go back to");

  const packageJson = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "package.json"), "utf8"));
  assert.equal(packageJson.main, "shell/loader.js");
  const recovery = packageJson.build.deb.desktop.desktopActions.Recovery;
  assert.match(recovery.Exec, /--recovery$/);
  assert.equal(packageJson.build.deb.desktop.entry.Actions, "Recovery;");
  assert.ok(packageJson.build.files.includes("shell/**"));
  const compat = require("../shell/compat.json");
  assert.ok(Number.isInteger(compat.shell_compat) && compat.min <= compat.shell_compat && compat.shell_compat <= compat.max);
}

/** A sealed version carries main.js and main/ only, so its code may load nothing else. */
function checkVersionCarriesItsOwnCode() {
  const desktop = path.join(__dirname, "..");
  const files = [path.join(desktop, "main.js")];
  const walk = (directory) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const full = path.join(directory, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name.endsWith(".js")) files.push(full);
    }
  };
  walk(path.join(desktop, "main"));
  for (const file of files) {
    for (const match of fs.readFileSync(file, "utf8").matchAll(/require\(\s*["'](\.{1,2}\/[^"']+)["']\s*\)/g)) {
      const target = path.relative(desktop, path.resolve(path.dirname(file), match[1]));
      assert.ok(target === "main" || target.startsWith(`main${path.sep}`),
        `${path.relative(desktop, file)} loads ${match[1]}, which a sealed version does not carry`);
    }
  }
}

(async () => {
  checkVersionCarriesItsOwnCode();
  await checkExtraction();
  await checkSeed();
  await checkWindowsLinks();
  await checkSeedSafeguards();
  checkIdentityRefresh();
  checkRelaunchPaths();
  checkRunningRelease();
  await checkUseVersion();
  await checkLoader();
  console.log("thin shell checks passed");
  process.exit(0);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
