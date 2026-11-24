// The host's tab store, as the toast host sees it inside the app: no tabs, so a toast
// centres over the app's own window. The real store reads the host's localStorage,
// which the app's sandboxed frame has none of.
const state = { activeId: null, tabs: [] as { id: string; sessionId?: string | null }[] };
export const useCenterTabs = { getState: () => state };
