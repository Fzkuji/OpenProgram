import type { PendingImage } from "@/components/chat/composer/attach/image/image-attach";
import type { PendingDoc } from "@/components/chat/composer/attach/strip/file-tiles";
import { buildAttachmentEnvelope } from "./attachment-marker";

export interface QueuedAttachments {
  images?: PendingImage[];
  docs?: PendingDoc[];
}

/** Queue owns its bytes; composer cleanup may revoke the original object URL. */
export function snapshotQueuedAttachments({ images = [], docs = [] }: QueuedAttachments) {
  return {
    images: images.map(image => ({
      ...image,
      attachment: { ...image.attachment },
      previewUrl: `data:${image.attachment.original_media_type || image.attachment.media_type};base64,${image.attachment.original_data ?? image.attachment.data}`,
    })),
    docs: docs.map(doc => ({ ...doc })),
  };
}

export function queuedHasAttachments(row: QueuedAttachments): boolean {
  return Boolean(row.images?.length || row.docs?.length);
}

export function queuedMessagePayload(row: QueuedAttachments & { text: string }) {
  const { mentions, imagesPayload, docsPayload } = buildAttachmentEnvelope(row.images ?? [], row.docs ?? []);
  return {
    text: [...mentions, row.text].filter(Boolean).join("\n\n"),
    attachments: [...imagesPayload, ...docsPayload],
    hasAttachments: queuedHasAttachments(row),
  };
}
