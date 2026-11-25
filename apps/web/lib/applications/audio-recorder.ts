/**
 * The microphone of an application's recording (capability `audio.record`),
 * recorded by the App's page.
 *
 * An application window is a sandboxed frame without a microphone of its
 * own; when it asks for a recording (`openprogramApp.audio.start`) the page
 * records the microphone with the browser's echo cancellation and posts it
 * to the worker, which records the computer's sound itself
 * (ScreenCaptureKit) and hands both tracks to the application's
 * operations (programs/_host/ui/audio.py). The microphone runs a
 * MediaRecorder restarted every CHUNK_MS so every chunk is a complete WebM
 * file the worker can convert on its own (a timeslice chunk after the
 * first has no header). Chunks upload in order, with retries, timed from
 * the worker's recording start so both tracks share one clock.
 *
 * The store lives at module level, keyed by application instance, so
 * switching tabs or closing the window does not stop a recording; only the
 * application's `stop` (or the worker's time limit) does.
 */
import { create } from "zustand";

import { applicationRequest } from "@/lib/net/applications";

/** Chunk length each track is recorded in (the page's microphone, the worker's computer sound). */
export const CHUNK_MS = 30_000;

export type AudioTrack = "mic" | "system";

export type CaptureError =
  | { kind: "capture"; name: string }
  | { kind: "server"; message: string };

/** The worker's recording, as `GET /api/application-instances/<id>/audio` reports it. */
export interface RecordingStatus {
  id: string;
  key: string;
  state: "recording" | "processing" | "done" | "cancelled";
  tracks: AudioTrack[];
  started_at: number;
  elapsed_ms: number;
  duration_ms: number;
  end_reason: string;
  max_seconds: number;
  received: Partial<Record<AudioTrack, number>>;
  delivered: number;
  failed: number;
  errors: string[];
  system_audio: "recording" | "unavailable" | "";
  system_audio_reason: string;
  system_audio_message: string;
}

/** The page's side of one instance's recording. */
export interface MicrophoneStatus {
  phase: "idle" | "starting" | "recording" | "stopping";
  uploaded: number;
  uploadFailures: number;
  /** Wall time (ms) of the worker's recording start, on this page's clock. */
  startedAt: number | null;
}

/** What `openprogramApp.audio.status()` answers. */
export interface AudioStatus {
  recording: RecordingStatus | null;
  microphone: MicrophoneStatus;
}

export interface StartOptions {
  key: string;
  tracks?: AudioTrack[];
  max_seconds?: number;
}

const IDLE: MicrophoneStatus = { phase: "idle", uploaded: 0, uploadFailures: 0, startedAt: null };

const MIME_CANDIDATES = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];

function pickMime(): string {
  if (typeof MediaRecorder === "undefined") return "";
  return MIME_CANDIDATES.find(type => MediaRecorder.isTypeSupported(type)) ?? "";
}

/** Opus frames are 20 ms; a chunk shorter than this holds no sound. */
const MIN_CHUNK_MS = 100;

/**
 * Numbering and timing of one track's chunks: each chunk carries its
 * sequence number and its offset from the recording's start, so the worker
 * can order and place them even when uploads finish out of order.
 */
export function createChunkClock(startedAt: number) {
  let seq = 0;
  return {
    next(chunkStartedAt: number): { seq: number; t_ms: number } {
      return { seq: seq++, t_ms: Math.max(0, Math.round(chunkStartedAt - startedAt)) };
    },
  };
}

/** One track: a MediaRecorder restarted every chunk, uploads in order. */
class TrackRecorder {
  private recorder: MediaRecorder | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private uploads: Promise<void> = Promise.resolve();
  private stopped = false;
  private readonly clock: ReturnType<typeof createChunkClock>;

  constructor(
    private readonly stream: MediaStream,
    private readonly url: string,
    startedAt: number,
    private readonly mime: string,
    private readonly report: (ok: boolean) => void,
    /** The worker refused a chunk: its recording ended (Stop elsewhere, the time limit). */
    private readonly ended: () => void,
  ) {
    this.clock = createChunkClock(startedAt);
  }

  start(): void {
    if (this.stopped || !this.stream.getAudioTracks().some(track => track.readyState === "live")) return;
    const parts: Blob[] = [];
    const chunkStartedAt = performance.now();
    const recorder = new MediaRecorder(this.stream, this.mime ? { mimeType: this.mime } : undefined);
    recorder.ondataavailable = event => { if (event.data.size) parts.push(event.data); };
    recorder.onstop = () => {
      // Stopped within a few milliseconds of starting (Stop right after a
      // rotation), the recorder hands over a WebM header with no sound in it,
      // which the worker could only report as a chunk it cannot convert.
      if (!parts.length || performance.now() - chunkStartedAt < MIN_CHUNK_MS) return;
      const { seq, t_ms } = this.clock.next(chunkStartedAt);
      const blob = new Blob(parts, { type: recorder.mimeType || this.mime || "audio/webm" });
      this.uploads = this.uploads.then(() => this.upload(seq, t_ms, blob));
    };
    recorder.start();
    this.recorder = recorder;
    this.timer = setTimeout(() => this.rotate(), CHUNK_MS);
  }

  private rotate(): void {
    const current = this.recorder;
    this.recorder = null;
    if (current && current.state !== "inactive") current.stop();
    this.start();
  }

