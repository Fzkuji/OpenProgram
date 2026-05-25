"use client";

/**
 * AttachCard — rendered for assistant rows whose ``function === "attach"``.
 *
 * An attach row records that this turn spawned another agent. The
 * spawned agent's reply is a branch in the SAME session, so opening
 * the card checks out that branch in this chat view (no
 * cross-session navigation). Legacy rows (from before the same-
 * session refactor) carry a foreign ``session_id`` and fall back to
 * navigating to that session.
 */
import { useEffect, useState } from "react";

import type { ChatMsg } from "@/lib/session-store";

import { useSessionStore } from "@/lib/session-store";
import { renderMarkdown, useMarkdownReady } from "./markdown";

function wsSend(payload: unknown): void {
  const w = window as unknown as { ws?: WebSocket };
  if (w.ws && w.ws.readyState === WebSocket.OPEN) {
    w.ws.send(JSON.stringify(payload));
  }
}

interface BranchRow {
  head_msg_id: string;
  name?: string;
  active?: boolean;
}

function _branchNameFor(
  sessionId: string | null | undefined,
  headId: string,
): string {
  if (!sessionId || !headId) return "";
  const w = window as unknown as {
    _branchesByConv?: Record<string, BranchRow[]>;
  };
  const list = w._branchesByConv?.[sessionId] || [];
  const match = list.find((b) => b.head_msg_id === headId);
  return (match?.name || "").trim();
}

function _activeHeadId(sessionId: string | null | undefined): string {
  if (!sessionId) return "";
  const w = window as unknown as {
    _branchesByConv?: Record<string, BranchRow[]>;
    conversations?: Record<string, { head_id?: string }>;
  };
  // Prefer the branch list's active flag — that's what the rest of
  // the UI uses to label the topbar chip / Branches panel HEAD pill.
  const list = w._branchesByConv?.[sessionId] || [];
  const active = list.find((b) => b.active);
  if (active?.head_msg_id) return active.head_msg_id;
  return w.conversations?.[sessionId]?.head_id || "";
}

