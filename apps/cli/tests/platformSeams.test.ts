import { EventEmitter } from 'events';
import { mkdirSync, mkdtempSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
import path from 'path';
import { describe, expect, it } from 'vitest';

import { browserOpenCommand, openInBrowser } from '../src/utils/backend.js';
import { clipboardInput } from '../src/utils/clipboard.js';
import { exportTarget, fileCompletions, mentionPath } from '../src/utils/fileCompletions.js';

describe('opening the browser', () => {
  it('uses a program that exists on each platform', () => {
    const url = 'http://127.0.0.1:18200/#a=1&b=2';
    expect(browserOpenCommand(url, 'darwin')).toEqual(['open', [url]]);
    expect(browserOpenCommand(url, 'linux')).toEqual(['xdg-open', [url]]);
    // `start` is a cmd.exe builtin: spawning it fails with ENOENT.
    expect(browserOpenCommand(url, 'win32'))
      .toEqual(['rundll32', ['url.dll,FileProtocolHandler', url]]);
  });

  it('survives a missing opener, which Node reports after spawn returns', async () => {
    const child = Object.assign(new EventEmitter(), { unref: () => undefined });
    const seen: unknown[] = [];
    openInBrowser('http://127.0.0.1:18200/', (command, args, options) => {
      seen.push(command, args, options);
      return child;
    }, 'win32');
    await new Promise((resolve) => setImmediate(resolve));
    expect(seen).toEqual([
      'rundll32',
      ['url.dll,FileProtocolHandler', 'http://127.0.0.1:18200/'],
      { stdio: 'ignore', detached: true, windowsHide: true },
    ]);
    // An EventEmitter with no 'error' listener throws on emit.
    expect(() => child.emit('error', new Error('spawn xdg-open ENOENT'))).not.toThrow();
  });
});

describe('clipboard helper input', () => {
  it('gives clip.exe UTF-16 with a byte order mark so CJK survives', () => {
    for (const binary of ['clip', 'clip.exe']) {
      const input = clipboardInput(binary, '你好 ok');
      expect(Buffer.isBuffer(input)).toBe(true);
      const bytes = input as Buffer;
      expect([...bytes.subarray(0, 2)]).toEqual([0xff, 0xfe]);
      expect(bytes.subarray(2).toString('utf16le')).toBe('你好 ok');
    }
  });

  it('leaves UTF-8 helpers alone', () => {
    for (const binary of ['pbcopy', 'wl-copy', 'xclip', 'xsel']) {
      expect(clipboardInput(binary, '你好 ok')).toBe('你好 ok');
    }
  });
});

describe('paths typed and shown in the prompt', () => {
  it('writes an @ mention with forward slashes on Windows', () => {
    expect(mentionPath('src\\screens\\REPL.tsx', '\\')).toBe('src/screens/REPL.tsx');
    expect(mentionPath('src/screens/REPL.tsx', '/')).toBe('src/screens/REPL.tsx');
  });

  it('finds a nested file by the path the user types', () => {
    const root = mkdtempSync(path.join(tmpdir(), 'openprogram-cli-'));
    mkdirSync(path.join(root, 'src', 'screens'), { recursive: true });
    writeFileSync(path.join(root, 'src', 'screens', 'REPL.tsx'), '');
    expect(fileCompletions('src/screens/re', root))
      .toEqual([{ path: 'src/screens/REPL.tsx', isDir: false }]);
  });

  it('keeps a full Windows path given to /export', () => {
    const cwd = 'C:\\Users\\me\\proj';
    expect(exportTarget('C:\\notes\\a.md', cwd, path.win32)).toBe('C:\\notes\\a.md');
    expect(exportTarget('\\\\server\\share\\a.md', cwd, path.win32)).toBe('\\\\server\\share\\a.md');
    expect(exportTarget('out\\a.md', cwd, path.win32)).toBe('C:\\Users\\me\\proj\\out\\a.md');
    expect(exportTarget('/tmp/a.md', '/home/me', path.posix)).toBe('/tmp/a.md');
    expect(exportTarget('a.md', '/home/me', path.posix)).toBe('/home/me/a.md');
  });
});
