import type { QueuedMessage } from "./send-queue";
import { readPastedElements } from "./paste-elements.ts";

/**
 * Persistence for the send queue, so a message typed during a run survives a
 * reload, a closed window and an App restart until it is sent.
 *
 * Each window writes its own entry in localStorage and holds a Web Lock named
 * after it for as long as it lives. A window that starts adopts the entries
 * whose lock nobody holds (its own earlier incarnation after a reload, every
 * window after a restart) under one exclusive lock, so two windows never take
 * the same row and a row is never sent twice. A drain removes its row before
 * the socket write, so a crash can lose that one send but never repeat it.
 *
 * Attachment bytes never enter localStorage: a row with files is saved with
 * `attachmentsStored` and its bytes go to IndexedDB (queued-attachment-store).
 */

const KEY = "openprogram.send-queue.v2";
/** Steering receipts were kept per tab before ordinary rows were saved. */
const LEGACY_KEY = "openprogram.pending-steers.v1";
const WINDOW_LOCK = "openprogram.send-queue.window:";
const ADOPT_LOCK = "openprogram.send-queue.adopt";

type Queues = Record<string, QueuedMessage[]>;
type Saved = Record<string, Queues>;

const windowId = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
  ? crypto.randomUUID()
  : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;

function webLocks(): LockManager | null {
  try {
    return typeof navigator !== "undefined" && navigator.locks ? navigator.locks : null;
  } catch { return null; }
}

// Until this window holds its lock another window could take its entry for
// an orphan, so writes wait for the grant and then store the latest queues.
let lockHeld = false;
let unsaved: Queues | null = null;
const lockGranted = new Promise<void>((resolve) => {
  const locks = webLocks();
  if (!locks) { lockHeld = true; resolve(); return; }
  void locks.request(WINDOW_LOCK + windowId, () => {
    lockHeld = true;
    resolve();
    if (unsaved) saveQueues(unsaved);
    unsaved = null;
    // Held until the window goes away.
    return new Promise<never>(() => {});
  }).catch(() => { lockHeld = true; resolve(); });
});

function readSaved(): Saved {
  const saved: unknown = JSON.parse(localStorage.getItem(KEY) ?? "{}");
  return saved && typeof saved === "object" && !Array.isArray(saved) ? saved as Saved : {};
}

function writeSaved(saved: Saved): void {
  if (Object.keys(saved).length) localStorage.setItem(KEY, JSON.stringify(saved));
  else localStorage.removeItem(KEY);
}

/** The fields a row needs to be sent later, for the rows that can be stored. */
export function storableQueues(queues: Queues): Queues {
  return Object.fromEntries(Object.entries(queues).map(([sid, rows]) => [sid,
    rows.map(row => ({id:row.id,text:row.text,queuedAt:row.queuedAt,
        ...(row.textElements?.length ? {textElements:row.textElements} : {}),
        ...(row.images?.length || row.docs?.length || row.attachmentsPending ? {attachmentsStored:true} : {}),
        thinking:row.thinking,
        webSearchEnabled:row.webSearchEnabled,serviceTier:row.serviceTier,background:row.background,
        deliveryError:row.deliveryError,steerCommand:row.steerCommand,steerError:row.steerError})),
  ]).filter(([, rows]) => (rows as QueuedMessage[]).length));
}

export function saveQueues(queues: Queues): void {
  if (!lockHeld) { unsaved = queues; return; }
  try {
    const saved = readSaved();
    const own = storableQueues(queues);
    if (Object.keys(own).length) saved[windowId] = own;
    else delete saved[windowId];
    writeSaved(saved);
  } catch {
    // Storage can be disabled or full. Keep the live queue and immutable receipt.
  }
}

/** Validate stored rows. A steering command comes back unconfirmed: only the
 *  server's receipt says whether it was delivered. */
export function parseQueues(saved: unknown): Queues {
  if (!saved || typeof saved !== "object" || Array.isArray(saved)) return {};
  const queues: Queues = {};
  for (const [sid, rows] of Object.entries(saved)) {
    if (!Array.isArray(rows)) continue;
    const valid: QueuedMessage[] = [];
    for (const row of rows) {
      if (!row || typeof row.id !== "string" || typeof row.text !== "string"
        || !Number.isFinite(row.queuedAt) || typeof row.thinking !== "string"
        || typeof row.webSearchEnabled !== "boolean"
        || typeof row.background !== "boolean" || row.images?.length || row.docs?.length) continue;
      const command = row.steerCommand;
      if (command && (command.type !== "execution.command" || command.action !== "execution.steer"
        || typeof command.command_id !== "string" || typeof command.execution_id !== "string"
        || !Number.isInteger(command.expected_version) || command.payload?.message !== row.text)) continue;
      const held = ["cancelled", "ended"].includes(row.steerError) ? row.steerError : undefined;
      const textElements = readPastedElements(row.textElements);
      const { attachmentsStored, ...stored } = row;
      valid.push({...stored, textElements: textElements.length ? textElements : undefined,
        injecting:false, editing:false, deliveryError:row.deliveryError === true,
        ...(attachmentsStored === true ? { attachmentsPending: true } : {}),
        steerError: held ?? (command ? "unconfirmed" : undefined)});
    }
    if (valid.length) Object.defineProperty(queues, sid, {value:valid,enumerable:true,writable:true,configurable:true});
  }
  return queues;
}

function mergeInto(target: Queues, source: Queues): void {
  for (const [sid, rows] of Object.entries(source)) {
    const seen = new Set((target[sid] ?? []).map(row => row.id));
    const fresh = rows.filter(row => !seen.has(row.id));
    if (fresh.length) {
      Object.defineProperty(target, sid, {value:[...(target[sid] ?? []), ...fresh],
        enumerable:true,writable:true,configurable:true});
    }
  }
}

/** Take over the rows of windows that are gone, oldest first. Without Web
 *  Locks no window can tell a live one from a gone one, so nothing is taken. */
export async function adoptOrphanedQueues(): Promise<Queues> {
  const locks = webLocks();
  if (!locks) return {};
  await lockGranted;
  try {
    return await locks.request(ADOPT_LOCK, async () => {
      const alive = new Set(((await locks.query()).held ?? []).map(lock => lock.name));
      const adopted: Queues = {};
      try {
        const legacy = sessionStorage.getItem(LEGACY_KEY);
        if (legacy) {
          mergeInto(adopted, parseQueues(JSON.parse(legacy)));
          sessionStorage.removeItem(LEGACY_KEY);
        }
      } catch { /* unreadable legacy receipts stay where they are */ }
      try {
        const saved = readSaved();
        const orphans = Object.keys(saved)
          .filter(id => id !== windowId && !alive.has(WINDOW_LOCK + id))
          .map(id => ({ id, queues: parseQueues(saved[id]) }))
          .sort((a, b) => firstQueuedAt(a.queues) - firstQueuedAt(b.queues));
        for (const orphan of orphans) {
          mergeInto(adopted, orphan.queues);
          delete saved[orphan.id];
        }
        if (orphans.length) {
          // Adopted rows become this window's before the lock is released.
          const own = parseQueues(saved[windowId]);
          mergeInto(own, adopted);
          if (Object.keys(own).length) saved[windowId] = storableQueues(own);
          writeSaved(saved);
        }
      } catch { /* disabled storage: nothing to adopt */ }
      return adopted;
    });
  } catch { return {}; }
}

function firstQueuedAt(queues: Queues): number {
  let first = Infinity;
  for (const rows of Object.values(queues)) for (const row of rows) first = Math.min(first, row.queuedAt);
  return first;
}
