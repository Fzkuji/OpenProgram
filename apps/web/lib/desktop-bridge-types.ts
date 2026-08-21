import type { ThemeId, ThemeMode, ThemeStyle } from "@/lib/prefs/theme-pref";

export interface DesktopThemeChrome {
  theme: ThemeId;
  style?: ThemeStyle;
  mode?: ThemeMode;
  backgroundColor?: string;
  /** Optional accent override; empty/omitted uses the package --accent-orange. */
  accentColor?: string;
}

export interface DesktopThemeApi {
  /** Update BrowserWindow background so the next show/reload matches the web theme. */
  setChrome(payload: DesktopThemeChrome): void;
}

/** Public WebTab contracts exposed by the Electron preload bridge. */
export interface DesktopWebTabState {
  id: string;
  url?: string;
  title?: string;
  loading?: boolean;
  canGoBack?: boolean;
  canGoForward?: boolean;
  /** "" means the new page has no favicon — clear the tab's icon. */
  faviconUrl?: string;
}

export interface DesktopWebTabFindResult {
  id: string;
  activeMatchOrdinal: number;
  matches: number;
  finalUpdate: boolean;
}

export interface DesktopWebTabBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface DesktopVisibleWebView {
  id: string;
  bounds: DesktopWebTabBounds;
}

export interface DesktopWebTabApi {
  /** Create the WebContentsView for `id` if missing, then loadURL. */
  ensure(id: string, url: string): void;
  /** loadURL on the existing view (created if missing). */
  navigate(id: string, url: string): void;
  /** Resolve this view's CDP target, optionally navigating it first. */
  activate(
    id: string,
    url?: string,
    requireVisible?: boolean,
  ): Promise<string | null>;
  /** Resolve an owned Page target without changing visible tabs or focus. */
  resolve?(id: string): Promise<string | null>;
  /** Read the exact native Page target and live metadata without revealing it. */
  inspect?(id: string): Promise<{
    target_id: string;
    url: string;
    title: string;
  } | null>;
  /** Bounded DOM/ARIA preview for a currently visible native view. */
  preview(id: string): Promise<{
    tab_id: string;
    target_id: string;
    url: string;
    title: string;
    preview: {
      visible_text_excerpt: string;
      text_truncated: boolean;
      aria_landmarks: Array<{ role: string; name: string }>;
      landmarks_truncated: boolean;
      interactive_count: number;
    };
  } | null>;
  /** DIP rect relative to the window content area. */
  setBounds(id: string, bounds: DesktopWebTabBounds): void;
  show(id: string): void;
  hide(id: string): void;
  /** Atomically replace the native views visible in this renderer window. */
  syncVisible(items: DesktopVisibleWebView[]): void;
  destroy(id: string): void;
  reload(id: string): void;
  stop(id: string): void;
  goBack(id: string): void;
  goForward(id: string): void;
  find?(
    id: string,
    query: string,
    options?: { forward?: boolean; findNext?: boolean },
  ): void;
  stopFind?(id: string, action: "clearSelection" | "keepSelection" | "activateSelection"): void;
  zoom?(id: string, action: "in" | "out" | "reset"): Promise<number | null>;
  print?(id: string): Promise<boolean>;
  /** Snapshot of the native page as a data URL, for PiP drag placeholders. */
  capture?(id: string): Promise<string | null>;
  /** Navigation/title/loading events pushed from main; returns the
   *  unsubscribe function. */
  onState(cb: (state: DesktopWebTabState) => void): () => void;
  /** Valid http(s) page popup delegated by the owning native web view. */
  onPopup?(cb: (popup: { openerId: string; url: string }) => void): () => void;
  onFindResult?(cb: (result: DesktopWebTabFindResult) => void): () => void;
  onCommand?(cb: (command: { id: string; command: "find" }) => void): () => void;
}
/** One recorded page visit from the desktop browsing history. */
export interface DesktopHistoryEntry {
  url: string;
  title: string;
  faviconUrl: string;
  visitedAt: number;
}

export interface DesktopHistoryApi {
  list(options?: { limit?: number; query?: string }): Promise<DesktopHistoryEntry[]>;
  remove(url: string, visitedAt: number): Promise<boolean>;
  clear(): Promise<boolean>;
}

export interface DesktopDownloadEntry {
  id: string;
  filename: string;
  path: string;
  url: string;
  state: "progressing" | "completed" | "cancelled" | "interrupted";
  receivedBytes: number;
  totalBytes: number;
  startedAt: number;
  updatedAt: number;
  active: boolean;
}

