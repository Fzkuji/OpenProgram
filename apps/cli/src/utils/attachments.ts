/**
 * Images and PDFs attached from the terminal.
 *
 * Three ways in, as in Claude Code and Codex: paste (or drag, which
 * terminals deliver as a pasted path) the path of an image or PDF; type
 * `@path` to one and send; or press the image-paste key to take an
 * image from the clipboard. The input shows a short placeholder such as
 * `[Image #1]`; deleting it drops the attachment. On send each
 * placeholder becomes the same `[attachment: name (ext, N KB) …]`
 * marker the web composer writes, so the server stores and shows it the
 * same way: an image goes as bytes for the model, a PDF on disk goes as
 * its path for the agent's `pdf` tool (the Electron composer's rule).
 */
import { existsSync, mkdtempSync, readFileSync, rmSync, statSync } from 'fs';
import { homedir, tmpdir } from 'os';
import { basename, extname, isAbsolute, join, resolve } from 'path';
import { execFileNoThrow } from '../runtime/utils/execFileNoThrow.js';

export const IMAGE_TYPES: Record<string, string> = {
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  gif: 'image/gif',
  webp: 'image/webp',
};
export const PDF_TYPE = 'application/pdf';

/** What a model accepts per image; the web composer resizes above it. */
export const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
/** The server's own cap for a stored attachment (attachments.MAX_ATTACH_MB). */
export const MAX_FILE_BYTES = 32 * 1024 * 1024;

export interface PendingAttachment {
  /** `[Image #1]`, `[PDF #2]` — the text the input shows. */
  label: string;
  kind: 'image' | 'pdf';
  filename: string;
  mediaType: string;
  sizeBytes: number;
  /** Absolute source path when the file came from disk. */
  path?: string;
  /** base64 bytes, images only. */
  data?: string;
}

export interface ChatAttachmentPayload {
  type: 'image' | 'document';
  data: string;
  media_type: string;
  filename?: string;
  source_path?: string;
}

const extOf = (name: string): string => extname(name).slice(1).toLowerCase();

export function attachableKind(path: string): 'image' | 'pdf' | null {
  const ext = extOf(path);
  if (IMAGE_TYPES[ext]) return 'image';
  if (ext === 'pdf') return 'pdf';
  return null;
}

/** One pasted token as a path: quotes, `\ ` escapes, `file://` and `~`. */
export function unescapePath(token: string, home: string = homedir()): string {
  let t = token.trim();
  if ((t.startsWith('"') && t.endsWith('"')) || (t.startsWith("'") && t.endsWith("'"))) {
    t = t.slice(1, -1);
  } else {
    t = t.replace(/\\(.)/g, '$1');
  }
  if (t.startsWith('file://')) {
    try { t = decodeURIComponent(new URL(t).pathname); } catch { /* keep as typed */ }
  }
  if (t === '~' || t.startsWith('~/')) t = join(home, t.slice(1));
  return t;
}

/** Split pasted text into path tokens (newlines, or spaces outside
 *  quotes and escapes — the shape a drag of several files takes). */
export function pathTokens(text: string): string[] {
  const out: string[] = [];
  let current = '';
  let quote: string | null = null;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]!;
    if (quote) {
      current += ch;
      if (ch === quote) quote = null;
    } else if (ch === '\\' && i + 1 < text.length) {
      current += ch + text[++i];
    } else if (ch === '"' || ch === "'") {
      quote = ch;
      current += ch;
    } else if (/\s/.test(ch)) {
      if (current) out.push(current);
      current = '';
    } else {
      current += ch;
    }
  }
  if (current) out.push(current);
  return out;
}

/**
 * The files a paste names, when every token is an existing image or
 * PDF; otherwise null and the paste stays text.
 */
export function pastedAttachmentPaths(
  text: string,
  exists: (path: string) => boolean = existsSync,
  cwd: string = process.cwd(),
): string[] | null {
  const trimmed = text.trim();
  if (!trimmed || trimmed.length > 4096) return null;
  const tokens = pathTokens(trimmed);
  if (!tokens.length) return null;
  const paths: string[] = [];
  for (const token of tokens) {
    const raw = unescapePath(token);
    const path = isAbsolute(raw) ? raw : resolve(cwd, raw);
    if (!attachableKind(path) || !exists(path)) return null;
    paths.push(path);
  }
  return paths;
}

const labelFor = (kind: 'image' | 'pdf', n: number) => (kind === 'image' ? `[Image #${n}]` : `[PDF #${n}]`);

export function nextAttachmentNumber(existing: PendingAttachment[]): number {
  let max = 0;
  for (const a of existing) {
    const m = /#(\d+)\]$/.exec(a.label);
    if (m) max = Math.max(max, Number(m[1]));
  }
  return max + 1;
}

export interface AttachFs {
  size: (path: string) => number;
  read: (path: string) => Buffer;
}

const realFs: AttachFs = {
  size: (path) => statSync(path).size,
  read: (path) => readFileSync(path),
};

