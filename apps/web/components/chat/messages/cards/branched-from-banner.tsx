"use client";

/**
 * "Copied from <conversation>" banner.
 *
 * Shown at the top of the transcript of a conversation made with "Copy
 * conversation" in the ⋯ menu: the server copied the source's history up
 * to its HEAD into this session and recorded `branched_from` on it, which
 * `session_loaded` carries. One click opens the source conversation the
 * same way a sidebar row does.
 * Styling is the sub-agent banner's (agent-branch-banner.css).
 */

import { useEffect, useState } from "react";

import { displayTitle } from "@/components/sidebar/sessions/helpers";
import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { runtimeState } from "@/lib/runtime-bridge/state";
import { openSession } from "@/lib/tabs/navigation/open-session";

export interface BranchedFrom {
  session_id: string;
  msg_id?: string | null;
  /** The source's title when the session was loaded. */
  title?: string | null;
  /** False once the source conversation was deleted. */
  exists?: boolean;
}

/** The lineage the last load of ``sessionId`` reported, if any. */
export function branchedFromOf(sessionId: string | null): BranchedFrom | null {
  if (!sessionId) return null;
  const conv = runtimeState.conversations[sessionId] as
    | { branched_from?: BranchedFrom | null }
    | undefined;
  const lineage = conv?.branched_from;
  if (!lineage || typeof lineage.session_id !== "string" || !lineage.session_id) return null;
  return lineage;
}

export function BranchedFromBanner() {
  const { text } = useTranslation();
  const sessionId = useSessionStore((s) => s.currentSessionId);
  // Re-read after each load of this session (it refreshes the branch list).
  const loading = useSessionStore((s) => s.transcriptLoadingId);
  const [lineage, setLineage] = useState<BranchedFrom | null>(null);
  useEffect(() => {
    const refresh = () => setLineage(branchedFromOf(sessionId));
    refresh();
    window.addEventListener("branches-updated", refresh);
    return () => window.removeEventListener("branches-updated", refresh);
  }, [sessionId, loading]);
  // The live session list wins, so a rename of the source shows here.
  const source = useSessionStore((s) =>
    lineage ? s.conversations[lineage.session_id] : undefined);
  if (!lineage || !sessionId) return null;
  const gone = lineage.exists === false && !source;
  const name = (source && displayTitle(source))
    || (lineage.title || "").trim()
    || text("another conversation", "另一个会话");
  return (
    <div className="agent-branch-banner">
      <span className="agent-branch-banner-label">
        {text(`Copied from ${name}`, `复制自 ${name}`)}
      </span>
      {gone ? null : (
        <button type="button" className="agent-branch-banner-back"
          onClick={() => openSession(lineage.session_id)}>
          {text("Open original", "打开原会话")}
        </button>
      )}
    </div>
  );
}
