"use strict";

const fs = require("fs");
const path = require("path");

const STATE_VERSION = 2;
const STATE_FILE_NAME = "window-state.json";

const DEFAULT_WIDTH = 1440;
const DEFAULT_HEIGHT = 900;
const MIN_WIDTH = 800;
const MIN_HEIGHT = 500;
const DETACHED_MAX_WIDTH = 1100;
const DETACHED_MAX_HEIGHT = 720;

// A rectangle within this many CSS pixels of a display work area is treated
// as "filled the screen" — never stored or restored as a normal window.
const FILL_TOLERANCE_PX = 8;
// When the default size itself would fill the work area, inset so restore
// always leaves grabable edges (and Zoom has a smaller target).
const EDGE_INSET_PX = 24;
const SAVE_DEBOUNCE_MS = 300;

const FALLBACK_WORK_AREA = { x: 0, y: 0, width: DEFAULT_WIDTH, height: DEFAULT_HEIGHT };

function isFiniteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function normalizeRect(raw) {
  if (!raw || typeof raw !== "object") return null;
  const x = Number(raw.x);
  const y = Number(raw.y);
  const width = Number(raw.width);
  const height = Number(raw.height);
  if (![x, y, width, height].every(Number.isFinite)) return null;
  return { x, y, width, height };
}

function isUsableBounds(bounds) {
  const rect = normalizeRect(bounds);
  return Boolean(rect && rect.width >= MIN_WIDTH && rect.height >= MIN_HEIGHT);
}

function rectsMatch(a, b, tolerance = 2) {
  const left = normalizeRect(a);
  const right = normalizeRect(b);
  if (!left || !right) return false;
  return (
    Math.abs(left.x - right.x) <= tolerance &&
    Math.abs(left.y - right.y) <= tolerance &&
    Math.abs(left.width - right.width) <= tolerance &&
    Math.abs(left.height - right.height) <= tolerance
  );
}

function boundsFillWorkArea(bounds, workArea, tolerance = FILL_TOLERANCE_PX) {
  return rectsMatch(bounds, workArea, tolerance);
}

function overlapArea(a, b) {
  const left = normalizeRect(a);
  const right = normalizeRect(b);
  if (!left || !right) return 0;
  const x = Math.max(0, Math.min(left.x + left.width, right.x + right.width) - Math.max(left.x, right.x));
  const y = Math.max(0, Math.min(left.y + left.height, right.y + right.height) - Math.max(left.y, right.y));
  return x * y;
}

// Same visibility heuristic the previous inline helper used: a stale
// position after a monitor-layout change must not reopen fully off-screen.
function boundsVisibleOn(bounds, workArea) {
  const b = normalizeRect(bounds);
  const a = normalizeRect(workArea);
  if (!b || !a) return false;
  return (
    b.x < a.x + a.width - 40 &&
    b.x + b.width > a.x + 40 &&
    b.y >= a.y - 20 &&
    b.y < a.y + a.height - 40
  );
}

function pickPrimary(displays, explicit) {
  if (explicit && displays.some((display) => display === explicit || display.id === explicit.id)) {
    return explicit;
  }
  if (!displays?.length) return { id: null, workArea: FALLBACK_WORK_AREA };
  return displays.find((display) => display.internal) || displays[0];
}

function displayForBounds(bounds, displays) {
  if (!displays?.length) return null;
  let best = null;
  let bestArea = 0;
  for (const display of displays) {
    const area = overlapArea(bounds, display.workArea || display.bounds);
    if (area > bestArea) {
      best = display;
      bestArea = area;
    }
  }
  return bestArea > 0 ? best : null;
}

function resolveDisplay(saved, displays) {
  if (!displays?.length) return null;
  if (saved.displayId != null) {
    const byId = displays.find((display) => display.id === saved.displayId);
    if (byId) return byId;
  }
  if (saved.displayWorkArea) {
    const byArea = displays.find((display) => rectsMatch(display.workArea, saved.displayWorkArea, 2));
    if (byArea) return byArea;
  }
  const bounds = normalizeRect(saved);
  if (bounds) {
    const overlapping = displays.find((display) => boundsVisibleOn(bounds, display.workArea));
    if (overlapping) return overlapping;
  }
  return null;
}

