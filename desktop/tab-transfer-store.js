const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const STORE_VERSION = 1;
const VALID_ROLES = new Set(["source", "destination"]);

function emptyTransferDecisions() {
  return { version: STORE_VERSION, decisions: {} };
}

function isPlainObject(value) {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function loadTransferDecisions(filePath) {
  let contents;
  try {
    contents = fs.readFileSync(filePath, "utf8");
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
    return emptyTransferDecisions();
  }
  let parsed;
  try {
    parsed = JSON.parse(contents);
  } catch (_error) {
    return emptyTransferDecisions();
  }
  if (
    parsed?.version !== STORE_VERSION
    || !isPlainObject(parsed.decisions)
  ) {
    return emptyTransferDecisions();
  }
  return { version: STORE_VERSION, decisions: parsed.decisions };
}

function temporaryPath(filePath, purpose) {
  const suffix = crypto.randomBytes(8).toString("hex");
  return path.join(
    path.dirname(filePath),
    `.${path.basename(filePath)}.${process.pid}.${purpose}.${suffix}.tmp`,
  );
}

function removeIfPresent(filePath) {
  try {
    fs.unlinkSync(filePath);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
}

function writeSyncedFile(filePath, bytes) {
  let fd = null;
  let failure = null;
  try {
    fd = fs.openSync(filePath, "wx", 0o600);
    fs.writeFileSync(fd, bytes);
    fs.fsyncSync(fd);
  } catch (error) {
    failure = error;
  }
  if (fd !== null) {
    try {
      fs.closeSync(fd);
    } catch (error) {
      if (!failure) failure = error;
    }
  }
  if (failure) throw failure;
}

function syncDirectory(directory) {
  let fd = null;
  let failure = null;
  try {
    fd = fs.openSync(directory, "r");
    fs.fsyncSync(fd);
  } catch (error) {
    failure = error;
  }
  if (fd !== null) {
    try {
      fs.closeSync(fd);
    } catch (error) {
      if (!failure) failure = error;
    }
  }
  if (failure) throw failure;
}

function restorePreviousFile(filePath, previousBytes) {
  const directory = path.dirname(filePath);
  if (previousBytes === null) {
    removeIfPresent(filePath);
    syncDirectory(directory);
    return;
  }

  const rollbackPath = temporaryPath(filePath, "rollback");
  try {
    writeSyncedFile(rollbackPath, previousBytes);
    fs.renameSync(rollbackPath, filePath);
    syncDirectory(directory);
  } finally {
    removeIfPresent(rollbackPath);
  }
}

function saveTransferDecisionsAtomic(filePath, value) {
  const directory = path.dirname(filePath);
  const bytes = Buffer.from(`${JSON.stringify(value, null, 2)}\n`, "utf8");
  fs.mkdirSync(directory, { recursive: true });

  let previousBytes = null;
  try {
    previousBytes = fs.readFileSync(filePath);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }

  const tempPath = temporaryPath(filePath, "write");
  let renameAttempted = false;
  let renamed = false;
  try {
    writeSyncedFile(tempPath, bytes);
    renameAttempted = true;
    fs.renameSync(tempPath, filePath);
    renamed = true;
    syncDirectory(directory);
    return value;
  } catch (error) {
    const targetWasReplaced = renamed
      || (renameAttempted && !fs.existsSync(tempPath));
    if (targetWasReplaced) {
      try {
        restorePreviousFile(filePath, previousBytes);
      } catch (rollbackError) {
        error.rollbackError = rollbackError;
      }
    }
    throw error;
  } finally {
    removeIfPresent(tempPath);
  }
}

function normalizeRole(value) {
  if (
    !isPlainObject(value)
    || !VALID_ROLES.has(value.role)
    || typeof value.windowId !== "string"
    || !value.windowId
  ) {
    throw new TypeError("Transfer role must contain a valid role and windowId");
  }
  return { role: value.role, windowId: value.windowId };
}

function roleKey(value) {
  return JSON.stringify([value.role, value.windowId]);
}

function uniqueRoles(values, fieldName) {
  if (!Array.isArray(values)) {
    throw new TypeError(`${fieldName} must be an array`);
  }
  const roles = new Map();
  for (const value of values) {
    const normalized = normalizeRole(value);
    const key = roleKey(normalized);
    if (!roles.has(key)) roles.set(key, normalized);
  }
  return [...roles.values()];
}

function normalizeDecision(value) {
  if (!isPlainObject(value) || typeof value.token !== "string" || !value.token) {
    throw new TypeError("Transfer decision requires a token");
  }
  if (value.status !== "committed" && value.status !== "rolled-back") {
    throw new TypeError("Transfer decision requires a terminal status");
  }

  const requiredRoles = uniqueRoles(value.requiredRoles, "requiredRoles");
  const requiredKeys = new Set(requiredRoles.map(roleKey));
  const finalizedRoles = uniqueRoles(value.finalizedRoles || [], "finalizedRoles");
  for (const role of finalizedRoles) {
    if (!requiredKeys.has(roleKey(role))) {
      throw new TypeError("finalizedRoles contains a non-required role");
    }
  }

  return {
    ...value,
    token: value.token,
    requiredRoles,
    finalizedRoles,
  };
}

function putTransferDecision(filePath, decision) {
  const normalized = normalizeDecision(decision);
  const store = loadTransferDecisions(filePath);
  store.decisions[normalized.token] = normalized;
  saveTransferDecisionsAtomic(filePath, store);
  return normalized;
}

function ackTransferDecision(filePath, token, role) {
  const store = loadTransferDecisions(filePath);
  const stored = store.decisions[token];
  if (!stored) {
    const error = new Error(`Unknown transfer decision: ${token}`);
    error.code = "ERR_TRANSFER_DECISION_UNKNOWN";
    throw error;
  }

  const decision = normalizeDecision(stored);
  const acknowledgedRole = normalizeRole(role);
  const acknowledgedKey = roleKey(acknowledgedRole);
  const requiredKeys = new Set(decision.requiredRoles.map(roleKey));
  if (!requiredKeys.has(acknowledgedKey)) {
    const error = new Error(
      `Transfer role ${acknowledgedRole.role}:${acknowledgedRole.windowId} is not required`,
    );
    error.code = "ERR_TRANSFER_ROLE_NOT_REQUIRED";
    throw error;
  }

  const finalizedKeys = new Set(decision.finalizedRoles.map(roleKey));
  if (finalizedKeys.has(acknowledgedKey)) {
    return {
      decision,
      complete: decision.requiredRoles.every((item) =>
        finalizedKeys.has(roleKey(item))),
    };
  }

  const updated = {
    ...decision,
    finalizedRoles: [...decision.finalizedRoles, acknowledgedRole],
  };
  const updatedKeys = new Set(updated.finalizedRoles.map(roleKey));
  const complete = updated.requiredRoles.every((item) =>
    updatedKeys.has(roleKey(item)));
  if (complete) delete store.decisions[token];
  else store.decisions[token] = updated;
  saveTransferDecisionsAtomic(filePath, store);
  return { decision: updated, complete };
}

module.exports = {
  loadTransferDecisions,
  saveTransferDecisionsAtomic,
  putTransferDecision,
  ackTransferDecision,
};
