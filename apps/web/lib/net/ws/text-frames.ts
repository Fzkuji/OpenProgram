/** Text frames waiting for the next paint, placed by offset into every view
 *  holding their row. A frame that shows one was lost asks for one read. */
import { applyBlockCitations, applyTextFrame, type TextFrame, type ViewRows } from "@/lib/chat/transcript/apply-transcript-update";
import { useSessionStore, type ChatMsg } from "@/lib/session-store";

let queued: TextFrame[] = [];
let raf = 0;
let onGap: (view: string) => void = () => {};

/** Who repairs a view that lost a frame. */
export function onTextFrameGap(handler: (view: string) => void): void {
  onGap = handler;
}

function viewOf(view: string): ViewRows {
  const store = useSessionStore.getState();
  return {
    order: store.messageOrder[view] ?? [],
    rows: store.rowsByView[view] ?? {},
    overlays: store.overlaysByView[view],
    heldFrames: store.heldFramesByView[view],
  };
}

function viewsHolding(rowId: string): string[] {
  const { rowsByView } = useSessionStore.getState();
  return Object.keys(rowsByView).filter((view) => rowsByView[view][rowId]);
}

/** Commit every queued frame now. */
export function flushTextFrames(): void {
  if (raf) {
    globalThis.cancelAnimationFrame?.(raf);
    raf = 0;
  }
  if (!queued.length) return;
  const frames = queued;
  queued = [];
  const next = new Map<string, ViewRows>();
  const gaps = new Set<string>();
  for (const frame of frames) {
    for (const view of viewsHolding(frame.rowId)) {
      const placed = applyTextFrame(next.get(view) ?? viewOf(view), frame);
      next.set(view, placed);
      if (placed.gap) gaps.add(view);
    }
  }
  const store = useSessionStore.getState();
  for (const [view, rows] of next) store.writeView(view, rows);
  for (const view of gaps) onGap(view);
}

export function queueTextFrame(frame: TextFrame): void {
  queued.push(frame);
  if (raf) return;
  const request = globalThis.requestAnimationFrame;
  if (typeof request !== "function") { flushTextFrames(); return; }
  raf = request(() => { raf = 0; flushTextFrames(); });
}

/** Set client-only fields on a row in every view holding it. */
export function patchHeldRow(rowId: string, fields: Partial<ChatMsg>): void {
  flushTextFrames();
  for (const view of viewsHolding(rowId)) {
    useSessionStore.getState().applyLocalChange(view, { kind: "local_patch", rowId, fields });
  }
}

/** Sources land on the block their `text_end` closes, after the text before it. */
export function placeCitations(rowId: string, itemId: string, citations: unknown[]): void {
  flushTextFrames();
  for (const view of viewsHolding(rowId)) {
    const before = viewOf(view);
    const after = applyBlockCitations(before, rowId, itemId, citations);
    if (after !== before) useSessionStore.getState().writeView(view, after);
  }
}
