"use strict";

const fs = require("fs");
const path = require("path");

// Keep these literals aligned with apps/web/lib/prefs/theme-config.ts.
// check-theme-contract.mjs compares the two lists.
const THEME_IDS = [
  "beige-dark",
  "beige-light",
  "dark",
  "light",
  "aurora",
  "aurora-light",
  "custom",
  "custom-light",
];
const THEME_STYLES = ["beige", "neutral", "aurora"];
const LEGACY_THEME_STYLES = ["beige", "neutral", "aurora", "custom"];
const THEME_MODES = ["auto", "dark", "light"];
const THEME_STYLE_PAIRS = {
  beige: { dark: "beige-dark", light: "beige-light" },
  neutral: { dark: "dark", light: "light" },
  aurora: { dark: "aurora", light: "aurora-light" },
};
const LEGACY_THEME_PREFERENCES = {
  auto: { style: "beige", mode: "auto" },
  "beige-dark": { style: "beige", mode: "dark" },
  "beige-light": { style: "beige", mode: "light" },
  dark: { style: "neutral", mode: "dark" },
  light: { style: "neutral", mode: "light" },
  aurora: { style: "aurora", mode: "dark" },
  "aurora-light": { style: "aurora", mode: "light" },
  custom: { style: "beige", mode: "dark" },
  "custom-light": { style: "beige", mode: "light" },
};
const DEFAULT_THEME_STYLE = "beige";
const DEFAULT_THEME_MODE = "auto";
const PREFS_FILE_NAME = "theme-prefs.json";
const STORAGE_KEYS = {
  schema: "agentic_theme_schema",
  style: "agentic_theme_style",
  mode: "agentic_theme_mode",
  legacy: "agentic_theme",
  accent: "agentic_theme_accent",
};

// Built-in chrome tokens copied from --bg-primary / --text-primary /
// --text-muted / --bg-tertiary / --accent-orange / --border-light in
// apps/web/app/styles/themes/*.css. custom* reuse the beige pair.
const THEME_CHROME = {
  "beige-dark": {
    bg: "#262624",
    text: "#b8b5ad",
    muted: "#757370",
    surface: "#30302e",
    link: "#d97757",
    border: "rgba(255, 255, 255, 0.10)",
    colorScheme: "dark",
  },
  "beige-light": {
    bg: "#faf9f5",
    text: "#3d3d3a",
    muted: "#91908c",
    surface: "#e8e6dc",
    link: "#c15f3c",
    border: "#dedcd1",
    colorScheme: "light",
  },
  dark: {
    bg: "#1e1e20",
    text: "#b6b6bb",
    muted: "#74747c",
    surface: "#252529",
    link: "#6ea8fe",
    border: "rgba(255, 255, 255, 0.12)",
    colorScheme: "dark",
  },
  light: {
    bg: "#ffffff",
    text: "#3a3a40",
    muted: "#8c8c94",
    surface: "#ebebef",
    link: "#2563eb",
    border: "#dcdce2",
    colorScheme: "light",
  },
  aurora: {
    bg: "#171528",
    text: "#c4c0e0",
    muted: "#7b77a0",
    surface: "#211e37",
    link: "#4fd6c0",
    border: "rgba(180, 165, 255, 0.18)",
    colorScheme: "dark",
  },
  "aurora-light": {
    bg: "#fbfaff",
    text: "#393549",
    muted: "#8f899f",
    surface: "#e9e5f5",
    link: "#0f766e",
    border: "#d9d1eb",
    colorScheme: "light",
  },
};
const CUSTOM_FALLBACK_THEME = {
  custom: "beige-dark",
  "custom-light": "beige-light",
};

function isThemeId(value) {
  return typeof value === "string" && THEME_IDS.includes(value);
}

function coerceThemeStyle(value) {
  if (value === "custom") return DEFAULT_THEME_STYLE;
  return typeof value === "string" && THEME_STYLES.includes(value)
    ? value
    : DEFAULT_THEME_STYLE;
}

function coerceThemeMode(value) {
  return typeof value === "string" && THEME_MODES.includes(value)
    ? value
    : DEFAULT_THEME_MODE;
}

