/**
 * 主题偏好（客户端）。结构对照 font-pref.ts：
 * localStorage 持久化 + 模块级订阅者，SSR 安全。
 *
 * Appearance 是 Obsidian 式四层：明暗模式、命名主题包、强调色、
 * 可选 CSS overlay。这里负责往 <html> 上打 data-theme / data-custom-css
 * 以及强调色 token 覆盖。
 *
 * 字体用 cookie 是因为 SSR 要在首帧就把 font-family 内联进去；主题不需要，
 * 因为 :root 兜底取值就是默认外观（beige-dark），首帧不会白屏，
 * layout.tsx 的 pre-paint 脚本在 React 水合前就把属性打上了。
 */
"use client";

import { useEffect, useState } from "react";

import {
  accentOverrideTokens,
  coerceAccentColor,
  coerceThemeMode,
  coerceThemeStyle,
  CUSTOM_CSS_TEMPLATE,
  DEFAULT_THEME_MODE,
  DEFAULT_THEME_STYLE,
  isThemeId,
  migrateAppearancePreferences,
  rewriteCustomCssForOverlay,
  resolveThemePreference,
  THEME_DEFAULT_ACCENTS,
  type ThemeId,
  type ThemeMode,
  type ThemeStyle,
} from "./theme-config";

export {
  ACCENT_PRESETS,
  CUSTOM_CSS_TEMPLATE,
  DEFAULT_THEME_MODE,
  DEFAULT_THEME_STYLE,
  isThemeId,
  THEME_DEFAULT_ACCENTS,
  THEME_IDS,
  THEME_MODES,
  THEME_STYLES,
  THEME_STYLE_PAIRS,
} from "./theme-config";
export type { ResolvedThemeMode, ThemeId, ThemeMode, ThemeStyle } from "./theme-config";

export const LEGACY_THEME_STORAGE_KEY = "agentic_theme";
export const THEME_STYLE_STORAGE_KEY = "agentic_theme_style";
export const THEME_MODE_STORAGE_KEY = "agentic_theme_mode";
export const ACCENT_STORAGE_KEY = "agentic_theme_accent";
export const CUSTOM_CSS_STORAGE_KEY = "agentic_custom_css";
export const CUSTOM_CSS_ENABLED_STORAGE_KEY = "agentic_custom_css_enabled";

function storageGet(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function storageSet(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Theme changes still apply for this document when persistence is blocked.
  }
}

/**
 * 向后兼容：schema 2 及更早版本只保存组合后的 agentic_theme。
 * schema 3 将它一次性拆成颜色风格与明暗模式，随后分别持久化。
 * Custom 不再是第四套皮肤：存量 custom / custom-light 迁到 beige，
 * 有保存的 CSS 则打开 overlay。
 */
const SCHEMA_KEY = "agentic_theme_schema";
const SCHEMA_VERSION = "3";

/** 存量值迁移，幂等。模块加载和 pre-paint 脚本各跑一次都安全。 */
export function migrateLegacyThemePreferences(): void {
  if (typeof window === "undefined") return;
  const next = migrateAppearancePreferences({
    schema: storageGet(SCHEMA_KEY),
    style: storageGet(THEME_STYLE_STORAGE_KEY),
    mode: storageGet(THEME_MODE_STORAGE_KEY),
    legacy: storageGet(LEGACY_THEME_STORAGE_KEY),
    customCss: storageGet(CUSTOM_CSS_STORAGE_KEY),
    customCssEnabled: storageGet(CUSTOM_CSS_ENABLED_STORAGE_KEY),
    accent: storageGet(ACCENT_STORAGE_KEY),
  });
  const storedStyle = storageGet(THEME_STYLE_STORAGE_KEY);
  const storedMode = storageGet(THEME_MODE_STORAGE_KEY);
  if (storageGet(SCHEMA_KEY) !== SCHEMA_VERSION || !storedStyle || !storedMode) {
    if (!storedStyle || storedStyle === "custom") {
      storageSet(THEME_STYLE_STORAGE_KEY, next.style);
    }
    if (!storedMode) {
      storageSet(THEME_MODE_STORAGE_KEY, next.mode);
    }
    storageSet(SCHEMA_KEY, SCHEMA_VERSION);
  }
  if (storageGet(THEME_STYLE_STORAGE_KEY) === "custom") {
    storageSet(THEME_STYLE_STORAGE_KEY, next.style);
  }
  storageSet(CUSTOM_CSS_ENABLED_STORAGE_KEY, next.customCssEnabled ? "1" : "0");
  currentStyle = next.style;
  currentMode = next.mode;
  currentAccent = next.accent;
  currentCssEnabled = next.customCssEnabled;
}

/** Resolved theme already stamped on <html>; desktop child surfaces forward it. */
export function activeThemeId(): ThemeId | undefined {
  if (typeof document === "undefined") return undefined;
  const value = document.documentElement.dataset.theme;
  return isThemeId(value) ? value : undefined;
}