export function AttachCard({ msg }: { msg: ChatMsg }) {
  useMarkdownReady();
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const attach = msg.attach || {};
  const targetSessionId = attach.session_id || "";
  const targetHead = attach.head_id || "";
  const label = (attach.label || "").trim();
  const preview = msg.content || "(no output)";
  const headTag = targetHead ? targetHead.slice(0, 8) : "";
  // The expanded-block stats — populated server-side when the
  // attach pointer pinned a source_commit_id (post attach-commit
  // expansion refactor). Absent on legacy attach rows; fall back to
  // the single-message preview in that case.
  const sourceCommitId = attach.source_commit_id || "";
  const embedCount = attach.embed_count;
  const embedTokens = attach.embed_tokens;
  const hasEmbedStats = typeof embedCount === "number" && embedCount > 0;
  const sameSession =
    !!targetSessionId && targetSessionId === currentSessionId;
  // If the chat is already on the branch the card points at, drop
  // the Switch button — there's nowhere to switch to. Drive this off
  // the same active-head signal the topbar chip uses so the two stay
  // in sync. Re-evaluate on the ``branches-updated`` window event
  // (dispatched by the BranchesPanel shim when the WS layer pushes a
  // fresh branch list) so checkout outside this card still updates
  // the indicator. Cross-session legacy attaches always show "Open".
  const [activeHead, setActiveHead] = useState(() =>
    _activeHeadId(currentSessionId),
  );
  // Independent tick so even when activeHead doesn't change, the
  // card still re-renders on branches-updated — that's how
  // _branchNameFor() picks up the freshly-loaded branch list and
  // swaps the hex tip id for the real branch name.
  const [, setBumpsTick] = useState(0);
  useEffect(() => {
    function refresh() {
      setActiveHead(_activeHeadId(currentSessionId));
      setBumpsTick((t) => t + 1);
    }
    refresh();
    window.addEventListener("branches-updated", refresh);
    return () => window.removeEventListener("branches-updated", refresh);
  }, [currentSessionId]);
  const alreadyHere =
    sameSession && !!targetHead && activeHead === targetHead;

  function open() {
    if (sameSession && targetHead) {
      // Same-session: checkout the spawned branch in place.
      wsSend({
        action: "checkout_branch",
        session_id: targetSessionId,
        head_msg_id: targetHead,
      });
      wsSend({ action: "load_session", session_id: targetSessionId });
      return;
    }
    if (!targetSessionId) return;
    const nav = (window as unknown as { __navigate?: (p: string) => void })
      .__navigate;
    if (nav) nav("/s/" + targetSessionId);
    else window.location.href = "/s/" + targetSessionId;
  }

  // Label intro: "Attached" for user-triggered attaches (Branches →
  // Attach to), "Spawned" for /task or task() invocations, "Imported"
  // for legacy cross-session attaches. Title-cased and human-readable
  // so the chat row reads "Attached: alpha A" not "attached · alpha A".
  const isManual = !!attach.manual;
  const labelKind = !sameSession
    ? "Imported from"
    : isManual
      ? "Attached"
      : "Spawned";
  // Prefer the source branch's named label; fall back to the manual
  // ``attach.label`` field; then to the looked-up branch name; then
  // to a short hex tip. ``9cc78e93_reply`` was leaking through as
  // the visible title because none of those layers fired.
  const lookedUpName = _branchNameFor(currentSessionId, targetHead);
  const sourceName = (label || lookedUpName || (targetHead ? targetHead.slice(0, 8) : "")) || "(branch)";
  // Sub-label: short, human-readable context so the user knows what
  // they're looking at without parsing a hex id.
  const subtitle = sameSession
    ? "source branch — its content is embedded below"
    : `from session ${targetSessionId}`;


  return (
    <div
      className="attach-card"
      data-peer-session-id={targetSessionId}
      data-head-id={targetHead}
    >
      <div className="attach-card-header">
        <div className="attach-card-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
               strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="4 17 10 11 4 5" />
            <line x1="12" y1="19" x2="20" y2="19" />
          </svg>
        </div>
        <div className="attach-card-meta">
          <div className="attach-card-label">
            {labelKind}: <span className="attach-card-source">{sourceName}</span>
            {attach.status && attach.status !== "completed" ? (
              <span
                className={`attach-card-status-pill attach-card-status-${attach.status}`}
                title={
                  attach.status === "running"
                    ? "Sub-agent is still running"
                    : attach.status === "errored"
                      ? "Sub-task errored"
                      : attach.status === "cancelled"
                        ? "Sub-task cancelled"
                        : attach.status === "pending" || attach.status === "queued"
                          ? "Sub-task is waiting to start"
                          : ""
                }
              >
                {attach.status === "running" || attach.status === "pending"
                  || attach.status === "queued" ? (
                  <span className="attach-card-status-dot" />
                ) : null}
                {attach.status}
              </span>
            ) : null}
          </div>
          <div className="attach-card-sub" title={targetHead || targetSessionId}>
            {subtitle}
            {!sameSession && headTag ? (
              <span className="attach-card-head">@{headTag}</span>
            ) : null}
          </div>
        </div>
        {alreadyHere ? (
          // Already on this branch — a "current branch" tag instead
          // of a Switch button. Keeps the row layout consistent.
          <span
            className="attach-card-here"
            title="You're already on this branch"
          >
            current
          </span>
        ) : (sameSession ? !!targetHead : !!targetSessionId) ? (
          <button
            type="button"
            className="attach-card-open"
            onClick={open}
            aria-label={sameSession ? "Switch to this branch" : "Open peer session"}
            title={sameSession ? "Switch to this branch" : "Open peer session"}
          >
            {sameSession ? "Switch" : "Open"}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                 aria-hidden="true">
              <line x1="7" y1="17" x2="17" y2="7" />
              <polyline points="7 7 17 7 17 17" />
            </svg>
          </button>
        ) : null}
      </div>
      <div className="attach-card-preview-label">
        {hasEmbedStats
          ? `EMBEDS ${embedCount} message${embedCount === 1 ? "" : "s"}${
              typeof embedTokens === "number"
                ? ` · ${embedTokens} tokens`
                : ""
            }${sourceCommitId ? ` · commit ${sourceCommitId.slice(0, 8)}` : ""}`
          : "Preview (tip of this branch)"}
      </div>
      <div
        className="attach-card-preview chat-text"
        dangerouslySetInnerHTML={{ __html: renderMarkdown(preview) }}
      />
      {isManual ? (
        <div className="attach-card-footer">
          <span className="attach-card-status">
            {hasEmbedStats
              ? "Will be expanded into your next message"
              : "Will be included in your next message"}
          </span>
        </div>
      ) : null}
      {/* Live-task footer: cancel button for in-flight tasks. */}
      {attach.task_id
        && (attach.status === "running" || attach.status === "pending"
            || attach.status === "queued") ? (
        <div className="attach-card-footer">
          <span className="attach-card-status">
            Running — embedding placeholder until done
          </span>
          <button
            type="button"
            className="attach-card-cancel"
            onClick={() => {
              wsSend({
                action: "cancel_task",
                task_id: attach.task_id,
              });
            }}
            title="Stop this sub-agent"
          >
            Cancel
          </button>
        </div>
      ) : null}
    </div>
  );
}
