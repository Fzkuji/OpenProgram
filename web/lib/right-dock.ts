/**
 * Imperative handle on the right sidebar, for callers that need to open
 * a view but don't own the component.
 *
 * `RightSidebar` registers the implementation on mount. Everything is a
 * no-op before that (or after unmount), so an early call is safe.
 *
 * State itself lives in `useSessionStore.rightDock` — prefer the store's
 * `setRightDockView` / `setRightDockOpen` when you're already in React.
 */

export interface RightDockApi {
  show: (view?: string) => void;
  close: () => void;
  toggle: (view?: string) => void;
  /** Legacy no-op: the store hydrates from localStorage at create time. */
  restore: () => void;
}

let api: RightDockApi | null = null;

/** Called by RightSidebar on mount; pass null on unmount. */
export function setRightDockApi(next: RightDockApi | null): void {
  api = next;
}

export const rightDock: RightDockApi = {
  show: (view) => api?.show(view),
  close: () => api?.close(),
  toggle: (view) => api?.toggle(view),
  restore: () => api?.restore(),
};
