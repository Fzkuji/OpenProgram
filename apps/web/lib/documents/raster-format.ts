export const MAX_RASTER_BYTES = 64 * 1024 * 1024;
export const MAX_RASTER_PIXELS = 16_000_000;
export type RasterFormat = "png" | "jpeg" | "webp";
const MIME: Record<RasterFormat, string> = { png: "image/png", jpeg: "image/jpeg", webp: "image/webp" };
type Dimensions = { width: number; height: number };
type RasterInfo = Dimensions & { mime: string; format: RasterFormat; animated: boolean };
const invalid = () => new Error("UNSUPPORTED_IMAGE: invalid or truncated raster container.");
function ascii(bytes: Uint8Array, offset: number, length: number): string {
  let result = "";
  for (let i = offset; i < offset + length && i < bytes.length; i++) result += String.fromCharCode(bytes[i]);
  return result;
}
export function rasterMime(bytes: Uint8Array): string | null {
  if (bytes.length >= 8 && [137,80,78,71,13,10,26,10].every((v,i)=>bytes[i] === v)) return MIME.png;
  if (bytes.length >= 3 && bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255) return MIME.jpeg;
  if (bytes.length >= 12 && ascii(bytes,0,4) === "RIFF" && ascii(bytes,8,4) === "WEBP") return MIME.webp;
  return null;
}
function dimensions(width: number, height: number): Dimensions {
  if (!width || !height || width * height > MAX_RASTER_PIXELS)
    throw new Error("IMAGE_RESOURCE_LIMIT: raster dimensions exceed 16 million pixels.");
  return { width, height };
}
/** Walk container headers; never scan compressed pixels for apparent chunk names. */
export function inspectRasterBytes(bytes: Uint8Array): RasterInfo {
  if (bytes.byteLength > MAX_RASTER_BYTES) throw new Error("IMAGE_RESOURCE_LIMIT: raster files are limited to 64 MiB.");
  const mime = rasterMime(bytes);
  if (!mime) throw invalid();
  const view = new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
  let size: Dimensions | undefined; let animated = false;
  if (mime === MIME.png) {
    if (bytes.length < 33 || ascii(bytes,12,4) !== "IHDR" || view.getUint32(8) !== 13) throw invalid();
    size = dimensions(view.getUint32(16),view.getUint32(20));
    if (bytes[24] === 16) throw new Error("UNSUPPORTED_IMAGE: 16-bit PNG files are read-only.");
    let ended = false;
    for (let offset = 8; offset < bytes.length;) {
      if (offset + 12 > bytes.length) throw invalid();
      const length = view.getUint32(offset), kind = ascii(bytes,offset+4,4);
      if (length > bytes.length - offset - 12) throw invalid();
      if (kind === "IHDR" && offset !== 8) throw invalid();
      if (kind === "acTL") animated = true;
      offset += length + 12;
      if (kind === "IEND") { ended = true; break; }
    }
    if (!ended) throw invalid();
    return { ...size, mime, format:"png", animated };
  }
  if (mime === MIME.webp) {
    const end = view.getUint32(4,true) + 8;
    if (end !== bytes.length) throw invalid();
    for (let offset=12; offset < end;) {
      if (offset + 8 > end) throw invalid();
      const kind=ascii(bytes,offset,4), length=view.getUint32(offset+4,true), data=offset+8;
      if (length > end-data || data+length+(length&1)>end) throw invalid();
      if (kind === "ANIM" || kind === "ANMF") animated = true;
      if (kind === "VP8X") {
        if (length !== 10) throw invalid();
        animated ||= Boolean(bytes[data]&2);
        size=dimensions(1+bytes[data+4]+(bytes[data+5]<<8)+(bytes[data+6]<<16),1+bytes[data+7]+(bytes[data+8]<<8)+(bytes[data+9]<<16));
      } else if (kind === "VP8L") {
        if (length < 5 || bytes[data] !== 0x2f) throw invalid();
        const bits=view.getUint32(data+1,true);
        const inner=dimensions((bits&0x3fff)+1,((bits>>>14)&0x3fff)+1);
        size ??= inner;
      } else if (kind === "VP8 ") {
        if (length < 10 || ascii(bytes,data+3,3) !== "\x9d\x01\x2a") throw invalid();
        const inner=dimensions(view.getUint16(data+6,true)&0x3fff,view.getUint16(data+8,true)&0x3fff);
        size ??= inner;
      }
      offset=data+length+(length&1);
    }
    if (!size) throw invalid();
    return { ...size, mime, format:"webp", animated };
  }
  const sof = new Set([0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf]);
  for (let offset=2; offset < bytes.length;) {
    if (bytes[offset++] !== 255) throw invalid();
    while (offset < bytes.length && bytes[offset] === 255) offset++;
    if (offset >= bytes.length) throw invalid();
    const marker=bytes[offset++];
    if (marker === 0xda || marker === 0xd9) break;
    if (marker === 1 || (marker >= 0xd0 && marker <= 0xd7)) continue;
    if (offset+2 > bytes.length) throw invalid();
    const length=view.getUint16(offset);
    if (length < 2 || length > bytes.length-offset) throw invalid();
    if (sof.has(marker)) {
      if (length < 8 || bytes[offset+2] !== 8) throw invalid();
      if (size) throw invalid();
      size=dimensions(view.getUint16(offset+5),view.getUint16(offset+3));
    }
    offset += length;
  }
  if (!size) throw invalid();
  return { ...size, mime, format:"jpeg", animated:false };
}
export function validateRasterInput(value: ArrayBuffer | Uint8Array): RasterInfo {
  const result=inspectRasterBytes(value instanceof Uint8Array ? value : new Uint8Array(value));
  if (result.animated) throw new Error("UNSUPPORTED_IMAGE: animated raster files are read-only.");
  return result;
}
function checkMime(value: Blob, mime: string): void {
  if (value.type && value.type !== "application/octet-stream" && value.type !== mime)
    throw new Error("UNSUPPORTED_IMAGE: invalid raster MIME.");
}
async function read(value: Blob): Promise<RasterInfo> {
  if (value.size > MAX_RASTER_BYTES) throw new Error("IMAGE_RESOURCE_LIMIT: raster files are limited to 64 MiB.");
  const info=validateRasterInput(new Uint8Array(await value.arrayBuffer()));
  checkMime(value,info.mime);
  return info;
}
export async function validateRasterDecoded(value: Blob): Promise<RasterInfo> {
  const info=await read(value);
  if (typeof createImageBitmap !== "function") throw new Error("UNSUPPORTED_IMAGE: browser image decoding is unavailable.");
  let bitmap: ImageBitmap;
  try { bitmap=await createImageBitmap(value); }
  catch { throw new Error("UNSUPPORTED_IMAGE: raster decoding failed."); }
  try { return { ...info, ...dimensions(bitmap.width,bitmap.height) }; }
  finally { bitmap.close(); }
}
export async function assertEncodedRaster(value: Blob, format: RasterFormat): Promise<void> {
  const info=await read(value);
  if (info.format !== format) throw new Error("UNSUPPORTED_IMAGE: editor returned an invalid same-format encoding.");
}