function defaultNormalBounds(workArea) {
  const area = normalizeRect(workArea) || FALLBACK_WORK_AREA;
  let width = Math.min(DEFAULT_WIDTH, area.width);
  let height = Math.min(DEFAULT_HEIGHT, area.height);
  if (width >= area.width - FILL_TOLERANCE_PX) {
    width = Math.max(Math.min(MIN_WIDTH, area.width), area.width - EDGE_INSET_PX * 2);
  }
  if (height >= area.height - FILL_TOLERANCE_PX) {
    height = Math.max(Math.min(MIN_HEIGHT, area.height), area.height - EDGE_INSET_PX * 2);
  }
  width = Math.max(Math.min(width, area.width), Math.min(MIN_WIDTH, area.width));
  height = Math.max(Math.min(height, area.height), Math.min(MIN_HEIGHT, area.height));
  return {
    x: Math.round(area.x + (area.width - width) / 2),
    y: Math.round(area.y + (area.height - height) / 2),
    width: Math.round(width),
    height: Math.round(height),
  };
}

function clampBounds(bounds, workArea) {
  const area = normalizeRect(workArea);
  const rect = normalizeRect(bounds);
  if (!rect) return defaultNormalBounds(area || FALLBACK_WORK_AREA);
  if (!area) return rect;
  const width = Math.round(Math.min(Math.max(rect.width, Math.min(MIN_WIDTH, area.width)), area.width));
  const height = Math.round(Math.min(Math.max(rect.height, Math.min(MIN_HEIGHT, area.height)), area.height));
  const maxX = area.x + area.width - width;
  const maxY = area.y + area.height - height;
  return {
    x: Math.round(Math.min(Math.max(rect.x, area.x), Math.max(area.x, maxX))),
    y: Math.round(Math.min(Math.max(rect.y, area.y), Math.max(area.y, maxY))),
    width,
    height,
  };
}

function serializeState(normal, flags, display) {
  const workArea = normalizeRect(display?.workArea || flags.displayWorkArea);
  return {
    version: STATE_VERSION,
    x: normal.x,
    y: normal.y,
    width: normal.width,
    height: normal.height,
    isMaximized: Boolean(flags.isMaximized),
    isFullScreen: Boolean(flags.isFullScreen),
    displayId: display?.id ?? flags.displayId ?? null,
    displayWorkArea: workArea,
  };
}

function migrateState(raw) {
  if (!raw || typeof raw !== "object") return null;
  const width = Number(raw.width);
  const height = Number(raw.height);
  if (!Number.isFinite(width) || !Number.isFinite(height)) return null;
  const x = Number(raw.x);
  const y = Number(raw.y);
  return {
    version: STATE_VERSION,
    x: Number.isFinite(x) ? x : undefined,
    y: Number.isFinite(y) ? y : undefined,
    width,
    height,
    isMaximized: Boolean(raw.isMaximized),
    isFullScreen: Boolean(raw.isFullScreen),
    displayId: raw.displayId ?? raw.display?.id ?? null,
    displayWorkArea: normalizeRect(raw.displayWorkArea || raw.display?.workArea),
  };
}

function planFromNormal(normal, isMaximized, isFullScreen, display) {
  return {
    x: normal.x,
    y: normal.y,
    width: normal.width,
    height: normal.height,
    isMaximized: Boolean(isMaximized),
    isFullScreen: Boolean(isFullScreen),
    minWidth: MIN_WIDTH,
    minHeight: MIN_HEIGHT,
    displayId: display?.id ?? null,
    displayWorkArea: normalizeRect(display?.workArea),
  };
}

