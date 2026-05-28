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

/** Single tree-node row for one tool call. Clicking it opens the
 *  right-rail detail panel (same UX as the execution-tree's
 *  TreeNodeRow) rather than expanding args/result inline. */
function ToolNodeRow({ call }: { call: ChatToolCall }) {
  const running = call.status === "running";
  const errored = call.isError || call.status === "error";
  const status = running ? "running" : errored ? "error" : "success";
  const icon =
    status === "success" ? (
      <span style={{ color: "var(--accent-green)" }}>{"✓"}</span>
    ) : status === "error" ? (
      <span style={{ color: "var(--accent-red)" }}>{"✗"}</span>
    ) : (
      <span className="indicator-dot pulse-opacity" />
    );

  // Build a 1-line result preview from the raw result string.
  let preview = "";
  if (call.result !== undefined && call.result !== null) {
    const s = String(call.result).replace(/\s+/g, " ").trim();
    preview = s.length > 80 ? s.slice(0, 80) + "…" : s;
  }

  function openDetail() {
    let parsedArgs: Record<string, unknown> | undefined;
    try {
      const v = JSON.parse(call.input);
      if (v && typeof v === "object") parsedArgs = v as Record<string, unknown>;
    } catch {
      /* leave undefined; detail panel handles missing params */
    }
    const node = {
      path: "tool/" + call.id,
      name: call.tool || "?",
      status,
      params: parsedArgs,
      output: call.result,
    };
    (window as unknown as { showDetail?: (n: unknown) => void }).showDetail?.(
      node,
    );
  }

  return (
    <div className="tree-node">
      <div className="node-row" onClick={openDetail} style={{ cursor: "pointer" }}>
        <span className="node-toggle leaf">{"▶"}</span>
        <span className="node-icon">{icon}</span>
        <span className="node-name">{call.tool || "?"}</span>
        <span className={"node-status " + status}>{status}</span>
        {preview ? (
          <span className="node-output-preview">{preview}</span>
        ) : null}
      </div>
    </div>
  );
}

function ToolInlineTree({ call }: { call: ChatToolCall }) {
  const [collapsed, setCollapsed] = useState(true);
  const [copied, setCopied] = useState(false);
  const { text } = useTranslation();
  const running = call.status === "running";
  const errored = call.isError || call.status === "error";

  function copy(e: React.MouseEvent) {
    e.stopPropagation();
    let parsedArgs: unknown = call.input;
    try { parsedArgs = JSON.parse(call.input); } catch { /* keep raw */ }
    const payload = {
      tool: call.tool,
      id: call.id,
      args: parsedArgs,
      result: call.result,
      status: call.status,
      is_error: call.isError,
    };
    const json = JSON.stringify(payload, null, 2);
    const done = () => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    };
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(json).then(done, done);
    } else { done(); }
  }
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
            <span className="indicator-dot pulse-opacity" />
          ) : errored ? (
            <span style={{ color: "var(--accent-red)" }}>{"✗"}</span>
          ) : (
            <span className="inline-tree-script" title="function">{"𝓕"}</span>
          )}
          {"\u00a0\u00a0"}
          {text("Function call", "函数调用")}
        </span>
        <span className="inline-tree-actions">
          <button
            className={"inline-tree-copy" + (copied ? " copied" : "")}
            title={text("Copy call as JSON", "复制调用 JSON")}
            onClick={copy}
          >
            {copied ? text("Copied", "已复制") : text("Copy", "复制")}
          </button>
          <span
            className={"inline-tree-toggle" + (collapsed ? " collapsed" : "")}
          >
            {"▶"}
          </span>
        </span>
      </div>
      <div className={"inline-tree-body" + (collapsed ? " collapsed" : "")}>
        <ToolNodeRow call={call} />
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
