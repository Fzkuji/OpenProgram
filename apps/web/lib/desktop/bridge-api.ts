// Desktop bridge api responsibilities.
import "@/lib/net/ws-events";
import type { DesktopBrowserDataApi, DesktopBrowserImportApi, DesktopDownloadsApi, DesktopHistoryApi, DesktopMainMenuApi, DesktopNativeMenuApi, DesktopTerminalApi, DesktopThemeApi, DesktopUpdateApi, DesktopWebTabApi } from "@/lib/desktop/desktop-bridge-types";
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
  selfUpdateCapture?(nonce: string): Promise<{ ok: boolean; reason?: string }>;
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
  /** Close this window (last tab closed → close window). */
  closeWindow?(): void;
  /** Move this window by a pixel delta (single-tab drag = move window). */
  moveWindowBy?(dx: number, dy: number): void;
  webTab: DesktopWebTabApi;
  tabTransfer: DesktopTabTransferApi;
  /** Top-layer ⋮ menu overlay. Absent in shells older than this build. */
  mainMenu?: DesktopMainMenuApi;
  contextMenu?: DesktopNativeMenuApi;
  /** Absent in shells older than the browsing-history build. */
  history?: DesktopHistoryApi;
  /** Desktop-only download history and active download controls. */
  downloads?: DesktopDownloadsApi;
  updates: DesktopUpdateApi;
  /** Desktop-only, explicit import from a detected local browser profile. */
  browserImport?: DesktopBrowserImportApi;
  /** Desktop-only clearing of the built-in browser profile. */
  browserData?: DesktopBrowserDataApi;
  /** Desktop-only local PTY. Never exposed by the Web server. */
  terminal?: DesktopTerminalApi;
  /** Keep the native window background aligned with the resolved web theme. */
  theme?: DesktopThemeApi;
}

/** The preload-exposed bridge, or null outside the desktop shell. */
export function desktopBridge(): DesktopBridge | null {
  if (typeof window === "undefined") return null;
  return window.openprogramDesktop ?? null;
}
