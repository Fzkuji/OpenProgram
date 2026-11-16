// Desktop bridge api responsibilities.
import "@/lib/net/ws/ws-events";
import type { DesktopAttentionApi, DesktopBrowserDataApi, DesktopBrowserImportApi, DesktopDownloadsApi, DesktopHistoryApi, DesktopMainMenuApi, DesktopNativeMenuApi, DesktopTerminalApi, DesktopThemeApi, DesktopUpdateApi, DesktopWebTabApi } from "@/lib/desktop/desktop-bridge-types";
import type { DesktopTabTransferApi } from "@/lib/desktop/desktop-transfer-types";



export interface DesktopReopenState {
  updateId: string | null;
  sessionId: string | null;
  status: "inactive" | "pending" | "acknowledged" | "unavailable" | "manual_navigation";
  reason: string | null;
}

export interface DesktopBridge {
  readonly isDesktop: true;
  readonly platform?: "darwin" | "win32" | "linux";
  readonly windowId: string;
  writeClipboardText?(text: string): Promise<void>;
  selfUpdateCapture?(nonce: string): Promise<{ ok: boolean; reason?: string }>;
  /** One-time owner token after worker credential rotation. Never persisted. */
  refreshOwnerAuth?(): Promise<string | null>;
  /** Update-triggered original-session recovery; absent in older shells. */
  selfUpdateReopen?: {
    getState(): Promise<DesktopReopenState | null>;
    sessionLoaded(sessionId: string): Promise<DesktopReopenState | null>;
    onState(callback: (state: DesktopReopenState) => void): () => void;
  };
  /** Absolute native path for a user-selected/dropped File. */
  getPathForFile?(file: File): string;
  /** shell.openExternal — http/https only. */
  openExternal(url: string): void;
  /** Open a local file in the app the system opens it with; false for a
   *  folder, a missing file or one the system would run. */
  openFileInDefaultApp?(path: string): Promise<boolean>;
  /** Close this window (last tab closed → close window). */
  closeWindow?(): void;
  /** Move this window by a pixel delta (single-tab drag = move window). */
  moveWindowBy?(dx: number, dy: number): void;
  /** A small JPEG of a region of this page (CSS px), for overview thumbnails. */
  captureRegion?(rect: { x: number; y: number; width: number; height: number }): Promise<string | null>;
  /** Video of one pane of this page. Absent in shells older than this build. */
  paneRecording?: {
    arm(): Promise<boolean>;
    open(id: string, filePath: string): Promise<boolean>;
    write(id: string, chunk: ArrayBuffer): Promise<boolean>;
    close(id: string, discard?: boolean): Promise<{ path: string; bytes: number } | null>;
  };
  webTab: DesktopWebTabApi;
  tabTransfer: DesktopTabTransferApi;
  /** Keeps reporting the cursor (page CSS px) while a right or middle button
   *  stays down, past the window's edge too. macOS only; returns the stop. */
  pointerFollow?: {
    start(
      held: number,
      onCursor: (point: { x: number; y: number }) => void,
      onReleased: () => void,
    ): () => void;
  };
  /** Top-layer ⋮ menu overlay. Absent in shells older than this build. */
  mainMenu?: DesktopMainMenuApi;
  contextMenu?: DesktopNativeMenuApi;
  /** Absent in shells older than the browsing-history build. */
  history?: DesktopHistoryApi;
  /** Desktop-only download history and active download controls. */
  downloads?: DesktopDownloadsApi;
  updates: DesktopUpdateApi;
  /** Notifications and keep-awake. Absent in shells older than this build. */
  attention?: DesktopAttentionApi;
  /** Desktop-only, explicit import from a detected local browser profile. */
  browserImport?: DesktopBrowserImportApi;
  /** Desktop-only clearing of the built-in browser profile. */
  browserData?: DesktopBrowserDataApi;
  /** Desktop-only local PTY. Never exposed by the Web server. */
  terminal?: DesktopTerminalApi;
  /** Keep the native window background aligned with the resolved web theme. */
  theme?: DesktopThemeApi;
  /** Draw the agent cursor at a screen point an Agent step acts on in a
   *  desktop app. Absent in shells older than this build. */
  nativeActionCue?(cue: NativeActionCue): void;
  /** Lay the control overlay (glowing edge, control badge, agent cursor)
   *  over the desktop app window an Agent controls, or take it down.
   *  Absent in shells older than this build. */
  nativeControl?(control: NativeControl): void;
  /** The floating bar of a demonstration recording. Absent in shells older
   *  than this build and on platforms without a recorder. */
  recording?: DesktopRecordingApi;
}

/** What the recording bar is opened with. */
export interface RecordingBarOptions {
  id: string;
  maxSeconds: number;
  mic: boolean;
  deviceId?: string;
}

/** The bar's result: the stopped recording's summary, or null when discarded. */
export interface RecordingFinished {
  id: string;
  summary: RecordingSummary | null;
  error?: string;
}

export interface RecordingSummary {
  id: string;
  purpose: string;
  steps: number;
  duration_ms: number;
  images: number;
  end_reason?: string;
}

export interface DesktopRecordingApi {
  /** Open the bar and move the calling window out of the way. */
  openBar(options: RecordingBarOptions): Promise<boolean>;
  /** Called by the bar: close it and tell the window that opened it. */
  finish(result: RecordingFinished): void;
  /** The window that opened the bar hears the result here. */
  onFinished(callback: (result: RecordingFinished) => void): () => void;
}

/** One Agent step in a desktop app: its screen point (points, origin at the
 *  top left of the main display) and the identity that dedupes the frame
 *  every App window receives. */
export interface NativeActionCue {
  session_id: string;
  x: number;
  y: number;
  sequence: number;
  at: number;
  action?: string;
}

/** Where the desktop app window an Agent controls is, in screen points, or
 *  (``active: false``) that control ended. Rects are [x, y, width, height];
 *  ``buttons`` is relative to the window, the rest are on screen. */
export interface NativeControl {
  session_id: string;
  window_id: number;
  active: boolean;
  app_name: string;
  frame: number[] | null;
  buttons: number[] | null;
  occluders: number[][];
}

/** The preload-exposed bridge, or null outside the desktop shell. */
export function desktopBridge(): DesktopBridge | null {
  if (typeof window === "undefined") return null;
  return window.openprogramDesktop ?? null;
}
