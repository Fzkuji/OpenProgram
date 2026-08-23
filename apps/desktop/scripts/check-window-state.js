const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const {
  STATE_VERSION,
  MIN_WIDTH,
  MIN_HEIGHT,
  DEFAULT_WIDTH,
  DEFAULT_HEIGHT,
  DETACHED_MAX_WIDTH,
  DETACHED_MAX_HEIGHT,
  EDGE_INSET_PX,
  migrateState,
  resolveRestorePlan,
  loadPersistedState,
  writePersistedState,
  captureWindowState,
  browserWindowOptionsForPlan,
  applyRestoredChrome,
  defaultNormalBounds,
  boundsFillWorkArea,
} = require("../window-state");

const MACBOOK_WORK_AREA = { x: 0, y: 38, width: 1512, height: 851 };
const MACBOOK = {
  id: 1,
  bounds: { x: 0, y: 0, width: 1512, height: 982 },
  workArea: MACBOOK_WORK_AREA,
};
const EXTERNAL = {
  id: 2,
  bounds: { x: 1512, y: 0, width: 2560, height: 1440 },
  workArea: { x: 1512, y: 25, width: 2560, height: 1415 },
};
const SMALL_PRIMARY = {
  id: 3,
  bounds: { x: 0, y: 0, width: 1440, height: 900 },
  workArea: { x: 0, y: 0, width: 1440, height: 900 },
};

function assertNormalHasGrabableEdges(plan, workArea) {
  assert.equal(boundsFillWorkArea(plan, workArea), false);
  assert.ok(plan.width <= workArea.width - 2 || plan.height <= workArea.height - 2);
}

function checkLegacyUsableBoundsMigrate() {
  const raw = { x: 120, y: 80, width: 1280, height: 800 };
  const saved = migrateState(raw);
  assert.equal(saved.version, STATE_VERSION);
  assert.equal(saved.isMaximized, false);
  assert.equal(saved.isFullScreen, false);
  const plan = resolveRestorePlan(raw, [MACBOOK]);
  assert.equal(plan.x, 120);
  assert.equal(plan.y, 80);
  assert.equal(plan.width, 1280);
  assert.equal(plan.height, 800);
  assert.equal(plan.isMaximized, false);
  assert.equal(plan.isFullScreen, false);
}

function checkLegacyFilledWorkAreaIsNotNormal() {
  const raw = { x: 0, y: 38, width: 1512, height: 851 };
  const plan = resolveRestorePlan(raw, [MACBOOK]);
  assert.equal(plan.isMaximized, true);
  assert.equal(plan.isFullScreen, false);
  assert.notEqual(plan.width, 1512);
  assert.notEqual(plan.height, 851);
  assertNormalHasGrabableEdges(plan, MACBOOK_WORK_AREA);
  const options = browserWindowOptionsForPlan(plan);
  assert.equal(options.width, plan.width);
  assert.equal(options.height, plan.height);
  assert.ok(options.width < MACBOOK_WORK_AREA.width);
  assert.ok(options.height < MACBOOK_WORK_AREA.height);
}

function checkMaximizedRestoreKeepsNormalSize() {
  const raw = {
    version: 2,
    x: 160,
    y: 70,
    width: 1280,
    height: 800,
    isMaximized: true,
    isFullScreen: false,
    displayId: 1,
    displayWorkArea: MACBOOK_WORK_AREA,
  };
  const plan = resolveRestorePlan(raw, [MACBOOK]);
  assert.equal(plan.isMaximized, true);
  assert.equal(plan.x, 160);
  assert.equal(plan.y, 70);
  assert.equal(plan.width, 1280);
  assert.equal(plan.height, 800);
  const options = browserWindowOptionsForPlan(plan);
  assert.deepEqual(
    { x: options.x, y: options.y, width: options.width, height: options.height },
    { x: 160, y: 70, width: 1280, height: 800 },
  );
}

