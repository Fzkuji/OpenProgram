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
}): "find" | "zoom-in" | "zoom-out" | "reset-zoom" | "print" | null {
  if (!input.metaKey && !input.ctrlKey) return null;
  switch (input.key.toLowerCase()) {
    case "f": return "find";
    case "+":
    case "=": return "zoom-in";
    case "-": return "zoom-out";
    case "0": return "reset-zoom";
    case "p": return "print";
    default: return null;
  }
}
