"use client";

/**
 * Assistant message bubble — React port of the legacy
 * `.message.assistant` + `.chat-stream-body` scaffold.
 *
 * Execution details share one timeline across streaming and history.
 * While the turn is still streaming with
 * nothing rendered yet, a typing indicator stands in.
 */
import { RewindErrorActions, useRewindOffered } from "../../rewind/rewind-error-actions";
import { lazy, memo, Suspense, useLayoutEffect, useMemo, useRef } from "react";
import { useShallow } from "zustand/react/shallow";
import { ChevronDown, CircleAlert, Square } from "lucide-react";

import {
  selectRow,
  selectViewRows,
  useSessionStore,
  type AssistantBlock,
  type ChatMsg,
} from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";

import { AttachCard } from "../cards/attach-card";
import { RetryStep, RetryStrip, RetryTag } from "../execution/retry-status";
import {
  ExecutionStrip,
  FunctionStep,
  ProviderSearchStep,
  RestartStep,
  SubAgentStep,
  SubAgentStrip,
  StepRow,
  ThinkingStep,
  toolOutcome,
} from "../execution/execution-strip";
import { StepExpansionScope } from "../execution/step-expansion";
import type { TNode } from "../execution/tree-types";
import { MessageActions, MessageTimestamp } from "./message-actions";
import { typesetMath } from "@/lib/runtime-bridge/dom/markdown-render";
import { renderMermaidLazily } from "@/lib/chat/markdown/mermaid-lazy";
import { mountVisualsIn } from "@/lib/chat/markdown/visual-frames";
import { renderMarkdown, useMarkdownReady } from "./markdown";
import { splitMarkdownBlocks } from "@/lib/chat/markdown/streaming-blocks";
import { TurnFilesChips } from "../turn-files/turn-files-chips";
import { summarizeToolGroup } from "../execution/tool-group-summary";
import { shouldRenderTurnFiles } from "../turn-files/turn-files-presentation";
import { AssistantFileCards, sentFilePath } from "../attachments/assistant-file-cards";
import { ProseFileLinks } from "./prose-file-links";
import { parseAttachments } from "../attachments/user-attachments";
import { ThinkingStatus } from "../execution/thinking-status";
import { UserBubble } from "./user-bubble";
import { PendingSteers } from "./pending-steers";
import { CitationSources } from "./web-sources";
import { isProviderSearch } from "@/lib/chat/transcript/web-sources";
import { blockIds, ownedChildren, spawnFacts } from "@/lib/chat/transcript/owned-children";
import { isLive, isStopped } from "@/lib/chat/transcript/row-status";
import { continuedRows, fallbackBlocks as flatBlocks, joinedReply } from "@/lib/chat/transcript/restart-continuation";
import { useRowSessionId } from "../list/transcript-view-scope";

const QuotaErrorCard = lazy(() => import("./quota-reply-card").then((m) => ({ default: m.QuotaErrorCard })));

function SteeringInput({block, sessionId, viewKey}: {block: AssistantBlock; sessionId?: string; viewKey?: string}) {
  const user = useSessionStore(selectRow(viewKey, block.message_id));
  const message: ChatMsg = user ?? {id:block.message_id!,role:"user",rowKind:"user",content:block.text ?? "",steering:true,status:"done",timestamp:block.timestamp};
  return <div className="steering-input"><UserBubble msg={message} sessionIdOverride={sessionId}/></div>;
}

/** Older agent calls can lack an ordered tool block. They still use the
 * same execution rows as current chat, never a second runtime-card renderer. */
function LegacyRuntimeTrace({ children: calls, sessionId }: { children: ChatMsg[]; sessionId?: string }) {
  const { text } = useTranslation();
  const blocks: AssistantBlock[] = calls.map((call) => ({
    type: "tool", tool: call.function || "call", tool_call_id: call.id,
    result: call.content || undefined, is_error: call.status === "error",
    outcome: call.status === "cancelled" ? "cancelled" : undefined,
  }));
  const running = calls.some((call) => isLive(call.status));
  return (
    <ExecutionStrip
      streaming={running}
      label={summarizeToolGroup(blocks, {
        text,
        active: running,
        runningIds: new Set(calls.filter((call) => isLive(call.status)).map((call) => call.id)),
      })}
    >
      {calls.map((call, index) => (
        <FunctionStep key={call.id}
          sessionId={sessionId}
          block={blocks[index]}
          tree={call.contextTree as TNode | undefined}
          running={isLive(call.status)}
        />
      ))}
    </ExecutionStrip>
  );
}

