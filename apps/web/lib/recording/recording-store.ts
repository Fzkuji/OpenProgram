/**
 * Demonstration recordings on the web side.
 *
 * The worker records (the workflow_recording application); the desktop shell shows the
 * floating bar. This store keeps the one recording in progress and, per
 * composer, the finished recording waiting to be sent with the next
 * message. A composer is keyed by its bound session id, or "focused" for
 * the main composer.
 */
import { create } from "zustand";

import { desktopBridge, type RecordingSummary } from "@/lib/desktop/bridge/bridge-api";

export const RECORDING_TAG = "recorded-demonstration";

interface RecordingState {
  /** The recording in progress and the composer that started it. */
  active: { id: string; composer: string } | null;
  /** Finished recordings waiting in a composer. */
  pending: Record<string, RecordingSummary>;
  setActive(active: RecordingState["active"]): void;
  setPending(composer: string, summary: RecordingSummary | null): void;
}

export const useRecordingStore = create<RecordingState>((set) => ({
  active: null,
  pending: {},
  setActive: (active) => set({ active }),
  setPending: (composer, summary) => set((state) => {
    const pending = { ...state.pending };
    if (summary) pending[composer] = summary;
    else delete pending[composer];
    return { pending };
  }),
}));

export function composerKey(bound: string | null): string {
  return bound ?? "focused";
}

/** Recording needs the desktop shell's bar, which exists on macOS only. */
export function recordingAvailable(): boolean {
  const bridge = desktopBridge();
  return !!bridge?.recording && bridge.platform === "darwin";
}

export interface RecordingStartError {
  message: string;
  /** The system access capability to open in System Settings, if one is missing. */
  capability?: string;
}

export async function startRecording(
  composer: string,
  options: { mic: boolean; deviceId?: string },
): Promise<RecordingStartError | null> {
  const bridge = desktopBridge();
  if (!bridge?.recording) return { message: "Recording needs the OpenProgram desktop app." };
  let response: Response;
  try {
    response = await fetch("/api/recordings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ purpose: "workflow" }),
    });
  } catch {
    return { message: "Could not reach the worker." };
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok || !body.id) {
    return { message: String(body.error || "The recording could not start."), capability: body.capability };
  }
  useRecordingStore.getState().setActive({ id: body.id, composer });
  const opened = await bridge.recording.openBar({
    id: body.id, maxSeconds: Number(body.max_seconds) || 600, mic: options.mic, deviceId: options.deviceId,
  }).catch(() => false);
  if (!opened) {
    await fetch(`/api/recordings/${body.id}/discard`, { method: "POST" }).catch(() => {});
    useRecordingStore.getState().setActive(null);
    return { message: "The recording bar could not open." };
  }
  return null;
}

let listening = false;

/** Route the bar's result to the composer that started the recording. */
export function listenForRecordingResults(): void {
  const bridge = desktopBridge();
  if (listening || !bridge?.recording) return;
  listening = true;
  bridge.recording.onFinished((result) => {
    const { active, setActive, setPending } = useRecordingStore.getState();
    const composer = active && active.id === result.id ? active.composer : "focused";
    setActive(null);
    if (result.summary) setPending(composer, result.summary);
  });
}

/** Screenshots sent to the model with a recording (describe.MAX_MODEL_IMAGES). */
const MAX_MODEL_IMAGES = 12;

/** The block the optimistic bubble shows until the server's real one arrives. */
export function recordingPlaceholder(summary: RecordingSummary): string {
  return `<${RECORDING_TAG} id="${summary.id}" steps="${summary.steps}" `
    + `duration_ms="${summary.duration_ms}" images="${Math.min(summary.images, MAX_MODEL_IMAGES)}"></${RECORDING_TAG}>`;
}

export function formatDuration(ms: number): string {
  const seconds = Math.max(0, ms) / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}
