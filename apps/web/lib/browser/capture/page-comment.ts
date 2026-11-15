// A comment on an element picked in a web tab, as the composer gets it.
// The desktop host picks the element (apps/desktop/main/web-views/web-views.js
// pickElementInView); this file only words it. Plain TypeScript so tests
// load it.

export interface PickedElement {
  id: string;
  url: string;
  title: string;
  tag: string;
  selector: string;
  text: string;
  label: string;
  href: string;
}

const QUOTE_LIMIT = 120;

function clip(value: string, limit: number): string {
  const flat = value.replace(/\s+/g, " ").trim();
  return flat.length > limit ? `${flat.slice(0, limit - 1)}…` : flat;
}

/** What the comment row names the element by: its visible text or label,
 *  else its tag. */
export function pickedElementName(pick: PickedElement): string {
  return clip(pick.text || pick.label, 60) || `<${pick.tag || "element"}>`;
}

/** The block added to the composer draft. */
export function pageCommentText(pick: PickedElement, comment: string): string {
  const page = pick.title ? `[${pick.title.replace(/[[\]]/g, "")}](${pick.url})` : pick.url;
  const quote = clip(pick.text || pick.label, QUOTE_LIMIT);
  const element = [
    pick.selector ? `\`${pick.selector.replace(/`/g, "")}\`` : `<${pick.tag || "element"}>`,
    quote ? `("${quote}")` : "",
    pick.href && pick.href !== pick.url ? `→ ${pick.href}` : "",
  ].filter(Boolean).join(" ");
  return `Comment on ${page}, element ${element}:\n${comment.trim()}`;
}

/** The conversation a page comment goes to: the one in focus, else the
 *  one whose agent opened the page, else the one the user was last in.
 *  Only a conversation that is still open counts. */
export function commentChatKey(
  candidates: readonly (string | null | undefined)[],
  isOpen: (key: string) => boolean,
): string | null {
  for (const key of candidates) if (key && isOpen(key)) return key;
  return null;
}
