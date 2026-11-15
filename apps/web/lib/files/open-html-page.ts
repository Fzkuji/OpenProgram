import { wsRequest } from "@/lib/net/ws/ws-request";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { fileUrlFromPath } from "./local-file-url";

/** Show an HTML file as a page in a browser tab: beside its chat when it
 *  belongs to one, otherwise as its own tab. An automatic open only takes
 *  the chat's preview slot and never adds a free-standing tab. */
export function openHtmlPage(absolutePath: string, sessionId?: string, automatic = false): boolean {
  const url = fileUrlFromPath(absolutePath);
  const tabs = useCenterTabs.getState();
  if (sessionId && tabs.openPagePreview(sessionId, { url, title: absolutePath.split("/").pop() || absolutePath }, automatic)) return true;
  if (automatic) return false;
  tabs.openWebTab(url);
  return true;
}

/** Absolute disk path of a project file, as the worker resolves it. */
export async function projectFileAbsolutePath(projectId: string, path: string): Promise<string | null> {
  const info = await wsRequest<{ project_id: string; path: string; absolute_path?: string }>(
    "project_file_info", { project_id: projectId, path }, "project_file_info_result",
    d => d.project_id === projectId && d.path === path, 10000,
  );
  return info?.absolute_path ?? null;
}
