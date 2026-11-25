/**
 * Meetings: the backend's shapes and the pure helpers the window formats
 * them with (no React, no browser APIs, so they unit-test in Node). The
 * host records the microphone and the computer's sound; the backend
 * (../../backend) merges them into one transcript: "me" is the
 * microphone, "others" the computer's sound output, "others:2" a
 * told-apart speaker ("Speaker 2") once the computer's sound was diarized
 * when the meeting stopped.
 */
import { spokenLanguageName } from "@/lib/voice/spoken-languages";

export type MeetingTrack = "mic" | "system";
export type MeetingState = "recording" | "transcribing" | "summarizing" | "done" | "interrupted";
/** Whether the server records the computer's sound; "" in meetings from before it reported this. */
export type SystemAudioState = "recording" | "unavailable" | "";
export type SystemAudioReason = "permission" | "unsupported" | "error" | "";
/** Whether the computer's sound was split by speaker when the meeting stopped; "" before that. */
export type DiarizationState = "running" | "done" | "unavailable" | "failed" | "";
export type DiarizationReason = "not_installed" | "no_model" | "error" | "";

export interface SystemAudio {
  state: SystemAudioState;
  reason: SystemAudioReason;
  message: string;
}

export interface MeetingStatus {
  id: string;
  title: string;
  started_at: number;
  ended_at?: number | null;
  duration_ms: number;
  state: MeetingState;
  chunks: Partial<Record<MeetingTrack, number>>;
  transcribed: number;
  skipped: number;
  failed: number;
  errors: string[];
  session_id: string;
  summary_error: string;
  has_summary: boolean;
  pending?: number;
  lines?: number;
  elapsed_ms?: number;
  system_audio?: SystemAudioState;
  system_audio_reason?: SystemAudioReason;
  system_audio_message?: string;
  language?: string;
  diarization?: DiarizationState;
  diarization_reason?: DiarizationReason;
  diarization_message?: string;
  speakers?: number;
}

export interface TranscriptLine {
  start_ms: number;
  end_ms: number;
  speaker: string;
  /** The server's English label ("Me", "Others", "Speaker 2"); the window localizes `speaker` instead. */
  label?: string;
  text: string;
}

/** What the backend's `readiness` says about this computer. */
export interface Readiness {
  speech: boolean;
  ffmpeg: boolean;
  ffmpeg_missing: string;
}

/** The application's state document. */
export interface MeetingDocument {
  meetings?: Record<string, MeetingStatus>;
  prefs?: { explained?: boolean; language?: string };
}

type Text = (en: string, zh: string) => string;

/** `05:07`, or `1:02:03` from an hour on. */
export function clock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const pad = (value: number) => String(value).padStart(2, "0");
  return hours ? `${hours}:${pad(minutes)}:${pad(seconds)}` : `${pad(minutes)}:${pad(seconds)}`;
}

/** "Me", "Others", "Speaker 2" in the reader's language. */
export function speakerLabel(speaker: string, text: Text): string {
  const [base, number] = speaker.split(":");
  if (base === "me") return text("Me", "我");
  return number ? `${text("Speaker", "发言人")} ${number}` : text("Others", "对方");
}

/** The whole transcript as plain text, one `[mm:ss] Speaker: text` line each. */
export function transcriptText(lines: TranscriptLine[], text: Text): string {
  return lines.map(line => `[${clock(line.start_ms)}] ${speakerLabel(line.speaker, text)}: ${line.text}`).join("\n");
}

/** The meeting's spoken language as the server reports it; "" means it is detected per chunk. */
export function languageLabel(language: string | undefined, text: Text): string {
  return language
    ? text(`Language: ${spokenLanguageName(language)}`, `语言：${spokenLanguageName(language)}`)
    : text("Language: detected", "语言：自动识别");
}

/** Chunks received across both tracks, how many are processed, how many wait. */
export function chunkProgress(status: Pick<MeetingStatus, "chunks" | "transcribed" | "skipped" | "failed">) {
  const received = Object.values(status.chunks ?? {}).reduce((sum, count) => sum + (count ?? 0), 0);
  const processed = status.transcribed + status.skipped + status.failed;
  return { received, processed, pending: Math.max(0, received - processed) };
}

/** One short line for a meeting's state. */
export function stateLabel(state: MeetingState, text: Text): string {
  switch (state) {
    case "recording": return text("Recording", "录制中");
    case "transcribing": return text("Transcribing", "转写中");
    case "summarizing": return text("Summarizing", "总结中");
    case "interrupted": return text("Interrupted", "已中断");
    default: return text("Done", "完成");
  }
}

/**
 * A meeting's state as the window shows it. The document says what the
 * backend last wrote; the host says whether its recording still runs. A
 * meeting the document calls live that no recording and no `summarize`
 * run is working on was left behind by a stopped worker: interrupted.
 * While the host still hands over chunks after Stop, it is transcribing.
 */
export function shownState(
  meeting: Pick<MeetingStatus, "id" | "state">,
  recording: { key: string; state: string } | null,
  summarizing: boolean,
): MeetingState {
  const mine = recording && recording.key === meeting.id ? recording.state : "";
  if (meeting.state === "recording") {
    if (mine === "recording") return "recording";
    return mine === "processing" ? "transcribing" : "interrupted";
  }
  if (meeting.state === "transcribing" || meeting.state === "summarizing") {
    return mine === "processing" || summarizing ? meeting.state : "interrupted";
  }
  return meeting.state;
}

/** The message for a refused or failed microphone capture, by the DOMException name. */
export function captureErrorMessage(name: string, text: Text): string {
  if (name === "NotAllowedError" || name === "SecurityError") {
    return text(
      "OpenProgram may not use the microphone. Allow OpenProgram under System Settings → Privacy & Security → Microphone, then start again.",
      "OpenProgram 没有使用麦克风的权限。请在“系统设置 → 隐私与安全性 → 麦克风”中允许 OpenProgram，然后再开始。",
    );
  }
  if (name === "NotFoundError") return text("No microphone was found.", "没有找到麦克风。");
  return text("The microphone could not be recorded.", "无法录制麦克风。");
}

/** The computer's sound as a meeting status reports it. */
export function systemAudioOf(status: Pick<MeetingStatus, "system_audio" | "system_audio_reason" | "system_audio_message">): SystemAudio {
  return {
    state: status.system_audio ?? "",
    reason: status.system_audio_reason ?? "",
    message: status.system_audio_message ?? "",
  };
}

/** What the recording card says about the two tracks. */
export function recordingNote(system: SystemAudio, text: Text): string {
  if (system.state === "recording") {
    return text("Recording your microphone and the computer's sound.", "正在录制你的麦克风和电脑声音。");
  }
  const lead = text(
    "Recording your microphone only: the computer's sound could not be recorded, so the other side is heard only through your microphone.",
    "只在录制麦克风：无法录制电脑声音，对方的声音只能通过你的麦克风录到。",
  );
  if (system.reason === "permission") {
    return lead + " " + text(
      "Allow OpenProgram under System Settings → Privacy & Security → Screen & System Audio Recording, then reopen the App.",
      "请在“系统设置 → 隐私与安全性 → 录屏与系统录音”中允许 OpenProgram，然后重新打开 App。",
    );
  }
  if (system.reason === "unsupported") {
    return lead + " " + text("The computer's sound is recorded on macOS only.", "电脑声音只能在 macOS 上录制。");
  }
  return system.message ? `${lead} (${system.message})` : lead;
}
