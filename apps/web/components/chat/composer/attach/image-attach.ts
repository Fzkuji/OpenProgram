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

import type { ChatAttachment } from "../submit/send-chat-message";

/** App attach cap for the original file (not a per-model discovery). */
export const MAX_ORIGINAL_IMAGE_BYTES = 32 * 1024 * 1024;
/** Send-representation cap for model image input (not a per-model discovery). */
export const MAX_MODEL_IMAGE_BYTES = 5 * 1024 * 1024;
/** Resize when the original long edge exceeds this. */
export const MODEL_IMAGE_LONG_EDGE_CAP = 8192;
/** First long-edge target when a send copy must be resized. */
export const MODEL_IMAGE_RESIZE_START = 2048;
/** @deprecated use MAX_ORIGINAL_IMAGE_BYTES; kept as the attach refusal cap. */
export const MAX_IMAGE_BYTES = MAX_ORIGINAL_IMAGE_BYTES;

export type ImageChatAttachment = ChatAttachment & {
  original_data?: string;
  original_media_type?: string;
};

export type ModelImagePlan =
  | { action: "keep-original" }
  | { action: "resize"; longEdges: number[]; outputMime: string }
  | { action: "reject"; reason: string };

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
  /** Object URL for the thumbnail (revoke on remove). ``null`` while
   *  the file is still being decoded — the chip shows a skeleton
   *  shimmer until this fills in. */
  previewUrl: string | null;
  /** Outgoing attachment payload. ``data`` is empty while reading;
   *  callers must wait for ``loading: false`` before sending. */
  attachment: ImageChatAttachment;
  /** Original native path when Electron supplied this File. */
  sourcePath?: string;
  sizeBytes: number;
  /** True while the decode + base64 + thumbnail pipeline is still
   *  running. The UI uses this to render a placeholder tile that
   *  fills in once processing completes. */
  loading?: boolean;
  /** Monotonic insertion rank shared with documents in the same chat. */
  order?: number;
  /** Read/decode failure shown on this item; blocks send. */
  error?: string;
}

/** Dropped files keep their original list order via ``order``, even though
 *  images and documents still live in separate pending arrays. */
export function planDroppedFiles<T extends { type: string }>(
  files: T[],
  startOrder: number,
): {
  images: Array<{ file: T; order: number }>;
  docs: Array<{ file: T; order: number }>;
} {
  let order = startOrder;
  const images: Array<{ file: T; order: number }> = [];
  const docs: Array<{ file: T; order: number }> = [];
  for (const file of files) {
    if (ACCEPTED_IMAGE_MIME.has(file.type)) images.push({ file, order: order++ });
    else docs.push({ file, order: order++ });
  }
  return { images, docs };
}

export function formatAttachmentLabel(ext: string): string {
  const trimmed = (ext || "").trim();
  if (!trimmed) return "File";
  if (trimmed.toLowerCase() === "folder") return "Folder";
  return trimmed.toUpperCase();
}

export function formatAttachmentSize(
  bytes: number | null | undefined,
  options?: { folder?: boolean },
): string {
  if (options?.folder) return "";
  const n = bytes ?? 0;
  if (n >= 1024 * 1024) {
    const mb = n / (1024 * 1024);
    return `${mb >= 10 ? mb.toFixed(0) : mb.toFixed(1)} MB`;
  }
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
}

export function nextModelResizeLongEdge(edge: number): number | null {
  if (edge <= 32) return null;
  const next = edge > 256 ? Math.floor(edge * 0.75) : Math.floor(edge / 2);
  return next >= 32 && next < edge ? next : null;
}

export function modelResizeLongEdges(width: number, height: number): number[] {
  const long = Math.max(width, height);
  let edge = Math.min(MODEL_IMAGE_RESIZE_START, Math.max(1, long));
  const edges = [edge];
  for (;;) {
    const next = nextModelResizeLongEdge(edge);
    if (next == null) break;
    edges.push(next);
    edge = next;
  }
  return edges;
}

export function modelImageOutputMime(mime: string): string {
  if (mime === "image/png" || mime === "image/gif") return "image/png";
  if (mime === "image/webp") return "image/webp";
  return "image/jpeg";
}

