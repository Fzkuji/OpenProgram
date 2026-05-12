"use client";

/** Card-style rendering for ``display: "runtime"`` messages (function calls). */
import { useState } from "react";
import { Loader2 } from "lucide-react";
import { type ChatMsg } from "@/lib/session-store";

export function RuntimeBlock({ msg }: { msg: ChatMsg }) {
  const [expanded, setExpanded] = useState(true);
  const headerColor =
    msg.status === "error"
      ? "var(--accent-red)"
      : msg.status === "cancelled"
        ? "var(--accent-yellow)"
        : msg.status === "done"
          ? "var(--accent-green)"
          : "var(--accent-blue)";
  return (
    <div
      className="mx-auto w-full max-w-[90%] overflow-hidden rounded-lg border"
      style={{
        background: "var(--bg-secondary)",
        borderColor: "var(--border)",
      }}
    >
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 border-b px-3 py-2 text-left"
        style={{
          background: "var(--bg-tertiary)",
          borderColor: "var(--border)",
        }}
      >
        <span
          className="h-2 w-2 shrink-0 rounded-full"
          style={{ background: headerColor }}
        />
        <span
          className="font-mono text-[12px] font-medium"
          style={{ color: "var(--text-bright)" }}
        >
          {msg.function ?? "runtime"}
        </span>
        <span
          className="text-[10px]"
          style={{ color: "var(--text-muted)" }}
        >
          {msg.status === "streaming"
            ? "running..."
            : msg.status === "done"
              ? "✓"
              : msg.status === "error"
                ? "error"
                : msg.status === "cancelled"
                  ? "cancelled"
                  : "pending"}
        </span>
        <span className="ml-auto text-[10px]" style={{ color: "var(--text-muted)" }}>
          {expanded ? "▼" : "▶"}
        </span>
      </button>
      {expanded && (
        <pre
          className="max-h-[400px] overflow-auto whitespace-pre-wrap p-3 font-mono text-[11px]"
          style={{
            background: "var(--bg-input)",
            color: "var(--text-primary)",
          }}
        >
          {msg.content ||
            (msg.status === "streaming"
              ? <Loader2 className="inline h-3 w-3 animate-spin" />
              : "(empty)")}
        </pre>
      )}
    </div>
  );
}
