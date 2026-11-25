/**
 * The host bridge (`window.openprogramApp`) as the Meetings window uses it:
 * its operations, its state document, the host's recording and the
 * conversations its backend opened. Nothing here reaches the host another way.
 */

export interface RecordingStatus {
  id: string;
  key: string;
  /** recording → processing (chunks still being handed over, then `finish`) → done. */
  state: "recording" | "processing" | "done" | "cancelled";
  started_at: number;
  elapsed_ms: number;
  duration_ms: number;
  end_reason: string;
  received: Record<string, number>;
  delivered: number;
  failed: number;
  errors: string[];
  system_audio: "recording" | "unavailable" | "";
  system_audio_reason: "permission" | "unsupported" | "error" | "";
  system_audio_message: string;
}

export interface MicrophoneStatus {
  /** idle → starting → recording → stopping → idle, in the App's page. */
  phase: "idle" | "starting" | "recording" | "stopping";
  uploaded: number;
  uploadFailures: number;
  /** performance.now() of the host page when its recording started, as wall time. */
  startedAt: number | null;
}

export interface AudioStatus {
  recording: RecordingStatus | null;
  microphone: MicrophoneStatus;
}

export type CaptureError = { kind: "capture"; name: string } | { kind: "server"; message: string };

export interface StateDocument<T> { value: T; version: number }

interface Bridge {
  ready: Promise<void>;
  /** Resolves once the App's theme, colour scheme and language are on this page. */
  appearance: Promise<void>;
  run(operation: string, input: object, key?: string): Promise<{ id: string; status: string; result?: unknown; error?: string }>;
  status(id: string, after?: number): Promise<{ id: string; status: string; result?: unknown; error?: string; operation: string }>;
  runs(): Promise<{ id: string; status: string; operation: string }[]>;
  load(): Promise<StateDocument<unknown>>;
  save(value: unknown, version: number): Promise<StateDocument<unknown>>;
  on(event: string, listener: (value: unknown) => void): () => void;
  audio: {
    start(options: { key: string; tracks: string[]; max_seconds?: number }): Promise<{ ok: true; status: AudioStatus } | { ok: false; error: CaptureError }>;
    stop(): Promise<AudioStatus>;
    status(): Promise<AudioStatus>;
  };
  conversations: { show(sessionId: string): Promise<boolean> };
}

declare global {
  interface Window { openprogramApp: Bridge }
}

export const bridge = (): Bridge => window.openprogramApp;

const FINISHED = new Set(["completed", "failed", "cancelled", "interrupted"]);

/** Run one operation and wait for its result; a failed run throws its error. */
export async function run<T>(operation: string, input: object = {}): Promise<T> {
  const app = bridge();
  let current = await app.run(operation, input);
  // Most operations answer within a few tens of milliseconds: ask soon, then less often.
  let wait = 30;
  while (!FINISHED.has(current.status)) {
    await new Promise(resolve => setTimeout(resolve, wait));
    wait = Math.min(500, wait * 2);
    current = await app.status(current.id);
  }
  if (current.status !== "completed") throw new Error(current.error || current.status);
  return current.result as T;
}

/** Start an operation without waiting for it (the state document tells how it goes). */
export function start(operation: string, input: object = {}): Promise<unknown> {
  return bridge().run(operation, input);
}
