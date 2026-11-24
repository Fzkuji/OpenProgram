"use client";

/**
 * "Copied from <conversation>" / "Branched from <conversation>" banner.
 *
 * Shown at the top of the transcript of a conversation made with "Copy
 * conversation" in the ⋯ menu or "Fork from here" on a message: the
 * server copied the source's history into this session and recorded
 * `branched_from` on it, which `session_loaded` carries. A whole copy
 * reads "Copied from …" and opens the source like a sidebar row does. A
 * fork (the lineage has a `msg_id`) also quotes the start of the prompt
 * it branched at (`msg_preview`) and goes back to that prompt in the
 * source, flashing it (`at_msg_id`); once the prompt is gone it only
 * opens the source.
 * Styling is the sub-agent banner's (agent-branch-banner.css).
 */

import { useEffect, useState } from "react";

import { displayTitle } from "@/components/sidebar/sessions/helpers";
import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { runtimeState } from "@/lib/runtime-bridge/state";
import { openSession } from "@/lib/tabs/navigation/open-session";
import { openSessionAtMessage } from "@/lib/chat/history/reveal-message";

export interface BranchedFrom {
  session_id: string;
  /** Set for a fork from an earlier message; null for a whole copy. */
  msg_id?: string | null;
  /** The source's title when the session was loaded. */
  title?: string | null;
  /** False once the source conversation was deleted. */
  exists?: boolean;
  /** A fork's prompt in the source; null once it is gone. */
  at_msg_id?: string | null;
  /** The start of that prompt's text. */
  msg_preview?: string | null;
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
  if (!lineage.msg_id) {
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
  const preview = (lineage.msg_preview || "").trim();
  return (
    <div className="agent-branch-banner">
      <span className="agent-branch-banner-label">
        {text("Branched from ", "分支自 ")}
        <strong>{name}</strong>
        {preview ? text(` · at “${preview}”`, ` · 在“${preview}”处`) : null}
      </span>
      {gone ? null : (
        <button type="button" className="agent-branch-banner-back"
          onClick={() => openSessionAtMessage(lineage.session_id, lineage.at_msg_id)}>
          {text("Back to original", "回到原会话")}
        </button>
      )}
    </div>
  );
}
