import type { ReactNode } from 'react';
import { Stream } from 'stream';
import { renderSync } from '../src/runtime/index';

/**
 * Render a tree with a stdin the test can type into. The stream claims
 * to be a TTY so the runtime enables raw input and reads keystrokes from
 * it; every `type()` waits for the runtime's input flush.
 */
export function renderWithKeys(node: ReactNode) {
  const frames: string[] = [];
  const stdout = new Stream.Writable({
    write(chunk, _enc, cb) {
      frames.push(String(chunk));
      cb();
    },
  }) as unknown as NodeJS.WriteStream;
  stdout.columns = 100;
  stdout.rows = 40;
  stdout.isTTY = true;
  const stdin = new Stream.Readable({ read() {} }) as unknown as NodeJS.ReadStream & {
    setRawMode: (on: boolean) => void;
    ref: () => void;
    unref: () => void;
  };
  stdin.isTTY = true;
  stdin.setRawMode = () => {};
  stdin.ref = () => {};
  stdin.unref = () => {};
  stdin.setEncoding('utf8');
  const realColumns = process.stdout.columns;
  process.stdout.columns = 100;
  const instance = renderSync(node, { stdout, stdin, exitOnCtrlC: false, patchConsole: false });
  const settle = () => new Promise((r) => setTimeout(r, 80));
  return {
    frames,
    lastFrame: () => frames.filter((f) => f.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '').trim()).at(-1) ?? '',
    type: async (data: string) => {
      (stdin as unknown as Stream.Readable).push(data);
      await settle();
    },
    rerender: (next: ReactNode) => instance.rerender(next),
    settle,
    unmount: () => {
      instance.unmount();
      instance.cleanup();
      process.stdout.columns = realColumns;
    },
  };
}

export const KEYS = {
  enter: '\r',
  up: '\x1b[A',
  down: '\x1b[B',
  escape: '\x1b',
  paste: (text: string) => `\x1b[200~${text}\x1b[201~`,
};
