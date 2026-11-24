"use client";

import { useCallback, useEffect, useState } from "react";

import { useSessionStore } from "@/lib/session-store";
import { listSessionProjects } from "@/lib/projects/list-projects";
import { onReconnected } from "@/lib/net/ws/changes";
import { useBoundChat } from "./bound-chat";

/** The project fields the composer's chips read. */
export interface ChatProject {
  id: string;
  name: string;
  path: string;
  is_default: boolean;
  machine_id?: string;
}

/** The project the shown chat is in: a draft's pick (else the home
 *  project), or the conversation's own. Follows `project-changed` and
 *  asks again when the socket comes back. Shares the page's one project
 *  listing per conversation (`listSessionProjects`). */
export function useChatProject(): ChatProject | null {
  // A bound draft (split view, an application's conversation) has no
  // session yet and shows its own pending project, not the main shell's chat.
  const { sessionId, chatKey } = useBoundChat();
  const pendingProjectId = useSessionStore((s) =>
    chatKey ? s.pendingProjectsByChat[chatKey] ?? null : null,
  );
  const [project, setProject] = useState<ChatProject | null>(null);

  const refresh = useCallback(async () => {
    const data = await listSessionProjects<{
      projects: ChatProject[] | null;
      current_project_id: string | null;
      session_id: string | null;
    }>(sessionId);
    if (!data || !Array.isArray(data.projects)) return;
    const wantId = (!sessionId ? pendingProjectId : data.current_project_id) ?? data.current_project_id;
    setProject(
      data.projects.find((p) => p.id === wantId) ??
      data.projects.find((p) => p.is_default) ??
      null,
    );
  }, [pendingProjectId, sessionId]);

  useEffect(() => {
    let cancelled = false;
    void refresh();
    const offOpen = onReconnected(() => { if (!cancelled) void refresh(); });
    const onChanged = () => void refresh();
    window.addEventListener("project-changed", onChanged);
    return () => {
      cancelled = true;
      offOpen();
      window.removeEventListener("project-changed", onChanged);
    };
  }, [refresh]);

  return project;
}
