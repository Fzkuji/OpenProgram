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
  /** Navigation/title/loading events pushed from main; returns the
   *  unsubscribe function. */
  onState(cb: (state: DesktopWebTabState) => void): () => void;
  /** Valid http(s) page popup delegated by the owning native web view. */
  onPopup?(cb: (popup: { openerId: string; url: string }) => void): () => void;
  onFindResult?(cb: (result: DesktopWebTabFindResult) => void): () => void;
  onCommand?(cb: (command: { id: string; command: "find" }) => void): () => void;
}