function resolveRestorePlan(raw, displays = [], options = {}) {
  const primary = pickPrimary(displays, options.primary);
  const fallbackArea = primary.workArea || FALLBACK_WORK_AREA;
  const saved = migrateState(raw);
  if (!saved) {
    return planFromNormal(defaultNormalBounds(fallbackArea), false, false, primary);
  }

  const display = resolveDisplay(saved, displays);
  if (!display) {
    return planFromNormal(defaultNormalBounds(fallbackArea), false, false, primary);
  }

  const area = display.workArea || fallbackArea;
  let normal = {
    x: saved.x,
    y: saved.y,
    width: saved.width,
    height: saved.height,
  };

  if (!isFiniteNumber(normal.x) || !isFiniteNumber(normal.y)) {
    const sized = {
      width: Math.min(Math.max(Number(normal.width) || DEFAULT_WIDTH, MIN_WIDTH), area.width),
      height: Math.min(Math.max(Number(normal.height) || DEFAULT_HEIGHT, MIN_HEIGHT), area.height),
    };
    if (
      sized.width >= area.width - FILL_TOLERANCE_PX &&
      sized.height >= area.height - FILL_TOLERANCE_PX
    ) {
      return planFromNormal(defaultNormalBounds(area), true, saved.isFullScreen, display);
    }
    return planFromNormal(
      {
        x: Math.round(area.x + (area.width - sized.width) / 2),
        y: Math.round(area.y + (area.height - sized.height) / 2),
        width: Math.round(sized.width),
        height: Math.round(sized.height),
      },
      saved.isMaximized,
      saved.isFullScreen,
      display,
    );
  }

  if (!isUsableBounds(normal)) {
    return planFromNormal(defaultNormalBounds(area), false, false, display);
  }

  if (boundsFillWorkArea(normal, area)) {
    return planFromNormal(defaultNormalBounds(area), true, saved.isFullScreen, display);
  }

  normal = clampBounds(normal, area);
  if (boundsFillWorkArea(normal, area)) {
    return planFromNormal(defaultNormalBounds(area), true, saved.isFullScreen, display);
  }
  if (!isUsableBounds(normal) && (normal.width < MIN_WIDTH || normal.height < MIN_HEIGHT)) {
    return planFromNormal(defaultNormalBounds(area), false, false, display);
  }
  return planFromNormal(normal, saved.isMaximized, saved.isFullScreen, display);
}

function readStateFile(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return null;
  }
}