/** Read a file from disk as an attachment, or say why it cannot be. */
export function attachmentFromPath(
  path: string,
  number: number,
  fs: AttachFs = realFs,
): PendingAttachment | { error: string } {
  const kind = attachableKind(path);
  const filename = basename(path);
  if (!kind) return { error: `${filename}: only images and PDFs can be attached` };
  let size: number;
  try {
    size = fs.size(path);
  } catch {
    return { error: `${filename}: cannot be read` };
  }
  if (kind === 'image' && size > MAX_IMAGE_BYTES) {
    return { error: `${filename}: too large to attach (images up to 5 MB)` };
  }
  if (size > MAX_FILE_BYTES) return { error: `${filename}: too large to attach (up to 32 MB)` };
  const base: PendingAttachment = {
    label: labelFor(kind, number),
    kind,
    filename,
    mediaType: kind === 'image' ? IMAGE_TYPES[extOf(path)]! : PDF_TYPE,
    sizeBytes: size,
    path,
  };
  if (kind === 'pdf') return base;
  try {
    return { ...base, data: fs.read(path).toString('base64') };
  } catch {
    return { error: `${filename}: cannot be read` };
  }
}

/** An image taken from the clipboard (no source path). */
export function attachmentFromBytes(bytes: Buffer, number: number): PendingAttachment | { error: string } {
  if (bytes.length > MAX_IMAGE_BYTES) return { error: 'clipboard image is too large to attach (up to 5 MB)' };
  return {
    label: labelFor('image', number),
    kind: 'image',
    filename: `image-${number}.png`,
    mediaType: 'image/png',
    sizeBytes: bytes.length,
    data: bytes.toString('base64'),
  };
}

const safeMarkerText = (value: string): string => (value || '').trim().replace(/[[\]()]+/g, '_') || 'file';

/** The web composer's marker for one attachment. */
export function attachmentMarker(a: PendingAttachment): string {
  const ext = safeMarkerText(extOf(a.filename) || (a.kind === 'pdf' ? 'pdf' : 'png'));
  const meta = `${ext}, ${Math.max(1, Math.round(a.sizeBytes / 1024))} KB`;
  const name = safeMarkerText(a.filename);
  return a.path
    ? `[attachment: ${name} (${meta}) @json ${JSON.stringify(a.path)}]`
    : `[attachment: ${name} (${meta})]`;
}

/**
 * The message as sent: placeholders still in the text become markers,
 * attachments whose placeholder was deleted are dropped.
 */
export function buildOutgoing(
  text: string,
  attachments: PendingAttachment[],
): { text: string; attachments: ChatAttachmentPayload[] } {
  let out = text;
  const payload: ChatAttachmentPayload[] = [];
  for (const a of attachments) {
    if (!out.includes(a.label)) continue;
    out = out.split(a.label).join(attachmentMarker(a));
    if (a.kind === 'image' && a.data) {
      payload.push({
        type: 'image',
        data: a.data,
        media_type: a.mediaType,
        filename: a.filename,
        ...(a.path ? { source_path: a.path } : {}),
      });
    }
  }
  return { text: out, attachments: payload };
}

/** `@path` tokens in a message that name an image or PDF on disk. */
export function mentionedAttachmentPaths(
  text: string,
  exists: (path: string) => boolean = existsSync,
  cwd: string = process.cwd(),
): Array<{ token: string; path: string }> {
  const found: Array<{ token: string; path: string }> = [];
  for (const m of text.matchAll(/(^|\s)@("[^"]+"|'[^']+'|(?:\\.|\S)+)/g)) {
    const token = `@${m[2]}`;
    const raw = unescapePath(m[2]!);
    const path = isAbsolute(raw) ? raw : resolve(cwd, raw);
    if (attachableKind(path) && exists(path)) found.push({ token, path });
  }
  return found;
}

type Exec = typeof execFileNoThrow;

/**
 * Read an image from the system clipboard into PNG bytes, or null when
 * the clipboard holds none. macOS through osascript, Linux through
 * wl-paste or xclip, Windows through PowerShell — the tools Claude Code
 * and Codex use.
 */
export async function readClipboardImage(
  exec: Exec = execFileNoThrow,
  platform: NodeJS.Platform = process.platform,
): Promise<Buffer | null> {
  const dir = mkdtempSync(join(tmpdir(), 'openprogram-paste-'));
  const file = join(dir, 'clipboard.png');
  const take = (): Buffer | null => {
    try {
      const bytes = readFileSync(file);
      return bytes.length ? bytes : null;
    } catch {
      return null;
    }
  };
  try {
    if (platform === 'darwin') {
      await exec('osascript', [
        '-e', 'set png_data to (the clipboard as «class PNGf»)',
        '-e', `set fp to open for access POSIX file ${JSON.stringify(file)} with write permission`,
        '-e', 'write png_data to fp',
        '-e', 'close access fp',
      ], { timeout: 3000 });
      return take();
    }
    if (platform === 'win32') {
      const script = [
        'Add-Type -AssemblyName System.Windows.Forms',
        'Add-Type -AssemblyName System.Drawing',
        '$i = [Windows.Forms.Clipboard]::GetImage()',
        `if ($i) { $i.Save('${file.replace(/'/g, "''")}', [Drawing.Imaging.ImageFormat]::Png) }`,
      ].join('; ');
      await exec('powershell', ['-NoProfile', '-STA', '-Command', script], { timeout: 5000 });
      return take();
    }
    const quoted = `'${file.replace(/'/g, `'\\''`)}'`;
    for (const command of [
      `wl-paste --no-newline --type image/png > ${quoted}`,
      `xclip -selection clipboard -t image/png -o > ${quoted}`,
    ]) {
      const result = await exec('sh', ['-c', command], { timeout: 3000 });
      if (result.code === 0) {
        const bytes = take();
        if (bytes) return bytes;
      }
    }
    return null;
  } finally {
    try { rmSync(dir, { recursive: true, force: true }); } catch { /* temp dir */ }
  }
}
