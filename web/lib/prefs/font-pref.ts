/**
 * Global font preference (client side). The stacks + cookie key live in
 * the framework-neutral `font-stacks.ts` so the SSR root layout can read
 * the same data and paint the correct font on the FIRST frame.
 *
 * Persistence: the cookie is the source of truth (server reads it during
 * SSR). We also mirror to localStorage and broadcast to subscribers so
 * open tabs update live. Base CSS binds `body { font-family:
 * var(--font-sans); }`, so overriding that variable on <html> cascades
 * through the whole UI.
 */
"use client";

import { useEffect, useState } from "react";
import {
  FONT_STACKS,
  FONT_STORAGE_KEY,
  FONT_COOKIE,
  DEFAULT_FONT,
  coerceFontKey,
  type FontKey,
} from "./font-stacks";

export type { FontKey };
export { fontStack } from "./font-stacks";

/** Display labels — shown using their OWN font in the picker so the
 *  user sees what they'll get. */
export const FONT_LABELS: Record<FontKey, string> = {
  system: "System",
  inter: "Inter",
  serif: "Serif",
  mono: "JetBrains Mono",
};

const subscribers = new Set<(f: FontKey) => void>();

function writeCookie(font: FontKey): void {
  if (typeof document === "undefined") return;
  // 1 year, root path, Lax so it rides the top-level navigation that
  // SSR reads. Not HttpOnly — the client needs to read/write it too.
  document.cookie =
    `${FONT_COOKIE}=${font}; path=/; max-age=31536000; samesite=lax`;
}

function readCookie(): FontKey | null {
  if (typeof document === "undefined") return null;
  const m = document.cookie.match(
    new RegExp(`(?:^|;\\s*)${FONT_COOKIE}=([^;]+)`),
  );
  return m ? coerceFontKey(m[1]) : null;
}

function readStored(): FontKey {
  if (typeof window === "undefined") return DEFAULT_FONT;
  // Cookie first (matches what SSR used); fall back to legacy
  // localStorage, then default.
  return readCookie() ?? coerceFontKey(localStorage.getItem(FONT_STORAGE_KEY));
}

function applyFont(font: FontKey): void {
  if (typeof document === "undefined") return;
  document.documentElement.style.setProperty("--font-sans", FONT_STACKS[font]);
}

let current: FontKey = DEFAULT_FONT;

export function setFont(next: FontKey): void {
  if (!(next in FONT_STACKS)) return;
  current = next;
  if (typeof window !== "undefined") {
    writeCookie(next);
    localStorage.setItem(FONT_STORAGE_KEY, next);
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
    // Heal a missing cookie (e.g. a user who only ever had localStorage
    // from before this change) so the NEXT SSR paint is already correct.
    if (readCookie() == null) writeCookie(stored);
    const sub = (v: FontKey) => setFontState(v);
    subscribers.add(sub);
    return () => {
      subscribers.delete(sub);
    };
  }, []);
  return { font, setFont };
}
