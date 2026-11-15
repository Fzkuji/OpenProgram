import { describe, expect, it } from 'vitest';

import {
  attachmentFromBytes,
  attachmentFromPath,
  attachmentMarker,
  buildOutgoing,
  mentionedAttachmentPaths,
  nextAttachmentNumber,
  pastedAttachmentPaths,
  pathTokens,
  readClipboardImage,
  unescapePath,
  type PendingAttachment,
} from '../src/utils/attachments.js';

const files = new Set(['/shots/a b.png', '/docs/spec.pdf', '/home/me/pic.JPG', '/work/rel.webp']);
const exists = (p: string) => files.has(p);

describe('pasted paths', () => {
  it('reads quoted, escaped, file:// and ~ forms', () => {
    expect(unescapePath("'/shots/a b.png'")).toBe('/shots/a b.png');
    expect(unescapePath('/shots/a\\ b.png')).toBe('/shots/a b.png');
    expect(unescapePath('file:///shots/a%20b.png')).toBe('/shots/a b.png');
    expect(unescapePath('~/pic.JPG', '/home/me')).toBe('/home/me/pic.JPG');
    expect(pathTokens('/shots/a\\ b.png "/docs/spec.pdf"')).toEqual(['/shots/a\\ b.png', '"/docs/spec.pdf"']);
  });

  it('attaches only when every token is an existing image or PDF', () => {
    expect(pastedAttachmentPaths('/shots/a\\ b.png /docs/spec.pdf\n', exists)).toEqual(['/shots/a b.png', '/docs/spec.pdf']);
    expect(pastedAttachmentPaths('rel.webp', exists, '/work')).toEqual(['/work/rel.webp']);
    expect(pastedAttachmentPaths('look at /docs/spec.pdf', exists)).toBeNull();
    expect(pastedAttachmentPaths('/docs/missing.pdf', exists)).toBeNull();
    expect(pastedAttachmentPaths('/docs/notes.txt', () => true)).toBeNull();
  });

  it('finds @path mentions of attachable files', () => {
    expect(mentionedAttachmentPaths('see @/docs/spec.pdf and @src/x.ts', exists)).toEqual([
      { token: '@/docs/spec.pdf', path: '/docs/spec.pdf' },
    ]);
  });
});

describe('attachments', () => {
  const fs = (size: number) => ({ size: () => size, read: () => Buffer.from('PNGDATA') });

  it('reads images as bytes and PDFs as paths', () => {
    const image = attachmentFromPath('/shots/a b.png', 1, fs(2048)) as PendingAttachment;
    expect(image).toMatchObject({ label: '[Image #1]', kind: 'image', mediaType: 'image/png', path: '/shots/a b.png' });
    expect(image.data).toBe(Buffer.from('PNGDATA').toString('base64'));
    const pdf = attachmentFromPath('/docs/spec.pdf', 2, fs(4096)) as PendingAttachment;
    expect(pdf).toMatchObject({ label: '[PDF #2]', kind: 'pdf', mediaType: 'application/pdf' });
    expect(pdf.data).toBeUndefined();
  });

  it('refuses what the model or server would not take', () => {
    expect(attachmentFromPath('/shots/a.png', 1, fs(6 * 1024 * 1024))).toEqual({ error: 'a.png: too large to attach (images up to 5 MB)' });
    expect(attachmentFromPath('/docs/big.pdf', 1, fs(40 * 1024 * 1024))).toEqual({ error: 'big.pdf: too large to attach (up to 32 MB)' });
    expect(attachmentFromPath('/x/notes.txt', 1, fs(1))).toEqual({ error: 'notes.txt: only images and PDFs can be attached' });
    expect(attachmentFromPath('/x/gone.png', 1, { size: () => { throw new Error('ENOENT'); }, read: () => Buffer.alloc(0) }))
      .toEqual({ error: 'gone.png: cannot be read' });
  });

  it('numbers placeholders after the highest one in the draft', () => {
    expect(nextAttachmentNumber([])).toBe(1);
    expect(nextAttachmentNumber([{ label: '[Image #1]' }, { label: '[PDF #3]' }] as PendingAttachment[])).toBe(4);
  });

  it('turns placeholders into the web composer’s markers and drops deleted ones', () => {
    const image = attachmentFromPath('/shots/a b.png', 1, fs(2048)) as PendingAttachment;
    const pdf = attachmentFromPath('/docs/spec.pdf', 2, fs(4096)) as PendingAttachment;
    const clip = attachmentFromBytes(Buffer.from('xyz'), 3) as PendingAttachment;
    const out = buildOutgoing('compare [Image #1] with [PDF #2]', [image, pdf, clip]);
    expect(out.text).toBe(
      'compare [attachment: a b.png (png, 2 KB) @json "/shots/a b.png"] with [attachment: spec.pdf (pdf, 4 KB) @json "/docs/spec.pdf"]',
    );
    expect(out.attachments).toEqual([
      { type: 'image', data: image.data, media_type: 'image/png', filename: 'a b.png', source_path: '/shots/a b.png' },
    ]);
    expect(attachmentMarker(clip)).toBe('[attachment: image-3.png (png, 1 KB)]');
    const pasted = buildOutgoing('[Image #3]', [clip]);
    expect(pasted.attachments[0]).toEqual({ type: 'image', data: clip.data, media_type: 'image/png', filename: 'image-3.png' });
  });
});

describe('clipboard image', () => {
  it('asks osascript on macOS and returns nothing for an empty clipboard', async () => {
    const calls: string[] = [];
    const exec = async (file: string) => {
      calls.push(file);
      return { stdout: '', stderr: '', code: 1 };
    };
    expect(await readClipboardImage(exec as never, 'darwin')).toBeNull();
    expect(calls).toEqual(['osascript']);
  });

  it('tries wl-paste then xclip on Linux', async () => {
    const commands: string[] = [];
    const exec = async (_file: string, args: string[]) => {
      commands.push(args[1]!.split(' ')[0]!);
      return { stdout: '', stderr: '', code: 1 };
    };
    expect(await readClipboardImage(exec as never, 'linux')).toBeNull();
    expect(commands).toEqual(['wl-paste', 'xclip']);
  });

  it('reads the image the tool wrote', async () => {
    const { writeFileSync } = await import('fs');
    const exec = async (_file: string, args: string[]) => {
      const target = /> '([^']+)'/.exec(args[1]!)![1]!;
      writeFileSync(target, Buffer.from([0x89, 0x50]));
      return { stdout: '', stderr: '', code: 0 };
    };
    expect(await readClipboardImage(exec as never, 'linux')).toEqual(Buffer.from([0x89, 0x50]));
  });
});
