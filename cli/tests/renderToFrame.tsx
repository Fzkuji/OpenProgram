import type { ReactNode } from 'react';
import { Stream } from 'stream';
import { renderSync } from '../src/runtime/index';

/**
 * Minimal render harness for the vendored cell-grid runtime.
 *
 * ink-testing-library is not usable here: it drives stock `ink`, while
 * every component under test renders through `src/runtime` (hermes-ink
 * vendored as the cell-grid renderer). Two renderers means two
 * react-reconciler trees, so the library can never see our frames.
 * renderSync already takes any writable stdout, so capturing writes off
 * a fake stream is all a `lastFrame()` needs.
 */
/** Width the components lay out against. Wide enough that the BottomBar
 * shows every width-gated segment (tokens gate at cols >= 96). */
const COLUMNS = 100;
const ROWS = 40;

export function render(node: ReactNode): {
  lastFrame: () => string | undefined;
  frames: string[];
  unmount: () => void;
} {
  // useStdout() is hardwired to process.stdout, so width-gated layout
  // (BottomBar's cols >= 96 token segment, Welcome's column split) reads
  // the real terminal — undefined under a non-TTY runner. Pin it for the
  // duration of the render so frames don't depend on the dev's window.
  const realColumns = process.stdout.columns;
  const realRows = process.stdout.rows;
  process.stdout.columns = COLUMNS;
  process.stdout.rows = ROWS;

  const frames: string[] = [];
  const stdout = new Stream.Writable({
    write(chunk, _enc, cb) {
      frames.push(String(chunk));
      cb();
    },
  }) as unknown as NodeJS.WriteStream;
  // The renderer reads these to size the frame; a Writable has neither.
  stdout.columns = COLUMNS;
  stdout.rows = ROWS;
  stdout.isTTY = true;

  let instance: ReturnType<typeof renderSync>;
  try {
    instance = renderSync(node, {
      stdout,
      // Real stdin would put the test runner's TTY into raw mode.
      stdin: new Stream.Readable({ read() {} }) as unknown as NodeJS.ReadStream,
      exitOnCtrlC: false,
      patchConsole: false,
    });
  } finally {
    // NODE_ENV=test makes the reconciler render synchronously, so the
    // frame is already captured by the time we get here.
    process.stdout.columns = realColumns;
    process.stdout.rows = realRows;
  }
  // Frames are already captured, so drop the tree now rather than leaving
  // a resize listener per render for the rest of the run.
  instance.unmount();
  instance.cleanup();

  // The cell-grid renderer advances over blank cells with cursor-forward
  // (CSI <n> C) instead of emitting spaces, so a caller that strips ANSI
  // would glue words together ("reply text" -> "replytext"). Expand those
  // back into real spaces to keep the frame plain text.
  const expandCursorForward = (s: string): string =>
    s.replace(/\x1b\[(\d*)C/g, (_m, n: string) => ' '.repeat(Number(n || '1')));

  // Bare control sequences (cursor hide/show, sync markers) are written as
  // their own chunks, so the final write is often not the frame. Take the
  // last write that still carries printable content.
  const hasContent = (s: string): boolean =>
    s.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '').replace(/\x1b\][^\x07\x1b]*(\x07|\x1b\\)/g, '').trim() !== '';

  return {
    frames,
    lastFrame: () => {
      const frame = frames.filter(hasContent).at(-1);
      return frame === undefined ? undefined : expandCursorForward(frame);
    },
    // Already unmounted above; kept so callers can write the usual
    // render/unmount pair without a double-unmount.
    unmount: () => {},
  };
}
