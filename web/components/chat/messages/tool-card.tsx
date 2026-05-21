"use client";

/**
 * Tool-calls card — React port of the legacy `.chat-tools` / `.chat-tool`
 * scaffold (chat-ws.js). All calls in one assistant turn collapse into
 * a single bordered card; each row inside is itself collapsible.
 *
 * Reuses 05-chat.css: both the outer card and each row fold purely on
 * the `data-collapsed` attribute, so this component just flips strings.
 */
import { useState } from "react";

import type { ChatToolCall } from "@/lib/session-store";

/** Compact a raw JSON args string for the one-line row preview —
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

function ToolRow({ call }: { call: ChatToolCall }) {
  const [collapsed, setCollapsed] = useState(true);
  const status =
    call.status === "running"
      ? "running…"
      : call.status === "error"
        ? "error"
        : "done";
  return (
    <div
      className={`chat-tool${call.isError ? " is-error" : ""}`}
      data-collapsed={collapsed ? "1" : "0"}
      data-call-id={call.id}
    >
      <button
        type="button"
        className="chat-fold-btn"
        onClick={() => setCollapsed((c) => !c)}
        onMouseDown={(e) => e.preventDefault()}
      >
        <span className="chat-fold-caret">{"▶"}</span>
        <span className="chat-fold-label">
          <span className="chat-tool-name">{call.tool || "?"}</span>
          <span className="chat-tool-args">({compactArgs(call.input)})</span>
        </span>
        <span className="chat-fold-elapsed chat-tool-status">{status}</span>
      </button>
      <div className="chat-fold-content">
        <div className="chat-tool-section">
          <div className="chat-tool-section-label">args</div>
          <pre className="chat-tool-pre">{call.input}</pre>
        </div>
        {call.result !== undefined ? (
          <div className="chat-tool-section chat-tool-result-section">
            <div className="chat-tool-section-label">result</div>
            <pre className="chat-tool-pre chat-tool-result">{call.result}</pre>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function ToolsBlock({ tools }: { tools: ChatToolCall[] }) {
  const [collapsed, setCollapsed] = useState(true);
  if (!tools.length) return null;
  const hasRunning = tools.some((t) => t.status === "running");
  return (
    <div className="chat-tools inline-tree" data-collapsed={collapsed ? "1" : "0"}>
      <div
        className="inline-tree-header chat-tools-header"
        onClick={() => setCollapsed((c) => !c)}
      >
        <span>
          <span
            style={{
              color: "var(--accent-cyan)",
              display: "inline-block",
              animation: hasRunning
                ? "tool-dot-pulse 1.1s ease-in-out infinite"
                : undefined,
            }}
          >
            ◆
          </span>
          {" Tool calls "}
          <span className="chat-tools-count">{tools.length}</span>
        </span>
        <span className="inline-tree-actions">
          <span className="inline-tree-toggle chat-tools-toggle">{"▶"}</span>
        </span>
      </div>
      <div className="inline-tree-body chat-tools-body">
        {tools.map((t) => (
          <ToolRow key={t.id} call={t} />
        ))}
      </div>
      {/* Pulse keyframe — colocated so this file owns the animation. */}
      <style jsx>{`
        @keyframes tool-dot-pulse {
          0%, 100% { transform: scale(1); opacity: 1; }
          50%      { transform: scale(1.35); opacity: 0.55; }
        }
      `}</style>
    </div>
  );
}
