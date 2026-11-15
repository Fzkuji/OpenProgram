/** Preserve logical response order across fragments and live notifications. */
export function createHistoryFragmentDecoder() {
  type Slot = { wire?: string; size: number };
  const order: Slot[] = [];
  const pending = new Map<string, { index: number; parts: string[]; slot: Slot }>();
  let total = 0;
  const clear = () => { pending.clear(); order.length = 0; total = 0; };
  const reserve = (size: number) => {
    if (total + size > 64 * 1024 * 1024 || order.length >= 1024) {
      throw new Error('History response exceeds the connection transfer budget');
    }
    total += size;
  };
  const drain = (): string[] => {
    const ready: string[] = [];
    while (order[0]?.wire !== undefined) {
      const slot = order.shift()!;
      total -= slot.size;
      ready.push(slot.wire!);
    }
    return ready;
  };
  return {
    clear,
    receive(wire: string): string[] {
      try {
        const message = JSON.parse(wire);
        if (message?.type !== 'history_fragment') {
          if (!order.length) return [wire];
          reserve(wire.length);
          order.push({ wire, size: wire.length });
          return drain();
        }
        const data = message.data;
        if (!data || typeof data.id !== 'string' || typeof data.text !== 'string'
            || !Number.isSafeInteger(data.index) || typeof data.final !== 'boolean') {
          throw new Error('Invalid history fragment');
        }
        let entry = pending.get(data.id);
        if (!entry) {
          if (data.index !== 0 || pending.size >= 8) throw new Error('Invalid history fragment order');
          const slot: Slot = { size: 0 };
          entry = { index: 0, parts: [], slot };
          pending.set(data.id, entry);
          order.push(slot);
        }
        if (entry.index !== data.index) throw new Error('Invalid history fragment order');
        reserve(data.text.length);
        entry.parts.push(data.text); entry.index++; entry.slot.size += data.text.length;
        if (data.final) {
          entry.slot.wire = entry.parts.join('');
          pending.delete(data.id);
        }
        return drain();
      } catch (error) {
        clear();
        throw error;
      }
    },
  };
}
