import type { CanvasLayout } from "./canvas-layout";

/** Layouts to return to, newest last. Each entry is found again by any tab
 *  or group it involved, so "Return to previous layout" still works after a
 *  pane was pulled out into its own tab or a lone tab became a group. */
const entries: Array<{ keys: Set<string>; layout: CanvasLayout }> = [];
const LIMIT = 30;

export function rememberCanvas(keys: Iterable<string>, layout: CanvasLayout) {
  entries.push({ keys: new Set(keys), layout });
  if (entries.length > LIMIT) entries.shift();
}

export function takePreviousCanvas(keys: Iterable<string>): CanvasLayout | undefined {
  const wanted = [...keys];
  for (let i = entries.length - 1; i >= 0; i--) {
    if (wanted.some(key => entries[i].keys.has(key))) return entries.splice(i, 1)[0].layout;
  }
  return undefined;
}
