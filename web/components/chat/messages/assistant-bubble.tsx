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
} from "@/lib/agent-style";
import { useTranslation } from "@/lib/i18n";

import { MessageActions } from "./message-actions";
import { renderMarkdown, useMarkdownReady } from "./markdown";
import { RuntimeBlock } from "./runtime-block";
import { ThinkingBlock } from "./thinking-block";
import { ToolsBlock } from "./tool-card";
import { TurnFilesChips } from "./turn-files-chips";

function TypingIndicator({ name }: { name: string }) {
  return (
    <div className="pending-body">
      <span className="pending-pulse" aria-hidden="true" />
      <span className="pending-label">{`${name} is thinking…`}</span>
    </div>
  );
}

export function AssistantBubble({ msg }: { msg: ChatMsg }) {
  // Subscribed so the bubble re-renders once `renderMd` lands and the
  // markdown can be rendered for real instead of escaped.
  useMarkdownReady();
  // Subscribed so the avatar/name pick up edits made in
  // /settings/general → Agent without a reload.
  useAgentProfile();
  const { text } = useTranslation();
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
            <RuntimeBlock msg={rc} />
          </div>
        );
      }
      return null;
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
      className="message assistant"
      data-msg-id={msg.id}
      data-agent-id={msg.agentId || undefined}
    >
      <div className="message-header">
        <div
          className="message-avatar bot-avatar"
          style={color ? { background: color, color: "#fff" } : undefined}
          title={msg.agentId || ""}
        >
          {initial}
        </div>
        <div className="message-sender">{sender}</div>
        {!streaming ? <MessageActions msg={msg} /> : null}
      </div>

      {msg.status === "error" ? (
        <div className="error-content">{msg.content || text("Request failed.", "请求失败。")}</div>
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
              const rendered = msg.blocks.map((b, i) => renderBlock(b, i, fifo));
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
                      <RuntimeBlock key={c.id} msg={c} />
                    ))}
                  </div>,
                );
              }
              // While streaming and no final chat-text has landed yet,
              // tail the body with the breathing pulse. Bottom of the
              // bubble, aligned to the chat-text column.
              if (streaming && !hasContent) {
                rendered.push(<TypingIndicator key="typing_tail" name={sender} />);
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
              {hasContent ? (
                <div
                  className="chat-text message-content"
                  dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }}
                />
              ) : null}
              {runtimeChildren.length > 0 ? (
                <div className="assistant-runtime-children">
                  {runtimeChildren.map((c) => (
                    <RuntimeBlock key={c.id} msg={c} />
                  ))}
                </div>
              ) : null}
              {streaming && !hasContent ? <TypingIndicator name={sender} /> : null}
            </>
          )}
          {!streaming && msg.id ? (
            <TurnFilesChips assistantMsgId={msg.id} />
          ) : null}
        </div>
      )}
    </div>
  );
}
