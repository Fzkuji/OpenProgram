"use client";

/**
 * Assistant message bubble — React port of the legacy
 * `.message.assistant` + `.chat-stream-body` scaffold.
 *
 * Layout order matches chat-ws.js: Thinking block, then the Tool-calls
 * card, then the answer text. While the turn is still streaming with
 * nothing rendered yet, a typing indicator stands in.
 */
import { memo } from "react";

import {
  useSessionStore,
  type AssistantBlock,
  type ChatMsg,
  type ChatToolCall,
} from "@/lib/session-store";
import {
  agentColor,
  agentDisplayName,
  agentInitial,
  useAgentProfile,
} from "@/lib/format-utils/agent-style";
import { useTranslation } from "@/lib/i18n";
import { Avatar } from "@/components/avatar";

import { AttachCard } from "./attach-card";
import {
  ExecutionStrip,
  execStripLabel,
  FunctionStep,
  SPAWNING_TOOL_NAMES,
  SubAgentStep,
  ThinkingStep,
} from "./execution-strip";
import type { TNode } from "./tree-types";
import { MessageActions, MessageTimestamp } from "./message-actions";
import { useAvatarAlign } from "./use-avatar-align";
import { renderMarkdown, useMarkdownReady } from "./markdown";
import { RuntimeBlock } from "./runtime-block";
import { ToolsBlock } from "./tool-card";
import { TurnFilesChips } from "./turn-files-chips";
import { AttachmentChips, parseAttachments } from "./user-attachments";

/** Categorized, actionable headline for a failed turn, by error reason
 *  (see docs/design/providers/reliability/error-taxonomy-propagation.md). Returns null
 *  for reasons with no copy better than the raw message. */
