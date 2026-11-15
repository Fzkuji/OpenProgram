const fs = require("fs");
const path = require("path");

// Web tabs ask before a site uses a device or private data, as Chrome does.
// Electron grants every permission unless a handler says otherwise, so a page
// in a web tab got the camera, microphone and location without a prompt; that
// is unsafe, and no ordinary browser behaves that way.
//
// Each answer is kept per origin and permission in userData, so a site asks
// once. Clearing web tab cookies forgets them too.
const FILE_NAME = "webtab-permissions.json";

// Chrome grants these without asking.
const ALWAYS_ALLOWED = new Set([
  "clipboard-sanitized-write",
  "fullscreen",
  "pointerLock",
  "keyboardLock",
  // External protocols (mailto:, zoommtg:) keep their existing handling.
  "openExternal",
]);

// These ask the user. Anything else is denied.
const ASKED = {
  media: "use your camera or microphone",
  geolocation: "know your location",
  notifications: "show notifications",
  midiSysex: "control your MIDI devices",
  "clipboard-read": "see text and images copied to the clipboard",
  "idle-detection": "know when you are actively using this device",
  "window-management": "manage windows on all your displays",
  "storage-access": "use cookies and site data in this context",
  "top-level-storage-access": "use cookies and site data in this context",
};

function mediaPurpose(details) {
  const types = new Set(details?.mediaTypes || []);
  if (types.has("video") && types.has("audio")) return "use your camera and microphone";
  if (types.has("video")) return "use your camera";
  if (types.has("audio")) return "use your microphone";
  return ASKED.media;
}

// Media answers are kept per device kind: allowing the microphone does not
// allow the camera.
function decisionKeys(permission, details) {
  if (permission !== "media") return [permission];
  const types = (details?.mediaTypes || []).filter((type) => type === "audio" || type === "video");
  return types.length ? types.map((type) => `media:${type}`) : ["media"];
}

function originOf(url) {
  try {
    const origin = new URL(url).origin;
    return origin && origin !== "null" ? origin : null;
  } catch (_error) {
    return null;
  }
}

function createDecisionStore(filePath) {
  let decisions = null;
  const load = () => {
    if (decisions) return decisions;
    try {
      const value = JSON.parse(fs.readFileSync(filePath, "utf8"));
      decisions = value && typeof value === "object" && !Array.isArray(value) ? value : {};
    } catch (_error) {
      decisions = {};
    }
    return decisions;
  };
  const save = () => {
    try {
      fs.mkdirSync(path.dirname(filePath), { recursive: true });
      const staged = `${filePath}.incoming`;
      fs.writeFileSync(staged, JSON.stringify(decisions, null, 2));
      fs.renameSync(staged, filePath);
    } catch (_error) {
      // The answer still holds for this run.
    }
  };
  return {
    get(origin, key) {
      const value = load()[origin]?.[key];
      return value === "allow" || value === "block" ? value : null;
    },
    set(origin, key, value) {
      const all = load();
      all[origin] = { ...(all[origin] || {}), [key]: value };
      save();
    },
    clear() {
      decisions = {};
      save();
    },
  };
}

/**
 * Install the request and check handlers on the web tab session.
 * `ask({ origin, purpose, webContents })` resolves true to allow, false to
 * block, or null when the question could not be put; the shell shows it as a
 * dialog on the tab's window.
 */
function registerWebTabPermissions(targetSession, { filePath, ask }) {
  const store = createDecisionStore(filePath);
  const pending = new Map();

  targetSession.setPermissionRequestHandler((webContents, permission, callback, details) => {
    if (ALWAYS_ALLOWED.has(permission)) return callback(true);
    if (!(permission in ASKED)) return callback(false);
    const origin = originOf(details?.requestingUrl || webContents?.getURL?.());
    if (!origin) return callback(false);
    const keys = decisionKeys(permission, details);
    const known = keys.map((key) => store.get(origin, key));
    if (known.every((value) => value === "allow")) return callback(true);
    if (known.some((value) => value === "block")) return callback(false);
    // One question per origin and request at a time; repeats wait for it.
    const id = `${origin} ${keys.join(",")}`;
    let answer = pending.get(id);
    if (!answer) {
      const purpose = permission === "media" ? mediaPurpose(details) : ASKED[permission];
      answer = Promise.resolve()
        .then(() => ask({ origin, purpose, webContents }))
        .then((allowed) => {
          // null: nobody was there to ask. Refused this time, asked again later.
          if (allowed === null) return false;
          for (const key of keys) store.set(origin, key, allowed ? "allow" : "block");
          return allowed === true;
        }, () => false)
        .finally(() => pending.delete(id));
      pending.set(id, answer);
    }
    answer.then(callback);
  });

  targetSession.setPermissionCheckHandler((_webContents, permission, requestingOrigin, details) => {
    if (ALWAYS_ALLOWED.has(permission)) return true;
    if (!(permission in ASKED)) return false;
    const origin = originOf(requestingOrigin);
    if (!origin) return false;
    const keys = permission === "media" && details?.mediaType
      ? [`media:${details.mediaType}`]
      : decisionKeys(permission, details);
    return keys.every((key) => store.get(origin, key) === "allow");
  });

  return { clear: () => store.clear() };
}

module.exports = { FILE_NAME, ALWAYS_ALLOWED, ASKED, registerWebTabPermissions };
