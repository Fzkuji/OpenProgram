"use strict";

const fs = require("fs");
const path = require("path");

// Keep these literals aligned with apps/web/lib/prefs/appearance/theme-config.ts.
// check-theme-contract.mjs compares the two lists.
const THEME_IDS = [
  "beige-dark",
  "beige-light",
  "dark",
  "light",
  "aurora",
  "aurora-light",
  "vivid",
  "vivid-light",
  "custom",
  "custom-light",
];
const THEME_STYLES = ["beige", "neutral", "aurora", "vivid"];
const LEGACY_THEME_STYLES = ["beige", "neutral", "aurora", "vivid", "custom"];
const THEME_MODES = ["auto", "dark", "light"];
const THEME_STYLE_PAIRS = {
  beige: { dark: "beige-dark", light: "beige-light" },
  neutral: { dark: "dark", light: "light" },
  aurora: { dark: "aurora", light: "aurora-light" },
  vivid: { dark: "vivid", light: "vivid-light" },
};
const LEGACY_THEME_PREFERENCES = {
  auto: { style: "beige", mode: "auto" },
  "beige-dark": { style: "beige", mode: "dark" },
  "beige-light": { style: "beige", mode: "light" },
  dark: { style: "neutral", mode: "dark" },
  light: { style: "neutral", mode: "light" },
  aurora: { style: "aurora", mode: "dark" },
  "aurora-light": { style: "aurora", mode: "light" },
  vivid: { style: "vivid", mode: "dark" },
  "vivid-light": { style: "vivid", mode: "light" },
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
    bg: "#191918",
    text: "#b8b5ad",
    muted: "#757370",
    surface: "#252523",
    link: "#d97757",
    border: "rgba(255, 255, 255, 0.10)",
    colorScheme: "dark",
  },
  "beige-light": {
    bg: "#fbfaf8",
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
  // Vivid derives from the tab-strip colour; values for the default one.
  vivid: {
    bg: "#18191d",
    text: "#b9babd",
    muted: "#76777a",
    surface: "#24262b",
    link: "#a7b8da",
    border: "rgba(255, 255, 255, 0.10)",
    colorScheme: "dark",
  },
  "vivid-light": {
    bg: "#fbfcfe",
    text: "#343538",
    muted: "#858689",
    surface: "#e6eaf1",
    link: "#526386",
    border: "#d6dbe4",
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
  // The renderer writes this snapshot from its current localStorage values.
  // Raw LevelDB scans cannot distinguish live values from neighboring/history
  // records and must only serve as migration when no valid snapshot exists.
  if (LEGACY_THEME_STYLES.includes(filePrefs.style) && THEME_MODES.includes(filePrefs.mode)) {
    return resolveFromPrefBag(filePrefs, systemDark);
  }
  const chromiumPrefs = userDataPath ? readChromium(userDataPath) : {};
  return resolveFromPrefBag(mergePrefSources(chromiumPrefs, filePrefs), systemDark);
}

const TRACE_SOURCES = new Set([
  "bootstrap", "apply", "settings-style", "settings-mode", "settings-accent", "settings-css",
  "settings-mount", "message-send", "system", "storage", "dom-change", "startup", "native-system", "chrome",
]);

/** Local, bounded metadata only. Diagnostic failure never interrupts the UI. */
function recordThemeEvent(userDataPath, payload = {}) {
  try {
    const file = path.join(userDataPath, "theme-events.json");
    let records = [];
    try {
      if (fs.statSync(file).size <= 65536) {
        const saved = JSON.parse(fs.readFileSync(file, "utf8"));
        if (Array.isArray(saved)) records = saved.slice(-63);
      }
    } catch { /* First run or unreadable log. */ }
    records.push({
      time: new Date().toISOString(),
      source: TRACE_SOURCES.has(payload?.source) ? payload.source : "unknown",
      theme: isThemeId(payload?.theme) ? payload.theme : null,
      previousTheme: isThemeId(payload?.previousTheme) ? payload.previousTheme : null,
      style: THEME_STYLES.includes(payload?.style) ? payload.style : null,
      mode: THEME_MODES.includes(payload?.mode) ? payload.mode : null,
      storedMode: THEME_MODES.includes(payload?.storedMode) ? payload.storedMode : null,
      systemDark: typeof payload?.systemDark === "boolean" ? payload.systemDark : null,
    });
    fs.writeFileSync(file, JSON.stringify(records));
  } catch { /* Best-effort diagnostics. */ }
}

/** The reasons the App shows its waiting page instead of the UI. */
const WAIT_PHASES = ["waiting", "updating", "port"];

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (char) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]
  ));
}