function checkFullScreenRestore() {
  const raw = {
    version: 2,
    x: 200,
    y: 100,
    width: 1100,
    height: 720,
    isMaximized: false,
    isFullScreen: true,
    displayId: 1,
    displayWorkArea: MACBOOK_WORK_AREA,
  };
  const plan = resolveRestorePlan(raw, [MACBOOK]);
  assert.equal(plan.isFullScreen, true);
  assert.equal(plan.width, 1100);
  assert.equal(plan.height, 720);
}

function checkMissingDisplayFallsBackToCenteredDefault() {
  const raw = {
    version: 2,
    x: 1600,
    y: 80,
    width: 1800,
    height: 1200,
    isMaximized: true,
    isFullScreen: true,
    displayId: 2,
    displayWorkArea: EXTERNAL.workArea,
  };
  const plan = resolveRestorePlan(raw, [MACBOOK], { primary: MACBOOK });
  assert.equal(plan.isMaximized, false);
  assert.equal(plan.isFullScreen, false);
  const expected = defaultNormalBounds(MACBOOK_WORK_AREA);
  assert.deepEqual(
    { x: plan.x, y: plan.y, width: plan.width, height: plan.height },
    expected,
  );
  assertNormalHasGrabableEdges(plan, MACBOOK_WORK_AREA);
}

function checkDisplayWorkAreaMatchWhenIdChanges() {
  const raw = {
    version: 2,
    x: 180,
    y: 70,
    width: 1200,
    height: 760,
    isMaximized: false,
    isFullScreen: false,
    displayId: 99,
    displayWorkArea: MACBOOK_WORK_AREA,
  };
  const plan = resolveRestorePlan(raw, [{ ...MACBOOK, id: 7 }]);
  assert.equal(plan.x, 180);
  assert.equal(plan.width, 1200);
  assert.equal(plan.isMaximized, false);
}

function checkCorruptTinyAndNanBoundsFallBack() {
  const tiny = resolveRestorePlan({ x: 10, y: 10, width: 40, height: 20 }, [MACBOOK]);
  const expected = defaultNormalBounds(MACBOOK_WORK_AREA);
  assert.deepEqual(
    { x: tiny.x, y: tiny.y, width: tiny.width, height: tiny.height },
    expected,
  );
  assert.equal(tiny.isMaximized, false);

  const nan = resolveRestorePlan({ x: Number.NaN, y: "nope", width: Number.NaN, height: 900 }, [MACBOOK]);
  assert.deepEqual(
    { x: nan.x, y: nan.y, width: nan.width, height: nan.height },
    expected,
  );

  const empty = resolveRestorePlan(null, [MACBOOK]);
  assert.deepEqual(
    { x: empty.x, y: empty.y, width: empty.width, height: empty.height },
    expected,
  );
}

function checkOffscreenLegacyDropsPosition() {
  const raw = { x: -4000, y: 80, width: 1280, height: 800 };
  const plan = resolveRestorePlan(raw, [MACBOOK], { primary: MACBOOK });
  assert.deepEqual(
    { x: plan.x, y: plan.y, width: plan.width, height: plan.height },
    defaultNormalBounds(MACBOOK_WORK_AREA),
  );
}

function checkClampKeepsWindowOnWorkArea() {
  const raw = { x: 200, y: 60, width: 1600, height: 700 };
  const plan = resolveRestorePlan(raw, [MACBOOK]);
  assert.ok(plan.x >= MACBOOK_WORK_AREA.x);
  assert.ok(plan.y >= MACBOOK_WORK_AREA.y);
  assert.ok(plan.x + plan.width <= MACBOOK_WORK_AREA.x + MACBOOK_WORK_AREA.width);
  assert.ok(plan.y + plan.height <= MACBOOK_WORK_AREA.y + MACBOOK_WORK_AREA.height);
  assert.ok(plan.width >= MIN_WIDTH);
  assert.ok(plan.height >= MIN_HEIGHT);
  assert.equal(plan.width, MACBOOK_WORK_AREA.width);
  assert.equal(plan.height, 700);
  assert.equal(plan.isMaximized, false);
  assertNormalHasGrabableEdges(plan, MACBOOK_WORK_AREA);
}

