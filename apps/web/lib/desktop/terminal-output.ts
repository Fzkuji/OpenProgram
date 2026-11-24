/** Reading a terminal's output when it writes some, not on a timer.
 *
 *  The native host pushes each piece of output as the terminal writes it
 *  (`terminal:data`, apps/desktop/main/terminal/terminal-resource-ipc.js). A
 *  view still reads from its own cursor, so nothing is lost or drawn twice:
 *  a push only says "read now". Reads never overlap; a push during a read
 *  reads once more after it. A read that fails is tried again a little later
 *  each time, up to `maxDelay`. */

/** What one read found: more already waiting, nothing more, a failure, or
 *  a reason to stop reading (the terminal became another instance). */
export type OutputRead = "more" | "idle" | "failed" | "stop";

export type OutputFollower = { poke(): void; dispose(): void };

export function followTerminalOutput(read: () => Promise<OutputRead>, options: {
  schedule?: (run: () => void, ms: number) => unknown;
  cancel?: (handle: unknown) => void;
  maxDelay?: number;
} = {}): OutputFollower {
  const schedule = options.schedule ?? ((run, ms) => setTimeout(run, ms));
  const cancel = options.cancel ?? (handle => clearTimeout(handle as ReturnType<typeof setTimeout>));
  const maxDelay = options.maxDelay ?? 10_000;
  let reading = false, again = false, stopped = false, failures = 0;
  let retry: unknown;
  const poke = () => {
    if (stopped) return;
    if (reading) { again = true; return; }
    if (retry !== undefined) { cancel(retry); retry = undefined; }
    reading = true;
    again = false;
    void read().catch((): OutputRead => "failed").then(result => {
      reading = false;
      if (stopped) return;
      if (result === "stop") { stopped = true; return; }
      if (result === "failed") {
        failures += 1;
        retry = schedule(() => { retry = undefined; poke(); }, Math.min(maxDelay, 500 * 2 ** failures));
        return;
      }
      failures = 0;
      if (result === "more" || again) poke();
    });
  };
  return {
    poke,
    dispose() {
      stopped = true;
      if (retry !== undefined) cancel(retry);
      retry = undefined;
    },
  };
}

/** Whether a pushed piece of output belongs to this view's terminal. */
export function outputFor(chunk: unknown, terminalId: string, generation: string): boolean {
  const value = chunk as { id?: unknown; generation?: unknown } | null;
  return !!value && value.id === terminalId && value.generation === generation;
}