/** Categorized, actionable headline for a failed turn, by error reason
 *  (see docs/design/providers/reliability/error-taxonomy-propagation.md). Returns null
 *  for reasons with no copy better than the raw message. */
export function errorHeadline(
  msg: ChatMsg,
  text: (en: string, zh: string) => string,
  rewindOffered: boolean,
): string | null {
  const after = msg.errorRetryAfterS ? Math.ceil(msg.errorRetryAfterS) : 0;
  switch (msg.errorReason) {
    case "rate_limit":
      return after
        ? text(`Rate limited — try again in ${after}s.`, `请求过于频繁 —— ${after} 秒后重试。`)
        : text("Rate limited — try again shortly.", "请求过于频繁 —— 稍后重试。");
    case "auth":
      return text(
        "Your API key was rejected — check it in Settings → Providers.",
        "API key 被拒 —— 去 设置 → Providers 检查。",
      );
    case "authz":
      return text(
        "Not authorized — check your plan / access for this model.",
        "无权限 —— 检查该模型的套餐 / 访问权限。",
      );
    case "blocked":
      return text(
        "The provider's network refused the address you connect from — switch your proxy node or turn off the VPN, then try again.",
        "模型服务的网络拒绝了你当前的出口地址 —— 换一个代理节点或关闭 VPN 后重试。",
      );
    case "context":
      return rewindOffered
        ? text(
          "This conversation is too long — rewind to an earlier message, compact it or start a new chat.",
          "对话太长 —— 回退到更早的消息、压缩或新开对话。",
        )
        : text(
          "This conversation is too long — compact it or start a new chat.",
          "对话太长 —— 压缩或新开对话。",
        );
    case "policy":
      return rewindOffered
        ? text(
          "The provider blocked this request (content policy). Rewind to change the message.",
          "提供商按内容政策拦截了此请求，可以回退后修改这条消息。",
        )
        : text(
          "The provider blocked this request (content policy).",
          "提供商按内容政策拦截了此请求。",
        );
    case "provider":
    case "transport":
      return text(
        "Temporary provider / network error — try again.",
        "提供商 / 网络临时错误 —— 重试即可。",
      );
    case "timeout":
      return text("The request timed out — try again.", "请求超时 —— 重试。");
    default:
      return null; // invalid / unknown → show the raw message only
  }
}

