"use client";

import { Code } from "lucide-react";
import { useTranslation } from "@/lib/i18n";
import { useSessionStore } from "@/lib/session-store";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { useCurrentProject } from "@/lib/files/files-shared";
import { previewTarget } from "@/lib/chat/transcript/file-outputs";
import { fileCapabilities } from "@/lib/documents/file-formats";
import { localPathFromUrl } from "@/lib/files/local-file-url";
import { ToolbarTip } from "./browser-chrome";
import { webToolbarButton } from "./toolbar-button";

/** A local page's source file, opened in the file window (edit, history).
 *  Only for file:// pages whose file the editor can show as text. */
export function ViewSourceButton({ tabId, url }: { tabId: string; url: string }) {
  const path = localPathFromUrl(url);
  if (!path || !fileCapabilities(path).textEditable) return null;
  return <ViewSource tabId={tabId} path={path} />;
}

function ViewSource({ tabId, path }: { tabId: string; path: string }) {
  const { text } = useTranslation();
  // A page previewing for a chat swaps back to its source in the same slot.
  const ownerSessionId = useCenterTabs(state => state.tabs.find(tab => tab.id === tabId)?.previewOwnerSessionId);
  const currentSessionId = useSessionStore(state => state.currentSessionId);
  const sessionId = ownerSessionId ?? currentSessionId ?? null;
  const project = useCurrentProject({ sessionId, chatKey: null });
  const target = previewTarget(path, project);
  // Files outside every project are read through a chat's file access.
  const available = project !== undefined && (!target.readOnly || Boolean(sessionId));
  const open = () => {
    const tabs = useCenterTabs.getState();
    if (ownerSessionId && tabs.openFilePreview(ownerSessionId, target)) return;
    if (!target.readOnly) tabs.openFileTab(target.projectId, target.path);
    else if (sessionId) tabs.openFilePreview(sessionId, target);
  };
  const label = text("View source", "查看源文件");
  return (
    <ToolbarTip label={label}>
      <button type="button" className={webToolbarButton()} onClick={open} disabled={!available} aria-label={label}>
        <Code size={14} />
      </button>
    </ToolbarTip>
  );
}
