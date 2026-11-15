// One answer to "which key is the shortcut key here": ⌘ on macOS, Ctrl on
// Windows and Linux. Every hint and every handler that depends on the
// platform asks this file, so a hint never names a key the handler ignores.

import { useSyncExternalStore } from "react";

type ModifierEvent = { metaKey: boolean; ctrlKey: boolean };

export function isMacPlatform(): boolean {
  return typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
}

/** ⌘ held on macOS, Ctrl held elsewhere. On Windows `metaKey` is the
 *  Windows key, which the system keeps; on macOS Ctrl+click is a right click. */
export function hasPrimaryModifier(event: ModifierEvent, mac = isMacPlatform()): boolean {
  return mac ? event.metaKey : event.ctrlKey;
}

const ELSEWHERE: Record<string, string> = { "⌘": "Ctrl", "⌥": "Alt", "⌃": "Ctrl" };
const MAC_ONLY = /[⌘⌥⌃]/g;

/** Key caps for `data-tip-keys` (one cap per space-separated word):
 *  "⌘ Z" on macOS, "Ctrl Z" elsewhere. */
export function platformKeyCaps(caps: string, mac = isMacPlatform()): string {
  return mac ? caps : caps.replace(MAC_ONLY, (glyph) => ELSEWHERE[glyph]);
}

/** A shortcut inside a sentence: "⌥Space" on macOS, "Alt+Space" elsewhere. */
export function platformKeys(keys: string, mac = isMacPlatform()): string {
  return mac ? keys : keys.replace(MAC_ONLY, (glyph) => `${ELSEWHERE[glyph]}+`);
}

const never = () => () => undefined;

/** `isMacPlatform()` for text that is in the page when it first renders:
 *  the prerendered page says macOS, and a Windows or Linux browser corrects
 *  it right after, without a hydration mismatch. */
export function useIsMacPlatform(): boolean {
  return useSyncExternalStore(never, isMacPlatform, () => true);
}
