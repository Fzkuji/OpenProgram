import { writeFileSync } from 'fs';

export const TUI_READY_MARKER = 'OpenProgram Ink TUI first frame ready\n';

export interface TuiReadyHandshake {
  onFrame: () => void;
  mounted: () => void;
}

/**
 * Coordinate Ink mount/raw-mode setup with its first completed frame.
 *
 * The Python launcher creates the path in a private temporary directory and
 * uses the marker to distinguish initialization failures from errors after the
 * TUI has started.  Keeping the acknowledgement on the first-frame boundary
 * proves that raw input setup, React mount, layout, terminal output, and the
 * Ink renderer all completed; elapsed wall-clock time cannot prove that.
 */
export function createTuiReadyHandshake(
  readyPath = process.env._OPENPROGRAM_TUI_READY_FILE,
  stdin = process.stdin,
): TuiReadyHandshake {
  let notified = false;
  let notificationScheduled = false;
  let frameRendered = false;
  let mountCompleted = false;

  const notifyIfReady = (): void => {
    // Ink also renders once while unwinding a synchronous mount error. Require
    // both a returned render() call and raw mode so that cleanup frame cannot
    // masquerade as successful startup.
    if (
      !readyPath
      || notified
      || notificationScheduled
      || !frameRendered
      || !mountCompleted
      || stdin.isRaw !== true
    ) return;

    // Ink records synchronous mount errors in waitUntilExit() instead of
    // throwing from render(). Defer acknowledgement one event-loop turn: the
    // already-rejected exit promise then terminates the process before a
    // cleanup render can be mistaken for successful startup.
    notificationScheduled = true;
    setImmediate(() => {
      notificationScheduled = false;
      if (notified || stdin.isRaw !== true) return;

      // The launcher guarantees that the path does not exist. Exclusive create
      // prevents a stale or externally replaced marker from being accepted.
      writeFileSync(readyPath, TUI_READY_MARKER, { encoding: 'utf8', flag: 'wx' });
      notified = true;
    });
  };

  return {
    onFrame: () => {
      frameRendered = true;
      notifyIfReady();
    },
    mounted: () => {
      mountCompleted = true;
      notifyIfReady();
    },
  };
}
