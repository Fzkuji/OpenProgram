/**
 * Attachment bytes of queued rows, kept in the composer's IndexedDB store so
 * a row with files survives a reload like a text row does. Chat attachments
 * travel inline with the turn (there is no upload before send), so the bytes
 * have to be kept locally until the row is sent.
 *
 * localStorage carries the row with `attachmentsStored`; a restored row waits
 * (`attachmentsPending`) until its files are read back, and never sends
 * without them.
 */
import {
  deleteQueuedAttachments,
  loadQueuedAttachments,
  saveQueuedAttachments,
  type StoredAttachments,
} from "@/components/chat/composer/attach/attach-idb";
import { queuedHasAttachments, snapshotQueuedAttachments, type QueuedAttachments } from "./queued-attachments";

export interface QueuedAttachmentBackend {
  save(rowId: string, data: StoredAttachments): Promise<void>;
  load(rowId: string): Promise<StoredAttachments | null>;
  remove(rowId: string): Promise<void>;
}

const indexedDbBackend: QueuedAttachmentBackend = {
  save: saveQueuedAttachments,
  load: loadQueuedAttachments,
  remove: deleteQueuedAttachments,
};
let backend = indexedDbBackend;

/** Tests swap in an in-memory store. */
export function setQueuedAttachmentBackend(next: QueuedAttachmentBackend | null): void {
  backend = next ?? indexedDbBackend;
}

type Row = QueuedAttachments & { id: string; attachmentsPending?: boolean };
type Queues = Record<string, Row[]>;

// Writes run one after another: a row removed for sending and restored after
// a failed write must end with its record present, not deleted.
let writes: Promise<void> = Promise.resolve();
function serial(write: () => Promise<void>): Promise<void> {
  writes = writes.then(write, write).catch(() => {});
  return writes;
}

function rowsWithFiles(queues: Queues): Map<string, Row> {
  const rows = new Map<string, Row>();
  for (const list of Object.values(queues)) {
    for (const row of list) if (queuedHasAttachments(row) || row.attachmentsPending) rows.set(row.id, row);
  }
  return rows;
}

/** Store the files of rows that gained or changed them; drop the files of
 *  rows that left the queue. Resolves once the writes are done. */
export function syncQueuedAttachments(previous: Queues, next: Queues): Promise<void> {
  const before = rowsWithFiles(previous);
  const after = rowsWithFiles(next);
  for (const [id, row] of after) {
    const old = before.get(id);
    if (!queuedHasAttachments(row) || (old && old.images === row.images && old.docs === row.docs)) continue;
    const data = { images: row.images ?? [], docs: row.docs ?? [] };
    void serial(() => backend.save(id, data));
  }
  for (const id of before.keys()) if (!after.has(id)) void serial(() => backend.remove(id));
  return writes;
}

/** Read a restored row's files back; null when they are gone. */
export async function restoreQueuedAttachments(rowId: string): Promise<Required<QueuedAttachments> | null> {
  await writes;
  const stored = await backend.load(rowId).catch(() => null);
  if (!stored || (!stored.images.length && !stored.docs.length)) return null;
  // The queue previews from data URLs; drop the object URLs the read made.
  for (const image of stored.images) {
    if (image.previewUrl?.startsWith("blob:")) {
      try { URL.revokeObjectURL(image.previewUrl); } catch { /* ignore */ }
    }
  }
  return snapshotQueuedAttachments(stored);
}
