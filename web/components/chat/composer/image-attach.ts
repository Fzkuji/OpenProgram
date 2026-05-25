/**
 * Image-attachment helpers for the chat composer.
 *
 * Sourcing: clipboard paste, drag-drop, file picker. All three funnel
 * into ``readImageFile`` → ``ChatAttachment`` then onto the chat WS
 * payload. Backend (``TurnRequest.attachments``) accepts the same
 * shape on the Python side — see ``openprogram/agent/dispatcher.py``.
 *
 * Modeled on claude-code's ``imagePaste.ts`` but stripped down to the
 * browser case: no platform detection, no resizer, no terminal escape
 * handling. The base64 + MIME conversion is the only piece we share.
 */

import type { ChatAttachment } from "./legacy-send";

/** Max single-image size before we refuse the attach. 5 MiB matches
 *  what most LLM provider APIs accept inline; larger files should be
 *  uploaded out-of-band first. */
export const MAX_IMAGE_BYTES = 5 * 1024 * 1024;

export const ACCEPTED_IMAGE_MIME = new Set([
  "image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp",
]);

/** Max single-text-file size that gets inlined via a paste token on
 *  drag-drop. Larger files would push past the long-paste token's
 *  practical readability limit anyway. */
export const MAX_TEXT_FILE_BYTES = 256 * 1024;

/** Mime / extension heuristics for "this drop is text content" — used
 *  by ``readDroppedNonImage`` to decide between text inlining and
 *  silent skip. Extensions are lower-cased without the leading dot. */
const TEXT_MIME_PREFIXES = ["text/", "application/json", "application/xml"];
const TEXT_EXTENSIONS = new Set([
  "txt", "md", "markdown", "rst", "log", "csv", "tsv",
  "json", "yaml", "yml", "toml", "ini", "conf", "cfg",
  "xml", "html", "htm", "css",
  "py", "ts", "tsx", "js", "jsx", "mjs", "cjs",
  "go", "rs", "java", "kt", "swift", "rb", "php", "cs",
  "c", "h", "cc", "cpp", "hpp", "hh", "m", "mm",
  "sh", "bash", "zsh", "fish", "ps1",
  "sql", "graphql", "proto",
  "Dockerfile", "Makefile",
]);

function looksLikeText(file: File | Blob, filename: string): boolean {
  if (file.type) {
    for (const p of TEXT_MIME_PREFIXES) {
      if (file.type.startsWith(p)) return true;
    }
  }
  const ext = filename.includes(".")
    ? filename.split(".").pop()!.toLowerCase()
    : filename;
  if (TEXT_EXTENSIONS.has(ext)) return true;
  if (TEXT_EXTENSIONS.has(filename)) return true;  // Dockerfile, Makefile
  return false;
}

export interface PendingImage {
  /** Stable id used by the chip row + remove callback. */
  id: string;
  /** Object URL for the thumbnail (revoke on remove). */
  previewUrl: string;
  attachment: ChatAttachment;
  sizeBytes: number;
}

/** Read a ``File`` / ``Blob`` into a PendingImage, base64-encoded.
 *  Rejects on unsupported MIME or oversize. */
export function readImageFile(file: File | Blob,
                              filename?: string): Promise<PendingImage> {
  return new Promise((resolve, reject) => {
    const mime = file.type || "application/octet-stream";
    if (!ACCEPTED_IMAGE_MIME.has(mime)) {
      reject(new Error(`unsupported image type: ${mime}`));
      return;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      reject(new Error(
        `image too large: ${(file.size / 1024 / 1024).toFixed(1)}MB ` +
        `(max ${MAX_IMAGE_BYTES / 1024 / 1024}MB)`,
      ));
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("read failed"));
    reader.onload = () => {
      const result = reader.result;
      if (typeof result !== "string") {
        reject(new Error("FileReader returned non-string"));
        return;
      }
      // ``result`` is "data:<mime>;base64,<payload>". Split off the
      // payload — backend's ImageContent.data is raw base64.
      const comma = result.indexOf(",");
      const data = comma >= 0 ? result.slice(comma + 1) : result;
      const previewUrl = URL.createObjectURL(file);
      resolve({
        id: cryptoRandom(),
        previewUrl,
        sizeBytes: file.size,
        attachment: {
          type: "image",
          data,
          media_type: mime,
          ...(filename ? { filename } : {}),
        },
      });
    };
    reader.readAsDataURL(file);
  });
}

/** Read a single dropped file as UTF-8 text if it looks like a
 *  text-y file under :data:`MAX_TEXT_FILE_BYTES`. Returns the content
 *  + filename, or ``null`` for non-text / oversize / read errors. */
export async function readDroppedTextFile(
  file: File,
): Promise<{ filename: string; content: string } | null> {
  const filename = file.name || "dropped";
  if (!looksLikeText(file, filename)) return null;
  if (file.size > MAX_TEXT_FILE_BYTES) return null;
  try {
    const content = await file.text();
    return { filename, content };
  } catch {
    return null;
  }
}

/** Walk a DataTransferItemList from a paste / drop event and read
 *  every image item it contains. */
export async function collectImagesFromTransfer(
  items: DataTransferItemList | DataTransfer,
): Promise<PendingImage[]> {
  const out: PendingImage[] = [];
  const list: DataTransferItem[] = [];
  // Both DataTransfer and DataTransferItemList expose ``items``; the
  // type union here lets callers pass either.
  const raw = ("items" in items ? items.items : items) as DataTransferItemList;
  for (let i = 0; i < raw.length; i++) list.push(raw[i]);
  for (const item of list) {
    if (item.kind !== "file") continue;
    const f = item.getAsFile();
    if (!f) continue;
    if (!ACCEPTED_IMAGE_MIME.has(f.type)) continue;
    try {
      out.push(await readImageFile(f, f.name || undefined));
    } catch {
      /* skip individual failures; remaining items still flow */
    }
  }
  return out;
}

/** Walk a FileList (file picker output) and read every image. */
export async function collectImagesFromFiles(
  files: FileList | File[],
): Promise<PendingImage[]> {
  const out: PendingImage[] = [];
  const arr: File[] = Array.from(files);
  for (const f of arr) {
    if (!ACCEPTED_IMAGE_MIME.has(f.type)) continue;
    try {
      out.push(await readImageFile(f, f.name || undefined));
    } catch {
      /* skip */
    }
  }
  return out;
}

/** Collect non-image text files from a transfer. Returns the
 *  filename + decoded content for each — caller folds them into
 *  paste tokens. Non-text / oversize files are silently skipped (we
 *  don't pretend to handle binary blobs in v1). */
export async function collectTextFilesFromTransfer(
  items: DataTransfer,
): Promise<{ filename: string; content: string }[]> {
  const out: { filename: string; content: string }[] = [];
  const list: DataTransferItem[] = [];
  for (let i = 0; i < items.items.length; i++) list.push(items.items[i]);
  for (const item of list) {
    if (item.kind !== "file") continue;
    const f = item.getAsFile();
    if (!f) continue;
    if (ACCEPTED_IMAGE_MIME.has(f.type)) continue;
    const read = await readDroppedTextFile(f);
    if (read) out.push(read);
  }
  return out;
}

function cryptoRandom(): string {
  // crypto.randomUUID isn't in every browser yet; fall back to a
  // Math.random suffix so we never throw.
  try {
    const c = (globalThis as { crypto?: Crypto }).crypto;
    if (c && "randomUUID" in c) return (c as Crypto).randomUUID();
  } catch {
    /* ignore */
  }
  return `img-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}