function checkClampThatFillsWorkAreaBecomesMaximized() {
  const raw = { x: 1400, y: 800, width: 1600, height: 1000 };
  const plan = resolveRestorePlan(raw, [MACBOOK]);
  assert.equal(plan.isMaximized, true);
  assertNormalHasGrabableEdges(plan, MACBOOK_WORK_AREA);
}

function checkDefaultOnFillingWorkAreaInsets() {
  const plan = resolveRestorePlan(null, [SMALL_PRIMARY], { primary: SMALL_PRIMARY });
  assert.equal(plan.width, SMALL_PRIMARY.workArea.width - EDGE_INSET_PX * 2);
  assert.equal(plan.height, SMALL_PRIMARY.workArea.height - EDGE_INSET_PX * 2);
  assert.equal(plan.isMaximized, false);
  assertNormalHasGrabableEdges(plan, SMALL_PRIMARY.workArea);
}

function checkCaptureDoesNotOverwriteNormalWithMaximizedRect() {
  const previous = { x: 160, y: 90, width: 1280, height: 800 };
  const captured = captureWindowState({
    bounds: { ...MACBOOK_WORK_AREA },
    normalBounds: { ...MACBOOK_WORK_AREA },
    isMaximized: true,
    isFullScreen: false,
    display: MACBOOK,
  }, previous);
  assert.equal(captured.isMaximized, true);
  assert.equal(captured.x, 160);
  assert.equal(captured.width, 1280);
  assert.equal(captured.height, 800);
}

function checkCaptureTreatsFilledWorkAreaAsMaximized() {
  const previous = { x: 200, y: 100, width: 1100, height: 720 };
  const captured = captureWindowState({
    bounds: { ...MACBOOK_WORK_AREA },
    normalBounds: { ...MACBOOK_WORK_AREA },
    isMaximized: false,
    isFullScreen: false,
    display: MACBOOK,
  }, previous);
  assert.equal(captured.isMaximized, true);
  assert.equal(captured.isFullScreen, false);
  assert.equal(captured.width, 1100);
  assert.equal(captured.height, 720);
  assert.equal(boundsFillWorkArea(captured, MACBOOK_WORK_AREA), false);
}

function checkCaptureFilledWithoutHistoryUsesDefaultNormal() {
  const captured = captureWindowState({
    bounds: { ...MACBOOK_WORK_AREA },
    normalBounds: { ...MACBOOK_WORK_AREA },
    isMaximized: false,
    isFullScreen: false,
    display: MACBOOK,
  }, null);
  assert.equal(captured.isMaximized, true);
  assert.deepEqual(
    { x: captured.x, y: captured.y, width: captured.width, height: captured.height },
    defaultNormalBounds(MACBOOK_WORK_AREA),
  );
}

function checkDetachedOptionsStayModestAndUnpositioned() {
  const plan = resolveRestorePlan({
    x: 0,
    y: 38,
    width: 1512,
    height: 851,
    isMaximized: true,
    isFullScreen: true,
    displayId: 1,
    displayWorkArea: MACBOOK_WORK_AREA,
  }, [MACBOOK]);
  const options = browserWindowOptionsForPlan(plan, { detached: true });
  assert.equal(Object.hasOwn(options, "x"), false);
  assert.equal(Object.hasOwn(options, "y"), false);
  assert.ok(options.width <= DETACHED_MAX_WIDTH);
  assert.ok(options.height <= DETACHED_MAX_HEIGHT);
  assert.equal(options.minWidth, MIN_WIDTH);
  assert.equal(options.minHeight, MIN_HEIGHT);
}

