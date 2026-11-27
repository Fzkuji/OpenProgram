"use strict";

// What a version-store App runs, for the channel check in update-service.js:
// the channel sequence it was built from and its shell compatibility.
const fs = require("node:fs");
const path = require("node:path");

const ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const MAX_ANCESTORS = 64;

/** The version `current` names, read only; null when the pointer cannot be read. */
function pointedVersion(root) {
  try {
    const match = /^version=(.*)$/m.exec(fs.readFileSync(path.join(root, "current"), "ascii"));
    const id = match?.[1]?.trim();
    return id && ID.test(id) ? id : null;
  } catch {
    return null;
  }
}

function readVersion(root, id) {
  try {
    const value = JSON.parse(fs.readFileSync(path.join(root, "versions", id, "version.json"), "utf8"));
    return value && value.id === id ? value : null;
  } catch {
    return null;
  }
}

/**
 * `fallbackId` is the version this main process was loaded from, used when
 * the pointer cannot be read. `shellCompat` is the shell's { min, max }, or
 * null from a shell that does not say it.
 */
function readRunningBuild(root, fallbackId, shellCompat = null) {
  if (!root) return null;
  const start = pointedVersion(root) || fallbackId;
  const running = start && ID.test(start) ? readVersion(root, start) : null;
  if (!running) return null;
  // The sequence of the nearest version up the chain built from the channel;
  // one with the owner's own commits on top names that version as its parent.
  let sequence = null;
  const seen = new Set();
  for (let id = start, count = 0; id && ID.test(id) && !seen.has(id) && count < MAX_ANCESTORS; count += 1) {
    seen.add(id);
    const manifest = id === start ? running : readVersion(root, id);
    if (!manifest) break;
    if (Number.isSafeInteger(manifest.official?.sequence)) {
      sequence = manifest.official.sequence;
      break;
    }
    id = typeof manifest.parent === "string" ? manifest.parent : null;
  }
  const range = shellCompat && Number.isInteger(shellCompat.min) && Number.isInteger(shellCompat.max)
    ? { min: shellCompat.min, max: shellCompat.max }
    : null;
  return { sequence, shell_compat: running.shell_compat, shellCompat: range };
}

module.exports = { pointedVersion, readRunningBuild };
