// Files headed for a conversation's composer from elsewhere in the window,
// such as a region screenshot taken in a web tab. The composer showing that
// conversation takes them as if they were dropped on it; until one shows it,
// they wait here. Plain TypeScript so tests load it.

const waiting = new Map<string, File[]>();
const listeners = new Set<() => void>();

export function queueComposerFiles(chatKey: string, files: File[]): void {
  if (!chatKey || files.length === 0) return;
  waiting.set(chatKey, [...(waiting.get(chatKey) ?? []), ...files]);
  for (const listener of Array.from(listeners)) listener();
}

/** The files waiting for ``chatKey``, removed from the inbox. */
export function takeComposerFiles(chatKey: string): File[] {
  const files = waiting.get(chatKey) ?? [];
  waiting.delete(chatKey);
  return files;
}

export function onComposerFiles(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
