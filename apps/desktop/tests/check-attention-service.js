const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");

const { DesktopAttentionService, DEDUPE_MS } = require("../main/windows/attention-service");

function fakeBlocker() {
  let next = 1;
  const started = new Set();
  return {
    started,
    start(kind) {
      assert.equal(kind, "prevent-app-suspension");
      const id = next++;
      started.add(id);
      return id;
    },
    stop(id) { started.delete(id); },
    isStarted(id) { return started.has(id); },
  };
}

function harness(overrides = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "op-attention-"));
  const shown = [];
  const opened = [];
  const emitted = [];
  let focused = false;
  let clock = 1_000;
  const blocker = fakeBlocker();
  const service = new DesktopAttentionService({
    statePath: path.join(dir, "attention.json"),
    createNotification: (options) => {
      const handlers = {};
      const notification = {
        options,
        on(name, fn) { handlers[name] = fn; },
        show() { shown.push(notification); },
        click() { handlers.click?.(); },
      };
      return notification;
    },
    powerSaveBlocker: blocker,
    isAppFocused: () => focused,
    openSession: (sender, sessionId) => opened.push([sender, sessionId]),
    emit: (prefs) => emitted.push(prefs),
    now: () => clock,
    ...overrides,
  });
  return {
    dir, service, shown, opened, emitted, blocker,
    setFocused(value) { focused = value; },
    advance(ms) { clock += ms; },
  };
}

function checkDefaultsAndPersistence() {
  const h = harness();
  assert.deepEqual(h.service.getPrefs(), { notifications: true, keepAwake: true, sound: "default" });
  h.service.setPrefs({ notifications: false });
  assert.deepEqual(h.emitted.at(-1), { notifications: false, keepAwake: true, sound: "default" });
  const reloaded = new DesktopAttentionService({
    statePath: path.join(h.dir, "attention.json"),
    createNotification: () => { throw new Error("unused"); },
    powerSaveBlocker: fakeBlocker(),
    isAppFocused: () => false,
    openSession: () => {},
  });
  assert.equal(reloaded.getPrefs().notifications, false, "prefs survive a restart");
  fs.writeFileSync(path.join(h.dir, "attention.json"), "{not json");
  const repaired = new DesktopAttentionService({
    statePath: path.join(h.dir, "attention.json"),
    createNotification: () => { throw new Error("unused"); },
    powerSaveBlocker: fakeBlocker(),
    isAppFocused: () => false,
    openSession: () => {},
  });
  assert.deepEqual(repaired.getPrefs(), { notifications: true, keepAwake: true, sound: "default" }, "a broken file falls back to defaults");
}

function checkOneNotificationPerKey() {
  const h = harness();
  const request = { key: "done:s1:e1", title: "Fix tests", body: "Finished", sessionId: "s1" };
  assert.equal(h.service.notify(1, request), true);
  assert.equal(h.service.notify(2, request), false, "a second window's copy of the same event is dropped");
  assert.equal(h.shown.length, 1);
  h.shown[0].click();
  assert.deepEqual(h.opened, [[1, "s1"]], "clicking opens the session in the window that reported it");
  h.advance(DEDUPE_MS + 1);
  assert.equal(h.service.notify(1, request), true, "the key is forgotten after the dedupe window");
}

function checkFocusedAppStaysQuiet() {
  const h = harness();
  h.setFocused(true);
  assert.equal(h.service.notify(1, { key: "k", title: "T", sessionId: "s" }), false);
  h.setFocused(false);
  assert.equal(h.service.notify(2, { key: "k", title: "T", sessionId: "s" }), false,
    "an event the user saw while focused is not replayed by a slower window");
  assert.equal(h.shown.length, 0);
}

function checkDisabledAndMalformed() {
  const h = harness();
  assert.equal(h.service.notify(1, { title: "no key" }), false);
  assert.equal(h.service.notify(1, null), false);
  h.service.setPrefs({ notifications: false });
  assert.equal(h.service.notify(1, { key: "k", title: "T" }), false);
  assert.equal(h.shown.length, 0);
  const long = harness();
  long.service.notify(1, { key: "x", title: "t".repeat(500), body: "b\n\n".repeat(500) });
  assert.ok(long.shown[0].options.title.length <= 120);
  assert.ok(long.shown[0].options.body.length <= 240);
  assert.ok(!long.shown[0].options.body.includes("\n"));
}

function checkSounds() {
  const played = [];
  const h = harness({ listSystemSounds: () => ["Ping", "Glass", "bad/name"], playSound: (name) => played.push(name) });
  assert.deepEqual(h.service.getSounds(), ["Glass", "Ping"], "only safe names are offered");
  h.service.notify(1, { key: "a", title: "Done" });
  assert.equal(h.shown.at(-1).options.sound, undefined, "default keeps the system sound");
  h.service.setPrefs({ sound: "system:Glass" });
  h.service.notify(1, { key: "b", title: "Done" });
  assert.equal(h.shown.at(-1).options.sound, "Glass");
  h.service.setPrefs({ sound: "none" });
  h.service.notify(1, { key: "c", title: "Done" });
  assert.equal(h.shown.at(-1).options.silent, true);
  h.service.setPrefs({ sound: "system:../../etc" });
  assert.equal(h.service.getPrefs().sound, "default", "an unknown value falls back to default");
  assert.equal(h.service.previewSound("system:Ping"), true);
  assert.equal(h.service.previewSound("system:Missing"), false);
  assert.deepEqual(played, ["Ping"]);
}

function checkKeepAwake() {
  const h = harness();
  h.service.reportBusy(1, true);
  h.service.reportBusy(2, true);
  assert.equal(h.blocker.started.size, 1, "one blocker however many windows are busy");
  h.service.reportBusy(1, false);
  assert.equal(h.service.isHoldingAwake(), true, "another window is still busy");
  h.service.forgetSender(2);
  assert.equal(h.service.isHoldingAwake(), false);
  assert.equal(h.blocker.started.size, 0);
  h.service.reportBusy(3, true);
  h.service.setPrefs({ keepAwake: false });
  assert.equal(h.blocker.started.size, 0, "turning the setting off releases the hold at once");
  h.service.setPrefs({ keepAwake: true });
  assert.equal(h.blocker.started.size, 1, "turning it back on while busy holds again");
}

function checkUrgentShowsWhileFocused() {
  const h = harness();
  h.setFocused(true);
  assert.equal(h.service.notify(1, { key: "turn", title: "Chat", body: "Finished", sessionId: "s1" }), false);
  assert.equal(h.service.notify(1, { key: "reminder:1", title: "Reminder", body: "Stretch", sessionId: "", urgent: true }), true,
    "a reminder the user set shows even while the App has focus");
  assert.equal(h.shown.length, 1);
}

checkDefaultsAndPersistence();
checkOneNotificationPerKey();
checkFocusedAppStaysQuiet();
checkDisabledAndMalformed();
checkKeepAwake();
checkSounds();
checkUrgentShowsWhileFocused();
console.log("attention service checks passed");
