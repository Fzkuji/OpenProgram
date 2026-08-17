/** Attachment marker protocol plus desktop attachment envelope construction. */

export interface LocalFilePathBridge {
  getPathForFile?(file: File): string;
}

interface PendingImageForEnvelope {
  sourcePath?: string;
  sizeBytes: number;
  attachment: {
    type: "image" | "document";
    data: string;
    media_type: string;
    filename?: string;
  };
}

interface PendingDocForEnvelope {
  filename: string;
  ext: string;
  sourcePath?: string;
  dataB64?: string | null;
  mediaType?: string;
  sizeBytes: number;
}

export interface ExtractedAttachmentMention {
  filename: string;
  ext: string;
  kb: string;
  count: string;
  path: string;
  previewPath: string;
}

const MARKER_UNSAFE = /[\[\]()]+/g;

export function safeMarkerText(value: string): string {
  return (value || "").trim().replace(MARKER_UNSAFE, "_") || "file";
}

export function localSourcePath(
  file: File,
  bridge: LocalFilePathBridge | null,
): string | undefined {
  try {
    const path = bridge?.getPathForFile?.(file);
    return path === "" || path === undefined ? undefined : path;
  } catch {
    return undefined;
  }
}

export function localAttachmentMention(
  filename: string,
  extension: string,
  sizeBytes: number,
  sourcePath: string,
): string {
  const safeName = safeMarkerText(filename);
  const safeExtension = safeMarkerText(extension || "file");
  const meta = `${safeExtension}, ${Math.max(1, Math.round(sizeBytes / 1024))} KB`;
  return `[attachment: ${safeName} (${meta}) @json ${JSON.stringify(sourcePath)}]`;
}

export const JSON_ATTACHED_MENTION =
  /\[attach(?:ed|ment):\s*([^()\[\]]+?)\s*\(([^,)]+),\s*([\d.]+)\s*KB(?:,\s*([^)]+))?\)\s*@json\s*("(?:\\.|[^"\\])*")(?:\s*@previewjson\s*("(?:\\.|[^"\\])*"))?\]/g;
const LEGACY_ATTACHED_MENTION =
  /\[attach(?:ed|ment):\s*([^()]+?)\s*\(([^,)]+),\s*([\d.]+)\s*KB(?:,\s*([^)]+))?\)(?:\s*@(?!json\s)\s*([^\]]+))?\]/g;

export function extractAttachmentMentions(content: string): {
  mentions: ExtractedAttachmentMention[];
  text: string;
} {
  const found: Array<{
    start: number;
    end: number;
    mention: ExtractedAttachmentMention;
  }> = [];
  for (const match of content.matchAll(JSON_ATTACHED_MENTION)) {
    let path = "";
    let previewPath = "";
    try {
      const decoded: unknown = JSON.parse(match[5] || '""');
      if (typeof decoded === "string") path = decoded;
      const decodedPreview: unknown = JSON.parse(match[6] || '""');
      if (typeof decodedPreview === "string") previewPath = decodedPreview;
    } catch {
      continue;
    }
    found.push({
      start: match.index,
      end: match.index + match[0].length,
      mention: {
        filename: match[1].trim() || "file",
        ext: match[2].trim(),
        kb: match[3],
        count: (match[4] || "").trim(),
        path,
        previewPath,
      },
    });
  }
  for (const match of content.matchAll(LEGACY_ATTACHED_MENTION)) {
    found.push({
      start: match.index,
      end: match.index + match[0].length,
      mention: {
        filename: match[1].trim() || "file",
        ext: match[2].trim(),
        kb: match[3],
        count: (match[4] || "").trim(),
        path: (match[5] || "").trim(),
        previewPath: "",
      },
    });
  }
  found.sort((left, right) => left.start - right.start);
  let text = content;
  for (const item of [...found].reverse()) {
    text = text.slice(0, item.start) + text.slice(item.end);
  }
  return { mentions: found.map((item) => item.mention), text };
}

export function buildAttachmentEnvelope(
  pendingImages: PendingImageForEnvelope[],
  pendingDocs: PendingDocForEnvelope[],
) {
  const mentions: string[] = [];
  const imagesPayload = pendingImages.map((image) => {
    const payload = {
      ...image.attachment,
      ...(image.sourcePath ? { source_path: image.sourcePath } : {}),
    };
    if (image.sourcePath) {
      const filename = image.attachment.filename || "image";
      const dot = filename.lastIndexOf(".");
      const extension = dot > 0
        ? filename.slice(dot + 1).toLowerCase()
        : image.attachment.media_type.split("/").pop() || "image";
      mentions.push(localAttachmentMention(
        filename,
        extension,
        image.sizeBytes,
        image.sourcePath,
      ));
    }
    return payload;
  });
  const docsPayload: Array<{
    type: "document";
    data: string;
    media_type: string;
    filename: string;
    source_path?: string;
  }> = [];
  for (const doc of pendingDocs) {
    const safeName = safeMarkerText(doc.filename);
    const safeExt = safeMarkerText(doc.ext || "file");
    const meta = `${safeExt}, ${Math.max(1, Math.round(doc.sizeBytes / 1024))} KB`;
    mentions.push(
      doc.sourcePath
        ? localAttachmentMention(doc.filename, doc.ext, doc.sizeBytes, doc.sourcePath)
        : doc.dataB64
          ? `[attachment: ${safeName} (${meta})]`
          : `[attachment: ${safeName} (${meta}, too large — not sent)]`,
    );
    if (doc.dataB64) {
      docsPayload.push({
        type: "document",
        data: doc.dataB64,
        media_type: doc.mediaType || "application/octet-stream",
        filename: doc.filename,
        ...(doc.sourcePath ? { source_path: doc.sourcePath } : {}),
      });
    }
  }
  return { mentions, imagesPayload, docsPayload };
}
