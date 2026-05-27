/**
 * Global font preference. Mirrors the theme helper: read on mount,
 * persist to localStorage, apply by overriding the `--font-sans` CSS
 * variable on `<html>`. Base CSS (`web/app/styles/base.css`) already
 * binds `body { font-family: var(--font-sans); }`, so changing the
 * variable cascades through the whole UI.
 */
"use client";

import { useEffect, useState } from "react";

export type FontKey = "system" | "inter" | "serif" | "mono" | "rounded";

const STORAGE_KEY = "agentic_font";

// One CSS font-stack per option. `system` is the original stack from
// base.css — picking it removes the inline override so the page falls
// back to the project default.
const FONT_STACKS: Record<FontKey, string> = {
  system: "",
  inter:
    "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
  serif:
    "'Source Serif Pro', Georgia, 'Times New Roman', serif",
  mono:
    "'JetBrains Mono', Menlo, Monaco, Consolas, 'Liberation Mono', monospace",
  rounded:
    "ui-rounded, 'SF Pro Rounded', 'Nunito', system-ui, sans-serif",
};

export const FONT_LABELS: Record<FontKey, { en: string; zh: string }> = {
  system: { en: "System default", zh: "系统默认" },
  inter: { en: "Inter (sans)", zh: "Inter（无衬线）" },
  serif: { en: "Serif", zh: "衬线" },
  mono: { en: "Monospace", zh: "等宽" },
  rounded: { en: "Rounded", zh: "圆润" },
};

const subscribers = new Set<(f: FontKey) => void>();

function readStored(): FontKey {
  if (typeof window === "undefined") return "system";
  const v = localStorage.getItem(STORAGE_KEY);
  return (v as FontKey) in FONT_STACKS ? (v as FontKey) : "system";
}

function applyFont(font: FontKey): void {
  if (typeof document === "undefined") return;
  const stack = FONT_STACKS[font];
  if (stack) {
    document.documentElement.style.setProperty("--font-sans", stack);
  } else {
    // Remove the override so the base.css fallback wins again.
    document.documentElement.style.removeProperty("--font-sans");
  }
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
