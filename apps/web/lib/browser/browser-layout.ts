export const BROWSER_MEDIUM_MAX_WIDTH = 719;
export const BROWSER_NARROW_MAX_WIDTH = 519;

export function browserActionPrefix(ownerId: string) {
  return `browsermenu:${ownerId}:`;
}

export function bookmarkFolderActionPrefix(ownerId: string, folderId: string) {
  return `bookmarkfolder:${ownerId}:${folderId}:`;
}

export function ownedActionId(id: string, prefix: string) {
  return id.startsWith(prefix) ? id.slice(prefix.length) : null;
}

export function browserResponsiveMenuItems(
  width: number,
  capabilities: { forward: boolean },
) {
  const medium = width <= BROWSER_MEDIUM_MAX_WIDTH;
  const narrow = width <= BROWSER_NARROW_MAX_WIDTH;
  return {
    home: medium,
    forward: narrow && capabilities.forward,
    openExternal: medium,
  };
}

export function browserPageShortcut(input: {
  key: string;
  metaKey: boolean;
  ctrlKey: boolean;
}): "find" | "zoom-in" | "zoom-out" | "reset-zoom" | "print" | "focus-address" | "back" | "forward" | null {
  if (!input.metaKey && !input.ctrlKey) return null;
  switch (input.key.toLowerCase()) {
    case "f": return "find";
    case "l": return "focus-address";
    case "[": return "back";
    case "]": return "forward";
    case "+":
    case "=": return "zoom-in";
    case "-": return "zoom-out";
    case "0": return "reset-zoom";
    case "p": return "print";
    default: return null;
  }
}

/** How a resting address field shows a URL: the host stands out and the
 *  scheme is dropped, as in Chrome. null means show the text unchanged. */
export function addressDisplayParts(value: string): { secure: boolean; host: string; rest: string } | null {
  let parsed: URL;
  try {
    parsed = new URL(value.trim());
  } catch {
    return null;
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return null;
  if (!parsed.host || parsed.username || parsed.password) return null;
  const path = parsed.pathname === "/" ? "" : parsed.pathname;
  return {
    secure: parsed.protocol === "https:",
    host: parsed.host,
    rest: `${path}${parsed.search}${parsed.hash}`,
  };
}

/** A shortcut as the platform writes it: ⌘[ on macOS, Ctrl+[ elsewhere. */
export function shortcutHint(key: string, { shift = false, mac }: { shift?: boolean; mac: boolean }): string {
  if (mac) return `${shift ? "⇧" : ""}⌘${key}`;
  return `Ctrl+${shift ? "Shift+" : ""}${key}`;
}
