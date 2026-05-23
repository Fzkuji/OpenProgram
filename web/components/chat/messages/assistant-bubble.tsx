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

import { MessageActions } from "./message-actions";
import { renderMarkdown, useMarkdownReady } from "./markdown";
import { ThinkingBlock } from "./thinking-block";
import { ToolsBlock } from "./tool-card";

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

  return (
    <div className="message assistant" data-msg-id={msg.id}>
      <div className="message-header">
        <div className="message-avatar bot-avatar">A</div>
        <div className="message-sender">Agentic</div>
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
          {/* Terminal-state badges. "interrupted" = worker restarted
              mid-turn (amber); "cancelled" = user clicked stop (gray).
              Real model / network errors fall into the status==="error"
              branch above which renders the whole bubble as an error
              block — these other terminal states are softer and sit at
              the bottom of the bubble so any partial output above
              stays readable. */}
          {msg.status === "interrupted" ? (
            <div className="bubble-badge bubble-badge-interrupted">
              Interrupted — worker restarted mid-turn
            </div>
          ) : null}
          {msg.status === "cancelled" ? (
            <div className="bubble-badge bubble-badge-cancelled">
              Cancelled
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
