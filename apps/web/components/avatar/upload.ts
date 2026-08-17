/**
 * Upload helpers for the "Custom" avatar source.
 *
 * The settings UI uses these to:
 *   1. tell the OS file picker which formats we accept
 *      (``UPLOAD_ACCEPT``)
 *   2. cap the file size client-side before reading it into a data
 *      URL — uploads land in localStorage, which is per-origin
 *      capped at ~5 MB total, so we keep avatar uploads well under
 *      that budget (``UPLOAD_MAX_BYTES``)
 *   3. convert the picked ``File`` into a data URL the rest of the
 *      app can stash on the profile (``fileToDataUrl``)
 *
 * Animated GIF / WebP / APNG play natively when the data URL ends up
 * in an ``<img src=…>``; no extra animation runtime needed.
 */

export const UPLOAD_MAX_BYTES = 4 * 1024 * 1024;

/** MIME list handed to ``<input type="file" accept=…>``. SVG is
 *  included even though it's unsafe-until-sanitised because the
 *  data-URL path renders via ``<img>``, which doesn't execute
 *  embedded ``<script>`` / ``onload``. (Don't switch to inline-SVG
 *  rendering for uploaded files without a sanitisation pass.) */
export const UPLOAD_ACCEPT =
  "image/png,image/jpeg,image/gif,image/webp,image/svg+xml,image/apng";

export interface UploadResult {
  ok: true;
  dataUrl: string;
}
export interface UploadError {
  ok: false;
  error: string;
}

/** Read a picked file into a data URL, with size validation up
 *  front. Returns a tagged result so callers don't have to think
 *  about whether ``FileReader.onerror`` fires synchronously or
 *  not. */
export function fileToDataUrl(file: File): Promise<UploadResult | UploadError> {
  if (file.size > UPLOAD_MAX_BYTES) {
    return Promise.resolve({
      ok: false,
      error: `File is ${(file.size / 1024 / 1024).toFixed(1)} MB, max ${
        UPLOAD_MAX_BYTES / 1024 / 1024
      } MB`,
    });
  }
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ ok: true, dataUrl: String(reader.result || "") });
    reader.onerror = () => resolve({ ok: false, error: "Failed to read file." });
    reader.readAsDataURL(file);
  });
}
