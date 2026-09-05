import React from 'react';
import { Stream } from 'stream';
import { describe, expect, it } from 'vitest';

import { renderSync, useInput } from '../src/runtime/index.js';

const RawInputConsumer: React.FC = () => {
  useInput(() => {});
  return null;
};

describe('runtime exit lifecycle', () => {
  it('preserves a synchronous raw-mode mount failure for waitUntilExit', async () => {
    const stdout = new Stream.Writable({
      write(_chunk, _encoding, callback) {
        callback();
      },
    }) as unknown as NodeJS.WriteStream;
    stdout.columns = 80;
    stdout.rows = 24;
    stdout.isTTY = false;

    const stdin = new Stream.Readable({ read() {} }) as unknown as NodeJS.ReadStream;
    stdin.isTTY = false;

    const instance = renderSync(<RawInputConsumer />, {
      stdout,
      stdin,
      stderr: stdout,
      exitOnCtrlC: false,
      patchConsole: false,
    });

    const timeout = new Promise<never>((_resolve, reject) => {
      setTimeout(() => reject(new Error('waitUntilExit timed out')), 500);
    });

    await expect(Promise.race([instance.waitUntilExit(), timeout])).rejects.toThrow(
      'Raw mode is not supported',
    );
    instance.cleanup();
  });
});
