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
import { useEffect, useRef, useState } from "react";

import type { ChatMsg } from "@/lib/session-store";

import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import {
  type AnimatedNavIconHandle,
  ArrowUpRightIcon,
} from "@/components/animated-icons";
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

/** Look up the auto-followup user msg that the runner wrote after
 *  this attach pointer's task completed. Matches by walking forward
 *  in this session's message order from the attach pointer and
 *  picking up the next user msg with ``source === "task_followup"``.
 *  Returns its rendered content (the "[系统消息]..." prompt) so the
 *  attach card can surface it inline. Returns null when the task
 *  hasn't completed or the relationship isn't found. */
function _findFollowupMsgId(
  s: ReturnType<typeof useSessionStore.getState>,
  sessionId: string | null,
  attachMsgId: string,
): string | null {
  if (!sessionId) return null;
  const order = s.messageOrder[sessionId] || [];
  const myIdx = order.indexOf(attachMsgId);
  if (myIdx < 0) return null;
  for (let i = myIdx + 1; i < order.length; i++) {
    const next = s.messagesById[order[i]];
    if (!next) continue;
    if (next.role === "user" && next.source === "task_followup") {
      return next.id;
    }
  }
  return null;
}

function useFollowupMsgId(attachMsgId: string): string | null {
  const sessionId = useSessionStore((s) => s.currentSessionId);
  return useSessionStore((s) => _findFollowupMsgId(s, sessionId, attachMsgId));
}

function useFollowupNotice(attachMsgId: string): string | null {
  const sessionId = useSessionStore((s) => s.currentSessionId);
  return useSessionStore((s) => {
    const id = _findFollowupMsgId(s, sessionId, attachMsgId);
    if (!id) return null;
    return s.messagesById[id]?.content || null;
  });
}

export function AttachCard({ msg }: { msg: ChatMsg }) {
  useMarkdownReady();
  const { text } = useTranslation();
  const switchIconRef = useRef<AnimatedNavIconHandle>(null);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const attach = msg.attach || {};
  const followupNotice = useFollowupNotice(msg.id);
  const followupMsgId = useFollowupMsgId(msg.id);
  const targetSessionId = attach.session_id || "";
  const targetHead = attach.head_id || "";
  const label = (attach.label || "").trim();
  const preview = msg.content || text("(no output)", "（无输出）");
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
    ? text("Imported from", "导入自")
    : isManual
      ? text("Attached", "已附加")
      : text("Spawned", "已创建子任务");
  // Prefer the source branch's named label; fall back to the manual
  // ``attach.label`` field; then to the looked-up branch name; then
  // to a short hex tip. ``9cc78e93_reply`` was leaking through as
  // the visible title because none of those layers fired.
  const lookedUpName = _branchNameFor(currentSessionId, targetHead);
  const sourceName = (label || lookedUpName || (targetHead ? targetHead.slice(0, 8) : "")) || text("(branch)", "（分支）");
  // Sub-label: short, human-readable context so the user knows what
  // they're looking at without parsing a hex id.
  const subtitle = sameSession
    ? text("source branch - its content is embedded below", "源分支，内容已嵌入下方")
    : `${text("from session", "来自会话")} ${targetSessionId}`;


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
                    ? text("Sub-agent is still running", "子 Agent 仍在运行")
                    : attach.status === "errored"
                      ? text("Sub-task errored", "子任务出错")
                      : attach.status === "cancelled"
                        ? text("Sub-task cancelled", "子任务已取消")
                        : attach.status === "pending" || attach.status === "queued"
                          ? text("Sub-task is waiting to start", "子任务等待开始")
                          : ""
                }
              >
                {attach.status === "running" || attach.status === "pending"
                  || attach.status === "queued" ? (
                  <span className="indicator-dot sm pulse-opacity" />
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
            title={text("You are already on this branch", "当前已在这个分支")}
          >
            {text("current", "当前")}
          </span>
        ) : (sameSession ? !!targetHead : !!targetSessionId) ? (
          <button
            type="button"
            className="attach-card-open"
            onClick={open}
            onMouseEnter={() => switchIconRef.current?.startAnimation?.()}
            onMouseLeave={() => switchIconRef.current?.stopAnimation?.()}
            aria-label={sameSession ? text("Switch to this branch", "切换到这个分支") : text("Open peer session", "打开关联会话")}
            title={sameSession ? text("Switch to this branch", "切换到这个分支") : text("Open peer session", "打开关联会话")}
          >
            {sameSession ? text("Switch", "切换") : text("Open", "打开")}
            <ArrowUpRightIcon ref={switchIconRef} size={14} />
          </button>
        ) : null}
      </div>
      <div className="attach-card-preview-label">
        {hasEmbedStats
          ? `${text("EMBEDS", "嵌入")} ${embedCount} ${text("messages", "条消息")}${
              typeof embedTokens === "number"
                ? ` · ${embedTokens} ${text("tokens", "tokens")}`
                : ""
            }${sourceCommitId ? ` · commit ${sourceCommitId.slice(0, 8)}` : ""}`
          : text("Preview (tip of this branch)", "预览（该分支末端）")}
      </div>
      <div
        className="attach-card-preview chat-text"
        dangerouslySetInnerHTML={{ __html: renderMarkdown(preview) }}
      />
      {isManual ? (
        <div className="attach-card-footer">
          <span className="attach-card-status">
            {hasEmbedStats
              ? text("Will be expanded into your next message", "会展开到你的下一条消息中")
              : text("Will be included in your next message", "会包含在你的下一条消息中")}
          </span>
        </div>
      ) : null}
      {/* Live-task footer: cancel button for in-flight tasks. */}
      {attach.task_id
        && (attach.status === "running" || attach.status === "pending"
            || attach.status === "queued") ? (
        <div className="attach-card-footer">
          <span className="attach-card-status">
            {text("Running - embedding placeholder until done", "运行中，完成前使用嵌入占位")}
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
            title={text("Stop this sub-agent", "停止这个子 Agent")}
          >
            {text("Cancel", "取消")}
          </button>
        </div>
      ) : null}
      {/* Auto-followup notice — surfaced after the task completes.
          The runner writes a synthetic ``[系统消息]…`` user msg to
          trigger the parent agent to react to the sub-task's output.
          That msg lives on the main lane (display=runtime, hidden
          from the chat panel) but the user kept asking "what is
          this DAG node I'm hovering?" because it wasn't surfaced
          anywhere. Show it inline as a small italic footer on the
          attach card so the card represents the full sub-task
          lifecycle: spawn → status → preview → auto-followup. */}
      {followupNotice ? (
        <div
          className="attach-card-followup"
          // The followup user msg has no chat bubble of its own
          // (display=runtime). Tag this card section with its id so
          // the history-graph visibility scan marks the DAG node
          // visible while the card is in view — and crucially, lets
          // it scroll out of view with the card.
          data-msg-id={followupMsgId || undefined}
        >
          <div className="attach-card-followup-label">{text("Auto follow-up", "自动 follow-up")}</div>
          <div className="attach-card-followup-body">{followupNotice}</div>
        </div>
      ) : null}
    </div>
  );
}