/** Decide whether the original bytes can go to the model, or a resized
 *  send copy is required. These are app limits, not per-model probes. */
export function planModelImageDelivery(opts: {
  byteLength: number;
  width: number;
  height: number;
  mime: string;
}): ModelImagePlan {
  if (opts.byteLength > MAX_ORIGINAL_IMAGE_BYTES) {
    return {
      action: "reject",
      reason:
        `image too large: ${(opts.byteLength / 1024 / 1024).toFixed(1)}MB ` +
        `(max ${MAX_ORIGINAL_IMAGE_BYTES / 1024 / 1024}MB)`,
    };
  }
  if (opts.width < 1 || opts.height < 1) {
    return { action: "reject", reason: "image decode failed" };
  }
  const long = Math.max(opts.width, opts.height);
  if (opts.byteLength <= MAX_MODEL_IMAGE_BYTES && long <= MODEL_IMAGE_LONG_EDGE_CAP) {
    return { action: "keep-original" };
  }
  return {
    action: "resize",
    longEdges: modelResizeLongEdges(opts.width, opts.height),
    outputMime: modelImageOutputMime(opts.mime),
  };
}

export function pickEncodedModelImage(
  attempts: Array<{ longEdge: number; bytes: number | null }>,
): { longEdge: number; bytes: number } | { fail: true } {
  for (const attempt of attempts) {
    if (attempt.bytes != null && attempt.bytes > 0
        && attempt.bytes <= MAX_MODEL_IMAGE_BYTES) {
      return { longEdge: attempt.longEdge, bytes: attempt.bytes };
    }
  }
  return { fail: true };
}

export function originalImageBytes(attachment: {
  data?: string;
  media_type?: string;
  original_data?: string;
  original_media_type?: string;
}): { data: string; media_type: string } | null {
  const data = attachment.original_data || attachment.data;
  if (!data) return null;
  return {
    data,
    media_type: attachment.original_media_type || attachment.media_type || "image/png",
  };
}

export function imagePreviewDataUrl(
  attachment: Parameters<typeof originalImageBytes>[0],
): string | null {
  const original = originalImageBytes(attachment);
  if (!original) return null;
  return `data:${original.media_type};base64,${original.data}`;
}

function readAsBase64(file: File | Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("read failed"));
    reader.onload = () => {
      const result = reader.result;
      if (typeof result !== "string") {
        reject(new Error("FileReader returned non-string"));
        return;
      }
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.readAsDataURL(file);
  });
}

function blobToBase64(blob: Blob): Promise<string | null> {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : null);
    };
    reader.onerror = () => resolve(null);
    reader.readAsDataURL(blob);
  });
}

async function encodeAtLongEdge(
  file: File | Blob,
  width: number,
  height: number,
  longEdge: number,
  outputMime: string,
): Promise<Blob | null> {
  const scale = longEdge / Math.max(width, height);
  const w = Math.max(1, Math.round(width * scale));
  const h = Math.max(1, Math.round(height * scale));
  const bitmap = await createImageBitmap(file, {
    resizeWidth: w,
    resizeHeight: h,
    resizeQuality: "medium",
  });
  try {
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(bitmap, 0, 0, w, h);
    return await new Promise((resolve) => {
      canvas.toBlob(
        (blob) => resolve(blob),
        outputMime,
        outputMime === "image/jpeg" ? 0.85 : undefined,
      );
    });
  } finally {
    bitmap.close();
  }
}

async function encodeModelImage(
  file: File | Blob,
  width: number,
  height: number,
  plan: Extract<ModelImagePlan, { action: "resize" }>,
): Promise<{ data: string; mediaType: string } | null> {
  for (const longEdge of plan.longEdges) {
    let blob: Blob | null = null;
    try {
      blob = await encodeAtLongEdge(file, width, height, longEdge, plan.outputMime);
    } catch {
      blob = null;
    }
    const picked = pickEncodedModelImage([{ longEdge, bytes: blob ? blob.size : null }]);
    if ("fail" in picked || !blob) continue;
    const data = await blobToBase64(blob);
    if (!data) continue;
    return { data, mediaType: plan.outputMime };
  }
  return null;
}

