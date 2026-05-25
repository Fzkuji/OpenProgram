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

import type { ChatAttachment } from "../legacy-send";

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
      // Build a small thumbnail blob (max 192px on the long edge) and
      // use ITS object-URL as the preview. The original full-res
      // bitmap stays in ``data`` for the backend; the composer no
      // longer pays the decode + scale cost on every layout reflow
      // — that was the perceptible stutter when adding images at
      // row-wrap boundaries.
      makeThumbnail(file).then((thumbUrl) => {
        resolve({
          id: cryptoRandom(),
          previewUrl: thumbUrl,
          sizeBytes: file.size,
          attachment: {
            type: "image",
            data,
            media_type: mime,
            ...(filename ? { filename } : {}),
          },
        });
      }).catch(() => {
        // Thumbnail failure (corrupt image, OOM, etc.) — fall back to
        // the raw file URL so the user still sees something.
        resolve({
          id: cryptoRandom(),
          previewUrl: URL.createObjectURL(file),
          sizeBytes: file.size,
          attachment: {
            type: "image",
            data,
            media_type: mime,
            ...(filename ? { filename } : {}),
          },
        });
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
  // Both DataTransfer and DataTransferItemList expose ``items``; the
  // type union here lets callers pass either.
  const raw = ("items" in items ? items.items : items) as DataTransferItemList;
  const candidates: File[] = [];
  for (let i = 0; i < raw.length; i++) {
    const item = raw[i];
    if (item.kind !== "file") continue;
    const f = item.getAsFile();
    if (!f) continue;
    if (!ACCEPTED_IMAGE_MIME.has(f.type)) continue;
    candidates.push(f);
  }
  return readImagesParallel(candidates);
}

/** Walk a FileList (file picker output) and read every image. */
export async function collectImagesFromFiles(
  files: FileList | File[],
): Promise<PendingImage[]> {
  const arr: File[] = Array.from(files).filter(
    (f) => ACCEPTED_IMAGE_MIME.has(f.type),
  );
  return readImagesParallel(arr);
}

/** Read N image files concurrently. Serial ``await`` in a for-loop
 *  multiplied per-file decode+thumbnail latency by N — for a drop of
 *  ~6 mid-size screenshots that was the difference between ~2s and
 *  ~12s wall time. */
async function readImagesParallel(files: File[]): Promise<PendingImage[]> {
  if (files.length === 0) return [];
  const results = await Promise.allSettled(
    files.map((f) => readImageFile(f, f.name || undefined)),
  );
  const out: PendingImage[] = [];
  for (const r of results) {
    if (r.status === "fulfilled") out.push(r.value);
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
  const candidates: File[] = [];
  for (let i = 0; i < items.items.length; i++) {
    const item = items.items[i];
    if (item.kind !== "file") continue;
    const f = item.getAsFile();
    if (!f) continue;
    if (ACCEPTED_IMAGE_MIME.has(f.type)) continue;
    candidates.push(f);
  }
  if (candidates.length === 0) return [];
  // Same parallel pattern as the image path — text-file reads were
  // serialised behind every paste-store add.
  const reads = await Promise.allSettled(
    candidates.map((f) => readDroppedTextFile(f)),
  );
  const out: { filename: string; content: string }[] = [];
  for (const r of reads) {
    if (r.status === "fulfilled" && r.value) out.push(r.value);
  }
  return out;
}

/** Long-edge cap (px) used for the in-composer thumbnail. Big enough
 *  to look crisp at our 64×64 chip on a 2× display, small enough to
 *  decode + paint in <1 frame. */
const THUMB_LONG_EDGE = 192;

/** Generate a downscaled JPEG thumbnail object-URL for ``file``.
 *  Decodes once, paints into an offscreen canvas, returns the canvas
 *  blob's object URL. The composer renders only this 192px-edge
 *  bitmap rather than the original (which can be multi-megapixel and
 *  blow up flex layout cost when rows wrap). */
async function makeThumbnail(file: File | Blob): Promise<string> {
  const src = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((res, rej) => {
      const im = new Image();
      im.onload = () => res(im);
      im.onerror = () => rej(new Error("image decode failed"));
      im.src = src;
    });
    const longEdge = Math.max(img.naturalWidth, img.naturalHeight);
    const scale = longEdge > THUMB_LONG_EDGE
      ? THUMB_LONG_EDGE / longEdge : 1;
    const w = Math.max(1, Math.round(img.naturalWidth * scale));
    const h = Math.max(1, Math.round(img.naturalHeight * scale));
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("no 2d context");
    ctx.drawImage(img, 0, 0, w, h);
    return await new Promise<string>((res, rej) => {
      canvas.toBlob((blob) => {
        if (!blob) { rej(new Error("toBlob failed")); return; }
        res(URL.createObjectURL(blob));
      }, "image/jpeg", 0.82);
    });
  } finally {
    URL.revokeObjectURL(src);
  }
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
