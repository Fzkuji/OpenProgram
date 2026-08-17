import type { PendingDoc } from "./file-tiles";
import type { PendingImage } from "./image-attach";

export interface StoredAttachments {
  images: PendingImage[];
  docs: PendingDoc[];
}

export interface AttachmentMergeChanges {
  /** The owner was cleared while its IndexedDB read was pending. */
  cleared?: boolean;
  /** Persisted entries removed before the pending read completed. */
  removedImageIds?: string[];
  removedDocIds?: string[];
}

export type AttachmentsByChat = Map<string, StoredAttachments>;

function currentAttachments(
  attachmentsByChat: AttachmentsByChat,
  chatKey: string,
): StoredAttachments {
  return attachmentsByChat.get(chatKey) ?? { images: [], docs: [] };
}

function replaceAttachments(
  attachmentsByChat: AttachmentsByChat,
  chatKey: string,
  patch: Partial<StoredAttachments>,
): StoredAttachments {
  const current = currentAttachments(attachmentsByChat, chatKey);
  const next = {
    images: patch.images ?? current.images,
    docs: patch.docs ?? current.docs,
  };
  attachmentsByChat.set(chatKey, next);
  return next;
}

export function addImagesForChat(
  attachmentsByChat: AttachmentsByChat,
  chatKey: string,
  images: PendingImage[],
): StoredAttachments {
  const current = currentAttachments(attachmentsByChat, chatKey);
  return replaceAttachments(attachmentsByChat, chatKey, {
    images: [...current.images, ...images],
  });
}

export function addDocsForChat(
  attachmentsByChat: AttachmentsByChat,
  chatKey: string,
  docs: PendingDoc[],
): StoredAttachments {
  const current = currentAttachments(attachmentsByChat, chatKey);
  return replaceAttachments(attachmentsByChat, chatKey, {
    docs: [...current.docs, ...docs],
  });
}

export function updateImageForChat(
  attachmentsByChat: AttachmentsByChat,
  chatKey: string,
  imageId: string,
  patch: Partial<PendingImage>,
): StoredAttachments {
  const current = currentAttachments(attachmentsByChat, chatKey);
  return replaceAttachments(attachmentsByChat, chatKey, {
    images: current.images.map((image) =>
      image.id === imageId ? { ...image, ...patch } : image,
    ),
  });
}

export function updateDocForChat(
  attachmentsByChat: AttachmentsByChat,
  chatKey: string,
  docId: string,
  patch: Partial<PendingDoc>,
): StoredAttachments {
  const current = currentAttachments(attachmentsByChat, chatKey);
  return replaceAttachments(attachmentsByChat, chatKey, {
    docs: current.docs.map((doc) =>
      doc.id === docId ? { ...doc, ...patch } : doc,
    ),
  });
}

export function removeImageForChat(
  attachmentsByChat: AttachmentsByChat,
  chatKey: string,
  imageId: string,
): StoredAttachments {
  const current = currentAttachments(attachmentsByChat, chatKey);
  return replaceAttachments(attachmentsByChat, chatKey, {
    images: current.images.filter((image) => image.id !== imageId),
  });
}

export function removeDocForChat(
  attachmentsByChat: AttachmentsByChat,
  chatKey: string,
  docId: string,
): StoredAttachments {
  const current = currentAttachments(attachmentsByChat, chatKey);
  return replaceAttachments(attachmentsByChat, chatKey, {
    docs: current.docs.filter((doc) => doc.id !== docId),
  });
}

export function setAttachmentsForChat(
  attachmentsByChat: AttachmentsByChat,
  chatKey: string,
  attachments: StoredAttachments,
): StoredAttachments {
  const next = {
    images: attachments.images,
    docs: attachments.docs,
  };
  attachmentsByChat.set(chatKey, next);
  return next;
}

function mergeById<T extends { id: string }>(
  persisted: T[],
  inMemory: T[],
): T[] {
  const merged = new Map<string, T>();
  for (const item of persisted) merged.set(item.id, item);
  for (const item of inMemory) merged.set(item.id, item);
  return [...merged.values()];
}

/** Merge an IndexedDB read with updates that arrived while it was pending. */
export function mergeAttachments(
  persisted: StoredAttachments,
  inMemory: StoredAttachments | undefined,
  changes: AttachmentMergeChanges = {},
): StoredAttachments {
  if (changes.cleared) {
    return inMemory ?? { images: [], docs: [] };
  }
  const merged = inMemory
    ? {
        images: mergeById(persisted.images, inMemory.images),
        docs: mergeById(persisted.docs, inMemory.docs),
      }
    : persisted;
  const removedImageIds = new Set(changes.removedImageIds ?? []);
  const removedDocIds = new Set(changes.removedDocIds ?? []);
  return {
    images: merged.images.filter((image) => !removedImageIds.has(image.id)),
    docs: merged.docs.filter((doc) => !removedDocIds.has(doc.id)),
  };
}