/** Read a ``File`` / ``Blob`` into a PendingImage, base64-encoded.
 *  Rejects on unsupported MIME, oversize original, or decode/encode failure. */
export async function readImageFile(
  file: File | Blob,
  filename?: string,
): Promise<PendingImage> {
  const mime = file.type === "image/jpg" ? "image/jpeg" : file.type || "application/octet-stream";
  if (!ACCEPTED_IMAGE_MIME.has(mime)) {
    throw new Error(`unsupported image type: ${mime}`);
  }
  if (file.size > MAX_ORIGINAL_IMAGE_BYTES) {
    throw new Error(
      `image too large: ${(file.size / 1024 / 1024).toFixed(1)}MB ` +
      `(max ${MAX_ORIGINAL_IMAGE_BYTES / 1024 / 1024}MB)`,
    );
  }
  const data = await readAsBase64(file);
  let probe: ImageBitmap;
  try {
    probe = await createImageBitmap(file);
  } catch {
    throw new Error("image decode failed");
  }
  const width = probe.width;
  const height = probe.height;
  probe.close();
  const plan = planModelImageDelivery({
    byteLength: file.size,
    width,
    height,
    mime,
  });
  if (plan.action === "reject") {
    throw new Error(plan.reason);
  }
  let sendData = data;
  let sendMime = mime;
  let original_data: string | undefined;
  let original_media_type: string | undefined;
  if (plan.action === "resize") {
    const encoded = await encodeModelImage(file, width, height, plan);
    if (!encoded) {
      throw new Error("could not encode a model-sized image");
    }
    original_data = data;
    original_media_type = mime;
    sendData = encoded.data;
    sendMime = encoded.mediaType;
  }
  let previewUrl: string;
  try {
    previewUrl = await makeThumbnail(file);
  } catch {
    previewUrl = URL.createObjectURL(file);
  }
  return {
    id: cryptoRandom(),
    previewUrl,
    sizeBytes: file.size,
    attachment: {
      type: "image",
      data: sendData,
      media_type: sendMime,
      ...(filename ? { filename } : {}),
      ...(original_data ? { original_data, original_media_type } : {}),
    },
  };
}

/** Upper bound on a binary doc we'll base64-upload so the backend can
 *  save it to disk for the agent. Bigger than the 5 MiB image cap since
 *  PDFs/decks are routinely larger; still bounded so a stray huge file
 *  doesn't blow up the WS frame. */
export const MAX_DOC_BYTES = 32 * 1024 * 1024;

/** Read a file as raw base64 (no ``data:...;base64,`` prefix), or
 *  ``null`` if it's over :data:`MAX_DOC_BYTES` or unreadable. Used for
 *  binary docs (PDF, etc.) that can't be inlined as text — the backend
 *  decodes this and writes the file under the session workdir so the
 *  agent's ``pdf`` / ``read`` / ``bash`` tools can open it. */
