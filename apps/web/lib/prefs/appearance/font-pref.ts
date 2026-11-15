/**
 * Global font preference (client side). The stacks + cookie key live in
 * the framework-neutral `font-stacks.ts` so the SSR root layout can read
 * the same data and paint the correct font on the FIRST frame.
 *
 * Persistence: the cookie is the single source of truth (the server
 * reads it during SSR); changes broadcast to in-page subscribers so
 * every mounted picker updates live. Base CSS binds `body { font-family:
 * var(--font-sans); }`, so overriding that variable on <html> cascades
 * through the whole UI.
 */
"use client";

import { useEffect, useState } from "react";
import {
  FONT_STACKS,
  FONT_COOKIE,
  TABULAR_NUMS_COOKIE,
  CHAT_WIDTH_COOKIE,
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
  return readCookie() ?? DEFAULT_FONT;
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
    return () => {
      subscribers.delete(sub);
    };
  }, []);
  return { font, setFont };
}

// ── Equal-width digits ───────────────────────────────────────────────

const tabularSubscribers = new Set<(on: boolean) => void>();

function readTabularNums(): boolean {
  if (typeof document === "undefined") return true;
  const m = document.cookie.match(
    new RegExp(`(?:^|;\\s*)${TABULAR_NUMS_COOKIE}=([^;]+)`),
  );
  return m?.[1] !== "off";
}

function applyTabularNums(on: boolean): void {
  if (typeof document === "undefined") return;
  if (on) delete document.documentElement.dataset.tabularNums;
  else document.documentElement.dataset.tabularNums = "off";
}

export function setTabularNums(on: boolean): void {
  if (typeof document !== "undefined") {
    document.cookie =
      `${TABULAR_NUMS_COOKIE}=${on ? "on" : "off"}; path=/; max-age=31536000; samesite=lax`;
    applyTabularNums(on);
  }
  tabularSubscribers.forEach((s) => s(on));
}

/** Every digit the same width, so numbers in columns line up. */
export function useTabularNumsPref() {
  const [on, setOn] = useState(true);
  useEffect(() => {
    const stored = readTabularNums();
    applyTabularNums(stored);
    setOn(stored);
    tabularSubscribers.add(setOn);
    return () => {
      tabularSubscribers.delete(setOn);
    };
  }, []);
  return { tabularNums: on, setTabularNums };
}

// ── Chat width ───────────────────────────────────────────────────────

export type ChatWidth = "narrow" | "full";

const chatWidthSubscribers = new Set<(width: ChatWidth) => void>();

function readChatWidth(): ChatWidth {
  if (typeof document === "undefined") return "narrow";
  const m = document.cookie.match(
    new RegExp(`(?:^|;\\s*)${CHAT_WIDTH_COOKIE}=([^;]+)`),
  );
  return m?.[1] === "full" ? "full" : "narrow";
}

function applyChatWidth(width: ChatWidth): void {
  if (typeof document === "undefined") return;
  if (width === "full") document.documentElement.dataset.chatWidth = "full";
  else delete document.documentElement.dataset.chatWidth;
}

export function setChatWidth(width: ChatWidth): void {
  if (typeof document !== "undefined") {
    document.cookie =
      `${CHAT_WIDTH_COOKIE}=${width}; path=/; max-age=31536000; samesite=lax`;
    applyChatWidth(width);
  }
  chatWidthSubscribers.forEach((s) => s(width));
}

/** How wide the conversation and its input box are: the narrow reading
 *  column, or the whole chat area. */
export function useChatWidthPref() {
  const [width, setWidth] = useState<ChatWidth>("narrow");
  useEffect(() => {
    const stored = readChatWidth();
    applyChatWidth(stored);
    setWidth(stored);
    chatWidthSubscribers.add(setWidth);
    return () => {
      chatWidthSubscribers.delete(setWidth);
    };
  }, []);
  return { chatWidth: width, setChatWidth };
}
