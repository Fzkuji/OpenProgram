// Files headed for a conversation's composer from elsewhere in the window,
// such as a region screenshot taken in a web tab. The composer showing that
// conversation takes them as if they were dropped on it; until one shows it,
// they wait here. Plain TypeScript so tests load it.

const waiting = new Map<string, File[]>();

/** Where files wait for a new chat, which has no key until its first message. */
const NEW_CHAT = "\u0000new";
const listeners = new Set<() => void>();

/** Queue files for ``chatKey``'s composer; ``null`` is the new chat's. */
export function queueComposerFiles(chatKey: string | null, files: File[]): void {
  if (files.length === 0) return;
  const key = chatKey || NEW_CHAT;
  waiting.set(key, [...(waiting.get(key) ?? []), ...files]);
  for (const listener of Array.from(listeners)) listener();
}

/** The files waiting for ``chatKey`` (``null``: the new chat), removed from the inbox. */
export function takeComposerFiles(chatKey: string | null): File[] {
  const key = chatKey || NEW_CHAT;
  const files = waiting.get(key) ?? [];
  waiting.delete(key);
  return files;
}

export function onComposerFiles(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