function writePersistedState(filePath, state) {
  const dir = path.dirname(filePath);
  fs.mkdirSync(dir, { recursive: true });
  const tmp = `${filePath}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(state));
  fs.renameSync(tmp, filePath);
}

function loadPersistedState(filePath, displays, options) {
  return resolveRestorePlan(readStateFile(filePath), displays, options);
}

function snapshotFromWindow(win, displays = []) {
  const bounds = normalizeRect(win.getBounds()) || defaultNormalBounds(FALLBACK_WORK_AREA);
  let normalBounds = bounds;
  try {
    if (typeof win.getNormalBounds === "function") {
      normalBounds = normalizeRect(win.getNormalBounds()) || bounds;
    }
  } catch {
    normalBounds = bounds;
  }
  const isMaximized = Boolean(typeof win.isMaximized === "function" && win.isMaximized());
  const isFullScreen = Boolean(typeof win.isFullScreen === "function" && win.isFullScreen());
  const display = displayForBounds(isMaximized || isFullScreen ? normalBounds : bounds, displays)
    || displays[0]
    || null;
  return { bounds, normalBounds, isMaximized, isFullScreen, display };
}

function usableNormal(bounds, workArea) {
  return isUsableBounds(bounds) && !boundsFillWorkArea(bounds, workArea);
}

function captureWindowState(snapshot, previousNormal) {
  const display = snapshot.display || null;
  const workArea = display?.workArea || null;
  const filled = Boolean(workArea && boundsFillWorkArea(snapshot.bounds, workArea));
  const chromeExpanded = snapshot.isMaximized || snapshot.isFullScreen || filled;

  let normal;
  if (chromeExpanded) {
    if (usableNormal(previousNormal, workArea)) {
      normal = normalizeRect(previousNormal);
    } else if (usableNormal(snapshot.normalBounds, workArea)) {
      normal = normalizeRect(snapshot.normalBounds);
    } else {
      normal = defaultNormalBounds(workArea || FALLBACK_WORK_AREA);
    }
  } else {
    normal = clampBounds(snapshot.normalBounds || snapshot.bounds, workArea);
  }

  return serializeState(normal, {
    isMaximized: snapshot.isMaximized || filled,
    isFullScreen: snapshot.isFullScreen,
  }, display);
}

function browserWindowOptionsForPlan(plan, { detached = false } = {}) {
  const width = detached ? Math.min(DETACHED_MAX_WIDTH, plan.width) : plan.width;
  const height = detached ? Math.min(DETACHED_MAX_HEIGHT, plan.height) : plan.height;
  const options = {
    width,
    height,
    minWidth: MIN_WIDTH,
    minHeight: MIN_HEIGHT,
  };
  if (!detached && isFiniteNumber(plan.x) && isFiniteNumber(plan.y)) {
    options.x = plan.x;
    options.y = plan.y;
  }
  return options;
}

function applyRestoredChrome(win, plan, { detached = false } = {}) {
  if (detached || !win || win.isDestroyed?.()) return;
  // Size to the work area before maximize so the first visible frame is
  // already full. Showing a normal rect then maximize() is the macOS
  // zoom-from-current-frame animation (often from the top-left).
  if (plan.isMaximized && typeof win.maximize === "function") {
    const area = normalizeRect(plan.displayWorkArea);
    if (area && typeof win.setBounds === "function") win.setBounds(area);
    win.maximize();
  }
  if (plan.isFullScreen && typeof win.setFullScreen === "function") win.setFullScreen(true);
}

function attachWindowStatePersistence(win, options) {
  const {
    filePath,
    getDisplays = () => [],
    debounceMs = SAVE_DEBOUNCE_MS,
    setTimeoutFn = setTimeout,
    clearTimeoutFn = clearTimeout,
  } = options;

  let timer = null;
  let lastNormal = null;
  const initial = snapshotFromWindow(win, getDisplays());
  if (usableNormal(initial.normalBounds, initial.display?.workArea)) {
    lastNormal = { ...initial.normalBounds };
  }

  const flush = () => {
    if (timer != null) {
      clearTimeoutFn(timer);
      timer = null;
    }
    if (!win || win.isDestroyed?.()) return;
    try {
      const captured = captureWindowState(snapshotFromWindow(win, getDisplays()), lastNormal);
      lastNormal = { x: captured.x, y: captured.y, width: captured.width, height: captured.height };
      writePersistedState(filePath, captured);
    } catch {
      /* non-fatal */
    }
  };

  const schedule = () => {
    if (timer != null) clearTimeoutFn(timer);
    timer = setTimeoutFn(flush, debounceMs);
  };

  win.on("resize", schedule);
  win.on("move", schedule);
  win.on("maximize", flush);
  win.on("unmaximize", flush);
  win.on("enter-full-screen", flush);
  win.on("leave-full-screen", flush);
  win.on("close", flush);

  return flush;
}

module.exports = {
  STATE_VERSION,
  STATE_FILE_NAME,
  DEFAULT_WIDTH,
  DEFAULT_HEIGHT,
  MIN_WIDTH,
  MIN_HEIGHT,
  DETACHED_MAX_WIDTH,
  DETACHED_MAX_HEIGHT,
  FILL_TOLERANCE_PX,
  EDGE_INSET_PX,
  SAVE_DEBOUNCE_MS,
  isUsableBounds,
  boundsFillWorkArea,
  defaultNormalBounds,
  clampBounds,
  migrateState,
  resolveDisplay,
  resolveRestorePlan,
  readStateFile,
  writePersistedState,
  loadPersistedState,
  snapshotFromWindow,
  captureWindowState,
  browserWindowOptionsForPlan,
  applyRestoredChrome,
  attachWindowStatePersistence,
};
