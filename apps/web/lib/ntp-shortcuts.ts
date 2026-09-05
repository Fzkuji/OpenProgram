/**
 * New-tab-page shortcuts — the Chrome-style tile grid's data, a flat
 * `{url, label}[]` under one localStorage key. Same read/subscribe shape
 * as lib/bookmarks.ts (custom event + `storage` for other tabs).
 *
 * Seeds only apply when the KEY IS ABSENT. Once the user touches the
 * grid the key exists, so an empty array means "user cleared it" and the
 * defaults must not come back.
 */

export interface Shortcut {
  url: string;
  label: string;
}

export const SHORTCUTS_STORAGE_KEY = "agentic_ntp_shortcuts";
export const SHORTCUTS_CHANGE_EVENT = "openprogram:ntp-shortcuts-changed";

export const DEFAULT_SHORTCUTS: Shortcut[] = [
  { url: "https://github.com", label: "GitHub" },
  { url: "https://www.google.com", label: "Google" },
  { url: "https://www.youtube.com", label: "YouTube" },
  { url: "https://www.zhihu.com", label: "知乎" },
  { url: "https://www.bilibili.com", label: "Bilibili" },
  { url: "https://arxiv.org", label: "arXiv" },
];

export function subscribeShortcuts(listener: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  const onStorage = (event: StorageEvent) => {
    if (event.key === SHORTCUTS_STORAGE_KEY) listener();
  };
  window.addEventListener(SHORTCUTS_CHANGE_EVENT, listener);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(SHORTCUTS_CHANGE_EVENT, listener);
    window.removeEventListener("storage", onStorage);
  };
}

export function readShortcuts(): Shortcut[] {
  if (typeof window === "undefined") return [];
  const raw = localStorage.getItem(SHORTCUTS_STORAGE_KEY);
  if (raw === null) return DEFAULT_SHORTCUTS;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (item): item is Shortcut =>
          !!item &&
          typeof item === "object" &&
          typeof (item as Shortcut).url === "string" &&
          typeof (item as Shortcut).label === "string",
      )
      .map(({ url, label }) => ({ url, label }));
  } catch {
    return [];
  }
}

function save(list: Shortcut[]): Shortcut[] {
  try {
    localStorage.setItem(SHORTCUTS_STORAGE_KEY, JSON.stringify(list));
  } catch {
    return readShortcuts();
  }
  window.dispatchEvent(new Event(SHORTCUTS_CHANGE_EVENT));
  return list;
}

export function addShortcut(shortcut: Shortcut): Shortcut[] {
  const url = shortcut.url.trim();
  if (!url) return readShortcuts();
  const label = shortcut.label.trim() || hostOf(url) || url;
  return save([...readShortcuts().filter((item) => item.url !== url), { url, label }]);
}

export function removeShortcut(url: string): Shortcut[] {
  return save(readShortcuts().filter((item) => item.url !== url));
}

/** Bare host for the local initial's fallback colour; "" when unparseable. */
export function hostOf(url: string): string {
  try {
    return new URL(url.includes("://") ? url : `https://${url}`).hostname;
  } catch {
    return "";
  }
}