  private async upload(seq: number, t_ms: number, blob: Blob): Promise<void> {
    const url = `${this.url}?track=mic&seq=${seq}&t_ms=${t_ms}`;
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const response = await fetch(url, { method: "POST", headers: { "Content-Type": blob.type }, body: blob });
        if (response.ok) { this.report(true); return; }
        if (response.status < 500) { this.report(false); this.ended(); return; }
      } catch { /* network: retry */ }
      await new Promise(resolve => setTimeout(resolve, 1000 * (attempt + 1)));
    }
    this.report(false);
  }

  /** Stop recording; resolves when the last chunk has uploaded. */
  async stop(): Promise<void> {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    const current = this.recorder;
    this.recorder = null;
    if (current && current.state !== "inactive") {
      await new Promise<void>(resolve => {
        current.addEventListener("stop", () => resolve(), { once: true });
        current.stop();
      });
    }
    await this.uploads;
  }
}

interface Live {
  recordingId: string;
  streams: MediaStream[];
  tracks: TrackRecorder[];
}

const live = new Map<string, Live>();

function release(entry: Live | undefined): void {
  for (const stream of entry?.streams ?? []) for (const track of stream.getTracks()) track.stop();
}

interface AudioRecorderState {
  /** The page's side of each instance's recording. */
  microphones: Record<string, MicrophoneStatus>;
  start(instanceId: string, options: StartOptions): Promise<{ ok: true; status: AudioStatus } | { ok: false; error: CaptureError }>;
  stop(instanceId: string): Promise<AudioStatus>;
  status(instanceId: string): Promise<AudioStatus>;
}

const base = (instanceId: string) => `/api/application-instances/${encodeURIComponent(instanceId)}/audio`;

export const useAudioRecorder = create<AudioRecorderState>((set, get) => {
  const microphone = (instanceId: string) => get().microphones[instanceId] ?? IDLE;
  const setMicrophone = (instanceId: string, change: Partial<MicrophoneStatus>) => set(state => ({
    microphones: { ...state.microphones, [instanceId]: { ...(state.microphones[instanceId] ?? IDLE), ...change } },
  }));

  async function status(instanceId: string): Promise<AudioStatus> {
    const read = await applicationRequest<{ recording: RecordingStatus | null }>(base(instanceId));
    return { recording: read.recording, microphone: microphone(instanceId) };
  }

  return {
    microphones: {},
    status,

    async start(instanceId, options) {
      if (microphone(instanceId).phase !== "idle") {
        return { ok: false, error: { kind: "server", message: "This application is already recording." } };
      }
      const tracks = (options.tracks?.length ? options.tracks : ["mic", "system"]) as AudioTrack[];
      setMicrophone(instanceId, { ...IDLE, phase: "starting" });
      const fail = (error: CaptureError, streams: MediaStream[] = []) => {
        for (const stream of streams) for (const track of stream.getTracks()) track.stop();
        setMicrophone(instanceId, IDLE);
        return { ok: false as const, error };
      };
      // 1. The microphone (macOS asks for Microphone).
      let mic: MediaStream | null = null;
      if (tracks.includes("mic")) {
        if (!navigator.mediaDevices?.getUserMedia || !pickMime()) return fail({ kind: "capture", name: "NotSupportedError" });
        try {
          mic = await navigator.mediaDevices.getUserMedia({
            audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
          });
        } catch (reason) {
          return fail({ kind: "capture", name: (reason as DOMException)?.name || "Error" });
        }
      }
      // 2. The worker's recording, which also starts recording the computer's
      //    sound (macOS asks for Screen & System Audio Recording).
      let recording: RecordingStatus;
      try {
        recording = await applicationRequest<RecordingStatus>(base(instanceId), "POST", {
          key: options.key, tracks, max_seconds: options.max_seconds,
        });
      } catch (reason) {
        return fail({ kind: "server", message: reason instanceof Error ? reason.message : String(reason) }, mic ? [mic] : []);
      }
      // The worker's clock started with the recording; the microphone's chunk offsets count from there.
      const startedAt = performance.now() - Math.max(0, recording.elapsed_ms ?? 0);
      const entry: Live = { recordingId: recording.id, streams: mic ? [mic] : [], tracks: [] };
      if (mic) {
        const report = (ok: boolean) => {
          const current = microphone(instanceId);
          setMicrophone(instanceId, ok ? { uploaded: current.uploaded + 1 } : { uploadFailures: current.uploadFailures + 1 });
        };
        const ended = () => {
          // The recording is over on the worker: stop the microphone here, upload nothing more.
          if (live.get(instanceId) !== entry) return;
          live.delete(instanceId);
          for (const track of entry.tracks) void track.stop();
          release(entry);
          setMicrophone(instanceId, IDLE);
        };
        entry.tracks.push(new TrackRecorder(mic, `${base(instanceId)}/${recording.id}/chunks`, startedAt, pickMime(), report, ended));
      }
      live.set(instanceId, entry);
      for (const track of entry.tracks) track.start();
      setMicrophone(instanceId, { phase: "recording", startedAt: performance.timeOrigin + startedAt });
      return { ok: true, status: { recording, microphone: microphone(instanceId) } };
    },

    async stop(instanceId) {
      const entry = live.get(instanceId);
      let recordingId = entry?.recordingId;
      if (!recordingId) {
        // A recording this page did not start (another window, before a reload): stop it by its id.
        recordingId = (await status(instanceId)).recording?.id;
      }
      if (entry) {
        setMicrophone(instanceId, { phase: "stopping" });
        await Promise.all(entry.tracks.map(track => track.stop()));
        release(entry);
        live.delete(instanceId);
      }
      try {
        if (recordingId) await applicationRequest(`${base(instanceId)}/${recordingId}/stop`, "POST", {});
      } finally {
        setMicrophone(instanceId, IDLE);
      }
      return status(instanceId);
    },
  };
});
