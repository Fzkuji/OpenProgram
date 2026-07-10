"use client";

/**
 * Assistant message bubble — React port of the legacy
 * `.message.assistant` + `.chat-stream-body` scaffold.
 *
 * Layout order matches chat-ws.js: Thinking block, then the Tool-calls
 * card, then the answer text. While the turn is still streaming with
 * nothing rendered yet, a typing indicator stands in.
 */
import type { AssistantBlock, ChatMsg, ChatToolCall } from "@/lib/session-store";
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
import type { TNode } from "./execution-dag/types";
import { MessageActions } from "./message-actions";
import { useAvatarAlign } from "./use-avatar-align";
import { renderMarkdown, useMarkdownReady } from "./markdown";
import { RuntimeBlock } from "./runtime-block";
import { ThinkingBlock } from "./thinking-block";
import { ToolsBlock } from "./tool-card";
import { TurnFilesChips } from "./turn-files-chips";

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

export function AssistantBubble({ msg }: { msg: ChatMsg }) {
  // Subscribed so the bubble re-renders once `renderMd` lands and the
  // markdown can be rendered for real instead of escaped.
  useMarkdownReady();
  // Subscribed so the avatar/name pick up edits made in
  // /settings/general → Agent without a reload.
  const profile = useAgentProfile();
  const { text } = useTranslation();
  // Align the side avatar to the first line of text (re-measures as the
  // message grows / blocks expand).
  const { containerRef, avatarTop } = useAvatarAlign(
    `${msg.id}:${msg.content?.length || 0}:${msg.blocks?.length || 0}:${msg.status}`,
  );
  const streaming =
    msg.status === "streaming" ||
    msg.status === "pending" ||
    msg.status === "running";
  const tools = msg.tools ?? [];
  const hasContent = !!msg.content;

  const AGENTIC_TOOL_NAMES = new Set(["gui_agent", "research_agent", "wiki_agent"]);
  const runtimeChildren = msg.runtimeChildren ?? [];
  const runtimeByToolId = new Map<string, ChatMsg>();
  for (const rc of runtimeChildren) {
    // RuntimeBlock children carry tool_call_id on the placeholder
    // row's id-suffix or function field — best effort match by
    // searching the children that haven't been consumed yet via
    // function name. Falls back to FIFO when ids don't match (older
    // wrappers didn't stamp tool_call_id).
    // Runtime placeholder ids are stamped as
    // `<assistant_msg_id>_rt_<tool_call_id>` by
    // _wrap_agentic_runtime_block — extract the tool_call_id suffix.
    const m = rc.id ? rc.id.match(/_rt_(.+)$/) : null;
    const tid = m ? m[1] : undefined;
    if (tid) runtimeByToolId.set(tid, rc);
  }
  // Spawned/attach 卡按调用顺序排队：每遇到一个 tool==="task" 的块就取
  // 一张，画在该工具块的紧后面——思考 → 工具调用 → Spawned 卡 → 回复
  //（在哪调用就画在哪）。剩下没配到块的卡（老数据没记 blocks）兜底画
  // 在回复文本之前。
  const attachFifo = [...(msg.attachCards ?? [])];
  // Renders one block in its source-order position.
  const renderBlock = (b: AssistantBlock, idx: number, fifo: ChatMsg[]) => {
    if (b.type === "thinking") {
      return <ThinkingBlock key={`thk_${idx}`} text={b.text || ""} streaming={streaming} />;
    }
    if (b.type === "text") {
      return (
        <div
          key={`txt_${idx}`}
          className="chat-text message-content"
          dangerouslySetInnerHTML={{ __html: renderMarkdown(b.text || "") }}
        />
      );
    }
    // tool block
    const tname = b.tool || "";
    if (AGENTIC_TOOL_NAMES.has(tname)) {
      const rc =
        (b.tool_call_id && runtimeByToolId.get(b.tool_call_id)) || fifo.shift();
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
          {msg.blocks && msg.blocks.length > 0 ? (
            (() => {
              // FIFO pool of unmatched agentic runtime children, used
              // when a tool block lacks a tool_call_id we can map to.
              const usedIds = new Set<string>();
              for (const b of msg.blocks) {
                if (b.type === "tool" && b.tool_call_id
                    && runtimeByToolId.has(b.tool_call_id)) {
                  usedIds.add(b.tool_call_id);
                }
              }
              const fifo = runtimeChildren.filter((rc) => {
                const m = rc.id ? rc.id.match(/_rt_(.+)$/) : null;
                const tid = m ? m[1] : undefined;
                return !tid || !usedIds.has(tid);
              });
              // Legacy backfill: pre-block-schema sessions only
              // persisted tool blocks (no text/thinking), but
              // ``msg.content`` carries the LLM's final narration.
              // If blocks has zero text entries, append the content
              // as one text node after the tool cards so the user
              // still sees the answer.
              const hasTextBlock = msg.blocks.some((b) => b.type === "text");
              // ── 分段：text 块常驻；连续的 thinking/tool 块聚成一段
              // 执行痕迹。已落定的轮次把每段折成一条摘要条（点击展开
              // 逐块序列）；流式进行中的轮次平铺，让用户实时看到它在
              // 干嘛。段内出现 task/message_branch 调用时，把对应的
              // Spawned 卡（一行态）挂在该段摘要条下面——在哪调用就
              // 画在哪。
              type ExecSeg = {
                kind: "exec";
                items: Array<{ b: AssistantBlock; i: number }>;
                cards: ChatMsg[];
              };
              type TextSeg = { kind: "text"; b: AssistantBlock; i: number };
              const segs: Array<ExecSeg | TextSeg> = [];
              msg.blocks.forEach((b, i) => {
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
                if (streaming) {
                  // 进行中：平铺实时块 + spawn 卡跟在调用块后。
                  const blockNodes: React.ReactNode[] = [];
                  seg.items.forEach(({ b, i }) => {
                    blockNodes.push(renderBlock(b, i, fifo));
                    if (b.type === "tool" && SPAWNING_TOOL_NAMES.has(b.tool || "")
                        && cardFifo.length > 0) {
                      const card = cardFifo.shift()!;
                      blockNodes.push(
                        <div
                          key={`attach_${card.id}`}
                          className="attach-row"
                          data-msg-id={card.id}
                        >
                          <AttachCard msg={card} />
                        </div>,
                      );
                    }
                  });
                  rendered.push(<div key={`seg_${si}`}>{blockNodes}</div>);
                  return;
                }
                // 落定的轮次：时间线步骤（chat-turn-visual-spec.html）。
                // thinking → 思考行；spawn 调用 → 子代理行；agentic 工具
                // → 函数行 + context_tree 递归子层级；普通工具 → 函数行。
                const steps: React.ReactNode[] = [];
                seg.items.forEach(({ b, i }) => {
                  if (b.type === "thinking") {
                    steps.push(<ThinkingStep key={`thk_${i}`} text={b.text || ""} />);
                    return;
                  }
                  if (b.type !== "tool") return;
                  const tname = b.tool || "";
                  if (SPAWNING_TOOL_NAMES.has(tname) && cardFifo.length > 0) {
                    const card = cardFifo.shift()!;
                    steps.push(
                      <div key={`sub_${card.id}`} data-msg-id={card.id}>
                        <SubAgentStep card={card} />
                      </div>,
                    );
                    return;
                  }
                  let tree: TNode | null = null;
                  if (AGENTIC_TOOL_NAMES.has(tname)) {
                    const rc =
                      (b.tool_call_id && runtimeByToolId.get(b.tool_call_id))
                      || fifo.shift();
                    tree = (rc?.contextTree as TNode | undefined) || null;
                  }
                  steps.push(<FunctionStep key={`fn_${i}`} block={b} tree={tree} />);
                });
                // 没配到 spawn 块的卡兜底成子代理行，不丢。
                cardFifo.forEach((card) => {
                  steps.push(
                    <div key={`sub_${card.id}`} data-msg-id={card.id}>
                      <SubAgentStep card={card} />
                    </div>,
                  );
                });
                const spawnNames = seg.cards.map((c) =>
                  (c.attach?.label || "").trim()
                  || (c.attach?.head_id || "").slice(0, 8)
                  || text("sub-agent", "子代理"));
                rendered.push(
                  <ExecutionStrip
                    key={`seg_${si}`}
                    label={execStripLabel(
                      seg.items.map(({ b }) => b), spawnNames, text)}
                  >
                    {steps}
                  </ExecutionStrip>,
                );
              });
              if (!hasTextBlock && hasContent) {
                rendered.push(
                  <div
                    key="legacy_content"
                    className="chat-text message-content"
                    dangerouslySetInnerHTML={{
                      __html: renderMarkdown(msg.content),
                    }}
                  />,
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
              // 兜底：blocks 里没记 task 调用块的老数据——剩余的
              // Spawned 卡仍画在本轮内部（尾部），不丢。
              attachFifo.forEach((card) => {
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
              // While streaming and no final chat-text has landed yet,
              // tail the body with the breathing pulse. Bottom of the
              // bubble, aligned to the chat-text column.
              if (streaming && !hasContent) {
                rendered.push(<TypingIndicator key="typing_tail" />);
              }
              return rendered;
            })()
          ) : (
            <>
              {msg.thinking ? (
                <ThinkingBlock text={msg.thinking} streaming={streaming} />
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
              {/* Spawned 卡：无 blocks 的回退分支里画在工具卡之后、
                  回复文本之前——与调用发生的位置一致。 */}
              {attachFifo.map((card) => (
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
              {hasContent ? (
                <div
                  className="chat-text message-content"
                  dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }}
                />
              ) : null}
              {streaming && !hasContent ? <TypingIndicator /> : null}
            </>
          )}
          {!streaming && msg.id ? (
            <TurnFilesChips assistantMsgId={msg.id} />
          ) : null}
        </div>
      )}
      {/* Action row at the BOTTOM-RIGHT of the message — you finish
          reading, then reach for copy/retry/branch right where your
          eyes land, instead of back up at the header. */}
      {!streaming ? (
        <div className="message-actions-footer">
          <MessageActions msg={msg} />
        </div>
      ) : null}
    </div>
  );
}
