import { create } from "zustand";

/** Ephemeral picture-in-picture host for an agent-opened WebTab.
 *  Not persisted — closing the preview or expanding to split/fullscreen
 *  clears it. The leaf tab itself stays in the center-tabs store. */
export const useWebTabPip = create<{
  tabId: string | null;
  show: (tabId: string) => void;
  hide: () => void;
}>((set) => ({
  tabId: null,
  show: (tabId) => set({ tabId }),
  hide: () => set({ tabId: null }),
}));

export function peekWebTabPipId(): string | null {
  return useWebTabPip.getState().tabId;
}
