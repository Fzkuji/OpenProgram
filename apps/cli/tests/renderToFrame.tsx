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

interface RenderResult {
  lastFrame: () => string | undefined;
  frames: string[];
  unmount: () => void;
}

interface UpdatingRenderResult extends RenderResult {
  rerender: (node: ReactNode) => void;
  resize: (columns: number, rows?: number) => Promise<void>;
}

const withTerminalSize = <T,>(
  columns: number,
  rows: number,
  callback: () => T,
): T => {
  // useStdout() is hardwired to process.stdout, so width-gated layout
  // (BottomBar's cols >= 96 token segment, Welcome's column split) reads
  // the real terminal — undefined under a non-TTY runner. Pin it for the
  // duration of the render so frames don't depend on the dev's window.
  const realColumns = process.stdout.columns;
  const realRows = process.stdout.rows;
  process.stdout.columns = columns;
  process.stdout.rows = rows;

  try {
    return callback();
  } finally {
    process.stdout.columns = realColumns;
    process.stdout.rows = realRows;
  }
};

const withTerminalSizeAsync = async <T,>(
  columns: number,
  rows: number,
  callback: () => Promise<T>,
): Promise<T> => {
  const realColumns = process.stdout.columns;
  const realRows = process.stdout.rows;
  process.stdout.columns = columns;
  process.stdout.rows = rows;

  try {
    return await callback();
  } finally {
    process.stdout.columns = realColumns;
    process.stdout.rows = realRows;
  }
};

const renderInternal = (node: ReactNode, keepMounted: boolean): UpdatingRenderResult => {
  let columns = COLUMNS;
  let rows = ROWS;
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

  const instance = withTerminalSize(columns, rows, () =>
    renderSync(node, {
      stdout,
      // Real stdin would put the test runner's TTY into raw mode.
      stdin: new Stream.Readable({ read() {} }) as unknown as NodeJS.ReadStream,
      exitOnCtrlC: false,
      patchConsole: false,
    }),
  );
  let mounted = true;

  const unmount = () => {
    if (!mounted) return;
    instance.unmount();
    instance.cleanup();
    mounted = false;
  };

  if (!keepMounted) {
    // Frames are already captured, so drop the tree now rather than leaving
    // a resize listener per render for the rest of the run.
    unmount();
  }

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
    rerender: (nextNode) =>
      withTerminalSize(columns, rows, () => instance.rerender(nextNode)),
    resize: async (nextColumns, nextRows = rows) => {
      columns = nextColumns;
      rows = nextRows;
      stdout.columns = columns;
      stdout.rows = rows;
      await withTerminalSizeAsync(columns, rows, async () => {
        stdout.emit('resize');
        // Ink coalesces resize events into one microtask before rendering.
        await Promise.resolve();
      });
    },
    unmount,
  };
};

export function render(node: ReactNode): RenderResult {
  return renderInternal(node, false);
}

/** Keep one mounted tree so tests can exercise React update behavior. */
export function renderWithUpdates(node: ReactNode): UpdatingRenderResult {
  return renderInternal(node, true);
}