export async function readFileAsBase64(file: File): Promise<string | null> {
  if (file.size > MAX_DOC_BYTES) return null;
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => {
      const r = String(reader.result || "");
      const comma = r.indexOf(",");
      resolve(comma >= 0 ? r.slice(comma + 1) : null);
    };
    reader.onerror = () => resolve(null);
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

/** Outcome of a multi-file image read: the ones that made it, plus a
 *  human-readable reason per file that didn't. Callers surface
 *  ``errors`` in the composer's error strip — a rejected file must
 *  never disappear without a word. */
export interface ImageReadResult {
  images: PendingImage[];
  errors: string[];
}

/** Walk a DataTransferItemList from a paste / drop event and read
 *  every image item it contains. */
export async function collectImagesFromTransfer(
  items: DataTransferItemList | DataTransfer,
): Promise<ImageReadResult> {
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
): Promise<ImageReadResult> {
  const arr: File[] = Array.from(files).filter(
    (f) => ACCEPTED_IMAGE_MIME.has(f.type),
  );
  return readImagesParallel(arr);
}

/** Read N image files concurrently. Serial ``await`` in a for-loop
 *  multiplied per-file decode+thumbnail latency by N — for a drop of
 *  ~6 mid-size screenshots that was the difference between ~2s and
 *  ~12s wall time.
 *
 *  Rejections are RETURNED, not dropped. ``readImageFile`` rejects on
 *  oversize (>5 MiB) and unsupported MIME; discarding those made a
 *  pasted 12 MB screenshot vanish with no chip and no error, and the
 *  caller's ``.catch`` could never fire because this always resolved. */
async function readImagesParallel(files: File[]): Promise<ImageReadResult> {
  if (files.length === 0) return { images: [], errors: [] };
  const results = await Promise.allSettled(
    files.map((f) => readImageFile(f, f.name || undefined)),
  );
  const images: PendingImage[] = [];
  const errors: string[] = [];
  results.forEach((r, i) => {
    if (r.status === "fulfilled") images.push(r.value);
    else {
      const name = files[i].name || "image";
      const why = r.reason instanceof Error ? r.reason.message : String(r.reason);
      errors.push(`${name}: ${why}`);
      images.push({
        id: cryptoRandom(),
        previewUrl: null,
        sizeBytes: files[i].size,
        attachment: {
          type: "image",
          data: "",
          media_type: files[i].type || "application/octet-stream",
          ...(files[i].name ? { filename: files[i].name } : {}),
        },
        loading: false,
        error: why,
      });
    }
  });
  return { images, errors };
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

/** Generate a downscaled thumbnail object-URL for ``file``.
 *
 * Decode is validated in ``readImageFile`` before this runs. A
 * thumbnail encode failure must not be treated as a successful
 * original image; the caller keeps the already-decoded original.
 *
 * Decoding via ``createImageBitmap`` instead of ``new Image()`` +
 * ``canvas.drawImage``: ``createImageBitmap`` runs the decode (and
 * the optional resize) on a browser worker thread, so 6 parallel
 * thumbnail builds don't queue up behind each other on the main
 * thread the way HTMLImage decodes do. That was the source of the
 * residual "卡一下" stall after the drop pipeline was parallelised.
 *
 * Resize is asked of the bitmap factory directly so the browser
 * picks a fast scaler and we never hold the full-resolution bitmap
 * in JS land. Encoding back to a blob uses ``toBlob`` (async) so
 * the JPEG encode also stays off the layout-critical path.
 */
async function makeThumbnail(file: File | Blob): Promise<string> {
  // Peek dimensions first via a tiny ImageBitmap, then build the
  // resized one. ``createImageBitmap`` ignores resize options if you
  // pass them on the first call AND the browser doesn't know the
  // dimensions yet — splitting in two phases is the documented
  // workaround. Cheap because phase 1 doesn't materialise pixels.
  const probe = await createImageBitmap(file);
  const longEdge = Math.max(probe.width, probe.height);
  const scale = longEdge > THUMB_LONG_EDGE ? THUMB_LONG_EDGE / longEdge : 1;
  const w = Math.max(1, Math.round(probe.width * scale));
  const h = Math.max(1, Math.round(probe.height * scale));
  probe.close();

  const bitmap = await createImageBitmap(file, {
    resizeWidth: w,
    resizeHeight: h,
    resizeQuality: "medium",
  });
  try {
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    // ``bitmaprenderer`` transfers the bitmap directly — no
    // additional draw call, no extra GPU upload.
    const renderCtx = canvas.getContext("bitmaprenderer");
    if (renderCtx) {
      renderCtx.transferFromImageBitmap(bitmap);
    } else {
      // Older Safari falls back to the 2D context; still fast at
      // 192×192.
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("no 2d context");
      ctx.drawImage(bitmap, 0, 0, w, h);
    }
    return await new Promise<string>((res, rej) => {
      canvas.toBlob((blob) => {
        if (!blob) { rej(new Error("toBlob failed")); return; }
        res(URL.createObjectURL(blob));
      }, "image/jpeg", 0.82);
    });
  } finally {
    bitmap.close();
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
