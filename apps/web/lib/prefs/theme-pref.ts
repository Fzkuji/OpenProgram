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

export const THEME_STORAGE_KEY = "agentic_theme";
export const CUSTOM_CSS_STORAGE_KEY = "agentic_custom_css";

/** 内置主题 id。'custom' 由用户自定义 CSS 提供取值。 */
export const THEME_IDS = [
  "beige-dark",
  "beige-light",
  "dark",
  "light",
  "aurora",
  "custom",
] as const;
export type ThemeId = (typeof THEME_IDS)[number];

/** 存进 localStorage 的值：主题 id，或 'auto'（跟随系统）。 */
export const THEME_PREFS = ["auto", ...THEME_IDS] as const;
export type ThemePref = (typeof THEME_PREFS)[number];

export const DEFAULT_THEME: ThemePref = "auto";

/** 'auto' 解析到的明暗一对。 */
export const AUTO_DARK: ThemeId = "beige-dark";
export const AUTO_LIGHT: ThemeId = "beige-light";

/**
 * 向后兼容：历史上只存 'light' / 'dark' / 'auto' 三个值，指的是暖奶油
 * 那一套。现在 'light' / 'dark' 同时又是新的中性主题名——同一个字符串
 * 有两种含义，所以**不能**在每次读取时翻译（那样用户选了中性 Light，
 * 重新加载就被翻回 beige-light，永远选不上）。
 *
 * 做法：一次性迁移。存量写入没有版本标记，新 UI 写入时同时置一个版本
 * 标记；只有"没有标记"的存量值才按老含义翻译，翻译完就补上标记，此后
 * 'light' / 'dark' 一律按新的中性主题解释。
 */
const SCHEMA_KEY = "agentic_theme_schema";
const SCHEMA_VERSION = "2";
const LEGACY: Record<string, ThemeId> = {
  dark: "beige-dark",
  light: "beige-light",
};

/** 存量值迁移，幂等。模块加载和 pre-paint 脚本各跑一次都安全。 */
export function migrateLegacyTheme(): void {
  if (typeof window === "undefined") return;
  if (localStorage.getItem(SCHEMA_KEY) === SCHEMA_VERSION) return;
  const v = localStorage.getItem(THEME_STORAGE_KEY);
  if (v && v in LEGACY) localStorage.setItem(THEME_STORAGE_KEY, LEGACY[v]);
  localStorage.setItem(SCHEMA_KEY, SCHEMA_VERSION);
}

export function coerceTheme(v: string | null | undefined): ThemePref {
  if (!v) return DEFAULT_THEME;
  if (v === "auto") return "auto";
  return isThemeId(v) ? v : DEFAULT_THEME;
}

export function isThemeId(value: string | null | undefined): value is ThemeId {
  return typeof value === "string"
    && (THEME_IDS as readonly string[]).includes(value);
}

/** Resolved theme already stamped on <html>; desktop child surfaces forward it. */
export function activeThemeId(): ThemeId | undefined {
  if (typeof document === "undefined") return undefined;
  const value = document.documentElement.dataset.theme;
  return isThemeId(value) ? value : undefined;
}

/** 把偏好解析成实际要打在 <html> 上的主题 id。 */
export function resolveTheme(pref: ThemePref): ThemeId {
  if (pref !== "auto") return pref;
  const dark =
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches;
  return dark ? AUTO_DARK : AUTO_LIGHT;
}

export function applyTheme(pref: ThemePref): void {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute("data-theme", resolveTheme(pref));
}

const subscribers = new Set<(t: ThemePref) => void>();

let current: ThemePref = DEFAULT_THEME;

export function getTheme(): ThemePref {
  return current;
}

export function setTheme(next: ThemePref): void {
  current = next;
  if (typeof window !== "undefined") {
    localStorage.setItem(THEME_STORAGE_KEY, next);
    applyTheme(next);
  }
  subscribers.forEach((s) => s(next));
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
  return localStorage.getItem(CUSTOM_CSS_STORAGE_KEY) ?? "";
}

export function setCustomCss(css: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(CUSTOM_CSS_STORAGE_KEY, css);
  applyCustomCss(css);
}

/** 主题 + 自定义 CSS 的读写 hook。挂载时从 localStorage 同步一次。 */
export function useThemePref() {
  const [theme, setThemeState] = useState<ThemePref>(current);
  const [customCss, setCustomCssState] = useState("");

  useEffect(() => {
    migrateLegacyTheme();
    const stored = coerceTheme(localStorage.getItem(THEME_STORAGE_KEY));
    current = stored;
    applyTheme(stored);
    setThemeState(stored);
    setCustomCssState(getCustomCss());

    const sub = (v: ThemePref) => setThemeState(v);
    subscribers.add(sub);

    // 'auto' 时跟随系统明暗切换。
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onSystemChange = () => {
      if (current === "auto") applyTheme("auto");
    };
    mq.addEventListener("change", onSystemChange);

    return () => {
      subscribers.delete(sub);
      mq.removeEventListener("change", onSystemChange);
    };
  }, []);

  return {
    theme,
    setTheme,
    customCss,
    setCustomCss: (css: string) => {
      setCustomCss(css);
      setCustomCssState(css);
    },
  };
}
