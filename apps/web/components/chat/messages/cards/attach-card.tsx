"use client";

/**
 * AttachCard — rendered for assistant rows whose ``function === "attach"``.
 *
 * An attach row records that this turn spawned another agent. The card is
 * one row: the agent's character and name, what it was asked, its state,
 * how long it ran and what it cost. Opening the row shows its final reply,
 * the files it changed, and the button that goes to its conversation. A
 * state the person has to act on (it needs approval, it failed) is on the
 * row itself.
 *
 * The spawned agent's reply is a branch in the SAME session, so Switch
 * checks out that branch in this chat view (no cross-session navigation).
 * Legacy rows (from before the same-session refactor) carry a foreign
 * ``session_id`` and fall back to navigating to that session.
 */
import { sendExecutionCancel } from "@/lib/execution/cancel-execution";
import { useEffect, useRef, useState } from "react";
import { ChevronRight, FileDiff } from "lucide-react";

import type { ChatMsg } from "@/lib/session-store";

import { selectRow, selectViewRows, useSessionStore } from "@/lib/session-store";
import { isAgentRunning } from "@/lib/chat/transcript/row-status";
import { agentCardLive, agentCardState, costOf, taskWords, type AgentCardState } from "@/lib/chat/transcript/agent-card";
import { onFindReveal } from "@/lib/chat/transcript/conversation-find";
import { subAgentShape } from "@/lib/runtime-bridge/dag/render/agent-look";
import { useTranslation } from "@/lib/i18n";
import type { JobResourceView } from "@/lib/net/ws/ws-events";
import { navigate } from "@/lib/nav/navigate";
import { runtimeState } from "@/lib/runtime-bridge/state";
import { sendRuntimeCommand } from "@/lib/runtime-bridge/session/session-load";
import { fmtUsd } from "@/components/settings/providers/status/model";
import { BotGlyph } from "@/components/avatar/bot/bot-glyph";
import { duration } from "@/components/right-sidebar/running/running-format";
import { RosterBadge, usageTip } from "@/components/right-sidebar/running/agent-roster";
import { Badge } from "@/components/ui/badge";
import { HoverTip, TipBody } from "@/components/ui/tooltip";
import {
  type AnimatedNavIconHandle,
  ArrowUpRightIcon,
} from "@/components/animated-icons";
import { renderMarkdown, useMarkdownReady } from "../bubbles/markdown";
import { MessageTimestamp } from "../bubbles/message-actions";
import { useRowSessionId } from "../list/transcript-view-scope";

/** Commands go out as they are; a `load_session` goes through the managed
 *  session read, since the transcript applies only the reply to a read it
 *  registered. */
function wsSend(payload: unknown): void {
  sendRuntimeCommand(payload);
}

interface BranchRow {
  head_msg_id: string;
  name?: string;
  active?: boolean;
}

/** Branch rows for a session. `runtimeState._branchesByConv` is typed
 *  loosely (`unknown[]`) because `fetchBranches` fills it; this narrows
 *  to the shape the card reads. */
function branchRows(sessionId: string): BranchRow[] {
  return (runtimeState._branchesByConv[sessionId] as BranchRow[]) || [];
}

function _branchNameFor(
  sessionId: string | null | undefined,
  headId: string,
): string {
  if (!sessionId || !headId) return "";
  const list = branchRows(sessionId);
  const match = list.find((b) => b.head_msg_id === headId);
  return (match?.name || "").trim();
}

function _activeHeadId(sessionId: string | null | undefined): string {
  if (!sessionId) return "";
  // Prefer the branch list's active flag — that's what the rest of
  // the UI uses to label the topbar chip / Branches panel HEAD pill.
  const active = branchRows(sessionId).find((b) => b.active);
  if (active?.head_msg_id) return active.head_msg_id;
  // TODO(store-migration): head_id is a heavy field not carried in
  // ConvSummary, so this still reads runtimeState.conversations. Migrate
  // once head_id is surfaced through the store (or the branch list alone).
  const conv = runtimeState.conversations[sessionId] as
    | { head_id?: string }
    | undefined;
  return conv?.head_id || "";
}

/** Look up the auto-followup user msg that the runner wrote after
 *  this attach pointer's job completed. Matches by walking forward
 *  in this session's message order from the attach pointer and
 *  picking up the next user msg with ``source === "job_followup"``.
 *  Returns its rendered content (the "[系统消息]..." prompt) so the
 *  attach card can surface it inline. Returns null when the job
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
  const rows = selectViewRows(sessionId)(s);
  for (let i = myIdx + 1; i < order.length; i++) {
    const next = rows[order[i]];
    if (!next) continue;
    if (next.role === "user" && next.source === "job_followup") {
      return next.id;
    }
  }
  return null;
}

function useFollowupMsgId(attachMsgId: string): string | null {
  const sessionId = useRowSessionId();
  return useSessionStore((s) => _findFollowupMsgId(s, sessionId, attachMsgId));
}

function useFollowupNotice(attachMsgId: string): string | null {
  const sessionId = useRowSessionId();
  return useSessionStore((s) => {
    const id = _findFollowupMsgId(s, sessionId, attachMsgId);
    if (!id) return null;
    return selectRow(sessionId, id)(s)?.content || null;
  });
}

/** Seconds since the card was written, counted while its agent runs. */
function useElapsed(timestamp: number | undefined, live: boolean): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [live]);
  if (!live || typeof timestamp !== "number" || !Number.isFinite(timestamp) || timestamp <= 0) return null;
  const started = timestamp > 1e12 ? timestamp : timestamp * 1000;
  return Math.max(0, (now - started) / 1000);
}

