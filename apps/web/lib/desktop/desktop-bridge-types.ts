import type { ThemeId, ThemeMode, ThemeStyle } from "@/lib/prefs/appearance/theme-pref";
import type { PickedElement } from "@/lib/browser/capture/page-comment";
import type { PickedRegion } from "@/lib/browser/capture/region-shot";
import type { MenuGlyph, MenuKeys } from "@/components/ui/menu/menu-parts";

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
  /** Bounded local diagnostic metadata; never includes messages or page URLs. */
  trace?(payload: { source: string; theme?: string | null; previousTheme?: string | null;
    style?: string; mode?: string; storedMode?: string | null; systemDark?: boolean }): void;
  /** The OS appearance as the main process reads it; what Auto follows in the App. */
  systemDark?(): boolean | undefined;
  /** Fires when the OS appearance changes; returns the unsubscribe function. */
  onSystemChange?(listener: (dark: boolean) => void): () => void;
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

export interface DesktopBrowserControlOverlay {
  resourceId: string;
  generation: number;
  conversationSessionId: string;
  controlState: string;
  showActions: boolean;
  connected: boolean;
  status: string;
  pauseLabel: string;
  showLabel: string;
  historyLabel: string;
  notice?: string;
  pauseDisabled: boolean;
  resumeDisabled: boolean;
  showTakeover: boolean;
  theme?: string;
  expandLabel?: string;
  foldLabel?: string;
  dismissLabel?: string;
  dragLabel?: string;
  historyItems?: Array<{ id: string; label: string; disabled?: boolean }>;
}

export type DesktopBrowserControlOverlayEvent =
  | { type: "pause" | "resume" | "reveal" | "toggle-show" | "stale"; id: string; generation: number }
  | { type: "ready" }
  | { type: "layout"; collapsed: boolean; width: number; height: number }
  | { type: "move"; id: string; generation: number; dx: number; dy: number }
  | { type: "history"; id: string; generation: number };

export interface DesktopWebTabActionMarker {
  x: number;
  y: number;
  width: number;
  height: number;
  sequence: number;
  /** Resource generation from the host receipt. Stale generation is rejected. */
  generation?: number;
  /** Host-provided reduced-motion preference for this click replay. */
  reducedMotion?: boolean;
  /** The operation's action name: a press plays where the cursor lands. */
  action?: string;
  /**
   * Backend resource_id for this receipt (`page:<instance>:<revision>`).
   * A new id on the same native Page is a worker incarnation; generation
   * may restart. Omit only when the renderer has no resource row.
   */
  resourceId?: string;
}

export interface DesktopWebTabBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** The rounded card edge a web tab's page meets at its bottom corners, in
 *  renderer CSS px: the App paints what lies outside that arc above the
 *  native view, which no DOM clip reaches. */
export interface DesktopWebTabCorners {
  radius: number;
  /** The colour behind the card. */
  outside: string;
  /** The card's computed box-shadow, or "". */
  shadow: string;
}

