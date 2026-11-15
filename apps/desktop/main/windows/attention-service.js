// Desktop notifications and keep-awake while an agent works.
//
// Every app window runs its own renderer and hears the same worker events,
// so the same "turn finished" or "needs your input" arrives once per window.
// This service is the one place that decides: it shows each event key once,
// only while no OpenProgram window has focus, and holds the system awake
// while any window reports a running session. An urgent request (a
// reminder the user set) shows even while the App has focus. Electron objects are injected
// so tests/check-attention-service.js can drive it in plain Node.
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const DEDUPE_MS = 10 * 60 * 1000;
const MAX_TITLE = 120;
const MAX_BODY = 240;

// "default" leaves the system's notification sound, "none" mutes it, and
// "system:<name>" picks one of the sounds macOS ships (/System/Library/Sounds).
const SOUND_PATTERN = /^(default|none|system:[A-Za-z0-9 _-]{1,64})$/;

function defaultPrefs() {
  return { schema: 1, notifications: true, keepAwake: true, sound: "default" };
}

function normalizePrefs(value) {
  const defaults = defaultPrefs();
  if (!value || typeof value !== "object" || value.schema !== 1) return defaults;
  return {
    schema: 1,
    notifications: typeof value.notifications === "boolean" ? value.notifications : defaults.notifications,
    keepAwake: typeof value.keepAwake === "boolean" ? value.keepAwake : defaults.keepAwake,
    sound: typeof value.sound === "string" && SOUND_PATTERN.test(value.sound) ? value.sound : defaults.sound,
  };
}

function readPrefs(filePath) {
  try {
    return normalizePrefs(JSON.parse(fs.readFileSync(filePath, "utf8")));
  } catch (_error) {
    return defaultPrefs();
  }
}

function writePrefs(filePath, prefs) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const temporary = `${filePath}.tmp-${process.pid}-${crypto.randomBytes(4).toString("hex")}`;
  const options = { encoding: "utf8" };
  if (process.platform !== "win32") options.mode = 0o600;
  try {
    fs.writeFileSync(temporary, `${JSON.stringify(normalizePrefs(prefs), null, 2)}\n`, options);
    fs.renameSync(temporary, filePath);
  } catch (error) {
    try { fs.rmSync(temporary, { force: true }); } catch (_cleanup) { /* best effort */ }
    throw error;
  }
}

function clip(value, limit) {
  const text = typeof value === "string" ? value.replace(/\s+/g, " ").trim() : "";
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

/** A renderer's request, reduced to the fields a notification may carry. */
function normalizeRequest(request) {
  if (!request || typeof request !== "object") return null;
  const key = clip(request.key, 200);
  const title = clip(request.title, MAX_TITLE);
  if (!key || !title) return null;
  return {
    key,
    title,
    body: clip(request.body, MAX_BODY),
    sessionId: clip(request.sessionId, 200),
    urgent: request.urgent === true,
  };
}

class DesktopAttentionService {
  constructor({
    statePath,
    createNotification,
    notificationsSupported = () => true,
    powerSaveBlocker,
    listSystemSounds = () => [],
    playSound = () => {},
    isAppFocused,
    openSession,
    emit = () => {},
    now = () => Date.now(),
  }) {
    this.statePath = statePath;
    this.createNotification = createNotification;
    this.notificationsSupported = notificationsSupported;
    this.powerSaveBlocker = powerSaveBlocker;
    this.listSystemSounds = listSystemSounds;
    this.playSound = playSound;
    this.isAppFocused = isAppFocused;
    this.openSession = openSession;
    this.emit = emit;
    this.now = now;
    this.prefs = readPrefs(statePath);
    this.seen = new Map();
    this.busySenders = new Set();
    this.blockerId = null;
    // Electron drops a Notification's click handler once the object is
    // garbage collected; hold each one until it is clicked or closed.
    this.live = new Set();
  }

  getPrefs() {
    return { notifications: this.prefs.notifications, keepAwake: this.prefs.keepAwake, sound: this.prefs.sound };
  }

  /** The sounds the picker offers besides Default and None. */
  getSounds() {
    try {
      const names = this.listSystemSounds();
      return Array.isArray(names) ? names.filter((name) => SOUND_PATTERN.test(`system:${name}`)).sort() : [];
    } catch (_error) {
      return [];
    }
  }

  /** Play a sound the user is choosing, so the picker can be heard. */
  previewSound(sound) {
    if (typeof sound !== "string" || !sound.startsWith("system:")) return false;
    const name = sound.slice("system:".length);
    if (!this.getSounds().includes(name)) return false;
    this.playSound(name);
    return true;
  }

  soundOptions() {
    if (this.prefs.sound === "none") return { silent: true };
    if (this.prefs.sound.startsWith("system:")) return { sound: this.prefs.sound.slice("system:".length) };
    return {};
  }

  setPrefs(patch) {
    const next = normalizePrefs({ ...this.prefs, ...(patch && typeof patch === "object" ? patch : {}) });
    writePrefs(this.statePath, next);
    this.prefs = next;
    this.syncPower();
    const prefs = this.getPrefs();
    this.emit(prefs);
    return prefs;
  }

  /** Show one notification for `request` unless it is a repeat or the user is already here. */
  notify(senderId, request) {
    const normalized = normalizeRequest(request);
    if (!normalized || !this.prefs.notifications) return false;
    const now = this.now();
    for (const [key, at] of this.seen) if (now - at > DEDUPE_MS) this.seen.delete(key);
    if (this.seen.has(normalized.key)) return false;
    this.seen.set(normalized.key, now);
    if ((!normalized.urgent && this.isAppFocused()) || !this.notificationsSupported()) return false;
    const notification = this.createNotification({
      title: normalized.title,
      body: normalized.body,
      ...this.soundOptions(),
    });
    const release = () => this.live.delete(notification);
    this.live.add(notification);
    notification.on("click", () => {
      release();
      this.openSession(senderId, normalized.sessionId);
    });
    notification.on("close", release);
    notification.show();
    return true;
  }

  reportBusy(senderId, busy) {
    if (busy) this.busySenders.add(senderId);
    else this.busySenders.delete(senderId);
    this.syncPower();
  }

  forgetSender(senderId) {
    this.reportBusy(senderId, false);
  }

  isHoldingAwake() {
    return this.blockerId !== null;
  }

  syncPower() {
    const want = this.prefs.keepAwake && this.busySenders.size > 0;
    if (want && this.blockerId === null) {
      // Keeps the system from idle-sleeping; the display may still turn off.
      this.blockerId = this.powerSaveBlocker.start("prevent-app-suspension");
    } else if (!want && this.blockerId !== null) {
      if (this.powerSaveBlocker.isStarted(this.blockerId)) this.powerSaveBlocker.stop(this.blockerId);
      this.blockerId = null;
    }
  }
}

module.exports = {
  DesktopAttentionService,
  normalizePrefs,
  normalizeRequest,
  DEDUPE_MS,
};