// The `port` page in English and Chinese; {port}, {holder} and {setting}
// are filled in. `setting` names where the user set the port.
const PORT_TEXT = {
  en: {
    title: "Port {port} is in use",
    held: "OpenProgram is set to use port {port}, but {holder} is using it.",
    wait: "Quit that program and OpenProgram connects by itself.",
    settings: "Or choose another port for OpenProgram:",
    env: "Or start OpenProgram without OPENPROGRAM_WEB_PORT, or with a free port in it.",
    instance: "Or stop the program on that port; this instance's port is fixed in its instance.json.",
  },
  zh: {
    title: "端口 {port} 已被占用",
    held: "OpenProgram 设定使用端口 {port}，但{holder}正在使用它。",
    wait: "退出那个程序后，OpenProgram 会自动连上。",
    settings: "也可以给 OpenProgram 换一个端口：",
    env: "也可以去掉 OPENPROGRAM_WEB_PORT，或在其中填一个空闲端口后再启动 OpenProgram。",
    instance: "也可以停掉占用该端口的程序；这个实例的端口写死在它的 instance.json 里。",
  },
};

function portPageBody(c, detail = {}) {
  const text = PORT_TEXT[detail.chinese ? "zh" : "en"];
  const fill = (line) => escapeHtml(line)
    .replaceAll("{port}", escapeHtml(detail.port ?? ""))
    .replaceAll("{holder}", `<b>${escapeHtml(detail.holder || (detail.chinese ? "另一个程序或账户" : "another program or account"))}</b>`);
  const how = detail.setting === "env"
    ? `<p>${fill(text.env)}</p>`
    : detail.setting === "instance"
      ? `<p>${fill(text.instance)}</p>`
      : `<p>${fill(text.settings)}</p>
        <pre style="background:${c.surface};padding:0.8em;border-radius:6px">openprogram config set ui.web_port &lt;port&gt;</pre>`;
  return `<h2>${fill(text.title)}</h2>
        <p>${fill(text.held)}</p>
        <p>${fill(text.wait)}</p>
        ${how}`;
}

/**
 * The page shown while the backend is unreachable. `waiting` is the plain
 * case (the worker is starting or has to be started by hand); `updating`
 * is shown while an installer holds the App install lock and is replacing
 * the App and its runtime, which the App must sit out; `port` says that a
 * port the user set is held by another program or account (`detail` names
 * the port, the holder and where the port was set).
 */
function buildErrorPageHtml(chrome, workerCommand, phase = "waiting", detail = {}) {
  const c = chrome || chromeForTheme("beige-dark");
  const shown = WAIT_PHASES.includes(phase) ? phase : "waiting";
  const body = shown === "updating"
    ? `<h2>Updating OpenProgram...</h2>
        <p>An installer is replacing the App and its backend.</p>
        <p>OpenProgram will reconnect automatically when it finishes.</p>`
    : shown === "port"
      ? portPageBody(c, detail)
      : `<h2>Waiting for backend to start...</h2>
        <p>OpenProgram will reconnect automatically when the backend is ready.</p>
        <p>If it does not start, run:</p>
        <pre style="background:${c.surface};padding:0.8em;border-radius:6px">${workerCommand}</pre>`;
  return `<body data-openprogram-wait="${shown}" style="background:${c.bg};color:${c.text};font-family:-apple-system,sans-serif;
        display:flex;align-items:center;justify-content:center;height:100vh;margin:0;color-scheme:${c.colorScheme}">
      <div style="max-width:32rem;text-align:center">
        ${body}
      </div>
    </body>`;
}

function buildErrorPageUrl(chrome, workerCommand, phase = "waiting", detail = {}) {
  return "data:text/html;charset=utf-8," + encodeURIComponent(
    buildErrorPageHtml(chrome, workerCommand, phase, detail),
  );
}

/** Which waiting page a URL shows, or null when it is not one. */
function waitPagePhase(url) {
  if (typeof url !== "string" || !url.startsWith("data:text/html")) return null;
  let html = url;
  try {
    html = decodeURIComponent(url);
  } catch {
    /* fall through: match the encoded form */
  }
  const match = /data-openprogram-wait=(?:"|%22)(\w+)/.exec(html);
  if (match && WAIT_PHASES.includes(match[1])) return match[1];
  // Pages built before the marker existed.
  return html.includes("Waiting for backend to start...") ? "waiting" : null;
}

function isErrorPageUrl(url) {
  return waitPagePhase(url) !== null;
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
  recordThemeEvent,
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
  waitPagePhase,
  isErrorPageUrl,
  directoryListingCss,
};
