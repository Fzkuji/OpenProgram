"use client";

/** A terminal the Agent uses, in a tab of its own: the same PTY as its row in
 *  the conversation's resources and its chat preview, never a new shell. */

import { TerminalSquare } from "lucide-react";

import { TerminalResourceView } from "@/components/session-resources/terminal-resources";
import { useTerminalResources } from "@/lib/desktop/terminal-resources";
import { useTranslation } from "@/lib/i18n";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";

export function AgentTerminalTab({ tabId, terminalId, sessionId }: { tabId: string; terminalId: string; sessionId?: string }) {
  const { text } = useTranslation();
  const resource = useTerminalResources((state) => state.rows[terminalId]);
  const close = () => useCenterTabs.getState().closeTab(tabId);
  if (!resource) {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center gap-3 p-6 text-center text-fs-sm text-muted-foreground">
        <TerminalSquare size={28} aria-hidden="true" />
        <p>{text("This terminal is no longer available.", "这个终端已经不在了。")}</p>
      </div>
    );
  }
  return (
    <div className="flex h-full w-full min-h-0 flex-col bg-background">
      <TerminalResourceView resource={resource} sessionId={sessionId || resource.session_ids[0] || ""} onHide={close} />
    </div>
  );
}
