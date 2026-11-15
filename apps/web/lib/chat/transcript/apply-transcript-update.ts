/** The changes of one numbered transcript update, applied to one view's rows.
 *
 *  Pure: it returns the view's next rows and says what the caller must do, so
 *  a test feeds it the same frames the server sends. No row is ever created
 *  from a change that names a row this view does not hold. */
import type { WireTurn } from "../history/history-window.ts";
import type { ChatMsg } from "../../session-store/types.ts";
import { isUnsentUserRow } from "../../session-store/messages/pending-sends.ts";
import { isSettled } from "./row-status.ts";
import { withToolBodies } from "./turn-rows.ts";
import type { Row, RowKind, TranscriptChange, Turn } from "./wire-types.generated.ts";

export interface ViewRows {
  order: readonly string[];
  rows: Readonly<Record<string, ChatMsg>>;
  /** Text frames that came ahead of the block before them, by row. */
  heldFrames?: Readonly<Record<string, readonly TextFrame[]>>;
  /** Fields shown ahead of the server, by row. They are never part of a row. */
  overlays?: Readonly<Record<string, readonly RowOverlay[]>>;
}

export interface UpdateContext {
  /** Whether the window holds this turn. */
  holdsTurn: (turnId: string) => boolean;
  /** Whether the window reaches the newest turn, so a new one is drawn. */
  atLatest: boolean;
  /** Whether the window itself lists this turn. A turn held only through its
   *  acknowledged opener row is one the window has yet to note. */
  windowListsTurn?: (turnId: string) => boolean;
}

export type UpdateEffect =
  | { kind: "read_anchor" }
  | { kind: "read_latest" }
  | { kind: "read_turn"; turnId: string }
  /** A row carrying this id arrived: a steer waiting on it is delivered. */
  | { kind: "settle_steer"; commandId: string };

export interface UpdateResult extends ViewRows {
  effects: UpdateEffect[];
  /** Turns now drawn, for the window to hold. */
  addedTurns: WireTurn[];
  /** Rows now drawn inside a held turn, by turn, for the window to hold. */
  addedRows: { turnId: string; afterRowId: string | null; rowId: string }[];
  /** New turns past the window's end: only the total grows. */
  turnsPastWindow: number;
}

/** A turn is held when the window has it or its opener row is drawn: a turn the
 *  old frames drew, and any turn of a sub-agent view, has no window entry. */
export function viewHoldsTurn(
  window: { holdsTurn: (turnId: string) => boolean } | undefined,
  rows: Readonly<Record<string, ChatMsg>>,
): UpdateContext["holdsTurn"] {
  return (turnId) => !!window?.holdsTurn(turnId) || !!rows[turnId];
}

/** Fields the server does not send and a server row never clears. */
const FRAME_ONLY_FIELDS = ["retryStatus", "autoPreviewFiles"] as const;

function asRow(row: Row): ChatMsg {
  return withToolBodies(row as unknown as ChatMsg);
}

export function withFrameOnlyFields(next: ChatMsg, held: ChatMsg | undefined): ChatMsg {
  if (!held) return next;
  const kept: Partial<ChatMsg> = {};
  for (const field of FRAME_ONLY_FIELDS) {
    if (held[field] !== undefined) kept[field] = held[field] as never;
  }
  return Object.keys(kept).length ? { ...next, ...kept } : next;
}

/** Where an item already sits. A call root is named by its path, and a tree
 *  with no path stands in for the running root of the same function. */
function heldItemAt(items: readonly Record<string, unknown>[], item: Record<string, unknown>, list: string): number {
  if (list !== "callRoots") return items.findIndex((held) => held.id === item.id);
  if (item.path) return items.findIndex((held) => held.path === item.path);
  return items.findIndex((held) => held.status === "running" && held.name === item.name);
}

function upsertItem(list: readonly Record<string, unknown>[] | undefined, item: Record<string, unknown>, name: string) {
  const items = [...(list ?? [])];
  const at = heldItemAt(items, item, name);
  if (at >= 0) items[at] = { ...items[at], ...item };
  else items.push(item);
  return items;
}

/** A block goes where the server says; past the end, one before it is missing. */
function placeBlock(blocks: ChatMsg["blocks"], item: Record<string, unknown>, index: number | undefined): ChatMsg["blocks"] | null {
  const placed = [...(blocks ?? [])];
  if (index === undefined || index > placed.length) return null;
  placed[index] = item as never;
  return placed;
}

/** Where a turn's rows go: before local rows no server turn has claimed. */
function tailInsertAt(order: readonly string[], rows: Readonly<Record<string, ChatMsg>>): number {
  let at = order.length;
  while (at > 0 && rows[order[at - 1]] && isTailRow(rows[order[at - 1]])) at -= 1;
  return at;
}

