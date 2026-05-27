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

/** Compact a raw JSON args string for the header signature —
 *  mirrors the legacy `_compactToolArgs` intent without its full
 *  path-shortening: parse, drop to a short `k: v, …` form, cap length. */
function compactArgs(raw: string): string {
  if (!raw) return "";
  let obj: unknown;
  try {
    obj = JSON.parse(raw);
  } catch {
    return raw.length > 60 ? raw.slice(0, 60) + "…" : raw;
  }
  if (!obj || typeof obj !== "object") return String(obj);
  const parts = Object.entries(obj as Record<string, unknown>).map(
    ([k, v]) => {
      let val = typeof v === "string" ? v : JSON.stringify(v);
      if (val.length > 28) val = val.slice(0, 28) + "…";
      return `${k}: ${val}`;
    },
  );
  const joined = parts.join(", ");
  return joined.length > 80 ? joined.slice(0, 80) + "…" : joined;
}

function ToolInlineTree({ call }: { call: ChatToolCall }) {
  const [collapsed, setCollapsed] = useState(true);
  const { text } = useTranslation();
  const argsLabel = compactArgs(call.input);
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
            <span style={{ color: "var(--accent-cyan)" }}>{"◆"}</span>
          )}{" "}
          <span className="runtime-func">{call.tool || "?"}</span>
          (<span className="runtime-params">{argsLabel}</span>)
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