type Text = (en: string, zh: string) => string;

/** The badge of each state. The states the Activity roster also names
 *  wear its badge, so a helper reads the same in both places. */
function StateBadge({ state, text }: { state: AgentCardState; text: Text }) {
  switch (state) {
    case "needs_approval": return <RosterBadge status="approval" text={text} />;
    case "needs_input": return <RosterBadge status="input" text={text} />;
    case "running": return <RosterBadge status="running" text={text} />;
    case "queued": return <RosterBadge status="waiting" text={text} />;
    case "failed": return <RosterBadge status="failed" text={text} />;
    case "cancelling":
      return <Badge variant="secondary"><span className="indicator-dot sm --neutral pulse-opacity" data-icon="inline-start" aria-hidden="true" />{text("Cancelling", "正在取消")}</Badge>;
    case "stopped":
      return <Badge variant="outline" className="text-text-muted">{text("Stopped", "已停止")}</Badge>;
    case "done":
      return <Badge variant="outline">{text("Done", "已完成")}</Badge>;
    case "attached":
      return <Badge variant="outline" className="text-text-muted">{text("Attached", "已附加")}</Badge>;
    case "none":
      return null;
  }
}

/** Why a limit or the person ended the agent, in the reader's words. */
function reasonWords(reason: string | undefined, text: Text): string {
  switch (reason) {
    case "user": return text("Stopped by you", "由你停止");
    case "timeout": return text("Ran past its time limit", "超过运行时限");
    case "spend cap": return text("Reached the spend cap", "达到花费上限");
    case "idle limit": return text("Idle too long", "空闲过久");
    case "cost limit": return text("Reached its cost limit", "达到费用上限");
    case "token limit": return text("Reached its token limit", "达到 Token 上限");
    default: return "";
  }
}

