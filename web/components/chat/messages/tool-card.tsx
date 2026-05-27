"use client";

/**
 * Regular-tool-call card — each tool call renders as its own
 * ``.inline-tree`` (the same frame agentic functions use via
 * ``RuntimeBlock`` → ``ExecutionDag``). No outer "Tool calls (N)"
 * grouping — when the LLM calls three functions in a turn the user
 * sees three stacked cards, the same way agentic function calls
 * stack.
 *
 * Header label = ``fnName(args)`` so the user sees what was called.
 * Body shows the args / result blocks when expanded.
 */
import { useState } from "react";

import type { ChatToolCall } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";

function ToolInlineTree({ call }: { call: ChatToolCall }) {
  const [collapsed, setCollapsed] = useState(true);
  const { text } = useTranslation();
  const running = call.status === "running";
  const errored = call.isError || call.status === "error";
  return (
    <div
      className={"inline-tree" + (errored ? " is-error" : "")}
      data-collapsed={collapsed ? "1" : "0"}
      data-call-id={call.id}
    >
      <div
        className="inline-tree-header"
        onClick={() => setCollapsed((c) => !c)}
      >
        <span>
          {running ? (
            <span className="pulse" style={{ color: "var(--accent-blue)" }}>
              {"●"}
            </span>
          ) : errored ? (
            <span style={{ color: "var(--accent-red)" }}>{"✗"}</span>
          ) : (
            <span
              style={{
                color: "var(--accent-cyan)",
                fontStyle: "italic",
                fontWeight: 700,
                fontFamily: "Georgia, 'Times New Roman', serif",
              }}
              title="function"
            >
              {"ƒ"}
            </span>
          )}{" "}
          {text("Function call", "函数调用")}
        </span>
        <span className="inline-tree-actions">
          <span
            className={"inline-tree-toggle" + (collapsed ? " collapsed" : "")}
          >
            {"▶"}
          </span>
        </span>
      </div>
      <div className={"inline-tree-body" + (collapsed ? " collapsed" : "")}>
        {call.tool ? (
          <div className="chat-tool-section">
            <div className="chat-tool-section-label">{text("function", "函数")}</div>
            <pre className="chat-tool-pre">{call.tool}</pre>
          </div>
        ) : null}
        <div className="chat-tool-section">
          <div className="chat-tool-section-label">{text("args", "参数")}</div>
          <pre className="chat-tool-pre">{call.input}</pre>
        </div>
        {call.result !== undefined ? (
          <div className="chat-tool-section chat-tool-result-section">
            <div className="chat-tool-section-label">
              {text("result", "结果")}
            </div>
            <pre className="chat-tool-pre chat-tool-result">{call.result}</pre>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function ToolsBlock({ tools }: { tools: ChatToolCall[] }) {
  if (!tools.length) return null;
  return (
    <>
      {tools.map((t) => (
        <ToolInlineTree key={t.id} call={t} />
      ))}
    </>
  );
}