function isTailRow(row: ChatMsg): boolean {
  return row.localAnchor === "tail" || isUnsentUserRow(row);
}

/** The local row a server row with this request id stands in for. */
function localRowFor(row: ChatMsg, rows: Readonly<Record<string, ChatMsg>>): ChatMsg | undefined {
  if (!row.clientRequestId) return undefined;
  for (const id in rows) {
    const held = rows[id];
    if (held.localAnchor && held.id !== row.id && held.clientRequestId === row.clientRequestId) return held;
  }
  return undefined;
}

/** A notice drawn after a row keeps its place when that row changes id. */
function reanchorNotices(rows: Record<string, ChatMsg>, fromId: string, toId: string): void {
  for (const id in rows) {
    const anchor = rows[id].localAnchor;
    if (typeof anchor === "object" && anchor.afterRowId === fromId) rows[id] = { ...rows[id], localAnchor: { afterRowId: toId } };
  }
}

/** The server's row arrived: the local row of the same send goes. */
function claimLocalRow(row: ChatMsg, rows: Record<string, ChatMsg>, order: string[], out: UpdateResult): void {
  if (row.clientRequestId && !out.effects.some((effect) => effect.kind === "settle_steer" && effect.commandId === row.clientRequestId)) {
    out.effects.push({ kind: "settle_steer", commandId: row.clientRequestId });
  }
  const local = localRowFor(row, rows);
  if (!local) return;
  delete rows[local.id];
  order.splice(order.indexOf(local.id), 1);
  reanchorNotices(rows, local.id, row.id);
}

function insertAfter(order: string[], afterRowId: string | null, rowId: string): void {
  const at = afterRowId === null ? -1 : order.indexOf(afterRowId);
  order.splice(at >= 0 ? at + 1 : order.length, 0, rowId);
}

function applyTurn(turn: Turn, rows: Record<string, ChatMsg>, order: string[], context: UpdateContext, out: UpdateResult): void {
  const wire = turn.rows.map(asRow);
  for (const row of wire) claimLocalRow(row, rows, order, out);
  if (context.holdsTurn(turn.id)) {
    let previous: string | null = null;
    for (const row of wire) {
      if (rows[row.id]) rows[row.id] = withFrameOnlyFields(row, rows[row.id]);
      else if (previous !== null && context.windowListsTurn?.(turn.id) === false) {
        // An acknowledged opener holds its turn before the turn's other rows exist here.
        rows[row.id] = row;
        insertAfter(order, previous, row.id);
      }
      if (rows[row.id]) previous = row.id;
    }
    if (context.windowListsTurn && !context.windowListsTurn(turn.id)) out.addedTurns.push(turn as unknown as WireTurn);
    return;
  }
  if (!context.atLatest) {
    out.turnsPastWindow += 1;
    return;
  }
  let at = tailInsertAt(order, rows);
  for (const row of wire) {
    if (rows[row.id]) {
      rows[row.id] = withFrameOnlyFields(row, rows[row.id]);
      continue;
    }
    rows[row.id] = row;
    order.splice(at, 0, row.id);
    at += 1;
  }
  out.addedTurns.push(turn as unknown as WireTurn);
}

function missItem(turnId: string, context: UpdateContext, out: UpdateResult): void {
  if (context.holdsTurn(turnId) && !out.effects.some((effect) => effect.kind === "read_turn" && effect.turnId === turnId)) {
    out.effects.push({ kind: "read_turn", turnId });
  }
}

/** One text or thinking frame, placed by its offset into the block it names. */
export interface TextFrame {
  turnId?: string;
  rowId: string;
  itemId: string;
  itemIndex: number;
  offset: number;
  type: "text" | "thinking";
  text: string;
  /** What the row's flat text takes before this frame's text: the blank line
   *  between two passages a tool call came between (the server's `reply_text`). */
  separator?: string;
}

const JOIN_FIELD = { text: "content", thinking: "thinking" } as const;

type FramePlacement = { row: ChatMsg; gap: boolean };

