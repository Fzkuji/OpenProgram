/**
 * 主题偏好（客户端）。结构对照 font-pref.ts：
 * localStorage 持久化 + 模块级订阅者，SSR 安全。
 *
 * 主题本身是**数据**——每套配色是 app/styles/themes/ 下一个 CSS 文件，
 * 里面把整套 token 契约填满。这里只负责"往 <html> 上打哪个 data-theme"。
 *
 * 字体用 cookie 是因为 SSR 要在首帧就把 font-family 内联进去；主题不需要，
 * 因为 :root 兜底取值就是默认外观（beige-dark），首帧不会白屏，
 * layout.tsx 的 pre-paint 脚本在 React 水合前就把属性打上了。
 */
"use client";

import { useEffect, useState } from "react";

import {
  coerceThemeMode,
  coerceThemeStyle,
  DEFAULT_THEME_MODE,
  DEFAULT_THEME_STYLE,
  isThemeId,
  legacyThemePreference,
  resolveThemePreference,
  type ThemeId,
  type ThemeMode,
  type ThemeStyle,
} from "./theme-config";

export {
  DEFAULT_THEME_MODE,
  DEFAULT_THEME_STYLE,
  isThemeId,
  THEME_IDS,
  THEME_MODES,
  THEME_STYLES,
  THEME_STYLE_PAIRS,
} from "./theme-config";
export type { ResolvedThemeMode, ThemeId, ThemeMode, ThemeStyle } from "./theme-config";

export const LEGACY_THEME_STORAGE_KEY = "agentic_theme";
export const THEME_STYLE_STORAGE_KEY = "agentic_theme_style";
export const THEME_MODE_STORAGE_KEY = "agentic_theme_mode";
export const CUSTOM_CSS_STORAGE_KEY = "agentic_custom_css";

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
 */
const SCHEMA_KEY = "agentic_theme_schema";
const SCHEMA_VERSION = "3";

/** 存量值迁移，幂等。模块加载和 pre-paint 脚本各跑一次都安全。 */
export function migrateLegacyThemePreferences(): void {
  if (typeof window === "undefined") return;
  if (
    storageGet(SCHEMA_KEY) === SCHEMA_VERSION
    && storageGet(THEME_STYLE_STORAGE_KEY)
    && storageGet(THEME_MODE_STORAGE_KEY)
  ) return;
  const legacy = legacyThemePreference(
    storageGet(LEGACY_THEME_STORAGE_KEY),
    storageGet(SCHEMA_KEY) ?? "1",
  );
  if (!storageGet(THEME_STYLE_STORAGE_KEY)) {
    storageSet(THEME_STYLE_STORAGE_KEY, legacy.style);
  }
  if (!storageGet(THEME_MODE_STORAGE_KEY)) {
    storageSet(THEME_MODE_STORAGE_KEY, legacy.mode);
  }
  storageSet(SCHEMA_KEY, SCHEMA_VERSION);
}

/** Resolved theme already stamped on <html>; desktop child surfaces forward it. */
export function activeThemeId(): ThemeId | undefined {
  if (typeof document === "undefined") return undefined;
  const value = document.documentElement.dataset.theme;
  return isThemeId(value) ? value : undefined;
}

/** 把偏好解析成实际要打在 <html> 上的主题 id。 */
export function applyTheme(style: ThemeStyle, mode: ThemeMode): void {
  if (typeof document === "undefined") return;
  const systemDark = typeof window !== "undefined"
    && window.matchMedia("(prefers-color-scheme: dark)").matches;
  const resolved = resolveThemePreference(style, mode, systemDark);
  document.documentElement.setAttribute("data-theme", resolved.theme);
  document.documentElement.setAttribute("data-theme-style", style);
  document.documentElement.setAttribute("data-theme-mode", resolved.resolvedMode);
  storageSet(LEGACY_THEME_STORAGE_KEY, resolved.theme);
}

type ThemePreferences = { style: ThemeStyle; mode: ThemeMode };
const subscribers = new Set<(preferences: ThemePreferences) => void>();
let currentStyle: ThemeStyle = DEFAULT_THEME_STYLE;
let currentMode: ThemeMode = DEFAULT_THEME_MODE;

function notify(): void {
  const preferences = { style: currentStyle, mode: currentMode };
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

/* ---- 用户自定义 CSS（Obsidian 式）------------------------------- */

/** 注入/更新 <style id="user-custom-css">。空串则移除。 */
export function applyCustomCss(css: string): void {
  if (typeof document === "undefined") return;
  const id = "user-custom-css";
  let el = document.getElementById(id) as HTMLStyleElement | null;
  if (!css.trim()) {
    el?.remove();
    return;
  }
  if (!el) {
    el = document.createElement("style");
    el.id = id;
    // 挂在 </head> 末尾：排在所有主题 import 之后，同优先级下后来者胜。
    document.head.appendChild(el);
  }
  el.textContent = css;
}

export function getCustomCss(): string {
  if (typeof window === "undefined") return "";
  return storageGet(CUSTOM_CSS_STORAGE_KEY) ?? "";
}

export function setCustomCss(css: string): void {
  if (typeof window === "undefined") return;
  storageSet(CUSTOM_CSS_STORAGE_KEY, css);
  applyCustomCss(css);
}

/** 主题 + 自定义 CSS 的读写 hook。挂载时从 localStorage 同步一次。 */
export function useThemePref() {
  const [style, setStyleState] = useState<ThemeStyle>(currentStyle);
  const [mode, setModeState] = useState<ThemeMode>(currentMode);
  const [customCss, setCustomCssState] = useState("");

  useEffect(() => {
    migrateLegacyThemePreferences();
    currentStyle = coerceThemeStyle(storageGet(THEME_STYLE_STORAGE_KEY));
    currentMode = coerceThemeMode(storageGet(THEME_MODE_STORAGE_KEY));
    applyTheme(currentStyle, currentMode);
    setStyleState(currentStyle);
    setModeState(currentMode);
    setCustomCssState(getCustomCss());

    const sub = (preferences: ThemePreferences) => {
      setStyleState(preferences.style);
      setModeState(preferences.mode);
    };
    subscribers.add(sub);

    // 'auto' 时跟随系统明暗切换。
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onSystemChange = () => {
      if (currentMode === "auto") applyTheme(currentStyle, currentMode);
    };
    mq.addEventListener("change", onSystemChange);

    const onStorage = (event: StorageEvent) => {
      if (event.key === THEME_STYLE_STORAGE_KEY || event.key === THEME_MODE_STORAGE_KEY) {
        currentStyle = coerceThemeStyle(storageGet(THEME_STYLE_STORAGE_KEY));
        currentMode = coerceThemeMode(storageGet(THEME_MODE_STORAGE_KEY));
        applyTheme(currentStyle, currentMode);
        notify();
      } else if (event.key === CUSTOM_CSS_STORAGE_KEY) {
        setCustomCssState(getCustomCss());
        applyCustomCss(getCustomCss());
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
    customCss,
    setCustomCss: (css: string) => {
      setCustomCss(css);
      setCustomCssState(css);
    },
  };
}