export interface DesktopVisibleWebView {
  id: string;
  bounds: DesktopWebTabBounds;
  corners?: DesktopWebTabCorners | null;
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
    input_scale?: number;
    url: string;
    title: string;
  } | null>;
  /** Bounded DOM/ARIA preview. Default is the currently visible native view. */
  preview(id: string, allowBackground?: boolean): Promise<{
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
  /** Destroy and report whether the native view was actually closed. */
  destroyConfirmed?(id: string): Promise<boolean>;
  reload(id: string, options?: { hard?: boolean }): void;
  stop(id: string): void;
  goBack(id: string): void;
  goForward(id: string): void;
  find?(
    id: string,
    query: string,
    options?: { forward?: boolean; findNext?: boolean },
  ): void;
  stopFind?(id: string, action: "clearSelection" | "keepSelection" | "activateSelection"): void;
  /** Let the user click one element on the page. null when cancelled. */
  pickElement?(id: string): Promise<PickedElement | null>;
  /** Let the user drag a rectangle over the page; its pixels, or null when cancelled. */
  pickRegion?(id: string): Promise<PickedRegion | null>;
  cancelPick?(id: string): void;
  zoom?(id: string, action: "in" | "out" | "reset"): Promise<number | null>;
  print?(id: string): Promise<boolean>;
  /** Snapshot of the native page as a data URL, for PiP drag placeholders. */
  capture?(id: string, mode?: "presentation"): Promise<string | null>;
  /** Host-owned action location on the exact native view. null clears. */
  showAction?(
    id: string,
    marker: DesktopWebTabActionMarker | null,
  ): Promise<boolean>;
  /** The page's edge glows, and the agent cursor shows, while the Agent
   *  holds it (desktop main/windows/action-cues.js). */
  setControlGlow?(id: string, held: boolean): void;
  /** App-owned floating Agent control over the native page. null hides. */
  setControlOverlay?(
    id: string,
    payload: DesktopBrowserControlOverlay | null,
  ): void;
  onControlOverlayEvent?(
    cb: (event: DesktopBrowserControlOverlayEvent) => void,
  ): () => void;
  /** PiP-only layout scale. Pass the content width, or null to restore user zoom. */
  setPipZoom?(id: string, width: number | null, height?: number): void;
  /** Navigation/title/loading events pushed from main; returns the
   *  unsubscribe function. */
  onState(cb: (state: DesktopWebTabState) => void): () => void;
  /** Valid http(s) page popup delegated by the owning native web view. */
  /** ``id`` is set when the desktop already made the popup's Page (it keeps window.opener). */
  onPopup?(cb: (popup: { openerId: string; url: string; id?: string }) => void): () => void;
  onFindResult?(cb: (result: DesktopWebTabFindResult) => void): () => void;
  onCommand?(cb: (command: { id: string; command: "find" | "focus-address" | "overview" | "palette" }) => void): () => void;
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
  artifactKind?: "dmg" | "windows-installer";
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

export interface DesktopAttentionPrefs {
  notifications: boolean;
  keepAwake: boolean;
  /** "default", "none", or "system:<name>" for a sound macOS ships. */
  sound: string;
}

/** Desktop notifications and keep-awake (desktop/main/windows/attention-service.js). */
export interface DesktopAttentionApi {
  getPrefs(): Promise<DesktopAttentionPrefs>;
  setPrefs(patch: Partial<DesktopAttentionPrefs>): Promise<DesktopAttentionPrefs>;
  /** Sound names offered besides Default and None; empty off macOS. Older shells lack it. */
  sounds?(): Promise<string[]>;
  previewSound?(sound: string): Promise<boolean>;
  onPrefs(cb: (prefs: DesktopAttentionPrefs) => void): () => void;
  notify(request: { key: string; title: string; body: string; sessionId: string; urgent?: boolean }): void;
  reportBusy(busy: boolean): void;
  onOpenSession(cb: (sessionId: string) => void): () => void;
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
  /** Separate explanatory text in application HTML menus. */
  description?: string;
  /** Checkbox selections may update without dismissing the HTML menu. */
  keepOpen?: boolean;
  iconUrl?: string;
  iconFallbackUrl?: string;
  icon?: "folder";
  /** The row's Solar icon (components/ui/menu/menu-parts.tsx), drawn by the overlay. */
  glyph?: MenuGlyph;
  /** The row's key badge: a menu-local letter or a system shortcut. */
  keys?: MenuKeys;
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
  updateItems?(items: DesktopContextMenuItem[]): void;
  close(): void;
  scheduleClose?(delay?: number): void;
  cancelClose?(): void;
  onUpdate?(cb: (state: {
    items: DesktopContextMenuItem[];
    x?: number;
    y?: number;
    theme?: ThemeId;
    width?: number;
  }) => void): () => void;
  onAction(cb: (id: string) => void): () => void;
  onClosed?(cb: () => void): () => void;
}

/** Native OS context menu, with a request-scoped lifetime. */
export interface DesktopNativeMenuApi {
  popup(opts: { requestId: string; x: number; y: number; items: DesktopContextMenuItem[] }): Promise<string | null>;
  close(requestId: string): void;
}
