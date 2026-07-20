const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const STORE_VERSION = 1;
const MAX_ENTRIES = 5000;

function emptyHistory() {
  return { version: STORE_VERSION, entries: [] };
}

function isWebUrl(value) {
  if (typeof value !== "string" || !value) return false;
  try {
    const { protocol } = new URL(value);
    return protocol === "http:" || protocol === "https:";
  } catch (_error) {
    return false;
  }
}

function normalizeEntry(value) {
  if (!value || typeof value !== "object" || !isWebUrl(value.url)) return null;
  const visitedAt = Number(value.visitedAt);
  return {
    url: value.url,
    title: typeof value.title === "string" ? value.title : "",
    faviconUrl: isWebUrl(value.faviconUrl) ? value.faviconUrl : "",
    visitedAt: Number.isFinite(visitedAt) ? visitedAt : Date.now(),
  };
}

function loadHistory(filePath) {
  let parsed;
  try {
    parsed = JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (error) {
    if (error?.code !== "ENOENT" && !(error instanceof SyntaxError)) throw error;
    return emptyHistory();
  }
  if (parsed?.version !== STORE_VERSION || !Array.isArray(parsed.entries)) {
    return emptyHistory();
  }
  const entries = [];
  for (const raw of parsed.entries) {
    const entry = normalizeEntry(raw);
    if (entry) entries.push(entry);
  }
  return { version: STORE_VERSION, entries };
}

// Same durability shape as tab-transfer-store: write a temp file, fsync it,
// rename over the target. A torn write leaves the previous file intact.
// ponytail: no rollback-on-failure dance here — losing history is cosmetic,
// unlike a half-applied tab transfer.
function saveHistoryAtomic(filePath, value) {
  const directory = path.dirname(filePath);
  fs.mkdirSync(directory, { recursive: true });
  const bytes = Buffer.from(`${JSON.stringify(value)}\n`, "utf8");
  const tempPath = path.join(
    directory,
    `.${path.basename(filePath)}.${process.pid}.${crypto
      .randomBytes(8)
      .toString("hex")}.tmp`,
  );
  let fd = null;
  try {
    fd = fs.openSync(tempPath, "wx", 0o600);
    fs.writeFileSync(fd, bytes);
    fs.fsyncSync(fd);
    fs.closeSync(fd);
    fd = null;
    fs.renameSync(tempPath, filePath);
  } finally {
    if (fd !== null) {
      try {
        fs.closeSync(fd);
      } catch (_error) {
        /* already failing */
      }
    }
    try {
      fs.unlinkSync(tempPath);
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
  }
  return value;
}

/**
 * Append a visit, newest first. Re-visiting the URL that is already at the
 * head only refreshes its timestamp/title/favicon, so holding a page (which
 * fires title and favicon events after did-navigate) stays one row.
 */
function recordVisit(filePath, visit) {
  const entry = normalizeEntry(visit);
  if (!entry) return null;
  const store = loadHistory(filePath);
  const head = store.entries[0];
  if (head && head.url === entry.url) {
    store.entries[0] = {
      ...head,
      title: entry.title || head.title,
      faviconUrl: entry.faviconUrl || head.faviconUrl,
      visitedAt: entry.visitedAt,
    };
  } else {
    store.entries.unshift(entry);
  }
  if (store.entries.length > MAX_ENTRIES) store.entries.length = MAX_ENTRIES;
  saveHistoryAtomic(filePath, store);
  return store.entries[0];
}

function listHistory(filePath, { limit = 200, query = "" } = {}) {
  const { entries } = loadHistory(filePath);
  const needle = String(query || "").trim().toLowerCase();
  const matched = needle
    ? entries.filter(
        (entry) =>
          entry.url.toLowerCase().includes(needle) ||
          entry.title.toLowerCase().includes(needle),
      )
    : entries;
  const count = Number(limit);
  return matched.slice(0, Number.isFinite(count) && count > 0 ? count : 200);
}

function deleteHistoryEntry(filePath, url, visitedAt) {
  const store = loadHistory(filePath);
  const before = store.entries.length;
  const stamp = Number(visitedAt);
  store.entries = store.entries.filter(
    (entry) =>
      entry.url !== url ||
      (Number.isFinite(stamp) && entry.visitedAt !== stamp),
  );
  if (store.entries.length === before) return false;
  saveHistoryAtomic(filePath, store);
  return true;
}

function clearHistory(filePath) {
  saveHistoryAtomic(filePath, emptyHistory());
  return true;
}

module.exports = {
  MAX_ENTRIES,
  loadHistory,
  saveHistoryAtomic,
  recordVisit,
  listHistory,
  deleteHistoryEntry,
  clearHistory,
};

// Self-check: `node desktop/browsing-history-store.js`
if (require.main === module) {
  const assert = require("node:assert");
  const os = require("node:os");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "op-history-"));
  const file = path.join(dir, "browsing-history.json");

  assert.deepStrictEqual(listHistory(file), [], "missing file reads empty");

  // Non-web URLs are rejected outright.
  assert.strictEqual(recordVisit(file, { url: "about:blank" }), null);
  assert.strictEqual(recordVisit(file, { url: "file:///etc/passwd" }), null);

  recordVisit(file, { url: "https://a.test/", title: "A", visitedAt: 1 });
  // Same URL at the head folds in, enriching rather than duplicating.
  recordVisit(file, {
    url: "https://a.test/",
    title: "A better",
    faviconUrl: "https://a.test/icon.png",
    visitedAt: 2,
  });
  let rows = listHistory(file);
  assert.strictEqual(rows.length, 1, "repeat head visit does not duplicate");
  assert.strictEqual(rows[0].title, "A better");
  assert.strictEqual(rows[0].faviconUrl, "https://a.test/icon.png");
  assert.strictEqual(rows[0].visitedAt, 2);

  // A different URL pushes to the front; returning to the old one is a new row.
  recordVisit(file, { url: "https://b.test/", title: "B", visitedAt: 3 });
  recordVisit(file, { url: "https://a.test/", title: "A", visitedAt: 4 });
  rows = listHistory(file);
  assert.deepStrictEqual(
    rows.map((row) => row.url),
    ["https://a.test/", "https://b.test/", "https://a.test/"],
    "newest first, revisits recorded",
  );

  assert.deepStrictEqual(
    listHistory(file, { query: "b.test" }).map((row) => row.url),
    ["https://b.test/"],
    "query matches url",
  );
  // Title search is case-insensitive.
  assert.deepStrictEqual(
    listHistory(file, { query: "A BETTER" }).map((row) => row.visitedAt),
    [2],
  );
  assert.strictEqual(listHistory(file, { limit: 1 }).length, 1);

  // Delete targets one visit, not every row sharing the URL.
  assert.strictEqual(deleteHistoryEntry(file, "https://a.test/", 4), true);
  assert.deepStrictEqual(
    listHistory(file).map((row) => row.visitedAt),
    [3, 2],
  );
  assert.strictEqual(deleteHistoryEntry(file, "https://nope.test/", 1), false);

  // Truncation keeps the newest MAX_ENTRIES.
  const many = {
    version: STORE_VERSION,
    entries: Array.from({ length: MAX_ENTRIES + 10 }, (_v, i) => ({
      url: `https://x.test/${i}`,
      title: "",
      faviconUrl: "",
      visitedAt: MAX_ENTRIES - i,
    })),
  };
  saveHistoryAtomic(file, many);
  recordVisit(file, { url: "https://newest.test/", visitedAt: 9e12 });
  rows = listHistory(file, { limit: MAX_ENTRIES + 100 });
  assert.strictEqual(rows.length, MAX_ENTRIES, "truncated to cap");
  assert.strictEqual(rows[0].url, "https://newest.test/");

  // Corrupt JSON degrades to empty instead of throwing.
  fs.writeFileSync(file, "{not json");
  assert.deepStrictEqual(listHistory(file), []);

  clearHistory(file);
  assert.deepStrictEqual(listHistory(file), []);

  fs.rmSync(dir, { recursive: true, force: true });
  console.log("browsing-history-store self-check passed");
}
