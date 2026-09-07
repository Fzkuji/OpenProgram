"use client";

import {
  sessionResourceView,
  useBrowserResourceStore,
} from "./state/session-resources";

/** Subscribe to the window-level resource projection. Does not own ingestion. */
export function useSessionResources(sessionId: string | null) {
  useBrowserResourceStore(s => s.rows);
  useBrowserResourceStore(s => s.snapshotComplete);
  useBrowserResourceStore(s => s.snapshotFailed);
  useBrowserResourceStore(s => s.connected);
  useBrowserResourceStore(s => (sessionId ? s.viewedBranch[sessionId] ?? null : null));
  return {
    ...sessionResourceView(sessionId),
    currentBranchName: null as string | null,
  };
}
