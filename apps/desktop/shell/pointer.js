"use strict";
// The `current` pointer, the frozen protocol shared with the launcher
// (runtime-launcher.c), the updater (versions/pointer.py) and
// bootstrap/versions.py. Keys, order and the write procedure must stay
// exactly as they are there: this file ships in the signed shell and does not
// change through a self-update.
const fs = require("node:fs");
const path = require("node:path");

const SCHEMA = "1";
const LOCK_BREAK_MS = 10_000;
const LOCK_WAIT_MS = 15_000;
const WINDOWS_RETRIES = 20;
const WINDOWS_RETRY_MS = 50;
const ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const KEYS = [
  "schema", "generation", "version", "base", "last_good", "last_good_base",
  "previous_good", "previous_good_base", "probation", "probation_deadline", "writer",
];

class PointerError extends Error {}
class PointerConflict extends Error {}

function validId(value) {
  return typeof value === "string" && ID.test(value);
}

function sleep(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

const SHARING = new Set(["EACCES", "EPERM", "EBUSY"]);
function retrying(operation, platform = process.platform) {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return operation();
    } catch (error) {
      if (platform !== "win32" || !SHARING.has(error.code) || attempt >= WINDOWS_RETRIES - 1) throw error;
      sleep(WINDOWS_RETRY_MS);
    }
  }
}

function parse(text) {
  if (!/^[\x00-\x7f]*$/.test(text)) throw new PointerError("the pointer is not ASCII");
  const values = new Map();
  for (const line of text.split(/\r?\n/)) {
    if (!line) continue;
    const separator = line.indexOf("=");
    if (separator <= 0) throw new PointerError(`malformed pointer line: ${line}`);
    const key = line.slice(0, separator);
    if (values.has(key)) throw new PointerError(`duplicate pointer key: ${key}`);
    values.set(key, line.slice(separator + 1));
  }
  if (values.get("schema") !== SCHEMA) throw new PointerError("unsupported pointer schema");
  if (!/^\d+$/.test(values.get("generation") || "")) throw new PointerError("invalid generation");
  if (!validId(values.get("version")) || !validId(values.get("base"))) {
    throw new PointerError("the pointer names no valid version and base");
  }
  for (const key of ["last_good", "last_good_base", "previous_good", "previous_good_base", "probation"]) {
    const value = values.get(key) || "";
    if (value && !validId(value)) throw new PointerError(`invalid ${key}`);
  }
  const deadline = values.get("probation_deadline") || "";
  if (deadline && !/^\d+$/.test(deadline)) throw new PointerError("invalid probation_deadline");
  const extras = [...values].filter(([key]) => !KEYS.includes(key));
  return {
    generation: Number(values.get("generation")),
    version: values.get("version"),
    base: values.get("base"),
    last_good: values.get("last_good") || "",
    last_good_base: values.get("last_good_base") || "",
    previous_good: values.get("previous_good") || "",
    previous_good_base: values.get("previous_good_base") || "",
    probation: values.get("probation") || "",
    probation_deadline: deadline,
    writer: values.get("writer") || "",
    extras,
  };
}

function render(pointer) {
  const lines = [
    ["schema", SCHEMA],
    ["generation", String(pointer.generation)],
    ["version", pointer.version],
    ["base", pointer.base],
    ["last_good", pointer.last_good || ""],
    ["last_good_base", pointer.last_good_base || ""],
    ["previous_good", pointer.previous_good || ""],
    ["previous_good_base", pointer.previous_good_base || ""],
    ["probation", pointer.probation || ""],
    ["probation_deadline", pointer.probation_deadline == null ? "" : String(pointer.probation_deadline)],
    ["writer", pointer.writer || ""],
    ...(pointer.extras || []),
  ];
  return lines.map(([key, value]) => `${key}=${value}\n`).join("");
}

function readFile(file) {
  return retrying(() => fs.readFileSync(file, "ascii"));
}

function read(root) {
  let text;
  try {
    text = readFile(path.join(root, "current"));
  } catch (error) {
    throw new PointerError(`cannot read the pointer: ${error.message}`);
  }
  return parse(text);
}

/** Where a failing `version` goes back to, if anywhere. */
function fallback(pointer) {
  const [version, base] = pointer.version === pointer.last_good && pointer.last_good
    ? [pointer.previous_good, pointer.previous_good_base]
    : [pointer.last_good, pointer.last_good_base];
  return version && base ? { version, base } : null;
}

function fsyncDirectory(directory, platform = process.platform) {
  if (platform === "win32") return;
  const descriptor = fs.openSync(directory, "r");
  try {
    fs.fsyncSync(descriptor);
  } finally {
    fs.closeSync(descriptor);
  }
}

function writeDurably(file, data) {
  const temporary = `${file}.${process.pid}.tmp`;
  try {
    const descriptor = fs.openSync(temporary, "w", 0o644);
    try {
      fs.writeSync(descriptor, data);
      fs.fsyncSync(descriptor);
    } finally {
      fs.closeSync(descriptor);
    }
    retrying(() => fs.renameSync(temporary, file));
    fsyncDirectory(path.dirname(file));
  } finally {
    fs.rmSync(temporary, { force: true });
  }
}

function acquire(root) {
  const lock = path.join(root, "current.lock");
  const deadline = Date.now() + LOCK_WAIT_MS;
  for (;;) {
    try {
      fs.closeSync(fs.openSync(lock, "wx", 0o600));
      return lock;
    } catch (error) {
      if (error.code !== "EEXIST" && !(process.platform === "win32" && SHARING.has(error.code))) throw error;
    }
    try {
      if (Date.now() - fs.statSync(lock).mtimeMs > LOCK_BREAK_MS) {
        fs.rmSync(lock, { force: true });
        continue;
      }
    } catch {
      continue;
    }
    if (Date.now() >= deadline) throw new PointerConflict("another writer holds the pointer lock");
    sleep(50);
  }
}

function withLock(root, action) {
  const lock = acquire(root);
  try {
    return action();
  } finally {
    fs.rmSync(lock, { force: true });
  }
}

/** The pointer, restored from `current.prev` when `current` is damaged. */
function readOrRecover(root) {
  try {
    return read(root);
  } catch (error) {
    if (!(error instanceof PointerError)) throw error;
  }
  const previous = parse(readFile(path.join(root, "current.prev")));
  withLock(root, () => writeDurably(path.join(root, "current"), render(previous)));
  return read(root);
}

function compareAndSet(root, expectedGeneration, writer, changes) {
  return withLock(root, () => {
    const present = read(root);
    if (present.generation !== expectedGeneration) {
      throw new PointerConflict(`the pointer moved to generation ${present.generation}`);
    }
    const following = { ...present, ...changes, generation: present.generation + 1, writer };
    const data = render(following);
    parse(data);
    writeDurably(path.join(root, "current.prev"), fs.readFileSync(path.join(root, "current")));
    writeDurably(path.join(root, "current"), data);
    return following;
  });
}

/** Write a pointer when the old one is gone and cannot be recovered. */
function replaceLost(root, pointer) {
  return withLock(root, () => {
    const data = render(pointer);
    parse(data);
    writeDurably(path.join(root, "current"), data);
    return pointer;
  });
}

module.exports = {
  PointerConflict,
  PointerError,
  compareAndSet,
  fallback,
  parse,
  read,
  readOrRecover,
  render,
  replaceLost,
  validId,
};