const SettledMarkdown = memo(function SettledMarkdown({ text }: { text: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const html = renderMarkdown(text);
  useLayoutEffect(() => {
    if (ref.current) {
      typesetMath(ref.current);
      renderMermaidLazily(ref.current);
      mountVisualsIn(ref.current);
    }
  }, [html]);
  return (
    <div
      ref={ref}
      className="chat-text message-content"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
});

type DrawnMarkdown = { host: HTMLElement; root: HTMLElement; blocks: { src: string; nodes: ChildNode[] }[] };

/** Text a reply is still writing. Finished top-level blocks keep their DOM
 *  (typeset math included); each delta re-renders only the open last block,
 *  into the same `.md-rendered` column the settled reply draws. */
function StreamingMarkdown({ text }: { text: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const drawn = useRef<DrawnMarkdown | null>(null);
  useLayoutEffect(() => {
    const host = ref.current;
    if (!host) return;
    if (drawn.current?.host !== host) {
      const root = document.createElement("span");
      root.className = "md-rendered";
      // Each block is typeset as it is drawn; a page-wide pass would
      // rewrite nodes this component still tracks.
      root.dataset.mathRendered = "1";
      host.replaceChildren(root);
      drawn.current = { host, root, blocks: [] };
    }
    const { root, blocks } = drawn.current;
    const chunks = splitMarkdownBlocks(text);
    let kept = 0;
    while (kept < blocks.length && kept < chunks.length && blocks[kept].src === chunks[kept]) kept++;
    for (const block of blocks.splice(kept)) block.nodes.forEach((node) => node.remove());
    chunks.slice(kept).forEach((src, index) => {
      const box = document.createElement("div");
      box.innerHTML = renderMarkdown(src, { cache: kept + index < chunks.length - 1 });
      typesetMath(box);
      const rendered = box.firstElementChild?.classList.contains("md-rendered") ? box.firstElementChild : box;
      const nodes = Array.from(rendered.childNodes);
      root.append(...nodes);
      blocks.push({ src, nodes });
    });
    renderMermaidLazily(host);
    mountVisualsIn(host);
  }, [text]);
  return <div ref={ref} className="chat-text message-content" />;
}

/** A settled reply renders its whole text at once; a streaming one keeps
 *  finished blocks and redraws only the last. */
export function MarkdownText({ text, streaming = false }: { text: string; streaming?: boolean }) {
  return streaming ? <StreamingMarkdown text={text} /> : <SettledMarkdown text={text} />;
}

/** Whether a reply is still being worked on, as its execution reports it
 *  (a status can lag the execution's end), and whether it waits on an approval. */
export function useReplyLive(msg: ChatMsg, sessionId: string | undefined) {
  const waitingApproval = useSessionStore((s) => s.pendingDecisions.some((decision) => {
    const order = s.executionUpdateOrders[decision.executionId];
    return decision.kind === "approval" && decision.sessionId === sessionId
      && order?.sessionId === sessionId && !order.terminal
      && order.messageIds.includes(msg.id);
  }));
  const streaming = isLive(msg.status);
  return { streaming, waitingApproval };
}

function TypingIndicator() {
  return (
    <div className="pending-body">
      <ThinkingStatus />
    </div>
  );
}

export function AssistantBubble({ msg: ownMsg, verdict, sessionIdOverride, embedded = false, viewKey }: {
  msg: ChatMsg;
  sessionIdOverride?: string;
  /** A sub-agent's reply inside its step row's transcript: no header or action row. */
  embedded?: boolean;
  /** The view these rows belong to, when it is not the conversation itself. */
  viewKey?: string;
  /** Internal Goal report, using the same trace and detail panel as tools. */
  verdict?: { summary: string; json: string };
}) {
  // Subscribed so the bubble re-renders once `renderMd` lands and the
  // markdown can be rendered for real instead of escaped.
  useMarkdownReady();
  const bubbleSessionId = useRowSessionId(sessionIdOverride || undefined) || undefined;
  const bubbleView = viewKey ?? bubbleSessionId;
  // A reply that carries on work a worker restart cut off draws the cut-off
  // reply's steps in front of its own, as one reply.
  const cutOff = useSessionStore(useShallow((s) => continuedRows(selectViewRows(bubbleView)(s), ownMsg)));
  const msg = useMemo(
    () => joinedReply(cutOff, ownMsg, (content) => parseAttachments(content).text),
    [cutOff, ownMsg],
  );
  const rewindOffered = useRewindOffered(bubbleSessionId, msg.errorReason);
  const quotaCause = msg.status === "error" && msg.errorCause?.cause === "quota_exhausted" ? msg.errorCause : null;
  const { text } = useTranslation();
  const stoppedAs = !msg.goalVerification && isStopped(msg.status) ? msg.status : undefined;
  // Set while drawing the blocks: whether the last row took the stopped tag.
  let stopTagged = false;
  const { streaming, waitingApproval } = useReplyLive(msg, bubbleSessionId);
  const activeExecution = streaming && !waitingApproval && !msg.retryStatus;
  // A retry the reply waits on rides the last tool group's row when the
  // reply ends in one, else it is a row of its own (retry-status.tsx).
  const retry = streaming && !waitingApproval ? msg.retryStatus : undefined;
  let retryPlaced = false;
  const tools = msg.tools ?? [];
  const runningToolIds = new Set(tools.filter((tool) => tool.status === "running").map((tool) => tool.id));
  // Files the turn handed back via ``send_file`` ride the reply text as
  // the same ``[attachment: … @ /abs]`` marker an inbound attachment
  // uses — one lexicon, one parser, one chip, both directions. Pull
  // them out here so the prose renders clean and the chips are
  // clickable.
  const { attachments: outboundFiles, text: contentText } =
    parseAttachments(msg.content);
  const hasContent = !!contentText;

  // 流式期间 chat-stream.ts 按事件到达顺序增量构建 msg.blocks（思考被
  // 工具调用打断后再来的 delta 开新段），所以进行中和落定/刷新走的是
  // 同一份交替时间线数据——边跑边往下长，落定时由 finalize 用后端权威
  // blocks 覆盖一次（顺序一致，视觉不动）。
  // Legacy/final frames may retain the flat fields without ordered blocks.
  // Adapt those existing records to the shared timeline, without inventing
  // interleaving that the stored data does not contain.
  const fallbackBlocks: AssistantBlock[] = flatBlocks(msg);
  const effBlocks = msg.blocks?.length ? msg.blocks
    : fallbackBlocks.length ? fallbackBlocks : undefined;
  // "进行中"的行 = 时间线的最后一个块（且还在流式）。只有它有动画：
  // 思考行刷最新一行字 + 呼吸点，其余行已定格。
  const lastBlockIdx = effBlocks ? effBlocks.length - 1 : -1;
  // A file sent with ``send_file`` is drawn right after the steps that sent
  // it, where the call sits in the reply; the rest stay at the end.
  const showFiles = !streaming && msg.status !== "error" && msg.status !== "cancelled";
  const sentFiles = (items: AssistantBlock[]) => showFiles
    ? items.flatMap((b) => b.type === "tool" && b.tool === "send_file" ? [sentFilePath(b.input, outboundFiles)] : [])
      .filter((path): path is string => !!path)
    : [];
  const placedFiles = [...new Set(sentFiles(effBlocks ?? []))];

  // Who owns each card and call tree (owned-children.ts).
  const blockKeys = effBlocks ? blockIds(effBlocks) : [];
  const owned = ownedChildren(effBlocks ?? [], blockKeys, {
    attachCards: msg.attachCards,
    runtimeChildren: msg.runtimeChildren,
    callRoots: msg.callRoots as unknown as TNode[] | undefined,
  });
  const runtimeChildren = msg.runtimeChildren ?? [];
  // Screen readers announce the agent that wrote the reply; nothing is drawn.
  const namedAgent = msg.agentId && msg.agentId !== "main" ? msg.agentId : null;
  const sender = namedAgent ?? text("Assistant", "助手");
  return (
    <div
      className={embedded ? "message assistant sub-agent-reply" : "message assistant"}
      data-msg-id={msg.id}
      data-agent-id={msg.agentId || undefined}
      data-session-id={bubbleSessionId}
      /* Each turn is an article so screen readers can jump message to
         message (and announce the sender) instead of hearing the whole
         transcript as one undifferentiated run of text. Streaming text
         itself is NOT a live region — token-by-token deltas would flood
         the announcement queue; the short pending/typing status below
         carries the aria-live instead. */
      role="article"
      aria-label={sender}
    >
      {(
        <ProseFileLinks sessionId={bubbleSessionId} settled={!streaming}>
        <div className="chat-stream-body">
          <StepExpansionScope view={viewKey ?? bubbleSessionId} row={msg.id}>
          {effBlocks ? (
            (() => {

              // Legacy backfill: pre-block-schema sessions only
              // persisted tool blocks (no text/thinking), but
              // ``msg.content`` carries the LLM's final narration.
              // If blocks has zero text entries, append the content
              // as one text node after the tool cards so the user
              // still sees the answer.
              const hasTextBlock = effBlocks.some((b) => b.type === "text");
              // ── 分段：text 块常驻；连续的 thinking/tool 块聚成一段
              // 执行痕迹。流式与结束后都保留同一个可展开的摘要条。
              // 段内出现 agent/send_message 调用时，把对应的
              // Spawned 卡（一行态）挂在该段摘要条下面——在哪调用就
              // 画在哪。
              type ExecSeg = {
                kind: "exec";
                items: Array<{ b: AssistantBlock; i: number }>;
                cards: ChatMsg[];
              };
              type TextSeg = { kind: "text"; b: AssistantBlock; i: number } | { kind: "steering"; b: AssistantBlock; i: number };
              const segs: Array<ExecSeg | TextSeg> = [];
              effBlocks.forEach((b, i) => {
                if (b.type === "text" || b.type === "steering") {
                  segs.push({ kind: b.type, b, i });
                  return;
                }
                const last = segs[segs.length - 1];
                const seg: ExecSeg =
                  last && last.kind === "exec"
                    ? last
                    : (() => {
                        const s: ExecSeg = { kind: "exec", items: [], cards: [] };
                        segs.push(s);
                        return s;
                      })();
                seg.items.push({ b, i });
                const card = owned.cards.get(blockKeys[i]);
                if (card) seg.cards.push(card);
              });
              const rendered: React.ReactNode[] = [];
              const drawnFiles = new Set<string>();
              // A turn stopped while it worked marks its last row; one stopped
              // while it wrote says so in a sentence below.
              const lastSeg = segs[segs.length - 1];
              stopTagged = Boolean(stoppedAs && lastSeg?.kind === "exec");
              segs.forEach((seg, si) => {
                if (seg.kind === "steering") {
                  rendered.push(<SteeringInput key={blockKeys[seg.i]} block={seg.b} sessionId={bubbleSessionId} viewKey={bubbleView}/>);
                  return;
                }
                if (seg.kind === "text") {
                  rendered.push(<MarkdownText key={blockKeys[seg.i]} text={seg.b.text || ""} streaming={streaming && seg.i === lastBlockIdx} />);
                  if (seg.b.citations?.length) {
                    rendered.push(<CitationSources key={`cite:${blockKeys[seg.i]}`} citations={seg.b.citations} />);
                  }
                  return;
                }

                // 时间线步骤（chat-turn-visual-spec.html）——流式与落定
                // 同一条路径：进行中折叠为 shimmer 摘要条，点击展开实时
                // 列表；运行中的行呼吸点。thinking → 思考行；spawn 调用
                // → 子代理行；agentic 工具 → 函数行 + context_tree 递归
                // 子层级；普通工具 → 函数行。
                const stripId = `exec:${blockKeys[seg.items[0].i]}`;
                const steps: React.ReactNode[] = [];
                seg.items.forEach(({ b, i }) => {
                  const blockId = blockKeys[i];
                  if (b.type === "thinking") {
                    steps.push(
                      <ThinkingStep
                        key={blockId}
                        expansionId={blockId}
                        text={b.text || ""}
                        running={activeExecution && i === lastBlockIdx}
                      />,
                    );
                    return;
                  }
                  if (b.type === "restart") {
                    steps.push(<RestartStep key={blockId} />);
                    return;
                  }
                  if (b.type !== "tool") return;
                  if (isProviderSearch(b)) {
                    // The provider ran it: no call tree, no runtime row,
                    // no function card — one "Searched the web" row.
                    steps.push(
                      <ProviderSearchStep
                        key={blockId}
                        expansionId={blockId}
                        block={b}
                        running={activeExecution && !!b.tool_call_id && runningToolIds.has(b.tool_call_id)}
                        sessionId={bubbleSessionId}
                        turnStatus={msg.status}
                      />,
                    );
                    return;
                  }
                  const card = owned.cards.get(blockId);
                  if (card) {
                    steps.push(<SubAgentStep key={blockId} expansionId={blockId} card={card} />);
                    return;
                  }
                  const tree = (owned.runtimeChildren.get(blockId)?.contextTree as TNode | undefined)
                    || owned.callRoots.get(blockId) || null;
                  steps.push(
                    <FunctionStep
                      key={blockId}
                      expansionId={blockId}
                      block={b}
                      tree={tree}
                      running={activeExecution && !!b.tool_call_id && runningToolIds.has(b.tool_call_id)}
                      sessionId={bubbleSessionId}
                      turnStatus={msg.status}
                    />,
                  );
                });
                const carriesRetry = !!retry && si === segs.length - 1;
                if (carriesRetry) {
                  steps.push(<RetryStep key="retry" retry={retry} />);
                  retryPlaced = true;
                }
                const cardFacts = spawnFacts(seg.cards, text);
                rendered.push(
                  <ExecutionStrip
                    key={stripId}
                    expansionId={stripId}
                    status={carriesRetry ? <RetryTag retry={retry} /> : undefined}
                    stopped={stopTagged && si === segs.length - 1 ? stoppedAs : undefined}
                    streaming={activeExecution && seg.items.some(({ i }) => i === lastBlockIdx)}
                    subagentHeads={cardFacts.spawnHeads}
                    label={summarizeToolGroup(seg.items.map(({ b }) => {
                      const outcome = b.type === "tool"
                        ? toolOutcome(b, msg.status, activeExecution && !!b.tool_call_id && runningToolIds.has(b.tool_call_id))
                        : b.outcome;
                      return outcome === b.outcome ? b : { ...b, outcome };
                    }), {
                      text,
                      active: activeExecution && seg.items.some(({ i }) => i === lastBlockIdx),
                      runningIds: runningToolIds,
                      ...cardFacts,
                      sessionId: bubbleSessionId,
                      turnFiles: msg.turnFiles,
                      turnBlocks: effBlocks,
                    })}
                  >
                    {steps}
                  </ExecutionStrip>,
                );
                const segFiles = sentFiles(seg.items.map(({ b }) => b)).filter((path) => {
                  if (drawnFiles.has(path)) return false;
                  drawnFiles.add(path);
                  return true;
                });
                if (segFiles.length) {
                  rendered.push(
                    <AssistantFileCards key={`files:${stripId}`} msg={msg} sessionId={bubbleSessionId} attachments={outboundFiles} only={segFiles} />,
                  );
                }
              });
              if (!hasTextBlock && hasContent && (msg.status !== "error" || !!msg.errorDetail)) {
                rendered.push(
                  <MarkdownText key="legacy_content" text={contentText} streaming={streaming} />,
                );
              }
              if (owned.unpairedRuntimeChildren.length > 0) {
                rendered.push(<LegacyRuntimeTrace key="legacy_runtime" sessionId={bubbleSessionId}>{owned.unpairedRuntimeChildren}</LegacyRuntimeTrace>);
              }
              // A card no block owns still draws as a timeline row, never as
              // AttachCard, so it reads the same before and after a reload.
              if (owned.unpairedCards.length > 0) {
                rendered.push(
                  <SubAgentStrip key="subagents" cards={owned.unpairedCards} streaming={activeExecution} sessionId={bubbleSessionId} expansionId="subagents" />,
                );
              }
              owned.references.forEach((card) => {
                rendered.push(
                  <div
                    key={`attach_${card.id}`}
                    className="attach-row"
                    data-msg-id={card.id}
                  >
                    <AttachCard msg={card} />
                  </div>,
                );
              });
              return rendered;
            })()
          ) : (
            <>
              {/* 无 blocks 的旧会话仍用普通时间线行，不切回卡片 UI。 */}
              {owned.unpairedCards.length > 0 ? (
                <SubAgentStrip cards={owned.unpairedCards} streaming={activeExecution} sessionId={bubbleSessionId} expansionId="subagents" />
              ) : null}
              {owned.references.map((card) => (
                <div key={`attach_${card.id}`} className="attach-row" data-msg-id={card.id}>
                  <AttachCard msg={card} />
                </div>
              ))}
              {runtimeChildren.length > 0
                ? <LegacyRuntimeTrace sessionId={bubbleSessionId}>{runtimeChildren}</LegacyRuntimeTrace> : null}
              {hasContent && (msg.status !== "error" || !!msg.errorDetail) ? <MarkdownText text={contentText} streaming={streaming} /> : null}
              {streaming && !hasContent && !waitingApproval && !verdict && !msg.retryStatus
                && msg.rowKind === "assistant" && runtimeChildren.length === 0
                && !msg.attachCards?.length && !msg.callRoots?.length ? <TypingIndicator /> : null}
            </>
          )}
          {msg.status === "error" && !msg.goalVerification ? (() => {
            const details = (
              <details className="turn-error-details">
                <summary>{text("Error details", "错误详情")}<ChevronDown size={12} aria-hidden="true" /></summary>
                <pre>{msg.errorDetail || msg.content || text("Request failed.", "请求失败。")}</pre>
              </details>
            );
            const rewind = embedded ? null : <RewindErrorActions msg={msg} sessionId={bubbleSessionId} />;
            const plain = (
              <div className="turn-error" role="status">
                <span className="turn-error-icon"><CircleAlert size={20} aria-hidden="true" /></span>
                <div className="turn-error-body">
                  <div className="turn-error-title">{errorHeadline(msg, text, rewindOffered && !embedded)
                    || text("This reply could not be completed.", "本次回复未能完成。")}</div>
                  {details}
                  {rewind}
                </div>
              </div>
            );
            // A spent quota names the provider, the limit and its reset, and
            // offers another model (loaded only when one is drawn).
            return quotaCause ? (
              <Suspense fallback={plain}>
                <QuotaErrorCard msg={msg} cause={quotaCause} sessionId={bubbleSessionId} embedded={embedded} after={rewind}>
                  {details}
                </QuotaErrorCard>
              </Suspense>
            ) : plain;
          })() : null}
          {stoppedAs && !stopTagged ? (
            <div className="turn-stopped-note" role="status">
              <Square size={14} aria-hidden="true" />
              {stoppedAs === "cancelled"
                ? text("You stopped this reply.", "你停止了这次回复。")
                : text("This reply was interrupted.", "这次回复被中断了。")}
            </div>
          ) : null}
          {streaming && !embedded && bubbleSessionId && msg.id
            ? <PendingSteers sessionId={bubbleSessionId} replyId={msg.id} />
            : null}
          {waitingApproval ? (
            <div className="pending-body" role="status">
              <span className="pending-label">{text("Waiting for approval", "等待审批")}</span>
            </div>
          ) : null}
          {verdict ? (
            <ExecutionStrip label={verdict.summary} streaming={activeExecution}>
              <StepRow
                icon="llm"
                title={text("Verification report", "验收报告")}
                running={activeExecution}
                copyText={verdict.json}
                detail={{
                  path: `chat-report:${bubbleSessionId || ""}:${msg.id}`,
                  name: text("Verification report", "验收报告"),
                  status: streaming ? "running" : msg.status === "error" ? "error" : "completed",
                  output: verdict.json,
                }}
              />
            </ExecutionStrip>
          ) : null}
          {retry && !retryPlaced ? <RetryStrip retry={retry} /> : null}
          {showFiles ? (
            <AssistantFileCards msg={msg} sessionId={bubbleSessionId} attachments={outboundFiles} skip={placedFiles} />
          ) : null}
          {!streaming && msg.id && shouldRenderTurnFiles(msg.turnFiles, msg.blocks) ? (
            <TurnFilesChips
              key={msg.id}
              assistantMsgId={msg.id}
              blocks={msg.blocks}
              summary={msg.turnFiles}
              initiallyReverted={msg.reverted}
              sessionIdOverride={sessionIdOverride}
            />
          ) : null}
          </StepExpansionScope>
        </div>
        </ProseFileLinks>
      )}
      {/* Action row at the BOTTOM-RIGHT of the message — you finish
          reading, then reach for copy/retry/branch right where your
          eyes land, instead of back up at the header. */}
      {embedded ? null : <div className="message-actions-footer">
        {streaming ? (
          <div className="message-actions">
            <MessageTimestamp timestamp={msg.timestamp} />
          </div>
        ) : (
          <MessageActions msg={msg} sessionIdOverride={sessionIdOverride} />
        )}
      </div>}
    </div>
  );
}
