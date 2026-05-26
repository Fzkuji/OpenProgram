"use client";

/**
 * Assistant message bubble — React port of the legacy
 * `.message.assistant` + `.chat-stream-body` scaffold.
 *
 * Layout order matches chat-ws.js: Thinking block, then the Tool-calls
 * card, then the answer text. While the turn is still streaming with
 * nothing rendered yet, a typing indicator stands in.
 */
import type { ChatMsg } from "@/lib/session-store";
import { agentColor, agentDisplayName, agentInitial } from "@/lib/agent-style";

import { MessageActions } from "./message-actions";
import { renderMarkdown, useMarkdownReady } from "./markdown";
import { ThinkingBlock } from "./thinking-block";
import { ToolsBlock } from "./tool-card";
import { TurnFilesChips } from "./turn-files-chips";

function TypingIndicator() {
  return (
    <div className="typing-indicator">
      <div className="dot" />
      <div className="dot" />
      <div className="dot" />
    </div>
  );
}

export function AssistantBubble({ msg }: { msg: ChatMsg }) {
  // Subscribed so the bubble re-renders once `renderMd` lands and the
  // markdown can be rendered for real instead of escaped.
  useMarkdownReady();
  const streaming = msg.status === "streaming" || msg.status === "pending";
  const tools = msg.tools ?? [];
  const hasContent = !!msg.content;
  const empty = !hasContent && !msg.thinking && tools.length === 0;

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
        <div className="error-content">{msg.content || "Request failed."}</div>
      ) : empty && streaming ? (
        <TypingIndicator />
      ) : (
        <div className="chat-stream-body">
          {msg.thinking ? (
            <ThinkingBlock text={msg.thinking} streaming={streaming} />
          ) : null}
          {tools.length > 0 ? <ToolsBlock tools={tools} /> : null}
          {hasContent ? (
            <div
              className="chat-text message-content"
              dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }}
            />
          ) : null}
          {!streaming && msg.id ? (
            <TurnFilesChips assistantMsgId={msg.id} />
          ) : null}
        </div>
      )}
    </div>
  );
}