function resolveThemePreference(style, mode, systemDark) {
  const resolvedMode = mode === "auto" ? (systemDark ? "dark" : "light") : mode;
  const resolvedStyle = coerceThemeStyle(style);
  const pair = THEME_STYLE_PAIRS[resolvedStyle] || THEME_STYLE_PAIRS[DEFAULT_THEME_STYLE];
  return { theme: pair[resolvedMode], resolvedMode };
}

function legacyThemePreference(value, schemaVersion = "2") {
  if (schemaVersion !== "2" && (value === "dark" || value === "light")) {
    return { style: "beige", mode: value };
  }
  const preference = value && Object.prototype.hasOwnProperty.call(LEGACY_THEME_PREFERENCES, value)
    ? LEGACY_THEME_PREFERENCES[value]
    : LEGACY_THEME_PREFERENCES.auto;
  return { style: preference.style, mode: preference.mode };
}

function chromeForTheme(themeId, accentColor) {
  const resolved = CUSTOM_FALLBACK_THEME[themeId]
    || (THEME_CHROME[themeId] ? themeId : "beige-dark");
  const chrome = { ...THEME_CHROME[resolved] };
  const accent = normalizeHex(accentColor);
  if (accent) chrome.link = accent;
  return chrome;
}

function backgroundForTheme(themeId) {
  return chromeForTheme(themeId).bg;
}

function normalizeHex(value) {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  const match = trimmed.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (!match) return null;
  if (match[1].length === 3) {
    return `#${[...match[1]].map((char) => char + char).join("").toLowerCase()}`;
  }
  return `#${match[1].toLowerCase()}`;
}