/** A frame whose block is reachable: extend it, or say one was lost. */
function placeFrame(held: ChatMsg, frame: TextFrame): FramePlacement {
  const blocks = [...(held.blocks ?? [])];
  const block = blocks[frame.itemIndex];
  const current = block && block.type === frame.type ? block.text ?? "" : "";
  if (frame.offset > current.length) return { row: held, gap: true };
  const fresh = frame.text.slice(current.length - frame.offset);
  if (!fresh) return { row: held, gap: false };
  blocks[frame.itemIndex] = { ...(block ?? { type: frame.type }), id: block?.id ?? frame.itemId, text: current + fresh } as never;
  const field = JOIN_FIELD[frame.type];
  // The frame that opens a block names what separates it from the text before.
  const lead = frame.separator && !current && frame.offset === 0 ? frame.separator : "";
  // New output ends the wait a retry notice was showing.
  return { row: { ...held, blocks, retryStatus: undefined, [field]: `${(held[field] as string | undefined) ?? ""}${lead}${fresh}` }, gap: false };
}

/** Apply a frame to a view. A frame past the block's end means one was lost and
 *  the row needs a read; a frame already covered changes nothing. A frame for a
 *  block further on than the row's end waits for the blocks before it, which
 *  come on the publisher's next tick. A row this view does not hold creates nothing. */
export function applyTextFrame(view: ViewRows, frame: TextFrame): ViewRows & { gap: boolean } {
  const held = view.rows[frame.rowId];
  if (!held || isSettled(held.status) || view.overlays?.[frame.rowId]?.some(isStopOverlay)) return { ...view, gap: false };
  if (frame.itemIndex > (held.blocks?.length ?? 0)) {
    const waiting = [...(view.heldFrames?.[frame.rowId] ?? []), frame];
    return { ...view, heldFrames: { ...view.heldFrames, [frame.rowId]: waiting }, gap: false };
  }
  const { row, gap } = placeFrame(held, frame);
  return { ...view, rows: row === held ? view.rows : { ...view.rows, [frame.rowId]: row }, gap };
}

/** Sources land on the text block their event closes; its text is untouched. */
export function applyBlockCitations(view: ViewRows, rowId: string, itemId: string, citations: unknown[]): ViewRows {
  const held = view.rows[rowId];
  const at = held?.blocks?.findIndex((block) => block.id === itemId) ?? -1;
  if (!held?.blocks || at < 0) return view;
  const blocks = [...held.blocks];
  blocks[at] = { ...blocks[at], citations } as never;
  return { ...view, rows: { ...view.rows, [rowId]: { ...held, blocks } } };
}

function isStopOverlay(overlay: RowOverlay): boolean {
  return overlay.fields.status === "cancelled";
}

/** Frames waiting on this row that its blocks now reach, oldest first. A finished
 *  row needs none of them. True when one of them turned out to be lost. */
function releaseHeldFrames(rowId: string, rows: Record<string, ChatMsg>, heldFrames: Record<string, readonly TextFrame[]>): boolean {
  let waiting = heldFrames[rowId];
  if (!waiting) return false;
  let gap = false;
  if (!isSettled(rows[rowId].status)) {
    for (let ready = true; ready;) {
      ready = false;
      const later: TextFrame[] = [];
      for (const frame of waiting) {
        if (frame.itemIndex > (rows[rowId].blocks?.length ?? 0)) { later.push(frame); continue; }
        const placed = placeFrame(rows[rowId], frame);
        rows[rowId] = placed.row;
        gap = gap || placed.gap;
        ready = true;
      }
      waiting = later;
    }
  } else waiting = [];
  if (waiting.length) heldFrames[rowId] = waiting;
  else delete heldFrames[rowId];
  return gap;
}

/** A row only this browser holds. The kind is required: dispatch meets no row without one. */
export type LocalRow = ChatMsg & { rowKind: RowKind };

/** A change only this browser knows about: rows it drew before the server
 *  named them, and fields it shows ahead of the server until a stated event. */
export type LocalChange =
  | { kind: "local_append"; row: LocalRow; afterRowId?: string | null }
  | { kind: "local_replace"; rowId: string; row: ChatMsg }
  | { kind: "local_patch"; rowId: string; fields: Partial<ChatMsg> }
  | { kind: "local_remove"; rowId: string }
  | { kind: "overlay_set"; rowId: string; fields: Partial<ChatMsg>; clear: readonly OverlayClear[] }
  | { kind: "overlay_clear"; rowId: string }
  | { kind: "overlay_read_landed" }
  | { kind: "overlay_command_rejected"; commandId: string };

/** What ends an overlay, from the Optimistic writes table. Any one is enough. */
export type OverlayClear =
  | "until_terminal"
  | "until_read"
  | "until_row_gone"
  | "until_field_matches"
  | { until_command_rejected: string };

export interface RowOverlay {
  fields: Partial<ChatMsg>;
  clear: readonly OverlayClear[];
}

export type OverlayEvent = { read: true } | { rejected: string };

function sameValue(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length
    && keys.every((key) => sameValue((a as Record<string, unknown>)[key], (b as Record<string, unknown>)[key]));
}

