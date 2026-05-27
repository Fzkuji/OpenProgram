/**
 * Minimal i18n helper. Stores the locale in localStorage and exposes
 * a `useTranslation()` hook returning a `t(key)` function + the
 * current locale + a setter that broadcasts to subscribers.
 *
 * Scope (v1): only the /settings/general page is translated. The rest
 * of the app stays English. The hook is wired through so any new page
 * can opt in by adding its strings to the dictionary below.
 */
"use client";

import { useEffect, useState } from "react";

export type Locale = "en" | "zh";

const STORAGE_KEY = "agentic_locale";

// Module-level subscribers — Zustand-lite. Each component using
// `useTranslation()` re-renders when the locale changes.
const subscribers = new Set<(loc: Locale) => void>();

function readStored(): Locale {
  if (typeof window === "undefined") return "en";
  const v = localStorage.getItem(STORAGE_KEY);
  return v === "zh" ? "zh" : "en";
}

let current: Locale = "en";

export function setLocale(next: Locale): void {
  if (next === current) return;
  current = next;
  if (typeof window !== "undefined") {
    localStorage.setItem(STORAGE_KEY, next);
    document.documentElement.setAttribute("lang", next === "zh" ? "zh-CN" : "en");
  }
  subscribers.forEach((s) => s(next));
}

export function getLocale(): Locale {
  return current;
}

// Per-key dictionary. Top-level keys are stable identifiers; the
// inner record holds the per-locale string. Falls back to English
// when a translation is missing.
const DICT = {
  // /settings/general page
  "general.title": { en: "General", zh: "通用" },
  "general.meta": {
    en: "App-wide preferences. Theme, font and language are per-browser; version info is read-only.",
    zh: "应用级偏好。主题、字体和语言按浏览器保存；版本信息只读。",
  },
  "general.section.preferences": { en: "Preferences", zh: "偏好设置" },
  "general.appearance": { en: "Appearance", zh: "外观" },
  "general.theme.light": { en: "Light", zh: "浅色" },
  "general.theme.auto": { en: "Auto", zh: "跟随系统" },
  "general.theme.dark": { en: "Dark", zh: "深色" },
  "general.font": { en: "Font", zh: "字体" },
  "general.language": { en: "Language", zh: "语言" },
  "general.section.application": { en: "Application", zh: "应用信息" },
  "general.version": { en: "Version", zh: "版本" },
  "general.framework": { en: "Framework", zh: "框架" },
} as const;

type Key = keyof typeof DICT;

export function useTranslation() {
  const [loc, setLoc] = useState<Locale>(current);
  useEffect(() => {
    // Hydrate from localStorage once the client mounts (SSR-safe).
    const stored = readStored();
    if (stored !== current) {
      current = stored;
      if (typeof document !== "undefined") {
        document.documentElement.setAttribute("lang", stored === "zh" ? "zh-CN" : "en");
      }
    }
    setLoc(current);
    const sub = (v: Locale) => setLoc(v);
    subscribers.add(sub);
    return () => { subscribers.delete(sub); };
  }, []);

  const t = (key: Key): string => {
    const row = DICT[key];
    return (row && (row[loc] ?? row.en)) || String(key);
  };
  return { t, locale: loc, setLocale };
}