function currentSystemDark(): boolean {
  return typeof window !== "undefined"
    && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function resolvedThemeId(
  style: ThemeStyle = currentStyle,
  mode: ThemeMode = currentMode,
  systemDark = currentSystemDark(),
): ThemeId {
  return resolveThemePreference(style, mode, systemDark).theme;
}

export function packageAccent(style?: ThemeStyle, mode?: ThemeMode): string {
  return THEME_DEFAULT_ACCENTS[resolvedThemeId(style, mode)];
}

/** 把偏好解析成实际要打在 <html> 上的主题 id。 */
export function applyTheme(style: ThemeStyle, mode: ThemeMode): void {
  if (typeof document === "undefined") return;
  const resolved = resolveThemePreference(style, mode, currentSystemDark());
  document.documentElement.setAttribute("data-theme", resolved.theme);
  document.documentElement.setAttribute("data-theme-style", style);
  document.documentElement.setAttribute("data-theme-mode", resolved.resolvedMode);
  if (currentCssEnabled && getCustomCss().trim()) {
    document.documentElement.setAttribute("data-custom-css", "on");
  } else {
    document.documentElement.removeAttribute("data-custom-css");
  }
  storageSet(LEGACY_THEME_STORAGE_KEY, resolved.theme);
  applyAccentColor(currentAccent);
  notifyDesktopChrome(resolved.theme, style, mode);
}

function applyAccentColor(hex: string | null): void {
  if (typeof document === "undefined") return;
  const tokens = hex ? accentOverrideTokens(hex) : null;
  const root = document.documentElement.style;
  if (!tokens) {
    root.removeProperty("--accent-orange");
    root.removeProperty("--accent-fill");
    root.removeProperty("--accent-orange-hover");
    return;
  }
  root.setProperty("--accent-orange", tokens["--accent-orange"]);
  root.setProperty("--accent-fill", tokens["--accent-fill"]);
  root.setProperty("--accent-orange-hover", tokens["--accent-orange-hover"]);
}

function notifyDesktopChrome(
  theme: ThemeId,
  style: ThemeStyle,
  mode: ThemeMode,
): void {
  const desktop = window.openprogramDesktop;
  if (!desktop?.theme?.setChrome) return;
  let backgroundColor: string | undefined;
  try {
    backgroundColor = getComputedStyle(document.documentElement)
      .getPropertyValue("--bg-primary")
      .trim() || undefined;
  } catch {
    backgroundColor = undefined;
  }
  desktop.theme.setChrome({
    theme,
    style,
    mode,
    backgroundColor,
    accentColor: currentAccent ?? undefined,
  });
}

type ThemePreferences = {
  style: ThemeStyle;
  mode: ThemeMode;
  accent: string | null;
  customCssEnabled: boolean;
};
const subscribers = new Set<(preferences: ThemePreferences) => void>();
let currentStyle: ThemeStyle = DEFAULT_THEME_STYLE;
let currentMode: ThemeMode = DEFAULT_THEME_MODE;
let currentAccent: string | null = null;
let currentCssEnabled = false;

function notify(): void {
  const preferences: ThemePreferences = {
    style: currentStyle,
    mode: currentMode,
    accent: currentAccent,
    customCssEnabled: currentCssEnabled,
  };
  subscribers.forEach((subscriber) => subscriber(preferences));
}

export function setThemeStyle(next: ThemeStyle): void {
  currentStyle = next;
  if (typeof window !== "undefined") {
    storageSet(THEME_STYLE_STORAGE_KEY, next);
    applyTheme(currentStyle, currentMode);
  }
  notify();
}

export function setThemeMode(next: ThemeMode): void {
  currentMode = next;
  if (typeof window !== "undefined") {
    storageSet(THEME_MODE_STORAGE_KEY, next);
    applyTheme(currentStyle, currentMode);
  }
  notify();
}

export function setAccentColor(next: string | null): void {
  currentAccent = coerceAccentColor(next);
  if (typeof window !== "undefined") {
    storageSet(ACCENT_STORAGE_KEY, currentAccent ?? "");
    applyTheme(currentStyle, currentMode);
  }
  notify();
}

export function resetAccentColor(): void {
  setAccentColor(null);
}

/* ---- 用户自定义 CSS overlay（Obsidian snippets）----------------- */

/** 注入/更新 <style id="user-custom-css">。未启用或空串则移除。 */
export function applyCustomCss(css: string, enabled = currentCssEnabled): void {
  if (typeof document === "undefined") return;
  const id = "user-custom-css";
  let el = document.getElementById(id) as HTMLStyleElement | null;
  if (!enabled || !css.trim()) {
    el?.remove();
    document.documentElement.removeAttribute("data-custom-css");
    return;
  }
  document.documentElement.setAttribute("data-custom-css", "on");
  if (!el) {
    el = document.createElement("style");
    el.id = id;
    // 挂在 </head> 末尾：排在所有主题 import 之后，同优先级下后来者胜。
    document.head.appendChild(el);
  }
  el.textContent = rewriteCustomCssForOverlay(css);
}

export function getCustomCss(): string {
  if (typeof window === "undefined") return "";
  return storageGet(CUSTOM_CSS_STORAGE_KEY) ?? "";
}

export function setCustomCss(css: string): void {
  if (typeof window === "undefined") return;
  storageSet(CUSTOM_CSS_STORAGE_KEY, css);
  applyCustomCss(css, currentCssEnabled);
}

export function setCustomCssEnabled(enabled: boolean): void {
  currentCssEnabled = enabled;
  if (typeof window !== "undefined") {
    storageSet(CUSTOM_CSS_ENABLED_STORAGE_KEY, enabled ? "1" : "0");
    applyCustomCss(getCustomCss(), enabled);
    applyTheme(currentStyle, currentMode);
  }
  notify();
}

/** 主题 + 强调色 + 自定义 CSS overlay 的读写 hook。挂载时从 localStorage 同步一次。 */
export function useThemePref() {
  const [style, setStyleState] = useState<ThemeStyle>(currentStyle);
  const [mode, setModeState] = useState<ThemeMode>(currentMode);
  const [accent, setAccentState] = useState<string | null>(currentAccent);
  const [customCss, setCustomCssState] = useState("");
  const [customCssEnabled, setCustomCssEnabledState] = useState(currentCssEnabled);

  useEffect(() => {
    migrateLegacyThemePreferences();
    currentStyle = coerceThemeStyle(storageGet(THEME_STYLE_STORAGE_KEY));
    currentMode = coerceThemeMode(storageGet(THEME_MODE_STORAGE_KEY));
    currentAccent = coerceAccentColor(storageGet(ACCENT_STORAGE_KEY));
    currentCssEnabled = storageGet(CUSTOM_CSS_ENABLED_STORAGE_KEY) === "1";
    applyTheme(currentStyle, currentMode);
    applyCustomCss(getCustomCss(), currentCssEnabled);
    setStyleState(currentStyle);
    setModeState(currentMode);
    setAccentState(currentAccent);
    setCustomCssState(getCustomCss());
    setCustomCssEnabledState(currentCssEnabled);

    const sub = (preferences: ThemePreferences) => {
      setStyleState(preferences.style);
      setModeState(preferences.mode);
      setAccentState(preferences.accent);
      setCustomCssEnabledState(preferences.customCssEnabled);
    };
    subscribers.add(sub);

    // 'auto' 时跟随系统明暗切换。
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onSystemChange = () => {
      if (currentMode === "auto") {
        applyTheme(currentStyle, currentMode);
        notify();
      }
    };
    mq.addEventListener("change", onSystemChange);

    const onStorage = (event: StorageEvent) => {
      if (
        event.key === THEME_STYLE_STORAGE_KEY
        || event.key === THEME_MODE_STORAGE_KEY
        || event.key === ACCENT_STORAGE_KEY
      ) {
        currentStyle = coerceThemeStyle(storageGet(THEME_STYLE_STORAGE_KEY));
        currentMode = coerceThemeMode(storageGet(THEME_MODE_STORAGE_KEY));
        currentAccent = coerceAccentColor(storageGet(ACCENT_STORAGE_KEY));
        applyTheme(currentStyle, currentMode);
        notify();
      } else if (
        event.key === CUSTOM_CSS_STORAGE_KEY
        || event.key === CUSTOM_CSS_ENABLED_STORAGE_KEY
      ) {
        currentCssEnabled = storageGet(CUSTOM_CSS_ENABLED_STORAGE_KEY) === "1";
        setCustomCssState(getCustomCss());
        setCustomCssEnabledState(currentCssEnabled);
        applyCustomCss(getCustomCss(), currentCssEnabled);
        applyTheme(currentStyle, currentMode);
      }
    };
    window.addEventListener("storage", onStorage);

    return () => {
      subscribers.delete(sub);
      mq.removeEventListener("change", onSystemChange);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  return {
    style,
    mode,
    setStyle: setThemeStyle,
    setMode: setThemeMode,
    accent,
    setAccent: setAccentColor,
    resetAccent: resetAccentColor,
    packageAccent: packageAccent(style, mode),
    customCss,
    customCssEnabled,
    setCustomCssEnabled,
    setCustomCss: (css: string) => {
      setCustomCss(css);
      setCustomCssState(css);
    },
    insertCustomCssTemplate: () => {
      setCustomCss(CUSTOM_CSS_TEMPLATE);
      setCustomCssState(CUSTOM_CSS_TEMPLATE);
      if (!currentCssEnabled) setCustomCssEnabled(true);
    },
    clearCustomCss: () => {
      setCustomCss("");
      setCustomCssState("");
    },
  };
}