function sameFields(a: Partial<ChatMsg>, b: Partial<ChatMsg>): boolean {
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every((key) => key in b);
}

function overlayEnds(overlay: RowOverlay, row: ChatMsg, event: OverlayEvent | undefined): boolean {
  return overlay.clear.some((clear) => {
    if (typeof clear === "object") return !!event && "rejected" in event && event.rejected === clear.until_command_rejected;
    switch (clear) {
      case "until_terminal": return isSettled(row.status);
      case "until_read": return !!event && "read" in event;
      case "until_field_matches":
        return Object.entries(overlay.fields).every(([field, value]) => sameValue(row[field as keyof ChatMsg], value));
      case "until_row_gone": return false; // a gone row takes every overlay with it
    }
  });
}

/** Overlays that still stand, each checked against the server's row. */
export function standingOverlays(
  overlays: ViewRows["overlays"], rows: ViewRows["rows"], event?: OverlayEvent,
): ViewRows["overlays"] {
  if (!overlays) return overlays;
  const standing: Record<string, readonly RowOverlay[]> = {};
  for (const [rowId, held] of Object.entries(overlays)) {
    const row = rows[rowId];
    const kept = row ? held.filter((overlay) => !overlayEnds(overlay, row, event)) : [];
    if (kept.length) standing[rowId] = kept;
  }
  return Object.keys(standing).length ? standing : undefined;
}

/** Stop draws a row as cancelled until it ends, or its cancel is rejected. */
export function stopOverlay(rowId: string, also: readonly OverlayClear[] = []): LocalChange {
  return { kind: "overlay_set", rowId, fields: { status: "cancelled" }, clear: ["until_terminal", ...also] };
}

/** A row as it is drawn: the server's fields with any overlay on top. */
export function drawnRow(view: ViewRows, rowId: string): ChatMsg | undefined {
  const row = view.rows[rowId];
  const overlays = view.overlays?.[rowId];
  return row && overlays ? withOverlays(row, overlays) : row;
}

export function withOverlays(row: ChatMsg, overlays: readonly RowOverlay[]): ChatMsg {
  return Object.assign({}, row, ...overlays.map((overlay) => overlay.fields)) as ChatMsg;
}

export function applyLocalChange(view: ViewRows, change: LocalChange): ViewRows {
  const rows: Record<string, ChatMsg> = { ...view.rows };
  const order = [...view.order];
  let overlays = view.overlays;
  let event: OverlayEvent | undefined;
  switch (change.kind) {
    case "local_append":
      if (rows[change.row.id]) break;
      rows[change.row.id] = { ...change.row, localAnchor: change.afterRowId === undefined || change.afterRowId === null ? "tail" : { afterRowId: change.afterRowId } };
      if (change.afterRowId === undefined || change.afterRowId === null) order.push(change.row.id);
      else insertAfter(order, change.afterRowId, change.row.id);
      break;
    case "local_replace": {
      const at = order.indexOf(change.rowId);
      if (at < 0) break;
      delete rows[change.rowId];
      rows[change.row.id] = change.row;
      if (order.includes(change.row.id)) order.splice(at, 1);
      else order[at] = change.row.id;
      if (change.row.id !== change.rowId) reanchorNotices(rows, change.rowId, change.row.id);
      break;
    }
    case "local_patch":
      if (rows[change.rowId]) rows[change.rowId] = { ...rows[change.rowId], ...change.fields };
      break;
    case "local_remove":
      if (!rows[change.rowId]) break;
      delete rows[change.rowId];
      order.splice(order.indexOf(change.rowId), 1);
      break;
    case "overlay_set":
      if (rows[change.rowId]) {
        // A newer value for the same fields stands in for the older one.
        const others = (overlays?.[change.rowId] ?? []).filter((overlay) => !sameFields(overlay.fields, change.fields));
        overlays = { ...overlays, [change.rowId]: [...others, { fields: change.fields, clear: change.clear }] };
      }
      break;
    case "overlay_clear":
      if (overlays?.[change.rowId]) {
        const { [change.rowId]: _cleared, ...rest } = overlays;
        overlays = rest;
      }
      break;
    case "overlay_read_landed":
      event = { read: true };
      break;
    case "overlay_command_rejected":
      event = { rejected: change.commandId };
      break;
    default: {
      const unknownChange: never = change;
      console.warn("[transcript] unknown local change", unknownChange);
    }
  }
  return { ...view, order, rows, overlays: standingOverlays(overlays, rows, event) };
}

/** A read, applied to a view. Local rows survive it: a send or placeholder at
 *  the tail, a notice after the row it followed when that row is still drawn.
 *  A local row the read answers by request id goes, and steers it names are
 *  reported. */
