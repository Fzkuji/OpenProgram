"use client";

/**
 * A sub-agent's own conversation (its prompts, steps and replies, live
 * while it runs), drawn under its step row in the turn's timeline when the
 * row opens. The turn's transcript keeps only its own reply, and the step
 * row is the one place a spawned agent shows (styles in
 * sub-agent-transcript.css).
 */
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import {
  closeSubAgentView,
  loadSubAgentTranscript,
  subAgentChatKey,
} from "@/lib/chat/transcript/sub-agent-transcripts";
import { useTranslation } from "@/lib/i18n";
import { isAgentRunning } from "@/lib/chat/transcript/row-status";
import { useSessionStore, type ChatMsg } from "@/lib/session-store";

import { TranscriptRows } from "../list/transcript-rows";
import { PromptTurn } from "./sub-agent-prompt";

/** The store key of a spawn card's agent: its first Job, else its branch head. */
export function subAgentKey(card: ChatMsg): string {
  const attach = card.attach || {};
  return attach.job_id || attach.head_id || card.id;
}

export function SubAgentTranscript({ card, sessionId, onTip }: {
  card: ChatMsg;
  sessionId: string;
  /** The agent's newest reply, once its branch is loaded. */
  onTip?: (tip: string) => void;
}) {
  const { text } = useTranslation();
  const attach = card.attach || {};
  const key = subAgentKey(card);
  const jobId = attach.job_id || undefined;
  const headId = attach.head_id || undefined;
  const status = attach.status || "completed";
  const chatKey = subAgentChatKey(sessionId, key);
  const order = useSessionStore((s) => s.messageOrder[chatKey]);
  const [loading, setLoading] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  // A running agent opens at its newest output; a finished one at its task.
  const followRef = useRef(isAgentRunning(status));
  const tipRef = useRef(onTip);
  tipRef.current = onTip;

  useEffect(() => {
    let live = true;
    const load = () => {
      setLoading(true);
      void loadSubAgentTranscript(sessionId, key, { jobId, headId })
        .then((tip) => { if (live && tip) tipRef.current?.(tip); })
        .finally(() => { if (live) setLoading(false); });
    };
    load();
    return () => {
      live = false;
      closeSubAgentView(sessionId, key);
    };
  }, [sessionId, key, jobId, headId]);

  // Stay at the newest output while it streams, unless the reader scrolled up.
  useLayoutEffect(() => {
    const body = bodyRef.current;
    if (!body || typeof ResizeObserver === "undefined") return;
    const content = body.firstElementChild;
    if (!content) return;
    const observer = new ResizeObserver(() => {
      if (followRef.current) body.scrollTop = body.scrollHeight;
    });
    observer.observe(content);
    return () => observer.disconnect();
  }, [chatKey]);

  const ids = order ?? [];
  return (
    <div
      ref={bodyRef}
      className="sub-agent-body"
      onScroll={(event) => {
        const body = event.currentTarget;
        followRef.current = body.scrollHeight - body.scrollTop - body.clientHeight < 24;
      }}
    >
      <div className="sub-agent-turns">
        {ids.length === 0 && attach.prompt ? (
          <PromptTurn msg={{ id: `${card.id}_prompt`, role: "user", rowKind: "user", content: attach.prompt }} />
        ) : null}
        <TranscriptRows sessionId={sessionId} viewKey={chatKey} ids={ids} embedded />
        {ids.length === 0 ? (
          <div className="sub-agent-empty" role="status">
            {loading
              ? text("Loading…", "加载中…")
              : isAgentRunning(status)
                ? text("Waiting for output…", "等待输出…")
                : text("No output.", "没有输出。")}
          </div>
        ) : null}
      </div>
    </div>
  );
}
