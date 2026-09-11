/** One decoder per socket: partial responses never survive reconnect. */
export function createHistoryFragmentDecoder() {
  const pending = new Map<string, { index: number; parts: string[]; size: number }>();
  let total = 0;
  return {
    clear() { pending.clear(); total = 0; },
    accept(data: { id?: unknown; index?: unknown; text?: unknown; final?: unknown }): string | null {
      if (typeof data.id !== 'string' || typeof data.text !== 'string' || !Number.isSafeInteger(data.index)) {
        throw new Error('Invalid history fragment');
      }
      let entry = pending.get(data.id);
      if (!entry) {
        if (data.index !== 0 || pending.size >= 8) throw new Error('Invalid history fragment order');
        entry = { index: 0, parts: [], size: 0 };
        pending.set(data.id, entry);
      }
      if (entry.index !== data.index || total + data.text.length > 64 * 1024 * 1024) {
        pending.clear(); total = 0;
        throw new Error('History response exceeds the transfer budget or is out of order');
      }
      entry.parts.push(data.text); entry.index++; entry.size += data.text.length; total += data.text.length;
      if (data.final !== true) return null;
      pending.delete(data.id); total -= entry.size;
      return entry.parts.join('');
    },
  };
}
