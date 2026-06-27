/**
 * Global font preference. Mirrors the theme helper: read on mount,
 * persist to localStorage, apply by overriding the `--font-sans` CSS
 * variable on `<html>`. Base CSS (`web/app/styles/base.css`) already
 * binds `body { font-family: var(--font-sans); }`, so changing the
 * variable cascades through the whole UI.
 *
 * Each stack ends with a CJK fallback (PingFang SC / Microsoft YaHei /
 * etc.) so Chinese text picks up a matching weight even when the
 * primary Latin face has no CJK glyphs of its own.
 */
"use client";

import { useEffect, useState } from "react";

export type FontKey = "system" | "inter" | "serif" | "mono";

const STORAGE_KEY = "agentic_font";

// Latin face + CJK fallback per option.
const FONT_STACKS: Record<FontKey, string> = {
  // OS-native stack (pure system look). No Inter — picks up SF on
  // macOS, Segoe on Windows, etc.
  system: [
    "system-ui",
    "-apple-system",
    "BlinkMacSystemFont",
    "'Segoe UI'",
    // CJK
    "'PingFang SC'",
    "'Microsoft YaHei'",
    "'Hiragino Sans GB'",
    "sans-serif",
  ].join(", "),
  // Inter Variable is bundled locally (see app/globals.css). High
  // x-height + tight rhythm — visually matches Claude's Anthropic
  // Sans. Fall back to any system-installed Inter, then to OS native.
  inter: [
    "'Inter Variable'",
    "'Inter'",
    "-apple-system",
    "BlinkMacSystemFont",
    "'Segoe UI'",
    "'PingFang SC'",
    "'Microsoft YaHei'",
    "'Hiragino Sans GB'",
    "sans-serif",
  ].join(", "),
  serif: [
    "'Source Serif Pro'",
    "'Iowan Old Style'",
    "Georgia",
    "'Times New Roman'",
    "'Songti SC'",
    "SimSun",
    "'Noto Serif CJK SC'",
    "serif",
  ].join(", "),
  mono: [
    "'JetBrains Mono'",
    "ui-monospace",
    "Menlo",
    "Monaco",
    "Consolas",
    "'PingFang SC'",
    "'Microsoft YaHei'",
    "monospace",
  ].join(", "),
};

/** Display labels — shown using their OWN font in the picker so the
 *  user sees what they'll get. Names match what most users recognise
 *  rather than CSS jargon ("Serif" not "Source Serif Pro"; "Inter"
 *  because the typeface itself is widely known). */
export const FONT_LABELS: Record<FontKey, string> = {
  system: "System",
  inter: "Inter",
  serif: "Serif",
  mono: "JetBrains Mono",
};

export function fontStack(key: FontKey): string {
  return FONT_STACKS[key];
}

const subscribers = new Set<(f: FontKey) => void>();

function readStored(): FontKey {
  if (typeof window === "undefined") return "system";
  const v = localStorage.getItem(STORAGE_KEY);
  return (v as FontKey) in FONT_STACKS ? (v as FontKey) : "system";
}

function applyFont(font: FontKey): void {
  if (typeof document === "undefined") return;
  document.documentElement.style.setProperty("--font-sans", FONT_STACKS[font]);
}

let current: FontKey = "system";

export function setFont(next: FontKey): void {
  if (!(next in FONT_STACKS)) return;
  current = next;
  if (typeof window !== "undefined") {
    localStorage.setItem(STORAGE_KEY, next);
    applyFont(next);
  }
  subscribers.forEach((s) => s(next));
}

export function getFont(): FontKey {
  return current;
}

export function useFontPref() {
  const [font, setFontState] = useState<FontKey>(current);
  useEffect(() => {
    const stored = readStored();
    current = stored;
    applyFont(stored);
    setFontState(stored);
    const sub = (v: FontKey) => setFontState(v);
    subscribers.add(sub);
    return () => { subscribers.delete(sub); };
  }, []);
  return { font, setFont };
}