export interface DesktopDownloadsApi {
  list(options?: { query?: string }): Promise<DesktopDownloadEntry[]>;
  open(id: string): Promise<boolean>;
  show(id: string): Promise<boolean>;
  cancel(id: string): Promise<boolean>;
  clear(): Promise<boolean>;
  onChanged(cb: (entry: DesktopDownloadEntry | null) => void): () => void;
}

export interface DesktopUpdateRelease {
  status: "available" | "up-to-date";
  currentVersion: string;
  latestVersion: string;
  publishedAt: string;
  releaseName: string;
  releaseNotes: string;
  releaseUrl: string;
}

export interface DesktopUpdateState {
  status: "idle" | "checking" | "up-to-date" | "available" | "downloading" | "downloaded" | "error";
  currentVersion: string;
  automaticChecks: boolean;
  checkedAt: number | null;
  release: DesktopUpdateRelease | null;
  progress: { downloaded: number; total: number } | null;
  error: string | null;
}

export interface DesktopUpdateApi {
  getState(): Promise<DesktopUpdateState | null>;
  check(): Promise<DesktopUpdateState | null>;
  setAutomaticChecks(enabled: boolean): Promise<DesktopUpdateState | null>;
  download(): Promise<DesktopUpdateState | null>;
  openRelease(): Promise<void>;
  onState(cb: (state: DesktopUpdateState) => void): () => void;
}

export interface DesktopBrowserImportProfile {
  id: string;
  name: string;
  available: { history: boolean; bookmarks: boolean; cookies: boolean };
}

export interface DesktopBrowserImportSource {
  id: string;
  name: string;
  profiles: DesktopBrowserImportProfile[];
}

export type DesktopBrowserImportBookmark =
  | { kind: "bookmark"; title: string; url: string; faviconUrl?: string }
  | { kind: "folder"; title: string; children: DesktopBrowserImportBookmark[] };

export interface DesktopBrowserImportResult {
  ok: boolean;
  error?: string;
  source?: { browserId: string; profileId: string; label: string };
  history?: { imported: number; total: number };
  bookmarks?: DesktopBrowserImportBookmark[];
  cookies?: { imported: number; failed: number };
}

export interface DesktopBrowserImportApi {
  listSources(): Promise<DesktopBrowserImportSource[]>;
  run(request: {
    requestId: string;
    browserId: string;
    profileId: string;
    items: Array<"history" | "bookmarks" | "cookies">;
  }): Promise<DesktopBrowserImportResult>;
  cancel?(requestId: string): Promise<boolean>;
}

export interface DesktopBrowserDataApi {
  clear(options: { history: boolean; cookies: boolean }): Promise<{ ok: boolean }>;
}

export interface DesktopTerminalApi {
  start(request: {
    id: string;
    cwd?: string;
    preset: "shell" | "claude";
    cols: number;
    rows: number;
  }): Promise<{ ok: boolean; error?: string; reused?: boolean; pid?: number }>;
  write(id: string, data: string): void;
  resize(id: string, cols: number, rows: number): void;
  stop(id: string): void;
  onData(cb: (payload: {
    id: string;
    data: string;
    done?: boolean;
    exitCode?: number;
  }) => void): () => void;
}

/** The ⋮ main-menu overlay: a top-layer WebContentsView the desktop
 *  shell opens so the menu covers native web-tab views. `open` from the
 *  UI window; `onAction` receives the chosen action id back in the UI
 *  window. Anchor: panel right edge sits `rightInset` px from the window
 *  right (measured against `vw`), top edge on the strip divider `top`. */
export interface DesktopContextMenuItem {
  id: string;
  label: string;
  iconUrl?: string;
  icon?: "folder";
  disabled?: boolean;
  checked?: boolean;
  separatorBefore?: boolean;
  children?: DesktopContextMenuItem[];
}

export interface DesktopMainMenuApi {
  open(opts: {
    /** Main menu: right-inset anchor. Generic context menu (`items`
     *  given): panel top-left at {x, y}, clamped inside the window. */
    anchor:
      | { rightInset: number; top: number; vw: number }
      | { x: number; y: number; vw: number; vh: number }
      | { right: number; y: number; align: "end"; vw: number; vh: number };
    theme?: ThemeId;
    /** Present → generic context-menu overlay instead of the ⋮ menu.
     *  Namespace the ids (e.g. "tabmenu:*") — every onAction subscriber
     *  shares one channel and must recognise only its own prefix. */
    items?: DesktopContextMenuItem[];
    cascade?: boolean;
    width?: number;
    height?: number;
  }): void;
  close(): void;
  scheduleClose?(delay?: number): void;
  cancelClose?(): void;
  onUpdate?(cb: (state: {
    items: DesktopContextMenuItem[];
    x: number;
    y: number;
    theme?: ThemeId;
    width?: number;
  }) => void): () => void;
  onAction(cb: (id: string) => void): () => void;
}
