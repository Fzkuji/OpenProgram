// Windows torn off the main window, reopened on the next launch.
//
// Each window keeps its tabs in the renderer's localStorage under its own
// window id (centerTabs:<id>), so reopening a window with the same id brings
// its tabs back. At quit this records which torn-off windows were open and
// where; the next launch reopens them once and forgets the list.
"use strict";

const fs = require("fs");
const path = require("path");

const FILE_NAME = "session-windows.json";
const MAX_WINDOWS = 20;
const WINDOW_ID = /^window-[A-Za-z0-9_-]{1,80}$/;

function normalizeEntry(entry) {
  if (!entry || typeof entry !== "object" || !WINDOW_ID.test(String(entry.windowId || ""))) return null;
  const b = entry.bounds || {};
  const bounds = ["x", "y", "width", "height"].every((key) => Number.isFinite(b[key]))
    ? { x: b.x, y: b.y, width: b.width, height: b.height }
    : null;
  return { windowId: entry.windowId, bounds };
}

function recordOpenWindows(filePath, entries) {
  const list = (Array.isArray(entries) ? entries : []).map(normalizeEntry).filter(Boolean).slice(0, MAX_WINDOWS);
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  if (!list.length) {
    fs.rmSync(filePath, { force: true });
    return list;
  }
  const temporary = `${filePath}.tmp-${process.pid}`;
  fs.writeFileSync(temporary, `${JSON.stringify({ version: 1, windows: list }, null, 2)}\n`, "utf8");
  fs.renameSync(temporary, filePath);
  return list;
}

/** The windows to reopen, read once: the file is gone afterwards. */
function takeOpenWindows(filePath) {
  let parsed;
  try {
    parsed = JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (_error) {
    return [];
  } finally {
    try { fs.rmSync(filePath, { force: true }); } catch (_error) { /* nothing to forget */ }
  }
  if (!parsed || parsed.version !== 1 || !Array.isArray(parsed.windows)) return [];
  const seen = new Set();
  return parsed.windows.map(normalizeEntry).filter((entry) => {
    if (!entry || seen.has(entry.windowId)) return false;
    seen.add(entry.windowId);
    return true;
  }).slice(0, MAX_WINDOWS);
}

module.exports = { FILE_NAME, recordOpenWindows, takeOpenWindows };