function errorHeadline(
  msg: ChatMsg,
  text: (en: string, zh: string) => string,
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
    case "context":
      return text(
        "This conversation is too long — compact it or start a new chat.",
        "对话太长 —— 压缩或新开对话。",
      );
    case "policy":
      return text(
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

const MarkdownText = memo(function MarkdownText({ text }: { text: string }) {
  return (
    <div
      className="chat-text message-content"
      dangerouslySetInnerHTML={{ __html: renderMarkdown(text) }}
    />
  );
});

function TypingIndicator() {
  // No name here — the bubble header already shows the agent name, so
  // "<name> is thinking" repeated it. Just the breathing dot + label,
  // sitting in the same content column as the answer text that replaces
  // it (``pending-body`` lives inside ``chat-stream-body`` alongside
  // ``chat-text``), so there's no horizontal jump when the reply lands.
  const { text } = useTranslation();
  return (
    <div className="pending-body">
      <span className="thinking-spinner" aria-hidden="true" />
      <span className="pending-label">{text("thinking…", "思考中…")}</span>
    </div>
  );
}

export function AssistantBubble({ msg, verdict, sessionIdOverride }: {
  msg: ChatMsg;
  sessionIdOverride?: string;
  /** goal 判定/完善内部轮：正文里剥出来的 JSON 尾巴 —— 折成一条
   *  <details>（summary = 裁决摘要，展开 = 原始 JSON，调试用）。
   *  由 message-list 的 AssistantMessage 包装层识别并传入；持久化
   *  数据不动，纯渲染层。 */
  verdict?: { summary: string; json: string };
}) {
  // Subscribed so the bubble re-renders once `renderMd` lands and the
  // markdown can be rendered for real instead of escaped.
  useMarkdownReady();
  // Subscribed so the avatar/name pick up edits made in
  // /settings/general → Agent without a reload.
  const profile = useAgentProfile();
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const { text } = useTranslation();
  // Align the side avatar to the first line of text (re-measures as the
  // message grows / blocks expand).
  const { containerRef, avatarTop } = useAvatarAlign(msg.id);
  const streaming =
    msg.status === "streaming" ||
    msg.status === "pending" ||
    msg.status === "running" ||
    msg.status === "cancelling";
  const tools = msg.tools ?? [];
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
  const runningToolIds = new Set(
    tools.filter((t) => t.status === "running").map((t) => t.id),
  );
  const effBlocks: AssistantBlock[] | undefined =
    msg.blocks && msg.blocks.length > 0 ? msg.blocks : undefined;
  // "进行中"的行 = 时间线的最后一个块（且还在流式）。只有它有动画：
  // 思考行刷最新一行字 + 呼吸点，其余行已定格。
  const lastBlockIdx = effBlocks ? effBlocks.length - 1 : -1;

  const AGENTIC_TOOL_NAMES = new Set(["gui_agent", "research_agent", "wiki_agent"]);
  // Agentic runtime cards are matched to their call site by ORDER, not
  // by id: `_wrap_agentic_runtime_block` persists no placeholder row —
  // the canonical record is the @agentic_function's own DAG code node,
  // whose id is a graph node id carrying no back-reference to the LLM's
  // `tool_call_id`. Both sides are single-turn sequences built in
  // execution order (blocks by `chat-stream`/`conv-mapper` in emit
  // order, runtimeChildren by predecessor append order), so the k-th
  // agentic tool block owns the k-th runtime child.
  //
  // Where this is wrong: agentic calls dispatched CONCURRENTLY within
  // one turn can finish out of order, so the cards swap. Fixing that
  // needs the backend to stamp the LLM tool_call_id onto the code node
  // (then match on it here and keep FIFO only as the fallback).
  const runtimeChildren = msg.runtimeChildren ?? [];
  // Spawned/attach 卡按调用顺序排队：每遇到一个 tool==="agent" 的块就取
  // 一张，画在该工具块的紧后面——思考 → 工具调用 → Spawned 卡 → 回复
  //（在哪调用就画在哪）。剩下没配到块的卡（老数据没记 blocks）兜底画
  // 在回复文本之前。
  const attachFifo = (msg.attachCards ?? []).filter((card) =>
    !card.attach?.manual
    && (!currentSessionId || !card.attach?.session_id
      || card.attach.session_id === currentSessionId));
  const externalAttachCards = (msg.attachCards ?? []).filter((card) =>
    card.attach?.manual
    || (!!currentSessionId && !!card.attach?.session_id
      && card.attach.session_id !== currentSessionId));
  const spawnNames = (cards: ChatMsg[]) => cards.map((card) =>
    (card.attach?.label || "").trim()
    || (card.attach?.head_id || "").slice(0, 8)
    || text("sub-agent", "子代理"));
  const spawnHeads = (cards: ChatMsg[]) =>
    cards.map((card) => card.attach?.head_id);
  // Renders one block in its source-order position.
  const renderBlock = (b: AssistantBlock, idx: number, fifo: ChatMsg[]) => {
    if (b.type === "thinking") {
      // Frameless timeline row (chat-turn-visual-spec.html), same as the
      // settled-turn path below — NOT the old boxed ThinkingBlock. The
      // streaming (flat) path used to render a框; unify on ThinkingStep so
      // thinking is a one-line summary row in both streaming and settled states.
      return <ThinkingStep key={`thk_${idx}`} text={b.text || ""} />;
    }
    if (b.type === "text") {
      return <MarkdownText key={`txt_${idx}`} text={b.text || ""} />;
    }
    // tool block
    const tname = b.tool || "";
    if (AGENTIC_TOOL_NAMES.has(tname)) {
      const rc = fifo.shift();
      if (rc) {
        return (
          <div key={`rt_${idx}`} className="assistant-runtime-children">
            <RuntimeBlock msg={rc} nested />
          </div>
        );
      }
      // No matching runtime row (e.g. the LLM's call was rejected by
      // validation before the @agentic_function body ran, so no
      // runtime placeholder was created). Fall through to the regular
      // ToolsBlock so the failed attempt is still visible — otherwise
      // the bubble silently drops it and adjacent thinking / runtime
      // rows collapse against each other.
    }
    const tc: ChatToolCall = {
      id: b.tool_call_id || `tc_${idx}`,
      tool: tname || "?",
      input: b.input || "",
      result: b.result,
      isError: !!b.is_error,
      status: b.is_error ? "error" : "done",
    };
    return <ToolsBlock key={`tool_${idx}`} tools={[tc]} />;
  };
  const color = agentColor(msg.agentId);
  const initial = agentInitial(msg.agentId);
  const sender = agentDisplayName(msg.agentId);
  return (
    <div
      ref={containerRef}
      className="message assistant"
      data-msg-id={msg.id}
      data-agent-id={msg.agentId || undefined}
      /* Each turn is an article so screen readers can jump message to
         message (and announce the sender) instead of hearing the whole
         transcript as one undifferentiated run of text. Streaming text
         itself is NOT a live region — token-by-token deltas would flood
         the announcement queue; the short pending/typing status below
         carries the aria-live instead. */
      role="article"
      aria-label={sender}
    >
      <div className="message-header" style={{ top: avatarTop }}>
        {/* Per-message agent avatar. Seeded on the sender's display
            name (not the volatile agent_id) so the "Agent" / named
            agent always renders the same glyph across sessions. Falls
            back to the legacy coloured-letter chip when the profile
            explicitly picks that mode in settings, and to upload mode
            when the user has supplied a custom image. */}
        <Avatar
          className="message-avatar bot-avatar"
          size={28}
          radius={8}
          name={sender}
          title={msg.agentId || ""}
          config={
            // Default profile (no agent_id / "main"): honour the user's
            // configured avatar so the glyph doesn't change when the
            // streaming bubble replaces the standalone pending indicator
            // (which uses ``profile.avatar``). Named agents keep their
            // deterministic shapes avatar seeded on the display name.
            !msg.agentId || msg.agentId === "main"
              ? (profile.avatar ?? {
                  kind: "dicebear",
                  style: "shapes",
                  seed: profile.name,
                })
              : {
                  kind: "dicebear",
                  style: "shapes",
                  seed: sender,
                }
          }
        />
        <div className="message-sender">{sender}</div>
      </div>

      {msg.status === "error" ? (
        <div className="error-content">
          {(() => {
            const headline = errorHeadline(msg, text);
            const detail = msg.content || text("Request failed.", "请求失败。");
            return headline ? (
              <>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>{headline}</div>
                <div style={{ opacity: 0.7, fontSize: "0.92em", whiteSpace: "pre-wrap" }}>
                  {detail}
                </div>
              </>
            ) : (
              detail
            );
          })()}
        </div>
      ) : (
        <div className="chat-stream-body">
          {effBlocks ? (
            (() => {
              // Ordered pool of agentic runtime children — each agentic
              // tool block claims the next one (see the note above the
              // `runtimeChildren` binding for why order is the key).
              const fifo = [...runtimeChildren];
              // Legacy backfill: pre-block-schema sessions only
              // persisted tool blocks (no text/thinking), but
              // ``msg.content`` carries the LLM's final narration.
              // If blocks has zero text entries, append the content
              // as one text node after the tool cards so the user
              // still sees the answer.
              const hasTextBlock = effBlocks.some((b) => b.type === "text");
              // ── 分段：text 块常驻；连续的 thinking/tool 块聚成一段
              // 执行痕迹。已落定的轮次把每段折成一条摘要条（点击展开
              // 逐块序列）；流式进行中的轮次平铺，让用户实时看到它在
              // 干嘛。段内出现 agent/send_message 调用时，把对应的
              // Spawned 卡（一行态）挂在该段摘要条下面——在哪调用就
              // 画在哪。
              type ExecSeg = {
                kind: "exec";
                items: Array<{ b: AssistantBlock; i: number }>;
                cards: ChatMsg[];
              };
              type TextSeg = { kind: "text"; b: AssistantBlock; i: number };
              const segs: Array<ExecSeg | TextSeg> = [];
              effBlocks.forEach((b, i) => {
                if (b.type === "text") {
                  segs.push({ kind: "text", b, i });
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
                if (b.type === "tool" && SPAWNING_TOOL_NAMES.has(b.tool || "")
                    && attachFifo.length > 0) {
                  seg.cards.push(attachFifo.shift()!);
                }
              });
              const rendered: React.ReactNode[] = [];
              segs.forEach((seg, si) => {
                if (seg.kind === "text") {
                  rendered.push(renderBlock(seg.b, seg.i, fifo));
                  return;
                }
                const cardFifo = seg.cards.slice();
                // 时间线步骤（chat-turn-visual-spec.html）——流式与落定
                // 同一条路径：进行中折叠为 shimmer 摘要条，点击展开实时
                // 列表；运行中的行呼吸点。thinking → 思考行；spawn 调用
                // → 子代理行；agentic 工具 → 函数行 + context_tree 递归
                // 子层级；普通工具 → 函数行。
                const steps: React.ReactNode[] = [];
                const callRootsFifo = [
                  ...((msg.callRoots as unknown as TNode[] | undefined) ?? []),
                ];
                seg.items.forEach(({ b, i }) => {
                  if (b.type === "thinking") {
                    steps.push(
                      <ThinkingStep
                        key={`thk_${i}`}
                        text={b.text || ""}
                        running={streaming && i === lastBlockIdx}
                      />,
                    );
                    return;
                  }
                  if (b.type !== "tool") return;
                  const tname = b.tool || "";
                  if (SPAWNING_TOOL_NAMES.has(tname) && cardFifo.length > 0) {
                    const card = cardFifo.shift()!;
                    steps.push(
                      <SubAgentStep key={`sub_${card.id}`} card={card} />,
                    );
                    return;
                  }
                  let tree: TNode | null = null;
                  if (AGENTIC_TOOL_NAMES.has(tname)) {
                    const rc = fifo.shift();
                    tree = (rc?.contextTree as TNode | undefined) || null;
                  }
                  if (!tree && callRootsFifo.length) {
                    // caller 链折出来的调用树：按工具名 FIFO 认领。
                    const ci = callRootsFifo.findIndex((r) => r.name === tname);
                    if (ci >= 0) tree = callRootsFifo.splice(ci, 1)[0];
                  }
                  steps.push(
                    <FunctionStep
                      key={`fn_${i}`}
                      block={b}
                      tree={tree}
                      running={!!b.tool_call_id && runningToolIds.has(b.tool_call_id)}
                    />,
                  );
                });
                // 没配到 spawn 块的卡兜底成子代理行，不丢。
                cardFifo.forEach((card) => {
                  steps.push(
                    <SubAgentStep key={`sub_${card.id}`} card={card} />,
                  );
                });
                rendered.push(
                  <ExecutionStrip
                    key={`seg_${si}`}
                    streaming={streaming}
                    subagentHeads={spawnHeads(seg.cards)}
                    label={execStripLabel(
                      seg.items.map(({ b }) => b), spawnNames(seg.cards), text)}
                  >
                    {steps}
                  </ExecutionStrip>,
                );
              });
              if (!hasTextBlock && hasContent) {
                rendered.push(
                  <MarkdownText key="legacy_content" text={contentText} />,
                );
              }
              // Render any leftover runtime children that none of the
              // tool blocks matched (legacy sessions whose extra.blocks
              // never recorded the agentic tool). Keeps RuntimeBlocks
              // from going missing on old data.
              if (fifo.length > 0) {
                rendered.push(
                  <div
                    key="legacy_runtime"
                    className="assistant-runtime-children"
                  >
                    {fifo.map((c) => (
                      <RuntimeBlock key={c.id} msg={c} nested />
                    ))}
                  </div>,
                );
              }
              // 兜底：blocks 里没记 agent/task 调用块的老数据。仍使用
              // 同一时间线行，不退回 AttachCard，避免刷新前后形态变化。
              if (attachFifo.length > 0) {
                rendered.push(
                  <ExecutionStrip
                    key="legacy_subagents"
                    streaming={streaming}
                    subagentHeads={spawnHeads(attachFifo)}
                    label={execStripLabel([], spawnNames(attachFifo), text)}
                  >
                    {attachFifo.map((card) => (
                      <SubAgentStep key={`sub_${card.id}`} card={card} />
                    ))}
                  </ExecutionStrip>,
                );
              }
              externalAttachCards.forEach((card) => {
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
              // 进行中不再叠加尾部呼吸点：折叠摘要条的 shimmer 已经表达
              // "正在思考/运行"，再挂一个 spinner 就是三种信号打架（用户
              // 截图里的乱象之一）。正文 token 一到，chat-text 自然接上。
              return rendered;
            })()
          ) : (
            <>
              {msg.thinking ? (
                <ThinkingStep text={msg.thinking} />
              ) : null}
              {(() => {
                // Filter agentic tool calls out of the folded "Tool calls"
                // card — they have their own RuntimeBlock (gui_agent
                // function card with Execution DAG, params, return
                // preview). Without this filter the user sees BOTH a
                // generic "Tool calls (1)" row AND the RuntimeBlock,
                // which double-renders the same call.
                const nonAgentic = tools.filter(
                  (t) => !AGENTIC_TOOL_NAMES.has(t.tool || ""),
                );
                return nonAgentic.length > 0
                  ? <ToolsBlock tools={nonAgentic} />
                  : null;
              })()}
              {/* 无 blocks 的旧会话仍用普通时间线行，不切回卡片 UI。 */}
              {attachFifo.length > 0 ? (
                <ExecutionStrip
                  streaming={streaming}
                  subagentHeads={spawnHeads(attachFifo)}
                  label={execStripLabel([], spawnNames(attachFifo), text)}
                >
                  {attachFifo.map((card) => (
                    <SubAgentStep key={`sub_${card.id}`} card={card} />
                  ))}
                </ExecutionStrip>
              ) : null}
              {externalAttachCards.map((card) => (
                <div key={`attach_${card.id}`} className="attach-row" data-msg-id={card.id}>
                  <AttachCard msg={card} />
                </div>
              ))}
              {/* Streaming fallback (msg.blocks not yet built): runtime
                  children BEFORE the chat-text so the final reply sits
                  below the function call card — matches the persisted
                  block order on refresh. */}
              {runtimeChildren.length > 0 ? (
                <div className="assistant-runtime-children">
                  {runtimeChildren.map((c) => (
                    <RuntimeBlock key={c.id} msg={c} nested />
                  ))}
                </div>
              ) : null}
              {hasContent ? <MarkdownText text={contentText} /> : null}
              {streaming && !hasContent ? <TypingIndicator /> : null}
            </>
          )}
          {verdict ? (
            <details className="goal-verdict">
              <summary>{verdict.summary}</summary>
              <pre>{verdict.json}</pre>
            </details>
          ) : null}
          {!streaming && outboundFiles.length > 0 ? (
            <AttachmentChips items={outboundFiles} />
          ) : null}
          {!streaming && msg.id ? (
            <TurnFilesChips
              assistantMsgId={msg.id}
              blocks={msg.blocks}
              summary={msg.turnFiles}
              initiallyReverted={msg.reverted}
              sessionIdOverride={sessionIdOverride}
            />
          ) : null}
        </div>
      )}
      {/* Action row at the BOTTOM-RIGHT of the message — you finish
          reading, then reach for copy/retry/branch right where your
          eyes land, instead of back up at the header. */}
      <div className="message-actions-footer">
        {streaming ? (
          <div className="message-actions">
            <MessageTimestamp timestamp={msg.timestamp} />
          </div>
        ) : (
          <MessageActions msg={msg} />
        )}
      </div>
    </div>
  );
}
