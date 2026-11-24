/** Bound decoder input even when the server omits Content-Length. */
export const MAX_PREVIEW_BYTES = 64 * 1024 * 1024;
/** A read the server answered with an error status; `status` tells a
 *  refusal (4xx) from a read worth asking again. */
export class PreviewReadError extends Error {
  readonly status: number;
  constructor(status: number) { super("PREVIEW_READ_FAILED"); this.status = status; }
}
export async function readPreviewBytes(sourceUrl: string, signal: AbortSignal): Promise<Uint8Array> {
  const response = await fetch(sourceUrl, { signal });
  if (!response.ok) {
    void response.body?.cancel().catch(() => undefined);
    throw new PreviewReadError(response.status);
  }
  const advertised = Number(response.headers.get("content-length"));
  if (advertised > MAX_PREVIEW_BYTES) {
    await response.body?.cancel();
    throw new Error("PREVIEW_RESOURCE_LIMIT");
  }
  if (!response.body) throw new Error("PREVIEW_READ_FAILED");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      if (signal.aborted) throw new DOMException("Aborted", "AbortError");
      const part = await reader.read();
      if (part.done) break;
      total += part.value.byteLength;
      if (total > MAX_PREVIEW_BYTES) throw new Error("PREVIEW_RESOURCE_LIMIT");
      chunks.push(part.value);
    }
  } catch (error) {
    await reader.cancel().catch(() => undefined);
    throw error;
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return bytes;
}
