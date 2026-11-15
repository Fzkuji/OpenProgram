/**
 * Whether the canvas's key sheet is open (dag/rendering.md §13). The
 * ``?`` key (keys.ts) and the zoom column's button (components/chat/dag/dag-view.tsx)
 * both flip it, so it lives outside React.
 */

let _open = false;
const _subs = new Set<() => void>();

export function keySheetOpen(): boolean {
  return _open;
}

export function setKeySheet(open: boolean): void {
  if (open === _open) return;
  _open = open;
  _subs.forEach((fn) => fn());
}

export function subscribeKeySheet(fn: () => void): () => void {
  _subs.add(fn);
  return () => { _subs.delete(fn); };
}