function checkApplyChromeSkipsDetached() {
  const calls = [];
  const win = {
    maximize() { calls.push("maximize"); },
    setFullScreen() { calls.push("fullscreen"); },
    setBounds(rect) { calls.push(["bounds", rect]); },
    isDestroyed() { return false; },
  };
  applyRestoredChrome(win, { isMaximized: true, isFullScreen: true }, { detached: true });
  assert.deepEqual(calls, []);
  applyRestoredChrome(win, { isMaximized: true, isFullScreen: true });
  assert.deepEqual(calls, ["maximize", "fullscreen"]);
}

function checkApplyChromeSizesToWorkAreaBeforeMaximize() {
  const calls = [];
  const win = {
    maximize() { calls.push("maximize"); },
    setFullScreen() { calls.push("fullscreen"); },
    setBounds(rect) { calls.push(["bounds", rect]); },
    isDestroyed() { return false; },
  };
  applyRestoredChrome(win, {
    isMaximized: true,
    isFullScreen: false,
    displayWorkArea: MACBOOK_WORK_AREA,
  });
  assert.deepEqual(calls, [["bounds", MACBOOK_WORK_AREA], "maximize"]);
}

function checkLoadRoundTripAndMinSizeOnOptions() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-window-state-"));
  const filePath = path.join(dir, "window-state.json");
  try {
    fs.writeFileSync(filePath, JSON.stringify({ x: 0, y: 38, width: 1512, height: 851 }));
    const plan = loadPersistedState(filePath, [MACBOOK], { primary: MACBOOK });
    assert.equal(plan.isMaximized, true);
    assertNormalHasGrabableEdges(plan, MACBOOK_WORK_AREA);

    writePersistedState(filePath, {
      version: 2,
      x: 140,
      y: 70,
      width: 1200,
      height: 760,
      isMaximized: true,
      isFullScreen: false,
      displayId: 1,
      displayWorkArea: MACBOOK_WORK_AREA,
    });
    const reloaded = loadPersistedState(filePath, [MACBOOK]);
    assert.equal(reloaded.isMaximized, true);
    assert.equal(reloaded.width, 1200);
    assert.equal(reloaded.height, 760);

    const options = browserWindowOptionsForPlan(reloaded);
    assert.equal(options.minWidth, MIN_WIDTH);
    assert.equal(options.minHeight, MIN_HEIGHT);
    assert.equal(options.width, 1200);
    assert.ok(options.width !== DEFAULT_WIDTH || options.height !== DEFAULT_HEIGHT);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

function checkBrowserWindowNeverOpensAtWorkAreaSize() {
  const plan = resolveRestorePlan({ x: 0, y: 38, width: 1512, height: 851 }, [MACBOOK]);
  const options = browserWindowOptionsForPlan(plan);
  assert.equal(
    boundsFillWorkArea(
      { x: options.x, y: options.y, width: options.width, height: options.height },
      MACBOOK_WORK_AREA,
    ),
    false,
    "BrowserWindow must be created at a normal size, never flush with the work area",
  );
}

checkLegacyUsableBoundsMigrate();
checkLegacyFilledWorkAreaIsNotNormal();
checkMaximizedRestoreKeepsNormalSize();
checkFullScreenRestore();
checkMissingDisplayFallsBackToCenteredDefault();
checkDisplayWorkAreaMatchWhenIdChanges();
checkCorruptTinyAndNanBoundsFallBack();
checkOffscreenLegacyDropsPosition();
checkClampKeepsWindowOnWorkArea();
checkClampThatFillsWorkAreaBecomesMaximized();
checkDefaultOnFillingWorkAreaInsets();
checkCaptureDoesNotOverwriteNormalWithMaximizedRect();
checkCaptureTreatsFilledWorkAreaAsMaximized();
checkCaptureFilledWithoutHistoryUsesDefaultNormal();
checkDetachedOptionsStayModestAndUnpositioned();
checkApplyChromeSkipsDetached();
checkApplyChromeSizesToWorkAreaBeforeMaximize();
checkLoadRoundTripAndMinSizeOnOptions();
checkBrowserWindowNeverOpensAtWorkAreaSize();

console.log("window state checks passed");
