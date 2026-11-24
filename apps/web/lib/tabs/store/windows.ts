import { nextWindowTabId } from "@/lib/tabs/center-tab-ids";
import type { StoreApi } from "zustand";
import { commitCenterTabsState, focusOrCreate } from "./core";
import type { CenterTabsState } from "./types";

/** Tabs that hold another application's window (lib/desktop/embedded-window).
 *  A new one shows the open windows to choose from; choosing one sets its
 *  target, and the tab keeps its id whichever window it shows. */
export function windowActions(set: StoreApi<CenterTabsState>["setState"]): Pick<CenterTabsState, "openWindowTab" | "openWindowTabFor" | "setWindowTabTarget" | "setWindowTabTitle"> {
  return {
    openWindowTab: () =>
      set((s) => {
        const id = nextWindowTabId();
        return focusOrCreate(s, id, () => ({ id, kind: "window", title: "" }), []);
      }),

    // The tab already holding that window comes to the front; otherwise a new
    // one opens on it (a chat preview's "Open in tab").
    openWindowTabFor: (target) =>
      set((s) => {
        const held = s.tabs.find((tab) => tab.kind === "window" && tab.nativeWindowId === target.id);
        const id = held?.id ?? nextWindowTabId();
        return focusOrCreate(s, id, () => ({
          id, kind: "window", title: target.title || target.app, nativeWindowId: target.id, nativeWindowApp: target.app,
        }), []);
      }),

    setWindowTabTarget: (id, target) =>
      set((s) => {
        const tab = s.tabs.find((candidate) => candidate.id === id && candidate.kind === "window");
        if (!tab) return {};
        const tabs = s.tabs.map((candidate) => candidate.id !== id ? candidate : target
          ? { ...candidate, nativeWindowId: target.id, nativeWindowApp: target.app, title: target.title || target.app }
          : { ...candidate, nativeWindowId: undefined, nativeWindowApp: undefined, title: "" });
        return commitCenterTabsState(s, { tabs });
      }),

    setWindowTabTitle: (id, title) =>
      set((s) => {
        const tab = s.tabs.find((candidate) => candidate.id === id && candidate.kind === "window");
        if (!tab || tab.title === title) return {};
        return commitCenterTabsState(s, { tabs: s.tabs.map((candidate) => candidate.id === id ? { ...candidate, title } : candidate) });
      }),
  };
}
