import { existsSync, mkdtempSync, readFileSync, rmSync } from 'fs';
import { Stream } from 'stream';
import { tmpdir } from 'os';
import { join } from 'path';

import { describe, expect, it } from 'vitest';

import {
  createTuiReadyHandshake,
  TUI_READY_MARKER,
} from '../src/startupHandshake.js';

describe('TUI startup handshake', () => {
  it('acknowledges exactly one completed frame', async () => {
    const directory = mkdtempSync(join(tmpdir(), 'openprogram-ready-'));
    const readyPath = join(directory, 'first-frame');

    try {
      const stdin = new Stream.Readable({ read() {} }) as NodeJS.ReadStream;
      stdin.isRaw = true;
      const startup = createTuiReadyHandshake(readyPath, stdin);
      startup.onFrame();
      expect(existsSync(readyPath)).toBe(false);
      startup.mounted();
      startup.onFrame();
      expect(existsSync(readyPath)).toBe(false);
      await new Promise<void>((resolve) => setImmediate(resolve));

      expect(readFileSync(readyPath, 'utf8')).toBe(TUI_READY_MARKER);
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it('does not acknowledge a cleanup frame without raw mode', () => {
    const directory = mkdtempSync(join(tmpdir(), 'openprogram-ready-'));
    const readyPath = join(directory, 'first-frame');

    try {
      const stdin = new Stream.Readable({ read() {} }) as NodeJS.ReadStream;
      stdin.isRaw = false;
      const startup = createTuiReadyHandshake(readyPath, stdin);
      startup.onFrame();
      startup.mounted();

      expect(existsSync(readyPath)).toBe(false);
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it('is disabled when no launcher path is provided', () => {
    const startup = createTuiReadyHandshake(undefined);
    expect(() => {
      startup.onFrame();
      startup.mounted();
    }).not.toThrow();
  });
});
