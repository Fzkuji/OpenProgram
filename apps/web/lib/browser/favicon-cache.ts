/** Decoded, bounded favicon reuse within one renderer. No persistent user data. */
const MAX_ENTRIES = 256;
const SUCCESS_TTL = 30 * 60_000;
const FAILURE_TTL = 5 * 60_000;
const LOAD_TIMEOUT = 5_000;

type Icon = HTMLCanvasElement | null;
type Entry = {
  value: Icon | undefined;
  expires: number;
  promise: Promise<Icon>;
  cancel(): void;
};
const entries = new Map<string, Entry>();

function safeSource(url?: string): string | undefined {
  if (!url) return undefined;
  if (/^data:image\/[a-z0-9.+-]+;base64,/i.test(url)) return url;
  try {
    const parsed = new URL(url);
    if (/^https?:$/.test(parsed.protocol) && !parsed.username && !parsed.password) return parsed.href;
  } catch { /* Invalid sources use the existing fallback. */ }
  return undefined;
}

function cached(source: string): Entry | undefined {
  const entry = entries.get(source);
  if (!entry) return undefined;
  if (entry.expires <= Date.now()) {
    entries.delete(source);
    return undefined;
  }
  entries.delete(source);
  entries.set(source, entry);
  return entry;
}

/** A cached failure permits the next candidate; undefined means it is not ready. */
export function cachedFavicon(url?: string, fallbackUrl?: string): Icon | undefined {
  for (const candidate of [url, fallbackUrl]) {
    const source = safeSource(candidate);
    if (!source) continue;
    const entry = cached(source);
    if (!entry || entry.value === undefined) return undefined;
    if (entry.value) return entry.value;
  }
  return null;
}

function decodeIcon(image: HTMLImageElement): Icon {
  if (!image.naturalWidth || !image.naturalHeight) return null;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 32;
  const context = canvas.getContext('2d');
  if (!context) return null;
  const scale = Math.min(32 / image.naturalWidth, 32 / image.naturalHeight);
  const width = image.naturalWidth * scale;
  const height = image.naturalHeight * scale;
  context.drawImage(image, (32 - width) / 2, (32 - height) / 2, width, height);
  try {
    const pixels = context.getImageData(0, 0, 32, 32).data;
    if (!pixels.some((alpha, index) => index % 4 === 3 && alpha > 0)) return null;
  } catch {
    // Cross-origin images are drawable; reading their pixels is unnecessary.
  }
  return canvas;
}

function loadSource(source: string): Promise<Icon> {
  const existing = cached(source);
  if (existing) return existing.promise;
  let resolve!: (icon: Icon) => void;
  const promise = new Promise<Icon>(done => { resolve = done; });
  const image = new Image();
  let settled = false;
  const finish = (value: Icon) => {
    if (settled) return;
    settled = true;
    clearTimeout(timeout);
    image.onload = image.onerror = null;
    if (!value) image.removeAttribute('src');
    entry.cancel = () => {};
    entry.value = value;
    entry.expires = Date.now() + (value ? SUCCESS_TTL : FAILURE_TTL);
    resolve(value);
  };
  const entry: Entry = {value: undefined, expires: Infinity, promise, cancel: () => finish(null)};
  const timeout = setTimeout(entry.cancel, LOAD_TIMEOUT);
  entries.set(source, entry);
  while (entries.size > MAX_ENTRIES) {
    const oldest = entries.entries().next().value!;
    entries.delete(oldest[0]);
    oldest[1].cancel();
  }
  image.referrerPolicy = 'no-referrer';
  image.onload = () => {
    try { finish(decodeIcon(image)); } catch { finish(null); }
  };
  image.onerror = () => finish(null);
  image.src = source;
  return promise;
}

export async function loadFavicon(url?: string, fallbackUrl?: string): Promise<Icon> {
  for (const candidate of new Set([url, fallbackUrl])) {
    const source = safeSource(candidate);
    if (!source) continue;
    const icon = await loadSource(source);
    if (icon) return icon;
  }
  return null;
}
