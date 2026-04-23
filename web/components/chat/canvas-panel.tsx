"use client";

import { useQuery } from "@tanstack/react-query";
import { FileText, X, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useState } from "react";

/**
 * Live view of the agent's canvas.md.
 *
 * Polls GET /api/canvas every 2s so blocks the agent writes via the
 * ``canvas`` tool show up without a reload. Keeps the render simple
 * — plain ``<pre>`` with per-block separators — rather than pulling
 * in a markdown renderer; the block structure is already scannable
 * at a glance and adding react-markdown doubles our frontend deps
 * for one panel.
 *
 * Shows the resolved path + last-modified time in the header so the
 * user knows which file this is and whether it's actively updating.
 */
export function CanvasPanel({ onClose }: { onClose: () => void }) {
  const [pathOverride, _setPathOverride] = useState<string | undefined>(
    undefined,
  );

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["canvas", pathOverride ?? ""],
    queryFn: () => api.getCanvas(pathOverride),
    refetchInterval: 2000,
    refetchOnWindowFocus: true,
  });

  const mtimeLabel = data?.mtime
    ? new Date(data.mtime).toLocaleTimeString()
    : "—";

  return (
    <aside
      className="flex w-[440px] shrink-0 flex-col border-l"
      style={{ borderColor: "var(--border-color)", background: "var(--bg-primary)" }}
    >
      <header
        className="flex shrink-0 items-center justify-between border-b px-3 py-2"
        style={{ borderColor: "var(--border-color)" }}
      >
        <div className="flex items-center gap-2 min-w-0">
          <FileText className="h-4 w-4 shrink-0" style={{ color: "var(--text-muted)" }} />
          <div className="min-w-0">
            <div className="text-[12px] truncate" style={{ color: "var(--text-secondary)" }}>
              Canvas
            </div>
            <div
              className="text-[10px] truncate font-mono"
              style={{ color: "var(--text-muted)" }}
              title={data?.path ?? ""}
            >
              {data?.path ?? "—"}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <span
            className="text-[10px] tabular-nums"
            style={{ color: "var(--text-muted)" }}
            title="Last modified"
          >
            {mtimeLabel}
          </span>
          <Button
            size="icon"
            variant="ghost"
            className="h-7 w-7"
            onClick={() => refetch()}
            title="Refresh now"
            disabled={isFetching}
          >
            <RefreshCw
              className={`h-3.5 w-3.5 ${isFetching ? "animate-spin" : ""}`}
            />
          </Button>
          <Button size="icon" variant="ghost" className="h-7 w-7" onClick={onClose} title="Close">
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      </header>

      {data?.blocks && data.blocks.length > 0 && (
        <div
          className="shrink-0 border-b px-3 py-1.5 text-[10px] flex gap-2 flex-wrap"
          style={{ borderColor: "var(--border-color)", color: "var(--text-muted)" }}
        >
          {data.blocks.map((b) => (
            <span
              key={b.id}
              className="rounded px-1.5 py-0.5 font-mono"
              style={{ background: "var(--bg-tertiary)" }}
              title={`${b.length} chars`}
            >
              {b.id}
            </span>
          ))}
        </div>
      )}

      <div className="flex-1 overflow-auto">
        {error && (
          <div className="p-3 text-[12px]" style={{ color: "var(--accent-red)" }}>
            Failed to load canvas: {String(error)}
          </div>
        )}
        {isLoading && !data && (
          <div className="p-3 text-[12px]" style={{ color: "var(--text-muted)" }}>
            Loading…
          </div>
        )}
        {data && !data.exists && (
          <div className="p-3 text-[12px]" style={{ color: "var(--text-muted)" }}>
            Canvas file doesn&apos;t exist yet. It will appear when the agent writes
            its first block via the <code className="font-mono">canvas</code> tool.
          </div>
        )}
        {data?.exists && (
          <pre
            className="whitespace-pre-wrap break-words px-3 py-2 text-[12px] font-mono"
            style={{ color: "var(--text-primary)" }}
          >
            {data.content || "(empty)"}
          </pre>
        )}
      </div>
    </aside>
  );
}
