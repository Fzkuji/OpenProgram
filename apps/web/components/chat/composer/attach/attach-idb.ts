"use client";

/**
 * Per-session pending-attachment persistence in IndexedDB.
 *
 * Why IndexedDB and not localStorage: a pending image/doc carries its
 * full base64 bytes (often several MB). localStorage has a ~5MB total
 * cap and is synchronous — a couple of images would blow it. IndexedDB
 * is the browser-native (no dependency) store for large binary-ish
 * blobs, so unsent attachments survive a refresh too.
 *
 * One object store keyed by sessionId; the value is the serialized
 * pending lists for that session. The blob/previewUrl of an image is
 * NOT stored (object URLs die on reload) — only the base64 attachment
 * payload is, and the preview is rebuilt from it on load.
 */

import type { PendingImage } from "./image-attach";
import type { PendingDoc } from "./file-tiles";

const DB_NAME = "openprogram-composer";
const STORE = "attachments";
const DB_VERSION = 1;

// Closing an unsent tab is synchronous, while FileReader and IndexedDB
// callbacks may complete later. Keep a process-local tombstone so those
// callbacks cannot recreate attachment state for a closed owner.
const closedAttachmentOwners = new Set<string>();
const attachmentOwnerClosedListeners = new Set<(sessionId: string) => void>();

export function markAttachmentOwnerClosed(sessionId: string): void {
  if (closedAttachmentOwners.has(sessionId)) return;
  closedAttachmentOwners.add(sessionId);
  for (const listener of attachmentOwnerClosedListeners) {
    try { listener(sessionId); } catch { /* ignore */ }
  }
}

export function attachmentOwnerIsClosed(sessionId: string): boolean {
  return closedAttachmentOwners.has(sessionId);
}

export function onAttachmentOwnerClosed(
  listener: (sessionId: string) => void,
): () => void {
  attachmentOwnerClosedListeners.add(listener);
  return () => attachmentOwnerClosedListeners.delete(listener);
}

export interface StoredAttachments {
  images: PendingImage[];
  docs: PendingDoc[];
}

function openDb(): Promise<IDBDatabase | null> {
  return new Promise((resolve) => {
    if (typeof window === "undefined" || !window.indexedDB) {
      resolve(null);
      return;
    }
    try {
      const req = window.indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          db.createObjectStore(STORE);
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

function waitForTransaction(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve) => {
    tx.oncomplete = () => resolve();
    tx.onabort = () => resolve();
    tx.onerror = () => resolve();
  });
}

/** Strip the un-persistable / heavy-but-derivable bits before saving.
 *  ``previewUrl`` is an object URL (dead after reload) — drop it; it's
 *  rebuilt from the base64 ``attachment.data`` on load. ``loading``
 *  placeholders are skipped entirely (their bytes aren't ready). */
function serialize(a: StoredAttachments): StoredAttachments {
  return {
    images: a.images
      .filter((i) => !i.loading && i.attachment?.data)
      .map((i) => ({ ...i, previewUrl: null })),
    docs: a.docs.filter((d) => !d.loading && d.dataB64),
  };
}

export async function saveAttachments(
  sessionId: string,
  data: StoredAttachments,
): Promise<void> {
  if (attachmentOwnerIsClosed(sessionId)) return;
  const db = await openDb();
  if (!db) return;
  if (attachmentOwnerIsClosed(sessionId)) {
    db.close();
    return;
  }
  try {
    const payload = serialize(data);
    const tx = db.transaction(STORE, "readwrite");
    const store = tx.objectStore(STORE);
    if (!payload.images.length && !payload.docs.length) {
      store.delete(sessionId);
    } else {
      store.put(payload, sessionId);
    }
    await waitForTransaction(tx);
  } catch {
    /* ignore */
  } finally {
    db.close();
  }
}

export async function loadAttachments(
  sessionId: string,
): Promise<StoredAttachments> {
  if (attachmentOwnerIsClosed(sessionId)) return { images: [], docs: [] };
  const db = await openDb();
  if (!db) return { images: [], docs: [] };
  if (attachmentOwnerIsClosed(sessionId)) {
    db.close();
    return { images: [], docs: [] };
  }
  return new Promise((resolve) => {
    try {
      const tx = db.transaction(STORE, "readonly");
      const req = tx.objectStore(STORE).get(sessionId);
      req.onsuccess = () => {
        const v = req.result as StoredAttachments | undefined;
        db.close();
        if (!v || attachmentOwnerIsClosed(sessionId)) {
          resolve({ images: [], docs: [] });
          return;
        }
        // Rebuild preview object URLs from the persisted base64 so chips
        // render after a refresh.
        const images = (v.images || []).map((i) => {
          let previewUrl: string | null = null;
          try {
            if (i.attachment?.data) {
              const byteStr = atob(i.attachment.data);
              const bytes = new Uint8Array(byteStr.length);
              for (let k = 0; k < byteStr.length; k++) bytes[k] = byteStr.charCodeAt(k);
              const blob = new Blob([bytes], {
                type: i.attachment.media_type || "image/png",
              });
              previewUrl = URL.createObjectURL(blob);
            }
          } catch {
            previewUrl = null;
          }
          return { ...i, previewUrl, loading: false };
        });
        const docs = (v.docs || []).map((d) => ({ ...d, loading: false }));
        resolve({ images, docs });
      };
      req.onerror = () => {
        db.close();
        resolve({ images: [], docs: [] });
      };
    } catch {
      db.close();
      resolve({ images: [], docs: [] });
    }
  });
}

export async function deleteAttachments(sessionId: string): Promise<void> {
  markAttachmentOwnerClosed(sessionId);
  const db = await openDb();
  if (!db) return;
  try {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).delete(sessionId);
    await waitForTransaction(tx);
  } catch {
    /* ignore */
  } finally {
    db.close();
  }
}