export function applyReadRows(view: ViewRows, loaded: readonly ChatMsg[]): { rows: ChatMsg[]; steers: string[] } {
  const steers = new Set<string>();
  const answered = new Map<string, string>();
  for (const row of loaded) {
    if (!row.clientRequestId || row.localAnchor) continue;
    steers.add(row.clientRequestId);
    answered.set(row.clientRequestId, row.id);
  }
  const answers = (row: ChatMsg) => !!row.localAnchor && !!row.clientRequestId && answered.has(row.clientRequestId);
  const rows = loaded.filter((row) => !answers(row));
  const drawn = new Set(rows.map((row) => row.id));
  const renamed = new Map<string, string>();
  const tail: ChatMsg[] = [];
  const notices: ChatMsg[] = [];
  for (const id of view.order) {
    const row = view.rows[id];
    if (!row?.localAnchor) continue;
    if (answers(row)) { renamed.set(row.id, answered.get(row.clientRequestId as string) as string); continue; }
    if (drawn.has(id)) continue;
    (row.localAnchor === "tail" ? tail : notices).push(row);
  }
  for (const notice of notices) {
    const anchor = notice.localAnchor as { afterRowId: string };
    const target = renamed.get(anchor.afterRowId) ?? anchor.afterRowId;
    const at = rows.findIndex((row) => row.id === target);
    if (at >= 0) rows.splice(at + 1, 0, target === anchor.afterRowId ? notice : { ...notice, localAnchor: { afterRowId: target } });
  }
  return { rows: [...rows, ...tail], steers: [...steers] };
}

/** A null takes the field off the row: a read leaves out an empty field. */
function patched(row: ChatMsg, fields: Record<string, unknown>): ChatMsg {
  const next: Record<string, unknown> = { ...row };
  for (const [field, value] of Object.entries(fields)) {
    if (value === null) delete next[field];
    else next[field] = value;
  }
  return "tools" in fields ? asRow(next as never) : next as unknown as ChatMsg;
}

export function applyTranscriptChanges(view: ViewRows, changes: readonly TranscriptChange[], context: UpdateContext): UpdateResult {
  const rows: Record<string, ChatMsg> = { ...view.rows };
  const order = [...view.order];
  const heldFrames: Record<string, readonly TextFrame[]> = { ...view.heldFrames };
  const out: UpdateResult = { ...view, order, rows, effects: [], addedTurns: [], addedRows: [], turnsPastWindow: 0 };
  const release = (rowId: string, turnId: string) => {
    if (rows[rowId] && releaseHeldFrames(rowId, rows, heldFrames)) missItem(turnId, context, out);
  };
  for (const change of changes) {
    switch (change.kind) {
      case "turn_upsert":
        applyTurn(change.turn, rows, order, context, out);
        for (const row of change.turn.rows) release(row.id as string, change.turn.id);
        break;
      case "row_upsert": {
        const row = asRow(change.row);
        claimLocalRow(row, rows, order, out);
        if (rows[row.id]) rows[row.id] = withFrameOnlyFields(row, rows[row.id]);
        else if (context.holdsTurn(change.turnId)) {
          rows[row.id] = row;
          insertAfter(order, change.afterRowId, row.id);
          out.addedRows.push({ turnId: change.turnId, afterRowId: change.afterRowId, rowId: row.id });
        }
        release(row.id, change.turnId);
        break;
      }
      case "item_upsert": {
        const held = rows[change.rowId];
        if (!held) { missItem(change.turnId, context, out); break; }
        if (change.list === "blocks") {
          const blocks = placeBlock(held.blocks, change.item, change.index);
          if (!blocks) { missItem(change.turnId, context, out); break; }
          rows[change.rowId] = { ...held, blocks };
          release(change.rowId, change.turnId);
          break;
        }
        const field = change.list as keyof ChatMsg;
        rows[change.rowId] = { ...held, [field]: upsertItem(held[field] as never, change.item, change.list) };
        break;
      }
      case "row_patch": {
        const held = rows[change.rowId];
        if (!held) { missItem(change.turnId, context, out); break; }
        rows[change.rowId] = patched(held, change.fields);
        break;
      }
      case "window_stale":
        out.effects.push({ kind: change.reason === "branch" ? "read_latest" : "read_anchor" });
        break;
      default: {
        const unknownChange: never = change;
        console.warn("[transcript] unknown change", unknownChange);
      }
    }
  }
  out.heldFrames = Object.keys(heldFrames).length ? heldFrames : undefined;
  out.overlays = standingOverlays(view.overlays, rows);
  return out;
}
