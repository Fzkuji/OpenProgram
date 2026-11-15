/**
 * The meeting recording in progress, on the page side.
 *
 * The page records the microphone (getUserMedia); the server records the
 * computer's sound output itself (ScreenCaptureKit) from the moment the
 * meeting starts, and the start response says whether it could
 * (`system_audio`). The microphone runs a MediaRecorder restarted every
 * CHUNK_MS so every chunk is a complete WebM file the server can convert
 * on its own (a timeslice chunk after the first has no header). Chunks
 * upload in order, with retries, to /api/meetings/<id>/audio, timed from
 * the server's meeting start so both tracks share one clock.
 *
 * The store lives at module level, so switching tabs or closing the
 * Meetings window does not stop a recording; only Stop does.
 */
import { create } from "zustand";

import {
  CHUNK_MS, createChunkClock, systemAudioOf, type MeetingStatus, type MeetingTrack, type SystemAudio,
} from "./meeting-format";

export type MeetingRecorderError =
  | { kind: "capture"; name: string }
  | { kind: "server"; message: string };

interface ActiveMeeting {
  id: string;
  title: string;
  /** performance.now() when recording started. */
  startedAt: number;
  /** Whether the server records the computer's sound, and why not. */
  systemAudio: SystemAudio;
  /** The spoken language the server transcribes in; "" detects it. */
  language: string;
}

interface MeetingRecorderState {
  active: ActiveMeeting | null;
  phase: "idle" | "starting" | "recording" | "stopping";
  uploaded: number;
  uploadFailures: number;
  error: MeetingRecorderError | null;
  /** `language` is an ISO 639 code; "" (the default) lets the server detect it. */
  start(title: string, language?: string): Promise<boolean>;
  stop(): Promise<MeetingStatus | null>;
  clearError(): void;
}

const MIME_CANDIDATES = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];

function pickMime(): string {
  if (typeof MediaRecorder === "undefined") return "";
  return MIME_CANDIDATES.find(type => MediaRecorder.isTypeSupported(type)) ?? "";
}

async function postJson<T>(url: string, body?: unknown): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(String((data as { error?: string }).error || `HTTP ${response.status}`));
  return data as T;
}

/** Opus frames are 20 ms; a chunk shorter than this holds no sound. */
const MIN_CHUNK_MS = 100;

/** One track: a MediaRecorder restarted every chunk, uploads in order. */
class TrackRecorder {
  private recorder: MediaRecorder | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private uploads: Promise<void> = Promise.resolve();
  private stopped = false;
  private readonly clock: ReturnType<typeof createChunkClock>;
  private readonly stream: MediaStream;
  private readonly track: MeetingTrack;
  private readonly meetingId: string;
  private readonly mime: string;
  private readonly report: (ok: boolean) => void;

  constructor(stream: MediaStream, track: MeetingTrack, meetingId: string, startedAt: number, mime: string,
    report: (ok: boolean) => void) {
    this.stream = stream;
    this.track = track;
    this.meetingId = meetingId;
    this.mime = mime;
    this.report = report;
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
      // which the server could only report as a chunk it cannot convert.
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
    const url = `/api/meetings/${this.meetingId}/audio?track=${this.track}&seq=${seq}&t_ms=${t_ms}`;
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const response = await fetch(url, { method: "POST", headers: { "Content-Type": blob.type }, body: blob });
        if (response.ok) { this.report(true); return; }
        if (response.status < 500) break;
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

let streams: MediaStream[] = [];
let tracks: TrackRecorder[] = [];

function releaseStreams(): void {
  for (const stream of streams) for (const track of stream.getTracks()) track.stop();
  streams = [];
}

export const useMeetingRecorder = create<MeetingRecorderState>((set, get) => ({
  active: null,
  phase: "idle",
  uploaded: 0,
  uploadFailures: 0,
  error: null,

  async start(title, language = "") {
    if (get().phase !== "idle") return false;
    set({ phase: "starting", error: null, uploaded: 0, uploadFailures: 0 });
    const fail = (error: MeetingRecorderError) => { releaseStreams(); set({ phase: "idle", error }); return false; };
    if (!navigator.mediaDevices?.getUserMedia || !pickMime()) {
      return fail({ kind: "capture", name: "NotSupportedError" });
    }
    // 1. The microphone (macOS asks for Microphone).
    let mic: MediaStream;
    try {
      mic = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (reason) {
      return fail({ kind: "capture", name: (reason as DOMException)?.name || "Error" });
    }
    streams.push(mic);
    // 2. The meeting on the server, which also starts recording the computer's
    //    sound (macOS asks for Screen & System Audio Recording).
    let meeting: MeetingStatus;
    try {
      meeting = await postJson<MeetingStatus>("/api/meetings", language ? { title, language } : { title });
    } catch (reason) {
      return fail({ kind: "server", message: reason instanceof Error ? reason.message : String(reason) });
    }
    // The server's clock started with the meeting; the microphone's chunk offsets count from there.
    const startedAt = performance.now() - Math.max(0, meeting.elapsed_ms ?? 0);
    const mime = pickMime();
    const report = (ok: boolean) => set(state => ok
      ? { uploaded: state.uploaded + 1 }
      : { uploadFailures: state.uploadFailures + 1 });
    tracks = [new TrackRecorder(mic, "mic", meeting.id, startedAt, mime, report)];
    for (const track of tracks) track.start();
    const systemAudio = systemAudioOf(meeting);
    set({ phase: "recording", active: {
      id: meeting.id, title: meeting.title, startedAt, systemAudio, language: meeting.language ?? language,
    } });
    return true;
  },

  async stop() {
    const active = get().active;
    if (!active || get().phase !== "recording") return null;
    set({ phase: "stopping" });
    await Promise.all(tracks.map(track => track.stop()));
    tracks = [];
    releaseStreams();
    let status: MeetingStatus | null = null;
    try {
      status = await postJson<MeetingStatus>(`/api/meetings/${active.id}/stop`);
    } catch (reason) {
      set({ error: { kind: "server", message: reason instanceof Error ? reason.message : String(reason) } });
    }
    set({ phase: "idle", active: null });
    return status;
  },

  clearError() { set({ error: null }); },
}));
