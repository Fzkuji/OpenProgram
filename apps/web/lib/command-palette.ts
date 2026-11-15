// Pure parts of the Cmd/Ctrl+K command palette (components/command-palette.tsx).
import { activityTs } from "@/components/sidebar/sessions/helpers";
import type { ConvSummary } from "@/lib/session-store";
import { isMacPlatform } from "./shortcut-keys.ts";

/** Sessions listed at most; enough for months of history, few enough to filter instantly. */
export const PALETTE_SESSION_LIMIT = 300;

type ShortcutEvent = Pick<KeyboardEvent, "key" | "metaKey" | "ctrlKey" | "altKey" | "shiftKey" | "isComposing"> & {
  target: EventTarget | null;
};

/** Cmd+K on macOS, Ctrl+K elsewhere. A terminal keeps the key: Cmd+K clears it. */
export function isPaletteShortcut(event: ShortcutEvent, mac = isMacPlatform()): boolean {
  if (event.isComposing || event.altKey || event.shiftKey) return false;
  if (event.key.toLowerCase() !== "k") return false;
  if (mac ? !event.metaKey || event.ctrlKey : !event.ctrlKey || event.metaKey) return false;
  const target = event.target as { closest?: (selector: string) => unknown } | null;
  return !target?.closest?.(".xterm");
}

/** Unarchived sessions, most recently active first. */
export function paletteSessions(conversations: ConvSummary[]): ConvSummary[] {
  return conversations
    .filter((conv) => conv && conv.id && !conv.archived)
    .sort((a, b) => activityTs(b) - activityTs(a))
    .slice(0, PALETTE_SESSION_LIMIT);
}
