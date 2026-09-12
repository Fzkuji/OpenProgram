export const MAX_RASTER_BYTES = 64 * 1024 * 1024;
export const MAX_RASTER_PIXELS = 16_000_000;
export type RasterFormat = "png" | "jpeg" | "webp";
const MIME: Record<RasterFormat, string> = { png: "image/png", jpeg: "image/jpeg", webp: "image/webp" };

function bytesOf(value: Blob | ArrayBuffer | Uint8Array): Uint8Array {
  if (value instanceof Uint8Array) return value;
  if (value instanceof ArrayBuffer) return new Uint8Array(value);
  throw new Error("Raster bytes must be read before validation.");
}
function ascii(bytes: Uint8Array, offset: number, length: number): string {
  return String.fromCharCode(...bytes.subarray(offset, offset + length));
}
export function rasterMime(value: Uint8Array): string | null {
  if (value.length >= 8 && value[0] === 0x89 && ascii(value, 1, 3) === "PNG") return MIME.png;
  if (value.length >= 3 && value[0] === 0xff && value[1] === 0xd8 && value[2] === 0xff) return MIME.jpeg;
  if (value.length >= 12 && ascii(value, 0, 4) === "RIFF" && ascii(value, 8, 4) === "WEBP") return MIME.webp;
  return null;
}
export function inspectRasterBytes(value: Uint8Array): { mime: string | null; format: RasterFormat | null; animated: boolean } {
  const mime = rasterMime(value);
  let animated = false;
  if (mime === MIME.png) animated = ascii(value, 0, Math.min(value.length, 1_000_000)).includes("acTL");
  if (mime === MIME.webp) {
    // VP8X animation flag is bit 1 of the feature byte; ANIM is also valid evidence.
    animated = (value.length >= 21 && (value[20] & 0x02) !== 0) || ascii(value, 12, Math.min(value.length - 12, 1_000_000)).includes("ANIM");
  }
  const format = mime === MIME.png ? "png" : mime === MIME.jpeg ? "jpeg" : mime === MIME.webp ? "webp" : null;
  return { mime, format, animated };
}
export function validateRasterInput(value: Blob | ArrayBuffer | Uint8Array): { format: RasterFormat; mime: string } {
  const size = value instanceof Blob ? value.size : value.byteLength;
  if (size > MAX_RASTER_BYTES) throw new Error("IMAGE_RESOURCE_LIMIT: raster files are limited to 64 MiB.");
  const info = inspectRasterBytes(bytesOf(value));
  if (!info.format || value instanceof Blob && value.type && value.type !== info.mime) throw new Error("UNSUPPORTED_IMAGE: invalid raster signature or MIME.");
  if (info.animated) throw new Error("UNSUPPORTED_IMAGE: animated raster files are read-only.");
  return { format: info.format, mime: info.mime! };
}
export async function validateRasterDecoded(value: Blob): Promise<{ format: RasterFormat; mime: string; width: number; height: number }> {
  const raw = new Uint8Array(await value.arrayBuffer());
  const result = validateRasterInput(raw);
  if (value.type && value.type !== result.mime) throw new Error("UNSUPPORTED_IMAGE: invalid raster MIME.");
  if (typeof createImageBitmap !== "function") return { ...result, width: 0, height: 0 };
  let bitmap: ImageBitmap;
  try { bitmap = await createImageBitmap(value); }
  catch { throw new Error("UNSUPPORTED_IMAGE: raster decoding failed."); }
  try {
    if (!bitmap.width || !bitmap.height || bitmap.width > MAX_RASTER_PIXELS || bitmap.height > MAX_RASTER_PIXELS || bitmap.width * bitmap.height > MAX_RASTER_PIXELS)
      throw new Error("IMAGE_RESOURCE_LIMIT: raster dimensions exceed 16 million pixels.");
    return { ...result, width: bitmap.width, height: bitmap.height };
  } finally { bitmap.close(); }
}
export function assertEncodedRaster(value: Blob, format: RasterFormat): void {
  const info = inspectRasterBytes(bytesOf(value));
  if (info.format !== format || info.animated) throw new Error("UNSUPPORTED_IMAGE: editor returned an invalid same-format encoding.");
}