function colorToHex(value) {
  const hex = normalizeHex(value);
  if (hex) return hex;
  if (typeof value !== "string") return null;
  const rgb = value.trim().match(/^rgba?\(\s*(\d+)\s*[, ]\s*(\d+)\s*[, ]\s*(\d+)/i);
  if (!rgb) return null;
  const to = (n) => Number(n).toString(16).padStart(2, "0");
  return `#${to(rgb[1])}${to(rgb[2])}${to(rgb[3])}`;
}

function readPrefsFile(filePath) {
  try {
    const raw = JSON.parse(fs.readFileSync(filePath, "utf8"));
    if (!raw || typeof raw !== "object") return {};
    return {
      schema: typeof raw[STORAGE_KEYS.schema] === "string" ? raw[STORAGE_KEYS.schema] : null,
      style: typeof raw[STORAGE_KEYS.style] === "string" ? raw[STORAGE_KEYS.style] : null,
      mode: typeof raw[STORAGE_KEYS.mode] === "string" ? raw[STORAGE_KEYS.mode] : null,
      legacy: typeof raw[STORAGE_KEYS.legacy] === "string" ? raw[STORAGE_KEYS.legacy] : null,
      accent: typeof raw[STORAGE_KEYS.accent] === "string" ? raw[STORAGE_KEYS.accent] : null,
    };
  } catch {
    return {};
  }
}

function writePrefsFile(filePath, { style, mode, theme, schema = "3", accent = null }) {
  const dir = path.dirname(filePath);
  fs.mkdirSync(dir, { recursive: true });
  const tmp = `${filePath}.tmp`;
  const payload = {
    [STORAGE_KEYS.schema]: schema,
    [STORAGE_KEYS.style]: style,
    [STORAGE_KEYS.mode]: mode,
    [STORAGE_KEYS.legacy]: theme,
  };
  const accentHex = normalizeHex(accent);
  if (accentHex) payload[STORAGE_KEYS.accent] = accentHex;
  fs.writeFileSync(tmp, JSON.stringify(payload));
  fs.renameSync(tmp, filePath);
}

function allowedMatch(text, allowed) {
  let found = null;
  for (const value of allowed) {
    const escaped = value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const re = new RegExp(`(?:^|[^a-z0-9-])${escaped}(?:[^a-z0-9-]|$)`);
    if (re.test(text)) found = value;
  }
  return found;
}

function extractPrefFromBuffer(buffer, key, allowed) {
  const encodings = [
    { keyBuf: Buffer.from(key, "utf8"), decode: (slice) => slice.toString("utf8") },
    { keyBuf: Buffer.from(key, "utf16le"), decode: (slice) => slice.toString("utf16le") },
  ];
  let last = null;
  for (const { keyBuf, decode } of encodings) {
    let idx = 0;
    while ((idx = buffer.indexOf(keyBuf, idx)) !== -1) {
      const after = buffer.subarray(idx + keyBuf.length, idx + keyBuf.length + 96);
      if (key === STORAGE_KEYS.legacy) {
        const nextUtf8 = after.length && after[0] === 0x5f; // '_'
        const nextUtf16 = after.length >= 2 && after[0] === 0x5f && after[1] === 0x00;
        if (nextUtf8 || nextUtf16) {
          idx += keyBuf.length;
          continue;
        }
      }
      const found = allowedMatch(decode(after), allowed);
      if (found) last = found;
      idx += keyBuf.length;
    }
  }
  return last;
}

function readChromiumLocalStorage(userDataPath) {
  const dir = path.join(userDataPath, "Local Storage", "leveldb");
  if (!fs.existsSync(dir)) return {};
  let names;
  try {
    names = fs.readdirSync(dir).filter((name) => /\.(?:log|ldb)$/.test(name));
  } catch {
    return {};
  }
  names.sort((a, b) => {
    const extA = path.extname(a);
    const extB = path.extname(b);
    if (extA !== extB) return extA === ".log" ? 1 : -1;
    return a.localeCompare(b);
  });
  const prefs = {};
  const searches = [
    [STORAGE_KEYS.style, LEGACY_THEME_STYLES, "style"],
    [STORAGE_KEYS.mode, THEME_MODES, "mode"],
    [STORAGE_KEYS.legacy, [...THEME_IDS, "auto"], "legacy"],
    [STORAGE_KEYS.schema, ["1", "2", "3"], "schema"],
  ];
  for (const name of names) {
    let buffer;
    try {
      buffer = fs.readFileSync(path.join(dir, name));
    } catch {
      continue;
    }
    for (const [key, allowed, field] of searches) {
      const value = extractPrefFromBuffer(buffer, key, allowed);
      if (value != null) prefs[field] = value;
    }
    const accent = extractHexFromBuffer(buffer, STORAGE_KEYS.accent);
    if (accent) prefs.accent = accent;
  }
  return prefs;
}

function extractHexFromBuffer(buffer, key) {
  const encodings = [
    { keyBuf: Buffer.from(key, "utf8"), decode: (slice) => slice.toString("utf8") },
    { keyBuf: Buffer.from(key, "utf16le"), decode: (slice) => slice.toString("utf16le") },
  ];
  let last = null;
  for (const { keyBuf, decode } of encodings) {
    let idx = 0;
    while ((idx = buffer.indexOf(keyBuf, idx)) !== -1) {
      const after = decode(buffer.subarray(idx + keyBuf.length, idx + keyBuf.length + 48));
      const match = after.match(/#([0-9a-f]{3}|[0-9a-f]{6})(?![0-9a-f])/i);
      const found = match ? normalizeHex(match[0]) : null;
      if (found) last = found;
      idx += keyBuf.length;
    }
  }
  return last;
}

function mergePrefSources(chromiumPrefs, filePrefs) {
  return {
    schema: chromiumPrefs.schema || filePrefs.schema || null,
    style: chromiumPrefs.style || filePrefs.style || null,
    mode: chromiumPrefs.mode || filePrefs.mode || null,
    legacy: chromiumPrefs.legacy || filePrefs.legacy || null,
    accent: chromiumPrefs.accent || filePrefs.accent || null,
  };
}

function resolveFromPrefBag(prefs, systemDark) {
  let style = prefs.style;
  let mode = prefs.mode;
  const schema = prefs.schema || "1";
  if (!THEME_STYLES.includes(style) || !THEME_MODES.includes(mode)) {
    const migrated = legacyThemePreference(prefs.legacy, schema);
    if (!THEME_STYLES.includes(style)) style = migrated.style;
    if (!THEME_MODES.includes(mode)) mode = migrated.mode;
  }
  style = coerceThemeStyle(style);
  mode = coerceThemeMode(mode);
  const resolved = resolveThemePreference(style, mode, Boolean(systemDark));
  const accentColor = normalizeHex(prefs.accent);
  const chrome = chromeForTheme(resolved.theme, accentColor);
  return {
    theme: resolved.theme,
    resolvedMode: resolved.resolvedMode,
    style,
    mode,
    accentColor,
    backgroundColor: chrome.bg,
    chrome,
  };
}

function loadResolvedChrome({
  userDataPath,
  systemDark,
  readChromium = readChromiumLocalStorage,
} = {}) {
  const filePrefs = userDataPath
    ? readPrefsFile(path.join(userDataPath, PREFS_FILE_NAME))
    : {};
  const chromiumPrefs = userDataPath ? readChromium(userDataPath) : {};
  return resolveFromPrefBag(mergePrefSources(chromiumPrefs, filePrefs), systemDark);
}

function buildErrorPageHtml(chrome, workerCommand) {
  const c = chrome || chromeForTheme("beige-dark");
  return `<body style="background:${c.bg};color:${c.text};font-family:-apple-system,sans-serif;
        display:flex;align-items:center;justify-content:center;height:100vh;margin:0;color-scheme:${c.colorScheme}">
      <div style="max-width:32rem;text-align:center">
        <h2>Waiting for backend to start...</h2>
        <p>OpenProgram will reconnect automatically when the backend is ready.</p>
        <p>If it does not start, run:</p>
        <pre style="background:${c.surface};padding:0.8em;border-radius:6px">${workerCommand}</pre>
      </div>
    </body>`;
}

function buildErrorPageUrl(chrome, workerCommand) {
  return "data:text/html;charset=utf-8," + encodeURIComponent(
    buildErrorPageHtml(chrome, workerCommand),
  );
}

function isErrorPageUrl(url) {
  if (typeof url !== "string" || !url.startsWith("data:text/html")) return false;
  try {
    return decodeURIComponent(url).includes("Waiting for backend to start...");
  } catch {
    return url.includes("Waiting%20for%20backend");
  }
}

function directoryListingCss(chrome) {
  const c = chrome || chromeForTheme("beige-dark");
  return `
  body { font-family: -apple-system, system-ui, sans-serif; margin: 24px; color: ${c.text}; background: ${c.bg}; color-scheme: ${c.colorScheme}; }
  h1 { font-size: 15px; font-weight: 600; word-break: break-all; }
  ul { list-style: none; padding: 0; max-width: 720px; }
  li { display: flex; justify-content: space-between; gap: 16px; line-height: 1.9; border-bottom: 1px solid ${c.border}; }
  a { color: ${c.link}; text-decoration: none; word-break: break-all; }
  a:hover { text-decoration: underline; }
  .size { color: ${c.muted}; font-size: 12px; white-space: nowrap; }
`;
}

module.exports = {
  THEME_IDS,
  THEME_STYLES,
  LEGACY_THEME_STYLES,
  THEME_MODES,
  THEME_STYLE_PAIRS,
  LEGACY_THEME_PREFERENCES,
  DEFAULT_THEME_STYLE,
  DEFAULT_THEME_MODE,
  PREFS_FILE_NAME,
  STORAGE_KEYS,
  THEME_CHROME,
  CUSTOM_FALLBACK_THEME,
  isThemeId,
  coerceThemeStyle,
  coerceThemeMode,
  resolveThemePreference,
  legacyThemePreference,
  chromeForTheme,
  backgroundForTheme,
  normalizeHex,
  colorToHex,
  readPrefsFile,
  writePrefsFile,
  extractPrefFromBuffer,
  extractHexFromBuffer,
  readChromiumLocalStorage,
  resolveFromPrefBag,
  loadResolvedChrome,
  buildErrorPageHtml,
  buildErrorPageUrl,
  isErrorPageUrl,
  directoryListingCss,
};