export function AttachCard({ msg }: { msg: ChatMsg }) {
  useMarkdownReady();
  const { text } = useTranslation();
  const switchIconRef = useRef<AnimatedNavIconHandle>(null);
  // One row by default; the reply, the files and the actions render only
  // once it is opened.
  const [expanded, setExpanded] = useState(false);
  const currentSessionId = useRowSessionId();
  const attach = msg.attach || {};
  const jobId = attach.job_id;
  const executionId = attach.execution_id;
  const expectedVersion = attach.status_version;
  const [resource, setResource] = useState<JobResourceView>();
  const followupNotice = useFollowupNotice(msg.id);
  const followupMsgId = useFollowupMsgId(msg.id);
  const targetSessionId = attach.session_id || "";
  const targetHead = attach.head_id || "";
  const label = (attach.label || "").trim();
  const headTag = targetHead ? targetHead.slice(0, 8) : "";
  const hasEmbedStats = typeof attach.embed_count === "number" && attach.embed_count > 0;
  const sameSession =
    !!targetSessionId && targetSessionId === currentSessionId;
  // A question the agent stopped at is asked in this conversation; the
  // row says so, since the card may be far above the composer.
  const waitId = executionId || jobId || "";
  const waiting = useSessionStore((s) => {
    if (!waitId) return null;
    const asked = s.pendingDecisions.find((decision) => decision.executionId === waitId);
    return asked ? (asked.kind === "approval" ? "approval" : "input") : null;
  });
  const state = agentCardState(attach, waiting);
  const live = agentCardLive(state);
  const elapsed = useElapsed(msg.timestamp, live);
  // A find match inside the folded reply opens the card.
  useEffect(() => onFindReveal(msg.id, () => setExpanded(true)), [msg.id]);
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

  // The running agent's cost so far comes with its Job's status.
  useEffect(() => {
    setResource(undefined);
    if (!jobId) return;
    const onStatus = (e: WindowEventMap["op:job-status"]) => {
      if (e.detail?.job_id !== jobId || !e.detail.resource) return;
      setResource(e.detail.resource);
    };
    const onMessage = (e: WindowEventMap["op:job-message"]) => {
      if (e.detail?.type !== "job") return;
      const data = e.detail.data as {
        job?: { id?: string; resource?: JobResourceView } | null;
      } | undefined;
      if (data?.job?.id !== jobId) return;
      setResource(data.job.resource);
    };
    window.addEventListener("op:job-status", onStatus);
    window.addEventListener("op:job-message", onMessage);
    wsSend({ action: "get_job", job_id: jobId });
    return () => {
      window.removeEventListener("op:job-status", onStatus);
      window.removeEventListener("op:job-message", onMessage);
    };
  }, [jobId]);

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
    navigate("/s/" + targetSessionId);
  }

  // What kind of reference this is: "Attached" for user-triggered
  // attaches (Branches → Attach to), "Spawned" for /job or job()
  // invocations, "Imported" for legacy cross-session attaches. It is the
  // row's hover tip; the row itself shows the agent's name.
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
  const subtitle = sameSession
    ? text("source branch", "源分支")
    : `${text("from session", "来自会话")} ${targetSessionId}${headTag ? ` @${headTag}` : ""}`;

  const task = taskWords(attach.prompt);
  const usage = attach.usage;
  const seconds = live ? elapsed : usage?.duration_s ?? null;
  const cost = live ? costOf(resource?.resource?.usage?.subtree?.cost_usd) : costOf(usage?.cost_usd);
  const facts = [
    seconds != null ? duration(seconds, text) : "",
    cost != null ? fmtUsd(cost) : "",
  ].filter(Boolean).join(" · ");
  // The rest of what it used, and why it ended, on the row's hover tip.
  const tipDetail = [
    subtitle,
    reasonWords(attach.result_reason, text),
    usageTip({ usage: usage ?? null }, text),
  ].filter(Boolean).join(" · ");
  const preview = msg.content || (live ? "" : text("(no output)", "（无输出）"));
  const files = attach.files && attach.files.count > 0 ? attach.files : null;
  const canOpen = sameSession ? !!targetHead : !!targetSessionId;
  const canCancel = !!executionId && typeof expectedVersion === "number" && isAgentRunning(attach.status);

  return (
    <div
      className="attach-card"
      data-kind="agent"
      data-peer-session-id={targetSessionId}
      data-head-id={targetHead}
      data-expanded={expanded ? "1" : "0"}
      data-state={state}
    >
      <HoverTip label={<TipBody title={`${labelKind}: ${sourceName}`} detail={tipDetail} />}>
        <button
          type="button"
          className="attach-card-row"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
        >
          <span className="attach-card-agent" aria-hidden="true">
            <BotGlyph shape={subAgentShape(sourceName)} size={20} />
          </span>
          <span className="attach-card-name">{sourceName}</span>
          <StateBadge state={state} text={text} />
          <span className="attach-card-task">{task}</span>
          {facts ? <span className="attach-card-facts">{facts}</span> : null}
          <ChevronRight className="attach-card-chevron" size={16} aria-hidden="true" />
        </button>
      </HoverTip>
      {expanded ? (
        <div className="attach-card-body">
          {preview ? (
            <div
              className="attach-card-preview chat-text"
              dangerouslySetInnerHTML={{ __html: renderMarkdown(preview) }}
            />
          ) : null}
          {files ? (
            <div className="attach-card-files">
              <FileDiff size={14} aria-hidden="true" />
              <span>
                {files.count === 1
                  ? text("1 file changed", "改动了 1 个文件")
                  : text(`${files.count} files changed`, `改动了 ${files.count} 个文件`)}
                {files.dirty ? text(", uncommitted", "，未提交") : ""}
              </span>
              {files.branch ? <code>{files.branch}</code> : null}
            </div>
          ) : null}
          {isManual ? (
            <div className="attach-card-footer">
              <span className="attach-card-status">
                {hasEmbedStats
                  ? text("Will be expanded into your next message", "会展开到你的下一条消息中")
                  : text("Will be included in your next message", "会包含在你的下一条消息中")}
              </span>
            </div>
          ) : null}
          {/* Auto-followup notice — surfaced after the job completes.
              The runner writes a synthetic ``[系统消息]…`` user msg to
              trigger the parent agent to react to the sub-job's output.
              That msg lives on the main lane (display=runtime, hidden
              from the chat panel), so the card shows it: spawn → status
              → reply → auto-followup. */}
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
          <div className="attach-card-actions">
            {alreadyHere ? (
              // Already on this branch — a "current branch" tag instead
              // of a Switch button.
              <span className="attach-card-here">
                {text("current", "当前")}
              </span>
            ) : canOpen ? (
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
            {canCancel ? (
              <button
                type="button"
                className="attach-card-cancel"
                onClick={() => {
                  sendExecutionCancel(sendRuntimeCommand, executionId, expectedVersion);
                }}
                title={text("Cancel execution", "取消运行")}
                aria-label={text("Cancel execution", "取消运行")}
              >
                {text("Cancel execution", "取消运行")}
              </button>
            ) : null}
            <div className="message-actions-footer attach-card-time">
              <div className="message-actions">
                <MessageTimestamp timestamp={msg.timestamp} />
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
