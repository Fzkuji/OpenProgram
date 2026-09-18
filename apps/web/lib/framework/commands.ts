/** Explicit product commands: never arbitrary JavaScript or an IPC channel. */
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { desktopBridge } from "@/lib/desktop/bridge-api";
import * as appearance from "@/lib/prefs/theme-pref";
import { setFont } from "@/lib/prefs/font-pref";
import { setShowBookmarksBar } from "@/lib/browser/browser-prefs";
import * as bookmarks from "@/lib/tabs/bookmarks";
import { setLocale } from "@/lib/i18n";
import { setUserProfile, getUserProfile } from "@/lib/prefs/user-profile";
import { setAgentProfile, getAgentProfile } from "@/lib/format-utils/agent-style";
import { agentCanAccessWebTab } from "@/lib/browser/web-page-management";
import definitions from "../../../../openprogram/framework/interface_commands.json" with { type: "json" };

export { definitions };
export type PageAuthority = { tab_id: string; window_id: string; session_id: string; target_id: string };
export async function executeInterface(operation: string, args: unknown[], page?: PageAuthority): Promise<unknown> {
  if (!Object.hasOwn(definitions, operation)) throw new Error("unsupported_interface_operation");
  if (operation === "tabs.openWebTab") throw new Error("use_resource_web_open");
  if (operation.startsWith("native.webTab.") || operation === "tabs.setWebTabPinned") {
    const state = useCenterTabs.getState();
    const tab = state.tabs.find(item => item.id === args[0]);
    if (!page || !tab || page.tab_id !== tab.id || page.window_id !== desktopBridge()?.windowId || !agentCanAccessWebTab(tab.id, page.session_id, state)) throw new Error("web_resource_authority_required");
    const target = await desktopBridge()?.webTab.resolve?.(tab.id);
    if (!target || target !== page.target_id || !agentCanAccessWebTab(tab.id, page.session_id, useCenterTabs.getState())) throw new Error("page_context_stale");
  }
  const [group, method, nativeMethod] = operation.split(".");
  if (group === "tabs") {
    const store = useCenterTabs.getState();
    if (method === "list") return { tabs: store.tabs, groups: store.groups, activeId: store.activeId, splitWebTabId: store.splitWebTabId };
    if (method === "closeTab" && store.tabs.find(tab => tab.id === args[0])?.kind === "web") throw new Error("use_resource_web_close");
    if (method === "closeTab" && store.tabs.find(tab => tab.id === args[0])?.dirty) throw new Error("save_or_discard_unsaved_file_first");
    const action = store[method as keyof typeof store];
    if (typeof action !== "function") throw new Error("unsupported_tab_operation");
    return (action as (...values: unknown[]) => unknown)(...args);
  }
  if (group === "preferences") {
    if (method === "get") return { theme: appearance.activeThemeId(), customCss: appearance.getCustomCss(), userProfile: getUserProfile(), agentProfile: getAgentProfile() };
    const handlers = { ...appearance, setFont, setLocale, setShowBookmarksBar, setUserProfile, setAgentProfile };
    const action = handlers[method as keyof typeof handlers];
    if (typeof action !== "function") throw new Error("unsupported_preference_operation");
    return (action as (...values: unknown[]) => unknown)(...args);
  }
  if (group === "bookmarks") {
    const action = bookmarks[method as keyof typeof bookmarks];
    if (typeof action !== "function") throw new Error("unsupported_bookmark_operation");
    return (action as (...values: unknown[]) => unknown)(...args);
  }
  if (group === "sidebar") {
    const { useSessionStore } = await import("@/lib/session-store");
    useSessionStore.getState().setRightDockOpen(method === "show");
    if (method === "show") useSessionStore.getState().setRightDockView(String(args[0]));
    return useSessionStore.getState().rightDock;
  }
  if (group === "resources") {
    const { useResourceSelection } = await import("./resource-selection");
    const { useSessionStore } = await import("@/lib/session-store");
    const { sessionResourceView, resourceSessionId, terminalResourceRows, hideResourcePreview } = await import("@/lib/chat/session-resources");
    const { useTerminalResources } = await import("@/lib/desktop/terminal-resources");
    const tabs = useCenterTabs.getState();
    const sessionId = resourceSessionId(tabs.tabs.find(tab => tab.id === tabs.activeId));
    const rows = [...sessionResourceView(sessionId).rows, ...terminalResourceRows(Object.values(useTerminalResources.getState().rows), sessionId)];
    if (method === "show" && !rows.some(row => row.id === args[0])) throw new Error("resource_not_in_current_session");
    if (method === "show") { useSessionStore.getState().setRightDockOpen(true); useSessionStore.getState().setRightDockView("resources"); }
    else if (sessionId) hideResourcePreview(sessionId, sessionResourceView(sessionId).currentBranchId);
    useResourceSelection.setState(state => ({ id: method === "show" ? String(args[0]) : null, revision: state.revision + 1 }));
    return { selected: useResourceSelection.getState().id };
  }
  const bridge = desktopBridge();
  if (!bridge) throw new Error("native_desktop_unavailable");
  const target = method === "window" ? bridge : bridge[method as keyof typeof bridge];
  if (!target || typeof target !== "object") throw new Error("native_capability_unavailable");
  const action = (target as unknown as Record<string, unknown>)[nativeMethod];
  if (typeof action !== "function") throw new Error("native_capability_unavailable");
  const result = await (action as (...values: unknown[]) => unknown).apply(target, args);
  if (method === "browserImport" && nativeMethod === "run" && result && typeof result === "object") {
    const imported = result as { ok?: boolean; bookmarks?: import("@/lib/tabs/bookmarks").ImportedBookmarkNode[] };
    if (imported.ok && imported.bookmarks) bookmarks.importBookmarkTree(imported.bookmarks);
  }
  return result;
}
